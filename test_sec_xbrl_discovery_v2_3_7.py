"""Read-only broad SEC XBRL discovery for unresolved Standard metrics.

V2.3.7 deliberately does NOT score or write anything. It widens discovery beyond
METRIC_TERMS so we can inspect the actual concepts used by difficult filers.
Raw SEC payloads stay in memory only.
"""
from __future__ import annotations

import os
from collections import defaultdict

import requests

from sec_xbrl_search_v2_3_5 import SECXBRLSearchV2_3_5
from sec_xbrl_search_v2_2 import _date, _annual_duration
from sec_xbrl_search_v2_3 import _hard_excluded

CASES = {
    "T": {
        "inventory": {"inventory", "inventor"},
    },
    "NEM": {
        "interest": {"interest", "finance", "borrowing", "debt", "loan"},
        "inventory": {"inventory", "inventor", "materials", "supplies", "concentrate", "preciousmetals"},
        "sga": {"selling", "marketing", "general", "administrative", "expense"},
    },
    "DE": {
        "current_assets": {"assets", "current", "noncurrent"},
        "current_liabilities": {"liabilities", "current", "noncurrent"},
    },
}

CIKS = {
    "T": "732717",
    "NEM": "1164727",
    "DE": "315189",
}


def _local(concept: str | None) -> str:
    return (concept or "").rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _compact(text: str | None) -> str:
    return "".join(ch.lower() for ch in (text or "") if ch.isalnum())


def _matches(concept: str, label: str, tokens: set[str]) -> bool:
    c = _compact(concept)
    l = _compact(label)
    return any(tok in c or tok in l for tok in tokens)


def _is_instant(row: dict) -> bool:
    return not bool(row.get("start"))


def _print_candidates(rows: list[dict], tokens: set[str], metric: str, target: int | None, max_rows: int = 30):
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        end = _date(r.get("end"))
        if target is not None and (end is None or end.year != int(target)):
            continue
        if r.get("dimensioned"):
            continue
        concept = _local(r.get("concept", ""))
        label = r.get("label", "") or ""
        if not _matches(concept, label, tokens):
            continue
        grouped[f"{r.get('namespace','')}:{concept}"].append(r)

    ranked = sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    print(f"\nDISCOVERY: {metric} | survivors={len(ranked)}")
    if not ranked:
        print("  NONE")
        return

    for key, facts in ranked[:max_rows]:
        print(f"  {key} | count={len(facts)}")
        for r in facts[:5]:
            start, end = r.get("start"), r.get("end")
            annual = bool(start) and _annual_duration(start, end)
            instant = _is_instant(r)
            excluded = _hard_excluded(metric, r.get("concept", ""), r.get("label", ""))
            print(
                f"      value={r.get('value')} | start={start} | end={end} "
                f"| instant={instant} | annual={annual} | context={r.get('contextRef')} "
                f"| excluded={excluded} | label={label_preview(r.get('label'))}"
            )


def label_preview(label: str | None) -> str:
    text = " ".join(str(label or "").split())
    return text[:140]


def print_context_groups(rows: list[dict], target: int | None, concepts: set[str], title: str):
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        end = _date(r.get("end"))
        if target is not None and (end is None or end.year != int(target)):
            continue
        if r.get("dimensioned") or not _is_instant(r):
            continue
        local = _compact(_local(r.get("concept", "")))
        if local in concepts:
            groups[str(r.get("contextRef"))].append(r)

    print(f"\nCONTEXT GROUPS: {title}")
    for context, facts in sorted(groups.items()):
        print(f"  context={context}")
        for r in facts:
            print(f"    {_local(r.get('concept'))} = {r.get('value')} | label={label_preview(r.get('label'))}")


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")})
    resolver = SECXBRLSearchV2_3_5(session=session)

    for ticker, metrics in CASES.items():
        cik = CIKS[ticker]
        submissions = resolver.submissions(cik)
        target = resolver._latest_annual_fy(submissions)
        rows, meta = resolver._inline_filing_rows(cik, submissions)

        print("=" * 110)
        print(f"{ticker} target_fy={target} rows={len(rows)} filing={meta.get('primary_document')}")

        for metric, tokens in metrics.items():
            _print_candidates(rows, tokens, metric, target)

        if ticker == "NEM":
            print_context_groups(
                rows,
                target,
                {"concentrateinventorynetofreserves", "materialssuppliesandotherinventorynetofreserves", "preciousmetalsinventorynetofreserves"},
                "NEM inventory components",
            )

        if ticker == "DE":
            print_context_groups(
                rows,
                target,
                {"assetscurrent", "assetsnoncurrent", "assets", "liabilitiescurrent", "liabilitiesnoncurrent", "liabilities", "stockholdersequity"},
                "DE balance-sheet identity candidates",
            )


if __name__ == "__main__":
    main()
