"""Repair usable US rows that have no canonical 1Y score period.

The normal profile-specific collectors are reused for a very small anomaly set.
This does not rewrite populated metric values beyond the collector's existing
canonical logic.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from supabase import create_client

from collector_us_fundamental import SUPABASE_KEY, SUPABASE_URL

PAGE_SIZE = 100
STANDARD_SECTORS = {
    "technology", "healthcare", "consumer", "industrials",
    "energy", "materials", "communication",
}


def fetch_rows(sb):
    rows = []
    offset = 0
    while True:
        page = (
            sb.table("US_Fundamental")
            .select("ticker,total_score,period_scores,data_unavailable")
            .eq("data_unavailable", False)
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    fundamentals = fetch_rows(sb)

    tickers = []
    for row in fundamentals:
        periods = row.get("period_scores") or {}
        one_year = periods.get("1y")
        if one_year is None or row.get("total_score") is None:
            tickers.append(row["ticker"])

    if args.limit:
        tickers = tickers[:max(0, args.limit)]

    if not tickers:
        print("[SCORE GAP] no anomalies found.")
        return

    meta_rows = []
    for start in range(0, len(tickers), PAGE_SIZE):
        batch = tickers[start:start + PAGE_SIZE]
        page = (
            sb.table("US_Companies")
            .select("ticker,cik,scoring_profile,sector_common")
            .in_("ticker", batch)
            .execute()
            .data
            or []
        )
        meta_rows.extend(page)

    standard = [
        row["ticker"] for row in meta_rows
        if (row.get("scoring_profile") or "standard") == "standard"
        and row.get("sector_common") in STANDARD_SECTORS
    ]
    utility = [
        row["ticker"] for row in meta_rows
        if row.get("scoring_profile") == "utility"
    ]
    other = [
        row["ticker"] for row in meta_rows
        if row["ticker"] not in set(standard) | set(utility)
    ]

    print(
        f"[SCORE GAP] anomalies={len(tickers)} "
        f"standard={len(standard)} utility={len(utility)} other={len(other)}",
        flush=True,
    )

    root = [sys.executable]
    if standard:
        rc = subprocess.run(
            root + ["collector_us_standard_fallback.py", "--tickers", ",".join(standard)],
            check=False,
        ).returncode
        if rc != 0:
            raise RuntimeError(f"standard score-gap recovery failed with exit code {rc}")

    if utility:
        rc = subprocess.run(
            root + ["collector_us_utility_v4.py", "--tickers", ",".join(utility)],
            check=False,
        ).returncode
        if rc != 0:
            raise RuntimeError(f"utility score-gap recovery failed with exit code {rc}")

    if other:
        rc = subprocess.run(
            root + [
                "collector_us_fundamental_v5.py",
                "--tickers", ",".join(other),
                "--refresh-existing",
            ],
            check=False,
        ).returncode
        if rc != 0:
            raise RuntimeError(f"special score-gap recovery failed with exit code {rc}")

    print("[SCORE GAP] Completed.", flush=True)


if __name__ == "__main__":
    main()
