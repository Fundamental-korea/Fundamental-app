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
    XBRLCandidate,
)

# Terms that indicate a disclosure/component rather than the requested
# economic total. These are hard semantic exclusions, not score penalties.
HARD_EXCLUDE = {
    "liabilities": (
        "liabilitiesandstockholdersequity", "deferredcreditsandotherliabilitiesnoncurrent",
        "liabilitiesfairvaluedisclosure", "accruedliabilities", "deferredincometaxliabilities",
        "longtermdebt", "debtcurrent", "operatingleaseliabilities",
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

# Labels that explicitly describe a total are preferred over labels that merely
# contain a metric keyword. This is intentionally conservative.
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
    for bad in HARD_EXCLUDE.get(metric, ()):
        b = _compact(bad)
        if b and (b in c or b in l):
            return True
    return False


def _label_quality(metric: str, label: str, concept: str) -> tuple[int, list[str]]:
    """Return semantic label points and reasons.

    Exact/total labels are strong. Component-like labels receive zero even if
    they contain the requested keyword. Concept-name overlap alone is weak.
    """
    l = _norm(label)
    c = _compact(concept)
    points = 0
    reasons: list[str] = []

    if not l:
        return 0, reasons

    for phrase in TOTAL_LABELS.get(metric, ()):
        p = _norm(phrase)
        if p and p in l:
            points = max(points, 45)
            reasons.append("total label")
            break

    for phrase in LABEL_PRIORS.get(metric, ()):
        p = _norm(phrase)
        if p and p in l:
            points = max(points, 30)
            reasons.append("label match")
            break

    # If the concept is custom and the label is materially different from the
    # concept name, label evidence is more trustworthy than keyword overlap.
    if metric in INSTANT_METRICS and any(x in c for x in ("component", "otherthan", "stockpiles")):
        points = min(points, 10)

    return points, reasons


class SECXBRLSearchV2_3(SECXBRLSearchV2_2):
    """V2.2 plus strict label/total semantic validation."""

    def _candidate_allowed(self, metric: str, concept: str, label: str, source: str) -> bool:
        if _hard_excluded(metric, concept, label):
            return False

        # A cross-statement/disclosure concept should not become a metric just
        # because its name contains one keyword. Canonical concepts remain safe.
        if concept in EXACT_CONCEPTS.get(metric, set()):
            return True

        label_score, _ = _label_quality(metric, label, concept)

        # Filing-level custom concepts require meaningful label evidence.
        if source == "filing-xbrl":
            if metric in {"liabilities", "current_assets", "current_liabilities", "inventory", "receivables", "assets", "equity"}:
                return label_score >= 30
            if metric in {"interest_expense", "operating_income", "sga"}:
                return label_score >= 30

        return label_score >= 30

    def search_company_facts(self, companyfacts: dict[str, Any], metric: str, year: int | None = None,
                             aliases: list[str] | None = None, limit: int = 20):
        rows = super().search_company_facts(companyfacts, metric, year=year, aliases=aliases, limit=limit * 3)
        out = [x for x in rows if self._candidate_allowed(metric, x.concept, x.label, x.source)]
        # Company Facts has no labels in this engine. Preserve canonical concepts
        # and discard broad keyword-only concepts when a safer canonical concept
        # exists for the metric.
        canonical = [x for x in out if x.concept in EXACT_CONCEPTS.get(metric, set())]
        if canonical:
            return canonical[:limit]
        return out[:limit]

    def search_filing(self, cik: str | int, metric: str, year: int | None = None,
                      include_dimensioned: bool = False, submissions: dict[str, Any] | None = None,
                      limit: int = 20):
        rows, meta = self.filing_candidates(cik, submissions)
        out: list[XBRLCandidate] = []
        for r in rows:
            d = _date(r.get("end"))
            if not d:
                continue
            if year is not None and d.year != year:
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

        # Filing candidates win only when they carry real label semantics.
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

    # V2.2 already implements this logic; expose it here so the resolver stays
    # self-contained for callers that subclass/monkey-patch the base engine.
    @staticmethod
    def _latest_annual_fy(submissions: dict[str, Any]) -> int | None:
        recent = submissions.get("filings", {}).get("recent", {})
        best: tuple[str, int | None] | None = None
        annual_forms = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
        forms = recent.get("form", [])
        filed = recent.get("filingDate", [])
        fys = recent.get("fy", [])
        report_dates = recent.get("reportDate", [])
        for i, form in enumerate(forms):
            if form not in annual_forms:
                continue
            fd = filed[i] if i < len(filed) else ""
            fy = fys[i] if i < len(fys) else None
            try:
                fy_int = int(fy) if fy is not None else None
            except (TypeError, ValueError):
                fy_int = None
            if fy_int is None and i < len(report_dates):
                rd = _date(report_dates[i]); fy_int = rd.year if rd else None
            if best is None or fd > best[0]:
                best = (fd, fy_int)
        return best[1] if best else None


SECXBRLSearchV2 = SECXBRLSearchV2_3
