"""V2 ranking layer for sec_xbrl_search.SECXBRLSearch.
Keeps the existing SEC parsing engine and replaces only concept ranking/selection.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sec_xbrl_search import SECXBRLSearch, XBRLCandidate, METRIC_TERMS, _date, _norm, _safe_float

INSTANT_METRICS = {
    "assets", "liabilities", "equity", "current_assets", "current_liabilities",
    "inventory", "cash", "receivables",
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
    "interest_expense": {"InterestExpense", "InterestExpenseNonoperating", "InterestAndDebtExpense"},
    "operating_cash_flow": {"NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"},
    "sga": {"SellingGeneralAndAdministrativeExpense"},
    "eps": {"EarningsPerShareDiluted", "EarningsPerShareBasic"},
}

NEGATIVE_TERMS = {
    "operating_income": {"nonoperating", "othernonoperating", "nonoperatingincomeexpense"},
    "current_assets": {"noncurrent"},
    "current_liabilities": {"noncurrent"},
    "liabilities": {"accrued", "accounts payable", "deferred revenue", "debtcurrent", "operating lease"},
    "interest_expense": {"interestpaid", "interestcostscapitalized", "interestincome", "interestincomenonoperating"},
    "inventory": {"fifo", "lifo", "finishedgoods", "workinprocess", "rawmaterials"},
    "receivables": {"notesandloans", "loans", "finance receivable"},
    "sga": {"generalandadministrative", "sellingandmarketing"},
}


def _latest_date_score(end: Any, target_year: int | None) -> float:
    d = _date(end)
    if not d:
        return 0.0
    if target_year is None:
        return min(30.0, max(0.0, (d.year - 2010) * 1.5))
    delta = abs(d.year - target_year)
    return max(0.0, 30.0 - delta * 10.0)


def _score(metric: str, concept: str, label: str, instant: bool, end: Any, year: int | None, namespace: str, explicit_alias: bool = False):
    c = _norm(concept)
    l = _norm(label)
    compact = c.replace(" ", "")
    score = 0.0
    reasons = []

    if concept in EXACT_CONCEPTS.get(metric, set()):
        score += 100
        reasons.append("canonical concept")

    if explicit_alias:
        score += 95
        reasons.append("explicit alias")

    for term in METRIC_TERMS.get(metric, []):
        t = _norm(term)
        tc = t.replace(" ", "")
        if t and t in l:
            score = max(score, 75)
            reasons.append("label match")
        elif tc and tc in compact:
            score = max(score, 65)
            reasons.append("concept semantic match")
        elif any(part in compact for part in tc.split() if len(part) > 4):
            score = max(score, 25)
            reasons.append("keyword overlap")

    for bad in NEGATIVE_TERMS.get(metric, set()):
        b = _norm(bad).replace(" ", "")
        if b and b in compact:
            score -= 80
            reasons.append(f"negative semantic: {bad}")

    if metric in INSTANT_METRICS:
        if instant:
            score += 20
            reasons.append("instant")
        else:
            score -= 60
            reasons.append("wrong period type")
    elif metric in DURATION_METRICS:
        if not instant:
            score += 20
            reasons.append("duration")
        else:
            score -= 60
            reasons.append("wrong period type")

    if namespace == "us-gaap":
        score += 5
        reasons.append("us-gaap")

    rec = _latest_date_score(end, year)
    score += rec
    if rec:
        reasons.append("recency")

    return score, ", ".join(dict.fromkeys(reasons))


class SECXBRLSearchV2(SECXBRLSearch):
    """Existing SEC parser + stricter semantic ranking and recency selection."""

    def _term_score(self, metric: str, concept: str, label: str):
        return _score(metric, concept, label, False, None, None, "")

    def search_company_facts(self, companyfacts: dict[str, Any], metric: str, year: int | None = None,
                             aliases: list[str] | None = None, limit: int = 20):
        facts_root = companyfacts.get("facts") or {}
        aliases = aliases or []
        alias_norm = {_norm(x).replace(" ", "") for x in aliases}
        out: list[XBRLCandidate] = []

        for ns, concepts in facts_root.items():
            for concept, fact in concepts.items():
                c_norm = _norm(concept).replace(" ", "")
                if not (concept in EXACT_CONCEPTS.get(metric, set())
                        or any(a and (a == c_norm or a in c_norm) for a in alias_norm)
                        or any(t and (t.replace(" ", "") in c_norm or any(p in c_norm for p in t.split() if len(p) > 4))
                               for t in METRIC_TERMS.get(metric, []))):
                    continue

                for unit, rows in (fact.get("units") or {}).items():
                    if not isinstance(rows, list):
                        continue
                    for row in rows:
                        end = row.get("end")
                        d = _date(end)
                        if not d:
                            continue
                        if year is not None and d.year != year:
                            continue
                        value = _safe_float(row.get("val"))
                        if value is None:
                            continue
                        start = row.get("start")
                        instant = not bool(start)
                        explicit = concept in aliases or c_norm in alias_norm
                        score, reason = _score(metric, concept, "", instant, end, year, ns, explicit)
                        if score <= 0:
                            continue
                        out.append(XBRLCandidate(
                            metric, ns, concept, "", value, unit, end, start,
                            row.get("fy"), row.get("form"), row.get("filed"),
                            instant, False, "company-facts", score, reason
                        ))

        out.sort(key=lambda x: (x.score, _date(x.end) or date.min, x.filed or ""), reverse=True)
        return out[:limit]

    def search_filing(self, cik: str | int, metric: str, year: int | None = None,
                      include_dimensioned: bool = False, submissions: dict[str, Any] | None = None,
                      limit: int = 20):
        rows, meta = self.filing_candidates(cik, submissions)
        candidates: list[XBRLCandidate] = []
        for r in rows:
            d = _date(r.get("end"))
            if year is not None and (not d or d.year != year):
                continue
            if not include_dimensioned and r.get("dimensioned"):
                continue
            score, reason = _score(metric, r["concept"], r.get("label", ""), r.get("instant", False), r.get("end"), year, r.get("namespace", ""))
            if score <= 0:
                continue
            candidates.append(XBRLCandidate(
                metric, r["namespace"], r["concept"], r.get("label", ""), r["value"], r.get("unit"),
                r.get("end"), r.get("start"), r.get("fy"), r.get("form"), r.get("filed"),
                r.get("instant", False), r.get("dimensioned", False), "filing-xbrl", score, reason
            ))
        candidates.sort(key=lambda x: (x.score, _date(x.end) or date.min, x.filed or ""), reverse=True)
        return candidates[:limit], meta

    def resolve(self, cik: str | int, metric: str, year: int | None = None, aliases: list[str] | None = None,
                suspicious: bool = False, limit: int = 10):
        facts = self.company_facts(cik)
        direct = self.search_company_facts(facts, metric, year=year, aliases=aliases, limit=limit)
        credible = [x for x in direct if x.score >= 90]
        filing: list[XBRLCandidate] = []
        meta = {"used": False, "reason": "company_facts_sufficient" if credible and not suspicious else "requested"}
        if suspicious or not credible:
            filing, meta = self.search_filing(cik, metric, year=year, limit=limit)
        best_pool = filing if filing and (not credible or filing[0].score > credible[0].score + 10) else credible or direct
        return {
            "metric": metric,
            "year": year,
            "company_facts": [x.compact() for x in direct],
            "filing_xbrl": [x.compact() for x in filing],
            "filing_meta": meta,
            "best": best_pool[0].compact() if best_pool else None,
        }
