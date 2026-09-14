"""SEC XBRL resolver V2.3.2.

Uses Inline XBRL directly for filing fallback and avoids the legacy standalone
instance parser, whose XML string parsing can fail on encoding declarations.
Raw SEC payloads remain in memory only.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

from sec_xbrl_search_v2_3_1 import SECXBRLSearchV2_3_1
from sec_xbrl_search_v2_3 import (
    ANNUAL_FORMS,
    INSTANT_METRICS,
    DURATION_METRICS,
    EXACT_CONCEPTS,
    XBRLCandidate,
    _date,
    _annual_duration,
    _hard_excluded,
    _label_quality,
)
from sec_xbrl_inline import parse_inline_xbrl

XLINK = "http://www.w3.org/1999/xlink"

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


class SECXBRLSearchV2_3_2(SECXBRLSearchV2_3_1):
    """V2.3.1 with robust Inline XBRL fallback."""

    def _parse_labels(self, xml_text):
        payload = xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text
        root = ET.fromstring(payload)
        locs, labels, rels = {}, {}, []
        standard = "http://www.xbrl.org/2003/role/label"
        for e in root.iter():
            n = _local(e.tag)
            if n == "loc":
                k = e.attrib.get(f"{{{XLINK}}}label") or e.attrib.get("label")
                href = e.attrib.get(f"{{{XLINK}}}href") or e.attrib.get("href")
                if k and href:
                    locs[k] = href.split("#")[-1]
            elif n == "label":
                k = e.attrib.get(f"{{{XLINK}}}label") or e.attrib.get("label")
                role = e.attrib.get(f"{{{XLINK}}}role") or e.attrib.get("role") or ""
                text = " ".join("".join(e.itertext()).split())
                if k and text:
                    labels[k] = (2 if role == standard else 1 if "Label" in role else 0, text)
            elif n == "labelArc":
                frm = e.attrib.get(f"{{{XLINK}}}from") or e.attrib.get("from")
                to = e.attrib.get(f"{{{XLINK}}}to") or e.attrib.get("to")
                if frm and to:
                    rels.append((frm, to))
        out = {}
        for frm, to in rels:
            concept, item = locs.get(frm), labels.get(to)
            if concept and item and (concept not in out or item[0] > 0):
                out[concept] = item[1]
        return out

    def _inline_filing_rows(self, cik: str | int, submissions: dict[str, Any]):
        accession, doc, filed = self.latest_annual_filing(submissions)
        if not accession or not doc:
            return [], {"used": False, "reason": "no_annual_filing"}

        compact = accession.replace("-", "")
        index, _ = self.filing_index(cik, accession)
        label_file = self._choose_label_file(index)
        labels = {}
        if label_file:
            label_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/{label_file}"
            label_text = self._get(label_url).text
            labels = self._parse_labels(label_text)

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

    def search_filing(self, cik: str | int, metric: str, year: int | None = None,
                      include_dimensioned: bool = False,
                      submissions: dict[str, Any] | None = None, limit: int = 20):
        """Search Inline XBRL directly; do not invoke legacy filing_candidates()."""
        submissions = submissions or self.submissions(cik)
        rows, meta = self._inline_filing_rows(cik, submissions)
        candidates: list[XBRLCandidate] = []

        for r in rows:
            end = _date(r.get("end"))
            if year is not None and (end is None or end.year != int(year)):
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
            if year is not None and end and end.year == int(year):
                score += 30.0
                reasons.append("target FY")

            candidates.append(XBRLCandidate(
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
            ))

        candidates.sort(
            key=lambda x: (x.score, _date(x.end) or date.min, x.filed or ""),
            reverse=True,
        )
        meta["target_fy"] = year
        return candidates[:limit], meta


SECXBRLSearchV2_3 = SECXBRLSearchV2_3_2
SECXBRLSearchV2 = SECXBRLSearchV2_3_2
