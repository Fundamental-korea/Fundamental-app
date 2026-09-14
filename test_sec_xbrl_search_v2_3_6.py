"""Read-only diagnostic for unresolved Standard SEC XBRL metrics.

This does not write Supabase and never persists raw SEC payloads.
It answers one question: which target-FY, non-dimensional filing concepts
survive hard exclusions for metrics that are still unresolved?
"""
from __future__ import annotations

import os
from collections import defaultdict

import requests

from sec_xbrl_search_v2_3_5 import SECXBRLSearchV2_3_5
from sec_xbrl_search_v2_2 import METRIC_TERMS, _date, _annual_duration
from sec_xbrl_search_v2_3 import _hard_excluded, _local_concept

TICKERS = {
    "HON": ["liabilities"],
    "VZ": [],
    "T": ["inventory"],
    "NEM": ["interest_expense", "inventory", "sga"],
    "DE": ["current_assets", "current_liabilities"],
}


def _metric_tokens(metric: str) -> set[str]:
    out = set()
    for term in METRIC_TERMS.get(metric, ()):
        t = "".join(ch.lower() for ch in term if ch.isalnum())
        if len(t) >= 4:
            out.add(t)
    return out


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")})
    resolver = SECXBRLSearchV2_3_5(session=session)

    for ticker, metrics in TICKERS.items():
        cik = {
            "HON": "773840",
            "VZ": "732712",
            "T": "732717",
            "NEM": "1164727",
            "DE": "315189",
        }[ticker]
        submissions = resolver.submissions(cik)
        target = resolver._latest_annual_fy(submissions)
        rows, meta = resolver._inline_filing_rows(cik, submissions)

        print("=" * 100)
        print(f"{ticker} target_fy={target} rows={len(rows)} filing={meta.get('primary_document')}")

        for metric in metrics:
            tokens = _metric_tokens(metric)
            counts = defaultdict(lambda: {"count": 0, "values": []})
            for r in rows:
                end = _date(r.get("end"))
                if target is not None and (end is None or end.year != int(target)):
                    continue
                if r.get("dimensioned"):
                    continue
                if _hard_excluded(metric, r.get("concept", ""), r.get("label", "")):
                    continue
                concept = _local_concept(r.get("concept", ""))
                compact = "".join(ch.lower() for ch in concept if ch.isalnum())
                if not any(tok in compact for tok in tokens):
                    continue
                key = f"{r.get('namespace','')}:{concept}"
                counts[key]["count"] += 1
                if len(counts[key]["values"]) < 3:
                    counts[key]["values"].append((r.get("value"), r.get("start"), r.get("end"), r.get("contextRef")))

            ranked = sorted(counts.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
            print(f"\nMETRIC: {metric} | keyword survivors={len(ranked)}")
            if not ranked:
                print("  NO KEYWORD SURVIVORS")
                continue
            for key, info in ranked[:20]:
                print(f"  {key} | count={info['count']}")
                for value, start, end, context in info["values"]:
                    annual = bool(start) and _annual_duration(start, end)
                    print(f"      value={value} | start={start} | end={end} | annual={annual} | context={context}")


if __name__ == "__main__":
    main()
