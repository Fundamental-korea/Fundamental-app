"""Reusable SEC XBRL concept search/normalization engine.

Company Facts is the fast path.  When a logical metric is missing or needs
semantic inspection, this module can inspect the latest annual filing's XBRL
instance and label linkbase in memory.  Raw SEC payloads are never persisted.

The engine is intentionally scoring-agnostic: it resolves concepts/facts and
reports provenance; sector scoring remains outside this module.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
import re
import xml.etree.ElementTree as ET
from typing import Any
import requests
import time

SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
XLINK = "http://www.w3.org/1999/xlink"
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

# Conservative process-wide SEC request pacing.
SEC_MIN_REQUEST_INTERVAL = 0.25
_SEC_LAST_REQUEST = 0.0

# Semantic search vocabulary. These are candidates, not automatic aliases.
METRIC_TERMS = {
    "revenue": ["revenue", "sales", "operating revenue"],
    "operating_income": ["operating income", "income from operations", "operating profit"],
    "net_income": ["net income", "profit loss", "net earnings"],
    "assets": ["total assets"],
    "equity": ["stockholders equity", "shareholders equity", "total equity", "proprietary capital"],
    "liabilities": ["total liabilities", "liabilities"],
    "current_assets": ["current assets"],
    "current_liabilities": ["current liabilities"],
    "inventory": ["inventory", "inventories"],
    "cash": ["cash and cash equivalents", "cash equivalents"],
    "receivables": ["accounts receivable", "receivables", "trade receivables", "notes and loans receivable"],
    "interest_expense": ["interest expense", "interest and debt expense", "finance costs", "interest costs incurred"],
    "operating_cash_flow": ["net cash provided by used in operating activities", "cash flows from operating activities", "operating cash flow"],
    "sga": ["selling general and administrative", "general and administrative", "selling and marketing"],
    "eps": ["earnings per share", "eps"],
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") and "}" in tag else ""


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def _duration_days(start: Any, end: Any) -> int | None:
    s, e = _date(start), _date(end)
    return (e - s).days if s and e else None


def _annual_duration(start: Any, end: Any) -> bool:
    d = _duration_days(start, end)
    return d is not None and 300 <= d <= 380


def _safe_float(v: Any) -> float | None:
    try:
        x = float(v)
        return x if x == x and abs(x) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _dimensioned(context: ET.Element) -> bool:
    return any(_local(e.tag) in {"explicitMember", "typedMember"} for e in context.iter())


def _choose_label(existing: str | None, new: str, role: str) -> str:
    if not existing:
        return new
    if role == "http://www.xbrl.org/2003/role/label":
        return new
    return existing


@dataclass
class XBRLCandidate:
    metric: str
    namespace: str
    concept: str
    label: str
    value: float | None
    unit: str | None
    end: str | None
    start: str | None
    fy: int | None
    form: str | None
    filed: str | None
    instant: bool
    dimensioned: bool
    source: str
    score: float = 0.0
    reason: str = ""

    def compact(self) -> dict[str, Any]:
        return asdict(self)


class SECXBRLSearch:
    """Search SEC Company Facts and filing-level XBRL concepts in memory."""

    def __init__(self, user_agent: str = "Fundamental-app contact@example.com", session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._company_facts_cache = {}
        self._submissions_cache = {}
        self._filing_index_cache = {}

    @staticmethod
    def _sec_sleep():
        global _SEC_LAST_REQUEST
        now = time.monotonic()
        wait = SEC_MIN_REQUEST_INTERVAL - (now - _SEC_LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _SEC_LAST_REQUEST = time.monotonic()

    @staticmethod
    def _retry_after(response, attempt):
        raw = response.headers.get("Retry-After") if response is not None else None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = None
        if value is not None and value >= 0:
            return min(max(value, 1.0), 30.0)
        return min(2.0 ** attempt, 16.0)

    def _get(self, url: str, timeout: int = 45) -> requests.Response:
        last_response = None
        for attempt in range(4):
            self._sec_sleep()
            try:
                r = self.session.get(url, timeout=timeout)
                last_response = r
                if r.status_code == 200:
                    return r
                if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(self._retry_after(r, attempt))
                    continue
                r.raise_for_status()
            except requests.RequestException:
                if attempt >= 3:
                    raise
                time.sleep(self._retry_after(last_response, attempt))
        raise RuntimeError(f"SEC request failed after retries: {url}")

    def prime_company(self, cik, company_facts, submissions):
        key = str(int(cik))
        self._company_facts_cache[key] = company_facts
        self._submissions_cache[key] = submissions

    def company_facts(self, cik: str | int) -> dict[str, Any]:
        key = str(int(cik))
        if key not in self._company_facts_cache:
            self._company_facts_cache[key] = self._get(
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json"
            ).json()
        return self._company_facts_cache[key]

    def submissions(self, cik: str | int) -> dict[str, Any]:
        key = str(int(cik))
        if key not in self._submissions_cache:
            self._submissions_cache[key] = self._get(
                f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"
            ).json()
        return self._submissions_cache[key]

    def latest_annual_filing(self, submissions: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
        recent = submissions.get("filings", {}).get("recent", {})
        best = None
        for i, form in enumerate(recent.get("form", [])):
            if form not in ANNUAL_FORMS:
                continue
            acc = recent.get("accessionNumber", [None])[i]
            doc = recent.get("primaryDocument", [None])[i]
            filed = recent.get("filingDate", [None])[i]
            if acc and (best is None or (filed or "") > (best[2] or "")):
                best = (acc, doc, filed)
        return best if best else (None, None, None)

    def filing_index(self, cik: str | int, accession: str) -> tuple[dict[str, Any], str]:
        compact = accession.replace("-", "")
        url = f"{SEC_ARCHIVE}/{int(cik)}/{compact}/index.json"
        return self._get(url).json(), compact

    @staticmethod
    def _is_instance(name: str) -> bool:
        low = name.lower()
        return low.endswith(".xml") and not any(x in low for x in ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml", "_ref.xml", "filingsummary"))

    def _choose_instance(self, index: dict[str, Any], primary_document: str | None) -> str | None:
        names = [x.get("name") for x in index.get("directory", {}).get("item", []) if x.get("name")]
        candidates = [n for n in names if self._is_instance(n)]
        stem = re.sub(r"\.[^.]+$", "", primary_document or "").lower()
        matching = [n for n in candidates if stem and n.lower().startswith(stem)]
        if matching:
            return matching[0]
        matching = [n for n in candidates if "instance" in n.lower() or "xbrl" in n.lower()]
        if matching:
            return matching[0]
        return max(candidates, key=len) if candidates else None

    @staticmethod
    def _choose_label_file(index: dict[str, Any]) -> str | None:
        names = [x.get("name") for x in index.get("directory", {}).get("item", []) if x.get("name")]
        labels = [n for n in names if n.lower().endswith("_lab.xml")]
        return labels[0] if labels else None

    def _parse_labels(self, xml_text: str) -> dict[str, str]:
        root = ET.fromstring(xml_text)
        locs: dict[str, str] = {}
        labels: dict[str, tuple[int, str]] = {}
        rels: list[tuple[str, str]] = []
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
        out: dict[str, str] = {}
        for frm, to in rels:
            concept = locs.get(frm); item = labels.get(to)
            if concept and item and (concept not in out or item[0] > 0):
                out[concept] = item[1]
        return out

    def _parse_instance(self, xml_text: str, labels: dict[str, str]) -> list[dict[str, Any]]:
        root = ET.fromstring(xml_text)
        contexts: dict[str, dict[str, Any]] = {}
        units: dict[str, str] = {}
        for e in root.iter():
            n = _local(e.tag)
            if n == "context":
                cid = e.attrib.get("id")
                if not cid:
                    continue
                instant = start = end = None
                for c in e.iter():
                    cn, text = _local(c.tag), (c.text or "").strip()
                    if cn == "instant" and text: instant = text
                    elif cn == "startDate" and text: start = text
                    elif cn == "endDate" and text: end = text
                contexts[cid] = {"instant": instant, "start": start, "end": end, "dimensioned": _dimensioned(e)}
            elif n == "unit":
                uid = e.attrib.get("id")
                if uid:
                    measure = next(((c.text or "").strip() for c in e.iter() if _local(c.tag) == "measure" and (c.text or "").strip()), None)
                    units[uid] = measure or ""
        rows = []
        for e in root.iter():
            cref = e.attrib.get("contextRef")
            if not cref or _local(e.tag) in {"context", "unit"}:
                continue
            ctx = contexts.get(cref)
            if not ctx:
                continue
            value = _safe_float((e.text or "").strip())
            if value is None:
                continue
            instant = bool(ctx.get("instant"))
            end = ctx.get("instant") or ctx.get("end")
            start = None if instant else ctx.get("start")
            rows.append({
                "concept": _local(e.tag), "namespace": _namespace(e.tag), "label": labels.get(_local(e.tag), ""),
                "value": value, "unit": units.get(e.attrib.get("unitRef"), ""), "end": end, "start": start,
                "fy": None, "form": "10-K", "filed": None, "instant": instant,
                "dimensioned": bool(ctx.get("dimensioned")),
            })
        return rows

    def filing_candidates(self, cik: str | int, submissions: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        submissions = submissions or self.submissions(cik)
        accession, doc, filed = self.latest_annual_filing(submissions)
        if not accession:
            return [], {"used": False, "reason": "no_annual_filing"}
        index, compact = self.filing_index(cik, accession)
        instance = self._choose_instance(index, doc)
        if not instance:
            return [], {"used": False, "reason": "no_xbrl_instance", "accession": accession}
        label_file = self._choose_label_file(index)
        labels: dict[str, str] = {}
        if label_file:
            labels = self._parse_labels(self._get(f"{SEC_ARCHIVE}/{int(cik)}/{compact}/{label_file}").text)
        rows = self._parse_instance(self._get(f"{SEC_ARCHIVE}/{int(cik)}/{compact}/{instance}").text, labels)
        for row in rows:
            row["filed"] = filed
            end_year = _date(row.get("end"))
            row["fy"] = end_year.year if end_year else None
        meta = {"used": True, "accession": accession, "primary_document": doc, "filed": filed,
                "instance": instance, "label_file": label_file, "concept_count": len({r["concept"] for r in rows})}
        return rows, meta

    def _term_score(self, metric: str, concept: str, label: str) -> tuple[float, str]:
        c, l = _norm(concept), _norm(label)
        terms = METRIC_TERMS.get(metric, [])
        score = 0.0; reasons = []
        for term in terms:
            t = _norm(term)
            if c == t.replace(" ", ""):
                score = max(score, 100); reasons.append("exact concept")
            elif t in l:
                score = max(score, 75); reasons.append("label match")
            elif t.replace(" ", "") in c:
                score = max(score, 65); reasons.append("concept semantic match")
            elif any(part in c for part in t.split() if len(part) > 3):
                score = max(score, 25); reasons.append("keyword overlap")
        return score, ", ".join(dict.fromkeys(reasons))

    def search_filing(self, cik: str | int, metric: str, year: int | None = None, include_dimensioned: bool = False,
                      submissions: dict[str, Any] | None = None, limit: int = 20) -> tuple[list[XBRLCandidate], dict[str, Any]]:
        rows, meta = self.filing_candidates(cik, submissions)
        candidates: list[XBRLCandidate] = []
        for r in rows:
            if year is not None and r.get("fy") != year and _date(r.get("end")).year != year if _date(r.get("end")) else year is not None:
                continue
            if not include_dimensioned and r.get("dimensioned"):
                continue
            score, reason = self._term_score(metric, r["concept"], r.get("label", ""))
            if score <= 0:
                continue
            if metric in {"assets", "liabilities", "equity", "current_assets", "current_liabilities", "inventory", "cash", "receivables"}:
                if not r.get("instant"):
                    score -= 30; reason += ", wrong period type"
            elif metric not in {"eps"} and r.get("instant"):
                score -= 30; reason += ", wrong period type"
            if r.get("namespace") == "us-gaap": score += 10
            candidates.append(XBRLCandidate(metric=metric, namespace=r["namespace"], concept=r["concept"], label=r.get("label", ""),
                value=r["value"], unit=r.get("unit"), end=r.get("end"), start=r.get("start"), fy=r.get("fy"),
                form=r.get("form"), filed=r.get("filed"), instant=r.get("instant", False), dimensioned=r.get("dimensioned", False),
                source="filing-xbrl", score=score, reason=reason))
        candidates.sort(key=lambda x: (x.score, x.end or "", x.filed or ""), reverse=True)
        return candidates[:limit], meta

    def search_company_facts(self, companyfacts: dict[str, Any], metric: str, year: int | None = None, aliases: list[str] | None = None,
                             limit: int = 20) -> list[XBRLCandidate]:
        facts_root = companyfacts.get("facts") or {}
        aliases = aliases or []
        terms = {_norm(x).replace(" ", "") for x in aliases}
        terms.update(_norm(x).replace(" ", "") for x in METRIC_TERMS.get(metric, []))
        out: list[XBRLCandidate] = []
        for ns, concepts in facts_root.items():
            for concept, fact in concepts.items():
                if not any(t and (t == _norm(concept).replace(" ", "") or t in _norm(concept).replace(" ", "")) for t in terms):
                    continue
                for unit, rows in (fact.get("units") or {}).items():
                    for row in rows if isinstance(rows, list) else []:
                        end = row.get("end"); end_year = _date(end).year if _date(end) else None
                        if year is not None and end_year != year:
                            continue
                        value = _safe_float(row.get("val"))
                        if value is None:
                            continue
                        start = row.get("start")
                        instant = not bool(start)
                        if instant and metric in {"revenue", "operating_income", "net_income", "interest_expense", "operating_cash_flow", "sga"}:
                            continue
                        if not instant and metric in {"assets", "liabilities", "equity", "current_assets", "current_liabilities", "inventory", "cash", "receivables"}:
                            continue
                        score, reason = self._term_score(metric, concept, "")
                        if concept in aliases: score += 100; reason = "explicit alias"
                        if ns == "us-gaap": score += 10
                        out.append(XBRLCandidate(metric, ns, concept, "", value, unit, end, start, row.get("fy"), row.get("form"), row.get("filed"), instant, False, "company-facts", score, reason))
        out.sort(key=lambda x: (x.score, x.end or "", x.filed or ""), reverse=True)
        return out[:limit]

    def resolve(self, cik: str | int, metric: str, year: int | None = None, aliases: list[str] | None = None,
                suspicious: bool = False, limit: int = 10) -> dict[str, Any]:
        """Return ranked Company Facts candidates, optionally enriched by filing-level XBRL."""
        facts = self.company_facts(cik)
        direct = self.search_company_facts(facts, metric, year=year, aliases=aliases, limit=limit)
        filing: list[XBRLCandidate] = []
        meta: dict[str, Any] = {"used": False, "reason": "company_facts_sufficient" if direct and not suspicious else "requested"}
        if suspicious or not direct:
            filing, meta = self.search_filing(cik, metric, year=year, limit=limit)
        return {"metric": metric, "year": year, "company_facts": [x.compact() for x in direct],
                "filing_xbrl": [x.compact() for x in filing], "filing_meta": meta,
                "best": (filing[0].compact() if filing else direct[0].compact() if direct else None)}


def resolve_metric(cik: str | int, metric: str, year: int | None = None, aliases: list[str] | None = None,
                   suspicious: bool = False, user_agent: str = "Fundamental-app contact@example.com") -> dict[str, Any]:
    return SECXBRLSearch(user_agent=user_agent).resolve(cik, metric, year, aliases=aliases, suspicious=suspicious)
