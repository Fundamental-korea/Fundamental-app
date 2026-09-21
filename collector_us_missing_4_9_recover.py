"""Recover useful Standard-profile US metric gaps of size 4-9.

This intentionally targets only normal operating-company sectors and reuses
the validated Standard fallback collector. It does not alter Financial/REIT/
BDC/Utility scoring with Standard-sector semantics.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from supabase import create_client

from collector_us_fundamental import SUPABASE_KEY, SUPABASE_URL

PAGE_SIZE = 100
BATCH_SIZE = 50
STANDARD_SECTORS = {
    "technology",
    "healthcare",
    "consumer",
    "industrials",
    "energy",
    "materials",
    "communication",
}


def fetch_targets(sb):
    rows = []
    offset = 0

    while True:
        page = (
            sb.table("US_Fundamental")
            .select("ticker,missing_metric_count,data_unavailable")
            .gte("missing_metric_count", 4)
            .lte("missing_metric_count", 9)
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

    tickers = [row["ticker"] for row in rows if row.get("ticker")]
    eligible = []
    for start in range(0, len(tickers), PAGE_SIZE):
        batch = tickers[start:start + PAGE_SIZE]
        companies = (
            sb.table("US_Companies")
            .select("ticker,scoring_profile,sector_common,company_type")
            .in_("ticker", batch)
            .execute()
            .data
            or []
        )
        for company in companies:
            if (
                (company.get("scoring_profile") or "standard") == "standard"
                and company.get("sector_common") in STANDARD_SECTORS
                and company.get("company_type") not in {"spac"}
            ):
                eligible.append(company["ticker"])

    return sorted(set(eligible))


def chunks(values, size):
    for i in range(0, len(values), size):
        yield i + 1, values[i:i + size]


def run_batch(batch, number, total):
    command = [
        sys.executable,
        "collector_us_standard_fallback.py",
        "--tickers",
        ",".join(batch),
    ]
    print(
        f"========== STANDARD 4-9 RECOVERY "
        f"batch {number} ({len(batch)} / {total}) ==========",
        flush=True,
    )
    print(" ".join(command), flush=True)
    rc = subprocess.run(command, check=False).returncode
    if rc != 0:
        raise RuntimeError(f"batch {number} failed with exit code {rc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    tickers = fetch_targets(sb)
    if args.limit:
        tickers = tickers[:max(0, args.limit)]

    print(f"[MISSING-4-9] targets={len(tickers)}", flush=True)

    for number, batch in chunks(tickers, BATCH_SIZE):
        run_batch(batch, number, len(tickers))

    print("[MISSING-4-9] Completed.", flush=True)


if __name__ == "__main__":
    main()
