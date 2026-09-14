"""Inline XBRL parser for SEC HTML filings.

Used only as an in-memory fallback when SEC Company Facts or the separate
XBRL instance file does not expose the requested concept. Raw filing content
is never persisted.
"""
from __future__ import annotations

import re
from typing import Any

from lxml import html


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") and "}" in tag else ""


def _num(text: str | None, scale: str | None = None, sign: str | None = None) -> float | None:
    if not text:
        return None
    s = "".join(text.split()).replace(",", "")
    if s in {"", "-", "—", "–"}:
        return None
    s = re.sub(r"[$€£¥]", "", s)
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        value = float(s)
    except ValueError:
        return None
    if negative or sign == "-":
        value = -abs(value)
    try:
        if scale:
            value *= 10 ** int(scale)
    except (TypeError, ValueError, OverflowError):
        pass
    return value


def _dimensioned(context) -> bool:
    for e in context.iter():
        if _local(e.tag) in {"explicitMember", "typedMember"}:
            return True
    return False


def parse_inline_xbrl(document_text: str | bytes, labels: dict[str, str] | None = None,
                      filed: str | None = None, form: str = "10-K") -> list[dict[str, Any]]:
    """Extract ix:nonFraction facts and their context periods from inline XBRL HTML.

    SEC Inline XBRL documents commonly contain an XML encoding declaration.
    lxml rejects such declarations when given a Unicode string, so pass bytes
    whenever the input is text. This preserves the document's declared encoding
    and avoids the "Unicode strings with encoding declaration" failure.
    """
    labels = labels or {}
    payload = document_text.encode("utf-8") if isinstance(document_text, str) else document_text
    root = html.fromstring(payload)

    contexts: dict[str, dict[str, Any]] = {}
    for e in root.xpath("//*[local-name()='context']"):
        cid = e.get("id")
        if not cid:
            continue
        instant = next((x.text.strip() for x in e.xpath(".//*[local-name()='instant']") if x.text), None)
        starts = next((x.text.strip() for x in e.xpath(".//*[local-name()='startDate']") if x.text), None)
        ends = next((x.text.strip() for x in e.xpath(".//*[local-name()='endDate']") if x.text), None)
        contexts[cid] = {
            "instant": instant,
            "start": starts,
            "end": ends,
            "dimensioned": _dimensioned(e),
        }

    units: dict[str, str] = {}
    for e in root.xpath("//*[local-name()='unit']"):
        uid = e.get("id")
        if not uid:
            continue
        measure = next((x.text.strip() for x in e.xpath(".//*[local-name()='measure"]") if x.text), "")
        units[uid] = measure

    rows: list[dict[str, Any]] = []
    for e in root.xpath("//*[local-name()='nonFraction']"):
        context_ref = e.get("contextRef")
        if not context_ref or context_ref not in contexts:
            continue
        if e.get("nil", "false").lower() == "true":
            continue
        name = e.get("name", "")
        if not name:
            continue
        if ":" in name:
            namespace, concept = name.split(":", 1)
        else:
            namespace, concept = "", name
        ctx = contexts[context_ref]
        instant = bool(ctx.get("instant"))
        end = ctx.get("instant") or ctx.get("end")
        start = None if instant else ctx.get("start")
        value = _num(" ".join(e.itertext()), e.get("scale"), e.get("sign"))
        if value is None or not end:
            continue
        rows.append({
            "concept": concept,
            "namespace": namespace,
            "label": labels.get(concept, ""),
            "value": value,
            "unit": units.get(e.get("unitRef"), ""),
            "end": end,
            "start": start,
            "fy": None,
            "form": form,
            "filed": filed,
            "instant": instant,
            "dimensioned": bool(ctx.get("dimensioned")),
        })
    return rows
