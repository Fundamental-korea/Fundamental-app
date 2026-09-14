"""SEC XBRL resolver V2.3.

Adds a stricter semantic layer on top of V2.2: label-first matching,
explicit total-vs-component classification, hard rejection of disclosure-only
concepts, and safer candidate selection. Raw SEC payloads remain in memory.
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
    _safe_float,
    _annual_duration,
    _latest_annual_fy,
    XBRLCandidate,
)

HARD_EXCLUDE = {
    "liabilities": (
        "liabilitiesandstockholdersequity", "liabilitiescurrent",
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


class SECXBRLSearchV2_3(SECXBRLSearchV2_2):
    """V2.2 plus strict label/total semantic validation."""

    def _latest_annual_fy(self, submissions: dict[str, Any]) -> int | None:
        """Return the fiscal year of the latest annual filing."""
        return _latest_annual_fy(submissions)

    def _candidate_allowed(self, metric: str, concept: str, label: str, source: str) -> bool:
        if _hard_excluded(metric, concept, label):
            return False
        if concept in EXACT_CONCEPTS.get(metric, set()):
            return True
        label_score, _ = _label_quality(metric, label, concept)
        # Filing-level custom concepts must prove their economic meaning via label.
        if source == "filing-xbrl":
            return label_score >= 30
        # Company Facts has no label field in this engine. Only retain candidates
        # that are explicit canonical concepts or explicit aliases.
        return label_score >= 30

    def search_company_facts(self, companyfacts: dict[str, Any], metric: str, year: int | None = None,
                             aliases: list[str] | None = None, limit: int = 20):
        rows = super().search_company_facts(companyfacts, metric, year=year, aliases=aliases, limit=limit * 3)
        out = [x for x in rows if self._candidate_allowed(metric, x.concept, x.label, x.source)]
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
            filing, fmeta = self.search_filing(cik, metric, year=target_year,
                                               submissions=submissions, limit=limit)
            fmeta["target_fy"] = target_year
            meta = fmeta
        if filing and (not credible or filing[0].score > credible[0].score):
            pool = filing
        else:
            pool = credible or direct
        return {
            "metric": metric,
            "year": target_year,
            "company_facts": [x.compact() for x in direct],
            "filing_xbrl": [x.compact() for x in filing],
            "filing_meta": meta,
            "best": pool[0].compact() if pool else None,
        }


SECXBRLSearchV2 = SECXBRLSearchV2_3
