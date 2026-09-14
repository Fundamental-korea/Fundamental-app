"""SEC XBRL resolver V2.3.

Adds stricter semantic validation and annual provenance. Company Facts rows must
come from the target annual fiscal year; filing-level XBRL is used only when
Company Facts has no credible candidate. Raw SEC payloads remain in memory.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sec_xbrl_search_v2_2 import (
    SECXBRLSearchV2_2,
    INSTANT_METRICS,
    DURATION_METRICS,
    EXACT_CONCEPTS,
    METRIC_TERMS,
    LABEL_PRIORS,
    _date,
    _norm,
    _annual_duration,
    _latest_annual_fy,
    XBRLCandidate,
)

ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

HARD_EXCLUDE = {
    "liabilities": (
        "liabilitiesandstockholdersequity", "liabilitiescurrent", "currentliabilities",
        "liabilitiesofdisposalgroupincludingdiscontinuedoperationcurrent",
        "deferredcreditsandotherliabilitiesnoncurrent", "liabilitiesfairvaluedisclosure",
        "accruedliabilities", "deferredincometaxliabilities", "longtermdebt",
        "debtcurrent", "operatingleaseliabilities",
    ),
    "interest_expense": (
        "unrecognizedtaxbenefits", "taxpenalties", "financeleaseinterestexpense",
        "interestcostscapitalized", "interestpaid", "interestincome",
    ),
    "inventory": (
        "orestockpiles", "leachpads", "finishedgoods", "workinprocess", "rawmaterials",
        "suppliesinventory",
    ),
    "receivables": (
        "notesandloansreceivable", "loansreceivable", "financereceivable",
    ),
    "operating_income": (
        "nonoperating", "othernonoperating", "nonoperatingincomeexpense",
    ),
    "current_assets": ("noncurrent",),
    "current_liabilities": ("noncurrent",),
}

TOTAL_LABELS = {
    "liabilities": ("total liabilities",),
    "current_assets": ("total current assets", "current assets"),
    "current_liabilities": ("total current liabilities", "current liabilities"),
    "inventory": ("total inventory", "inventory", "inventories"),
    "receivables": ("accounts receivable", "accounts receivable net", "trade accounts receivable"),
    "assets": ("total assets", "assets"),
    "equity": ("total equity", "stockholders equity", "shareholders equity", "proprietary capital"),
}


def _compact(text: str | None) -> str:
    return _norm(text).replace(" ", "")


def _hard_excluded(metric: str, concept: str, label: str = "") -> bool:
    c = _compact(concept)
    l = _compact(label)
    return any(_compact(bad) in c or _compact(bad) in l for bad in HARD_EXCLUDE.get(metric, ()) if bad)


def _label_quality(metric: str, label: str, concept: str) -> tuple[int, list[str]]:
    l = _norm(label)
    c = _compact(concept)
    points = 0
    reasons: list[str] = []
    if not l:
        return 0, reasons
    for phrase in TOTAL_LABELS.get(metric, ()):
        if _norm(phrase) in l:
            points = max(points, 45)
            reasons.append("total label")
            break
    for phrase in LABEL_PRIORS.get(metric, ()):
        if _norm(phrase) in l:
            points = max(points, 30)
            reasons.append("label match")
            break
    if metric in INSTANT_METRICS and any(x in c for x in ("component", "otherthan", "stockpiles")):
        points = min(points, 10)
    return points, reasons


def _infer_latest_annual_fy_from_companyfacts(companyfacts: dict[str, Any]) -> int | None:
    """Infer latest annual FY when the caller omits year.

    This is only a convenience fallback. resolve() uses SEC submissions as the
    authoritative source for the target annual fiscal year.
    """
    best: tuple[str, int | None] | None = None
    facts = companyfacts.get("facts") or {}
    for concepts in facts.values():
        for fact in (concepts or {}).values():
            for rows in (fact.get("units") or {}).values():
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if str(row.get("form") or "").upper() not in ANNUAL_FORMS:
                        continue
                    try:
                        fy = int(row.get("fy")) if row.get("fy") is not None else None
                    except (TypeError, ValueError):
                        fy = None
                    filed = str(row.get("filed") or "")
                    if fy is not None and (best is None or filed > best[0]):
                        best = (filed, fy)
    return best[1] if best else None


def _annual_company_fact(row: dict[str, Any], target_year: int, metric: str) -> bool:
    """Require a Company Facts row to belong to the target annual filing."""
    form = str(row.get("form") or "").upper()
    fy = row.get("fy")
    end = _date(row.get("end"))

    if form not in ANNUAL_FORMS:
        return False
    if fy is not None:
        try:
            if int(fy) != int(target_year):
                return False
        except (TypeError, ValueError):
            return False
    if end is None:
        return False

    if metric in DURATION_METRICS and metric != "eps":
        if not row.get("start") or not _annual_duration(row.get("start"), row.get("end")):
            return False
    if metric in INSTANT_METRICS and row.get("start"):
        return False
    return True


class SECXBRLSearchV2_3(SECXBRLSearchV2_2):
    """V2.2 plus strict label/total semantics and annual provenance."""

    def _latest_annual_fy(self, submissions: dict[str, Any]) -> int | None:
        return _latest_annual_fy(submissions)

    def _candidate_allowed(self, metric: str, concept: str, label: str, source: str) -> bool:
        if _hard_excluded(metric, concept, label):
            return False
        if concept in EXACT_CONCEPTS.get(metric, set()):
            return True
        label_score, _ = _label_quality(metric, label, concept)
        return label_score >= 30

    def search_company_facts(self, companyfacts: dict[str, Any], metric: str, year: int | None = None,
                             aliases: list[str] | None = None, limit: int = 20):
        # V2.3 callers may omit year. Infer the latest annual FY instead of
        # falling back to calendar-end/quarterly selection.
        target_year = year if year is not None else _infer_latest_annual_fy_from_companyfacts(companyfacts)
        if target_year is None:
            raise ValueError("V2.3 could not infer target annual FY from Company Facts")

        rows = super().search_company_facts(
            companyfacts, metric, year=None, aliases=aliases, limit=limit * 10
        )
        out = []
        for x in rows:
            row = x.compact()
            if not _annual_company_fact(row, target_year, metric):
                continue
            if not self._candidate_allowed(metric, x.concept, x.label, x.source):
                continue
            out.append(x)

        canonical = [x for x in out if x.concept in EXACT_CONCEPTS.get(metric, set())]
        return (canonical or out)[:limit]

    def search_filing(self, cik: str | int, metric: str, year: int | None = None,
                      include_dimensioned: bool = False, submissions: dict[str, Any] | None = None,
                      limit: int = 20):
        rows, meta = self.filing_candidates(cik, submissions)
        out: list[XBRLCandidate] = []
        for r in rows:
            d = _date(r.get("end"))
            if not d or (year is not None and d.year != year):
                continue
            if not include_dimensioned and r.get("dimensioned"):
                continue
            concept = r.get("concept", "")
            label = r.get("label", "")
            if not self._candidate_allowed(metric, concept, label, "filing-xbrl"):
                continue
            instant = bool(r.get("instant"))
            annual = bool(r.get("start")) and _annual_duration(r.get("start"), r.get("end"))
            if metric in INSTANT_METRICS and not instant:
                continue
            if metric in DURATION_METRICS and metric != "eps" and not annual:
                continue
            label_points, label_reasons = _label_quality(metric, label, concept)
            score = float(label_points)
            reasons = list(label_reasons)
            if concept in EXACT_CONCEPTS.get(metric, set()):
                score = max(score, 100.0)
                reasons.append("canonical concept")
            elif metric in DURATION_METRICS and annual:
                score += 20.0
                reasons.append("annual duration")
            elif metric in INSTANT_METRICS and instant:
                score += 20.0
                reasons.append("instant")
            if r.get("namespace") == "us-gaap":
                score += 5.0
                reasons.append("us-gaap")
            out.append(XBRLCandidate(
                metric, r["namespace"], concept, label, r["value"], r.get("unit"),
                r.get("end"), r.get("start"), r.get("fy"), r.get("form"),
                r.get("filed"), instant, r.get("dimensioned", False), "filing-xbrl",
                score, ", ".join(dict.fromkeys(reasons))
            ))
        out.sort(key=lambda x: (x.score, _date(x.end) or date.min, x.filed or ""), reverse=True)
        return out[:limit], meta

    def resolve(self, cik: str | int, metric: str, year: int | None = None,
                aliases: list[str] | None = None, suspicious: bool = False, limit: int = 10):
        submissions = self.submissions(cik)
        target_year = year if year is not None else self._latest_annual_fy(submissions)
        if target_year is None:
            raise ValueError("V2.3 could not determine target annual FY")

        facts = self.company_facts(cik)
        direct = self.search_company_facts(facts, metric, year=target_year, aliases=aliases, limit=limit)
        credible = [x for x in direct if x.score >= 90]
        filing: list[XBRLCandidate] = []
        need_filing = suspicious or not credible
        meta: dict[str, Any] = {
            "used": False,
            "reason": "company_facts_sufficient" if not need_filing else "missing_or_low_confidence",
            "target_fy": target_year,
        }
        if need_filing:
            filing, fmeta = self.search_filing(
                cik, metric, year=target_year, submissions=submissions, limit=limit
            )
            fmeta["target_fy"] = target_year
            meta = fmeta

        pool = filing if filing and (not credible or filing[0].score > credible[0].score) else (credible or direct)
        return {
            "metric": metric,
            "year": target_year,
            "company_facts": [x.compact() for x in direct],
            "filing_xbrl": [x.compact() for x in filing],
            "filing_meta": meta,
            "best": pool[0].compact() if pool else None,
        }


SECXBRLSearchV2 = SECXBRLSearchV2_3
