"""V2 ranking layer for sec_xbrl_search.SECXBRLSearch.

Keeps the existing SEC parsing/fetching engine and replaces only concept
ranking/selection. Raw SEC payloads remain in memory only.
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
)

INSTANT_METRICS = {
    "assets", "liabilities", "equity", "current_assets", "current_liabilities",
    "inventory", "cash", "receivables",
}

DURATION_METRICS = {
    "revenue", "operating_income", "net_income", "interest_expense",
    "operating_cash_flow", "sga",
}

# Only concepts that are safe canonical targets. Do not put broad/component
# concepts here; those need semantic/fallback handling instead.
EXACT_CONCEPTS = {
    "revenue": {
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    },
    "operating_income": {"OperatingIncomeLoss"},
    "net_income": {"NetIncomeLoss", "ProfitLoss"},
    "assets": {"Assets"},
    "equity": {
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "Equity",
        "ProprietaryCapital",
    },
    "liabilities": {"Liabilities"},
    "current_assets": {"AssetsCurrent"},
    "current_liabilities": {"LiabilitiesCurrent"},
    "inventory": {"InventoryNet"},
    "cash": {
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    },
    "receivables": {"AccountsReceivableNetCurrent", "AccountsReceivableNet"},
    "interest_expense": {
        "InterestExpense",
        "InterestExpenseNonoperating",
        "InterestAndDebtExpense",
    },
    "operating_cash_flow": {
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    },
    "sga": {"SellingGeneralAndAdministrativeExpense"},
    "eps": {"EarningsPerShareDiluted", "EarningsPerShareBasic"},
}

# Concepts that are not acceptable substitutes for the requested metric.
# These are checked against normalized concept names.
NEGATIVE_TERMS = {
    "operating_income": {
        "nonoperating",
        "othernonoperating",
        "nonoperatingincomeexpense",
    },
    "current_assets": {"noncurrent"},
    "current_liabilities": {"noncurrent"},
    "liabilities": {
        "accruedliabilities",
        "accruedliabilitiescurrent",
        "accountspayableandaccruedliabilitiescurrent",
        "deferredrevenueliabilitiescurrent",
        "deferredincometaxliabilitiesnet",
        "longtermdebt",
        "debtcurrent",
        "operatinglease",
    },
    "interest_expense": {
        "interestpaid",
        "interestcostscapitalized",
        "interestincome",
        "interestincomenonoperating",
        "interestincomeexpense",
    },
    "inventory": {
        "fifo",
        "lifo",
        "finishedgoods",
        "workinprocess",
        "rawmaterials",
    },
    "receivables": {
        "notesandloansreceivable",
        "notesandloansreceivablenetcurrent",
        "loansreceivable",
        "financereceivable",
    },
}

# If a Company Facts candidate is older than this, do not trust it merely
# because the concept itself is canonical. Force filing-level inspection.
STALE_YEARS = 2
MIN_MATCH_SCORE = 25.0


def _latest_date_score(end: Any, target_year: int | None) -> float:
    d = _date(end)
    if not d:
        return 0.0
    if target_year is None:
        return min(30.0, max(0.0, (d.year - 2010) * 1.5))
    delta = abs(d.year - target_year)
    return max(0.0, 30.0 - delta * 10.0)


def _is_stale(end: Any, target_year: int | None = None) -> bool:
    d = _date(end)
    if not d:
        return True
    reference_year = target_year if target_year is not None else date.today().year
    return (reference_year - d.year) > STALE_YEARS


def _negative_hit(metric: str, concept: str) -> str | None:
    compact = _norm(concept).replace(" ", "")
    for bad in NEGATIVE_TERMS.get(metric, set()):
        b = _norm(bad).replace(" ", "")
        if b and b in compact:
            # Canonical SGA itself contains "general and administrative";
            # do not penalize it just because that phrase occurs in the
            # canonical concept name.
            if metric == "sga" and concept == "SellingGeneralAndAdministrativeExpense":
                continue
            return bad
    return None


def _base_match_score(metric: str, concept: str, label: str, explicit_alias: bool = False):
    c = _norm(concept)
    l = _norm(label)
    compact = c.replace(" ", "")
    score = 0.0
    reasons: list[str] = []

    if concept in EXACT_CONCEPTS.get(metric, set()):
        score += 100.0
        reasons.append("canonical concept")

    if explicit_alias:
        score += 95.0
        reasons.append("explicit alias")

    for term in METRIC_TERMS.get(metric, []):
        t = _norm(term)
        tc = t.replace(" ", "")
        if not t:
            continue
        if t in l:
            score = max(score, 75.0)
            reasons.append("label match")
        elif tc and tc in compact:
            score = max(score, 65.0)
            reasons.append("concept semantic match")
        elif any(part in compact for part in tc.split() if len(part) > 4):
            score = max(score, 25.0)
            reasons.append("keyword overlap")

    bad = _negative_hit(metric, concept)
    if bad:
        score -= 100.0
        reasons.append(f"negative semantic: {bad}")

    return score, reasons


def _score(
    metric: str,
    concept: str,
    label: str,
    instant: bool,
    end: Any,
    year: int | None,
    namespace: str,
    explicit_alias: bool = False,
):
    score, reasons = _base_match_score(metric, concept, label, explicit_alias)

    # Never let a candidate with no semantic match become valid merely from
    # instant/duration/recency bonuses.
    if score <= 0:
        return 0.0, ", ".join(dict.fromkeys(reasons))

    if metric in INSTANT_METRICS:
        if instant:
            score += 20.0
            reasons.append("instant")
        else:
            score -= 60.0
            reasons.append("wrong period type")
    elif metric in DURATION_METRICS:
        if not instant:
            score += 20.0
            reasons.append("duration")
        else:
            score -= 60.0
            reasons.append("wrong period type")

    if namespace == "us-gaap":
        score += 5.0
        reasons.append("us-gaap")

    rec = _latest_date_score(end, year)
    score += rec
    if rec:
        reasons.append("recency")

    return score, ", ".join(dict.fromkeys(reasons))


class SECXBRLSearchV2(SECXBRLSearch):
    """Existing SEC parser + stricter semantic ranking and stale-data fallback."""

    def _term_score(self, metric: str, concept: str, label: str):
        return _base_match_score(metric, concept, label)

    def search_company_facts(
        self,
        companyfacts: dict[str, Any],
        metric: str,
        year: int | None = None,
        aliases: list[str] | None = None,
        limit: int = 20,
    ):
        facts_root = companyfacts.get("facts") or {}
        aliases = aliases or []
        alias_norm = {_norm(x).replace(" ", "") for x in aliases}
        out: list[XBRLCandidate] = []

        for ns, concepts in facts_root.items():
            for concept, fact in concepts.items():
                c_norm = _norm(concept).replace(" ", "")
                has_semantic_match = (
                    concept in EXACT_CONCEPTS.get(metric, set())
                    or any(a and (a == c_norm or a in c_norm) for a in alias_norm)
                    or any(
                        t and (
                            t.replace(" ", "") in c_norm
                            or any(p in c_norm for p in t.split() if len(p) > 4)
                        )
                        for t in METRIC_TERMS.get(metric, [])
                    )
                )
                if not has_semantic_match:
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
                        score, reason = _score(
                            metric, concept, "", instant, end, year, ns, explicit
                        )
                        if score < MIN_MATCH_SCORE:
                            continue

                        out.append(
                            XBRLCandidate(
                                metric,
                                ns,
                                concept,
                                "",
                                value,
                                unit,
                                end,
                                start,
                                row.get("fy"),
                                row.get("form"),
                                row.get("filed"),
                                instant,
                                False,
                                "company-facts",
                                score,
                                reason,
                            )
                        )

        out.sort(
            key=lambda x: (
                x.score,
                _date(x.end) or date.min,
                x.filed or "",
            ),
            reverse=True,
        )
        return out[:limit]

    def search_filing(
        self,
        cik: str | int,
        metric: str,
        year: int | None = None,
        include_dimensioned: bool = False,
        submissions: dict[str, Any] | None = None,
        limit: int = 20,
    ):
        rows, meta = self.filing_candidates(cik, submissions)
        candidates: list[XBRLCandidate] = []

        for r in rows:
            d = _date(r.get("end"))
            if year is not None and (not d or d.year != year):
                continue
            if not include_dimensioned and r.get("dimensioned"):
                continue

            # Critical: require actual semantic evidence before adding period
            # type, namespace, or recency points.
            base, _ = _base_match_score(
                metric,
                r["concept"],
                r.get("label", ""),
            )
            if base <= 0:
                continue

            score, reason = _score(
                metric,
                r["concept"],
                r.get("label", ""),
                r.get("instant", False),
                r.get("end"),
                year,
                r.get("namespace", ""),
            )
            if score < MIN_MATCH_SCORE:
                continue

            candidates.append(
                XBRLCandidate(
                    metric,
                    r["namespace"],
                    r["concept"],
                    r.get("label", ""),
                    r["value"],
                    r.get("unit"),
                    r.get("end"),
                    r.get("start"),
                    r.get("fy"),
                    r.get("form"),
                    r.get("filed"),
                    r.get("instant", False),
                    r.get("dimensioned", False),
                    "filing-xbrl",
                    score,
                    reason,
                )
            )

        candidates.sort(
            key=lambda x: (
                x.score,
                _date(x.end) or date.min,
                x.filed or "",
            ),
            reverse=True,
        )
        return candidates[:limit], meta

    def resolve(
        self,
        cik: str | int,
        metric: str,
        year: int | None = None,
        aliases: list[str] | None = None,
        suspicious: bool = False,
        limit: int = 10,
    ):
        facts = self.company_facts(cik)
        direct = self.search_company_facts(
            facts,
            metric,
            year=year,
            aliases=aliases,
            limit=limit,
        )

        # A canonical concept is not enough if the only value is stale.
        # Force filing inspection for stale duration/balance-sheet values.
        fresh = [x for x in direct if not _is_stale(x.end, year)]
        credible = [x for x in fresh if x.score >= 90]

        filing: list[XBRLCandidate] = []
        need_filing = suspicious or not credible
        meta = {
            "used": False,
            "reason": "company_facts_sufficient" if not need_filing else "stale_or_low_confidence",
        }

        if need_filing:
            filing, meta = self.search_filing(
                cik,
                metric,
                year=year,
                limit=limit,
            )

        if filing:
            # Prefer a fresh filing result if Company Facts is stale or weak.
            if not credible or filing[0].score >= credible[0].score:
                best_pool = filing
            else:
                best_pool = credible
        else:
            best_pool = credible or fresh or direct

        return {
            "metric": metric,
            "year": year,
            "company_facts": [x.compact() for x in direct],
            "filing_xbrl": [x.compact() for x in filing],
            "filing_meta": meta,
            "best": best_pool[0].compact() if best_pool else None,
        }
