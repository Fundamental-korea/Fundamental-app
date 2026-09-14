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
    """Return a case-insensitive local element name."""
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()


def _attr(element, name: str) -> str | None:
    """Get an HTML/XML attribute case-insensitively, including namespaced attrs."""
    wanted = name.lower()
    for key, value in element.attrib.items():
        local = key.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()
        if local == wanted:
            return value
    return None


def _namespace(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    if ":" in tag:
        return tag.split(":", 1)[0]
    return ""


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
        if _local(e.tag) in {"explicitmember", "typedmember"}:
            return True
    return False


def _first_text(element, local_name: str) -> str | None:
    wanted = local_name.lower()
    for child in element.iter():
        if child is element:
            continue
        if _local(child.tag) == wanted and child.text:
            return child.text.strip()
    return None


def parse_inline_xbrl(document_text: str | bytes, labels: dict[str, str] | None = None,
                      filed: str | None = None, form: str = "10-K") -> list[dict[str, Any]]:
    """Extract ix:nonFraction facts and their context periods from inline XBRL HTML.

    Raw SEC filing content is parsed in memory only. Element and attribute
    matching is deliberately namespace/case insensitive.
    """
    labels = labels or {}
    payload = document_text.encode("utf-8") if isinstance(document_text, str) else document_text
    root = html.fromstring(payload)

    contexts: dict[str, dict[str, Any]] = {}
    for e in root.iter():
        if _local(e.tag) != "context":
            continue
        cid = _attr(e, "id")
        if not cid:
            continue
        instant = _first_text(e, "instant")
        starts = _first_text(e, "startDate")
        ends = _first_text(e, "endDate")
        contexts[cid] = {
            "instant": instant,
            "start": starts,
            "end": ends,
            "dimensioned": _dimensioned(e),
        }

    units: dict[str, str] = {}
    for e in root.iter():
        if _local(e.tag) != "unit":
            continue
        uid = _attr(e, "id")
        if not uid:
            continue
        measure = _first_text(e, "measure") or ""
        units[uid] = measure

    rows: list[dict[str, Any]] = []
    for e in root.iter():
        if _local(e.tag) != "nonfraction":
            continue

        context_ref = _attr(e, "contextRef")
        if not context_ref or context_ref not in contexts:
            continue
        if (_attr(e, "nil") or "false").lower() == "true":
            continue

        name = _attr(e, "name") or ""
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
        value = _num(" ".join(e.itertext()), _attr(e, "scale"), _attr(e, "sign"))
        if value is None or not end:
            continue

        unit_ref = _attr(e, "unitRef")
        rows.append({
            "concept": concept,
            "namespace": namespace,
            "label": labels.get(concept, ""),
            "value": value,
            "unit": units.get(unit_ref or "", ""),
            "end": end,
            "start": start,
            "fy": None,
            "form": form,
            "filed": filed,
            "instant": instant,
            "dimensioned": bool(ctx.get("dimensioned")),
            "contextRef": context_ref,
        })
    return rows
