"""V2.2 SEC XBRL resolver: annual-first, label-first, total-vs-component aware.

The underlying SEC parser/fetcher remains in sec_xbrl_search.py. This layer is
scoring/selection only. Raw SEC payloads are never persisted.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sec_xbrl_search import (
    SECXBRLSearch,
    XBRLCandidate,
    METRIC_TERMS,
    _date,
    _norm,
    _safe_float,
    _annual_duration,
)

INSTANT_METRICS = {
    "assets", "liabilities", "equity", "current_assets", "current_liabilities",
    "inventory", "cash", "receivables",
    "debt_current", "debt_noncurrent", "debt_total",
}
DURATION_METRICS = {
    "revenue", "operating_income", "net_income", "interest_expense",
    "operating_cash_flow", "sga",
}

EXACT_CONCEPTS = {
    "revenue": {"RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet"},
    "operating_income": {"OperatingIncomeLoss"},
    "net_income": {"NetIncomeLoss", "ProfitLoss"},
    "assets": {"Assets"},
    "equity": {"StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "Equity", "ProprietaryCapital"},
    "liabilities": {"Liabilities"},
    "current_assets": {"AssetsCurrent"},
    "current_liabilities": {"LiabilitiesCurrent"},
    "inventory": {"InventoryNet"},
    "cash": {"CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"},
    "receivables": {"AccountsReceivableNetCurrent", "AccountsReceivableNet"},
    "debt_current": {
        "LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
        "LongTermDebtAndFinanceLeaseObligationsCurrent", "CurrentBorrowings",
        "CurrentPortionOfLongtermBorrowings", "ShortTermBorrowings",
        "FinanceLeaseLiabilityCurrent", "ConvertibleDebtCurrent",
        "DebtCurrent", "NotesPayableCurrent", "CommercialPaper",
        "LineOfCreditCurrent", "RevolvingCreditFacilityCurrent",
    },
    "debt_noncurrent": {
        "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent", "NoncurrentBorrowings",
        "LongtermBorrowings", "Borrowings", "FinanceLeaseLiabilityNoncurrent",
        "ConvertibleDebtNoncurrent",
    },
    "debt_total": {
        "LongTermDebt", "DebtAndCapitalLeaseObligations",
        "LongTermDebtCurrentAndNoncurrent", "LongTermDebtAndFinanceLeaseObligations",
        "DebtInstrumentCarryingAmount", "DebtAndFinanceLeaseLiabilities",
        "Debt", "NotesPayable", "LineOfCredit", "RevolvingCreditFacility",
    },
    "interest_expense": {"InterestExpense", "InterestExpenseNonoperating", "InterestAndDebtExpense"},
    "operating_cash_flow": {"NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"},
    "sga": {"SellingGeneralAndAdministrativeExpense"},
    "eps": {"EarningsPerShareDiluted", "EarningsPerShareBasic"},
}

# Broad words are useful only after a metric-specific semantic check. These
# negatives prevent component, income, tax, lease and non-current concepts
# from masquerading as the requested total.
NEGATIVE_TERMS = {
    "operating_income": {"nonoperating", "othernonoperating", "nonoperatingincomeexpense"},
    "current_assets": {"noncurrent"},
    "current_liabilities": {"noncurrent"},
    "liabilities": {
        "liabilitiesandstockholdersequity", "deferredcreditsandotherliabilitiesnoncurrent",
        "accruedliabilities", "accruedliabilitiescurrent", "accountspayableandaccruedliabilitiescurrent",
        "deferredrevenueliabilitiescurrent", "deferredincometaxliabilitiesnet", "longtermdebt",
        "debtcurrent", "operatinglease", "liabilitiesfairvaluedisclosure",
    },
    "interest_expense": {
        "interestpaid", "interestcostscapitalized", "interestincome", "interestincomenonoperating",
        "interestincomeexpense", "unrecognizedtaxbenefits", "taxpenalties", "financeleaseinterestexpense",
    },
    "inventory": {"fifo", "lifo", "finishedgoods", "workinprocess", "rawmaterials", "orestockpiles", "leachpads"},
    "receivables": {"notesandloansreceivable", "notesandloansreceivablenetcurrent", "loansreceivable", "financereceivable"},
    "sga": {"entitycentralindexkey"},
    "debt_current": {
        "noncurrent", "non-current", "longterm", "long term"
    },
    "debt_noncurrent": {
        "current", "shortterm", "short-term"
    },
    "debt_total": {
        "current", "noncurrent", "currentportion", "noncurrentportion"
    },
}

# Label phrases are deliberately stronger than loose concept-name overlap.
# They let filing XBRL identify the economic meaning the user requested.
LABEL_PRIORS = {
    "liabilities": ("total liabilities", "total liabilities and stockholders equity"),
    "current_assets": ("total current assets", "current assets"),
    "current_liabilities": ("total current liabilities", "current liabilities"),
    "inventory": ("inventories", "inventory", "total inventory"),
    "receivables": ("accounts receivable", "accounts receivable, net", "trade accounts receivable"),
    "assets": ("total assets",),
    "equity": ("total equity", "stockholders equity", "shareholders equity", "proprietary capital"),
    "operating_income": ("operating income", "income from operations", "operating profit"),
    "interest_expense": ("interest expense", "interest and debt expense", "finance costs"),
    "sga": ("selling, general and administrative", "selling general and administrative"),
    "debt_current": ("current debt", "current borrowings", "current portion of long-term debt"),
    "debt_noncurrent": ("long-term debt", "noncurrent debt", "noncurrent borrowings"),
    "debt_total": ("total debt", "total borrowings", "debt and capital lease obligations"),
}

MIN_MATCH_SCORE = 25.0


def _latest_annual_fy(submissions: dict[str, Any]) -> int | None:
    """Return FY of the latest annual filing, not the latest 10-Q date."""
    recent = submissions.get("filings", {}).get("recent", {})
    best: tuple[str, int | None] | None = None
    forms = recent.get("form", [])
    filed = recent.get("filingDate", [])
    fys = recent.get("fy", [])
    report_dates = recent.get("reportDate", [])
    for i, form in enumerate(forms):
        if form not in {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
            continue
        fdate = filed[i] if i < len(filed) else ""
        fy = fys[i] if i < len(fys) else None
        try:
            fy_int = int(fy) if fy is not None else None
        except (TypeError, ValueError):
            fy_int = None
        if fy_int is None and i < len(report_dates):
            d = _date(report_dates[i]); fy_int = d.year if d else None
        if best is None or fdate > best[0]:
            best = (fdate, fy_int)
    return best[1] if best else None


def _is_instant(metric: str, start: Any) -> bool:
    return metric in INSTANT_METRICS or (metric == "eps" and not start)


def _negative_hit(metric: str, concept: str) -> str | None:
    compact = _norm(concept).replace(" ", "")
    for bad in NEGATIVE_TERMS.get(metric, ()):
        b = _norm(bad).replace(" ", "")
        if b and b in compact:
            return bad
    return None


def _base_match_score(metric: str, concept: str, label: str = "", explicit_alias: bool = False) -> tuple[float, list[str]]:
    c = _norm(concept)
    l = _norm(label)
    compact = c.replace(" ", "")
    score = 0.0
    reasons: list[str] = []

    if concept in EXACT_CONCEPTS.get(metric, set()):
        score = max(score, 100.0); reasons.append("canonical concept")
    if explicit_alias:
        score = max(score, 95.0); reasons.append("explicit alias")

    # Filing labels carry the strongest semantic evidence for custom concepts.
    for phrase in LABEL_PRIORS.get(metric, ()):
        p = _norm(phrase)
        if p and p in l:
            score = max(score, 90.0)
            reasons.append("label match")
            break

    for term in METRIC_TERMS.get(metric, ()):
        t = _norm(term); tc = t.replace(" ", "")
        if not t:
            continue
        if t in l:
            score = max(score, 80.0); reasons.append("label match")
        elif tc and tc in compact:
            score = max(score, 65.0); reasons.append("concept semantic match")
        elif len(tc) >= 6 and tc in compact:
            score = max(score, 25.0); reasons.append("keyword overlap")

    bad = _negative_hit(metric, concept)
    if bad:
        score -= 100.0
        reasons.append(f"negative semantic: {bad}")
    return score, list(dict.fromkeys(reasons))


def _score(metric: str, concept: str, label: str, instant: bool, end: Any, target_year: int | None, namespace: str, explicit_alias: bool = False, annual: bool = False) -> tuple[float, str]:
    score, reasons = _base_match_score(metric, concept, label, explicit_alias)
    if score <= 0:
        return 0.0, ", ".join(reasons)

    if metric in INSTANT_METRICS:
        if instant: score += 20.0; reasons.append("instant")
        else: score -= 100.0; reasons.append("wrong period type")
    elif metric in DURATION_METRICS:
        if annual: score += 25.0; reasons.append("annual duration")
        elif not instant: score += 10.0; reasons.append("duration")
        else: score -= 100.0; reasons.append("wrong period type")

    if namespace == "us-gaap":
        score += 5.0; reasons.append("us-gaap")

    d = _date(end)
    if target_year is not None and d:
        delta = abs(d.year - target_year)
        rec = max(0.0, 30.0 - delta * 15.0)
        score += rec
        if rec: reasons.append("target FY")
    return score, ", ".join(dict.fromkeys(reasons))


class SECXBRLSearchV2_2(SECXBRLSearch):
    """Annual-first resolver with filing-label semantics and strict totals."""

    def search_company_facts(self, companyfacts: dict[str, Any], metric: str, year: int | None = None, aliases: list[str] | None = None, limit: int = 20):
        aliases = aliases or []
        alias_norm = {_norm(x).replace(" ", "") for x in aliases}
        out: list[XBRLCandidate] = []
        for ns, concepts in (companyfacts.get("facts") or {}).items():
            for concept, fact in concepts.items():
                c_norm = _norm(concept).replace(" ", "")
                semantic = concept in EXACT_CONCEPTS.get(metric, set()) or any(a and (a == c_norm or a in c_norm) for a in alias_norm)
                semantic = semantic or any(_norm(t).replace(" ", "") in c_norm for t in METRIC_TERMS.get(metric, ()))
                if not semantic or _negative_hit(metric, concept):
                    continue
                for unit, rows in (fact.get("units") or {}).items():
                    if not isinstance(rows, list): continue
                    for row in rows:
                        end = row.get("end"); d = _date(end)
                        if not d or (year is not None and d.year != year): continue
                        value = _safe_float(row.get("val"))
                        if value is None: continue
                        start = row.get("start")
                        instant = not bool(start)
                        annual = bool(start) and _annual_duration(start, end)
                        # Annual-first: duration metrics must be annual. Balance metrics are instant.
                        if metric in DURATION_METRICS and metric != "eps" and not annual:
                            continue
                        if metric in INSTANT_METRICS and not instant:
                            continue
                        score, reason = _score(metric, concept, "", instant, end, year, ns, concept in aliases or c_norm in alias_norm, annual)
                        if score < MIN_MATCH_SCORE: continue
                        out.append(XBRLCandidate(metric, ns, concept, "", value, unit, end, start, row.get("fy"), row.get("form"), row.get("filed"), instant, False, "company-facts", score, reason))
        out.sort(key=lambda x: (x.score, _date(x.end) or date.min, x.filed or ""), reverse=True)
        return out[:limit]

    def search_filing(self, cik: str | int, metric: str, year: int | None = None, include_dimensioned: bool = False, submissions: dict[str, Any] | None = None, limit: int = 20):
        rows, meta = self.filing_candidates(cik, submissions)
        out: list[XBRLCandidate] = []
        for r in rows:
            d = _date(r.get("end"))
            if not d or (year is not None and d.year != year): continue
            if not include_dimensioned and r.get("dimensioned"): continue
            annual = bool(r.get("start")) and _annual_duration(r.get("start"), r.get("end"))
            instant = bool(r.get("instant"))
            if metric in INSTANT_METRICS and not instant: continue
            if metric in DURATION_METRICS and metric != "eps" and not annual: continue
            base, _ = _base_match_score(metric, r["concept"], r.get("label", ""))
            if base <= 0: continue
            score, reason = _score(metric, r["concept"], r.get("label", ""), instant, r.get("end"), year, r.get("namespace", ""), annual=annual)
            if score < MIN_MATCH_SCORE: continue
            out.append(XBRLCandidate(metric, r["namespace"], r["concept"], r.get("label", ""), r["value"], r.get("unit"), r.get("end"), r.get("start"), r.get("fy"), r.get("form"), r.get("filed"), instant, r.get("dimensioned", False), "filing-xbrl", score, reason))
        out.sort(key=lambda x: (x.score, _date(x.end) or date.min, x.filed or ""), reverse=True)
        return out[:limit], meta

    def resolve(self, cik: str | int, metric: str, year: int | None = None, aliases: list[str] | None = None, suspicious: bool = False, limit: int = 10):
        submissions = self.submissions(cik)
        target_year = year if year is not None else _latest_annual_fy(submissions)
        facts = self.company_facts(cik)
        direct = self.search_company_facts(facts, metric, year=target_year, aliases=aliases, limit=limit)
        # Freshness is evaluated against the requested/latest annual FY, not calendar-now.
        credible = [x for x in direct if x.score >= 90]
        filing: list[XBRLCandidate] = []
        need_filing = suspicious or not credible
        meta = {"used": False, "reason": "company_facts_sufficient" if not need_filing else "missing_or_low_confidence", "target_fy": target_year}
        if need_filing:
            filing, fmeta = self.search_filing(cik, metric, year=target_year, submissions=submissions, limit=limit)
            fmeta["target_fy"] = target_year
            meta = fmeta
        if filing and (not credible or filing[0].score >= credible[0].score):
            pool = filing
        else:
            pool = credible or direct
        return {"metric": metric, "year": target_year, "company_facts": [x.compact() for x in direct], "filing_xbrl": [x.compact() for x in filing], "filing_meta": meta, "best": pool[0].compact() if pool else None}


# Backward-friendly alias for test/import code.
SECXBRLSearchV2 = SECXBRLSearchV2_2
