"""SEC XBRL resolver V2.3.1.

V2.3.1 fixes two important provenance problems found in the V2.3 regression:
1. Company Facts annual rows are selected by annual FORM + target end year,
   not by the SEC ``fy`` field alone. Some issuers report comparative annual
   facts with an ``fy`` value that does not equal the requested year.
2. Filing-level fallback supports Inline XBRL embedded in the primary HTML
   document. Modern SEC 10-K filings often do not expose a standalone XML
   instance file; the facts are embedded as ix:nonFraction elements in the
   HTML filing.

Raw SEC payloads remain in memory only.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sec_xbrl_search_v2_3 import (
    SECXBRLSearchV2_3,
    INSTANT_METRICS,
    DURATION_METRICS,
    EXACT_CONCEPTS,
    XBRLCandidate,
    _date,
    _annual_duration,
    _hard_excluded,
    _label_quality,
    ANNUAL_FORMS,
)
from sec_xbrl_search_v2_2 import SECXBRLSearchV2_2
from sec_xbrl_inline import parse_inline_xbrl


class SECXBRLSearchV2_3_1(SECXBRLSearchV2_3):
    """V2.3 with strict annual provenance and Inline XBRL fallback."""

    def search_company_facts(
        self,
        companyfacts: dict[str, Any],
        metric: str,
        year: int | None = None,
        aliases: list[str] | None = None,
        limit: int = 20,
    ):
        target_year = year
        if target_year is None:
            raise ValueError("V2.3.1 search_company_facts requires target annual FY")

        # IMPORTANT: call V2.2 directly, bypassing V2.3's stricter fy filter.
        # V2.3.1 deliberately validates annual provenance using FORM + end year
        # below because SEC comparative rows can carry a filing-year FY value.
        rows = SECXBRLSearchV2_2.search_company_facts(
            self,
            companyfacts,
            metric,
            year=None,
            aliases=aliases,
            limit=limit * 20,
        )

        out = []
        for x in rows:
            row = x.compact()
            form = str(row.get("form") or "").upper()
            end = _date(row.get("end"))
            if form not in ANNUAL_FORMS or end is None or end.year != int(target_year):
                continue
            if metric in DURATION_METRICS and metric != "eps":
                if not row.get("start") or not _annual_duration(row.get("start"), row.get("end")):
                    continue
            if metric in INSTANT_METRICS and row.get("start"):
                continue
            if _hard_excluded(metric, x.concept, x.label):
                continue
            if not self._candidate_allowed(metric, x.concept, x.label, x.source):
                continue
            out.append(x)

        # Prefer the fact whose end date is the latest within the target FY.
        out.sort(key=lambda x: (_date(x.end) or date.min, x.score, x.filed or ""), reverse=True)
        return out[:limit]

    def _inline_filing_rows(
        self,
        cik: str | int,
        submissions: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        accession, doc, filed = self.latest_annual_filing(submissions)
        if not accession or not doc:
            return [], {"used": False, "reason": "no_annual_filing"}

        compact = accession.replace("-", "")
        index, _ = self.filing_index(cik, accession)
        label_file = self._choose_label_file(index)
        labels: dict[str, str] = {}
        if label_file:
            labels = self._parse_labels(
                self._get(
                    f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/{label_file}"
                ).text
            )

        # Primary 10-K HTML is the authoritative Inline XBRL container.
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/{doc}"
        text = self._get(url).text
        rows = parse_inline_xbrl(text, labels=labels, filed=filed, form="10-K")

        for r in rows:
            d = _date(r.get("end"))
            r["fy"] = d.year if d else None

        return rows, {
            "used": bool(rows),
            "reason": "inline_xbrl_primary_document" if rows else "inline_xbrl_no_numeric_facts",
            "accession": accession,
            "primary_document": doc,
            "filed": filed,
            "label_file": label_file,
            "concept_count": len({r["concept"] for r in rows}),
        }

    def search_filing(
        self,
        cik: str | int,
        metric: str,
        year: int | None = None,
        include_dimensioned: bool = False,
        submissions: dict[str, Any] | None = None,
        limit: int = 20,
    ):
        submissions = submissions or self.submissions(cik)

        # First try the standalone instance parser inherited from V2.3.
        base_candidates, meta = super().search_filing(
            cik,
            metric,
            year=year,
            include_dimensioned=include_dimensioned,
            submissions=submissions,
            limit=limit,
        )
        if base_candidates:
            return base_candidates, meta

        rows, inline_meta = self._inline_filing_rows(cik, submissions)
        target_year = year
        candidates: list[XBRLCandidate] = []
        for r in rows:
            end = _date(r.get("end"))
            if target_year is not None and (end is None or end.year != int(target_year)):
                continue
            if not include_dimensioned and r.get("dimensioned"):
                continue
            concept = r.get("concept", "")
            label = r.get("label", "")
            if _hard_excluded(metric, concept, label):
                continue
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
            if metric in DURATION_METRICS and annual:
                score += 25.0
                reasons.append("annual duration")
            elif metric in INSTANT_METRICS and instant:
                score += 20.0
                reasons.append("instant")
            if r.get("namespace") == "us-gaap":
                score += 5.0
                reasons.append("us-gaap")
            if target_year is not None and end and end.year == int(target_year):
                score += 30.0
                reasons.append("target FY")

            candidates.append(
                XBRLCandidate(
                    metric=metric,
                    namespace=r.get("namespace", ""),
                    concept=concept,
                    label=label,
                    value=r.get("value"),
                    unit=r.get("unit"),
                    end=r.get("end"),
                    start=r.get("start"),
                    fy=r.get("fy"),
                    form=r.get("form"),
                    filed=r.get("filed"),
                    instant=instant,
                    dimensioned=r.get("dimensioned", False),
                    source="filing-xbrl-inline",
                    score=score,
                    reason=", ".join(dict.fromkeys(reasons)),
                )
            )

        candidates.sort(
            key=lambda x: (x.score, _date(x.end) or date.min, x.filed or ""),
            reverse=True,
        )
        inline_meta["target_fy"] = target_year
        return candidates[:limit], inline_meta


SECXBRLSearchV2_3 = SECXBRLSearchV2_3_1
SECXBRLSearchV2 = SECXBRLSearchV2_3_1
