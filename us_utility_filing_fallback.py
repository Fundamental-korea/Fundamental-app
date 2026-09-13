"""Filing-level XBRL fallback for utility extraction.

Downloads the latest annual SEC filing's XBRL instance in memory and converts
its contexts/facts into a compact internal fact namespace. Nothing is stored.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import requests


INSTANCE_EXCLUDE = {
    "filingsummary.xml", "filingsummary.json", "metaLinks.json".lower(),
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return ""


def _date(value):
    if not value:
        return None
    value = str(value)[:10]
    try:
        from datetime import date
        return date.fromisoformat(value)
    except ValueError:
        return None


def _duration_days(start, end):
    s, e = _date(start), _date(end)
    if not s or not e:
        return None
    return (e - s).days


def _is_instance_candidate(name: str) -> bool:
    low = name.lower()
    if not low.endswith(".xml"):
        return False
    if low in INSTANCE_EXCLUDE:
        return False
    if any(x in low for x in ("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml", "_ref.xml")):
        return False
    return True


def _accession(submissions):
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    for i, form in enumerate(forms):
        if form not in {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
            continue
        acc = accs[i] if i < len(accs) else None
        doc = docs[i] if i < len(docs) else None
        if acc:
            return acc, doc
    return None, None


def _filing_index(session, cik, accession):
    acc_compact = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_compact}/index.json"
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.json(), acc_compact


def _choose_instance(index_json, primary_document):
    items = index_json.get("directory", {}).get("item", [])
    names = [x.get("name") for x in items if x.get("name")]
    # Prefer a file that looks like the filing's XBRL instance and is not a
    # taxonomy/support file. Primary-document stem is a useful tie breaker.
    stem = re.sub(r"\.[^.]+$", "", primary_document or "").lower()
    candidates = [n for n in names if _is_instance_candidate(n)]
    preferred = [n for n in candidates if stem and n.lower().startswith(stem)]
    if preferred:
        return preferred[0]
    preferred = [n for n in candidates if "instance" in n.lower() or "xbrl" in n.lower()]
    if preferred:
        return preferred[0]
    # Last resort: largest-looking XML name; filing instances commonly contain
    # the registrant ticker/date in the filename.
    return max(candidates, key=len) if candidates else None


def _parse_instance(xml_text: str):
    root = ET.fromstring(xml_text)
    contexts = {}
    units = {}

    for elem in root.iter():
        local = _local(elem.tag)
        if local == "context":
            cid = elem.attrib.get("id")
            if not cid:
                continue
            instant = None
            start = end = None
            for child in elem.iter():
                name = _local(child.tag)
                text = (child.text or "").strip()
                if name == "instant" and text:
                    instant = text
                elif name == "startDate" and text:
                    start = text
                elif name == "endDate" and text:
                    end = text
            contexts[cid] = {"instant": instant, "start": start, "end": end}
        elif local == "unit":
            uid = elem.attrib.get("id")
            if not uid:
                continue
            measure = None
            for child in elem.iter():
                if _local(child.tag) == "measure" and (child.text or "").strip():
                    measure = (child.text or "").strip()
                    break
            units[uid] = measure

    facts = {}
    for elem in root.iter():
        if not elem.attrib.get("contextRef"):
            continue
        local = _local(elem.tag)
        ns = _namespace(elem.tag)
        if not local or local in {"context", "unit"} or not ns:
            continue
        text = (elem.text or "").strip()
        if not text:
            continue
        try:
            value = float(text.replace(",", ""))
        except ValueError:
            continue
        ctx = contexts.get(elem.attrib.get("contextRef"))
        if not ctx:
            continue
        uid = elem.attrib.get("unitRef")
        unit = units.get(uid) if uid else None
        row = {
            "val": value,
            "form": "10-K",
            "filed": "",
            "frame": None,
            "fy": None,
            "end": ctx.get("instant") or ctx.get("end"),
            "start": ctx.get("start"),
        }
        # Only annual-looking facts are useful to the current extractor. Keep
        # instant facts separate by their absence of start date.
        if ctx.get("start") and ctx.get("end"):
            row["days"] = _duration_days(ctx["start"], ctx["end"])
        else:
            row["days"] = None
        facts.setdefault(local, []).append((unit, row))
    return facts


def augment_with_latest_filing(session: requests.Session, cik: str, submissions: dict, facts: dict):
    """Augment facts in memory with filing-instance concepts.

    Returns (augmented_facts, metadata). On any failure, the original facts are
    returned unchanged so the production collector remains resilient.
    """
    try:
        accession, primary_document = _accession(submissions)
        if not accession:
            return facts, {"used": False, "reason": "no_annual_filing"}
        index_json, acc_compact = _filing_index(session, cik, accession)
        instance = _choose_instance(index_json, primary_document)
        if not instance:
            return facts, {"used": False, "reason": "no_xbrl_instance", "accession": accession}
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_compact}/{instance}"
        r = session.get(url, timeout=45)
        r.raise_for_status()
        parsed = _parse_instance(r.text)
        merged = dict(facts)
        root = dict((facts or {}).get("facts", facts or {}))
        root.setdefault("filing-xbrl", {})
        for tag, rows in parsed.items():
            units = root["filing-xbrl"].setdefault(tag, {}).setdefault("units", {})
            for unit, row in rows:
                unit_key = unit or "USD"
                units.setdefault(unit_key, []).append(row)
        merged["facts"] = root
        return merged, {
            "used": True,
            "accession": accession,
            "primary_document": primary_document,
            "instance": instance,
            "concept_count": len(parsed),
        }
    except Exception as exc:
        return facts, {"used": False, "reason": type(exc).__name__, "error": str(exc)[:240]}
