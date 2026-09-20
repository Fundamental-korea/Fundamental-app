"""Targeted recovery runner for companies not refreshed by a prior US recollection.

The caller supplies the previous run start time via --since. Only eligible
companies whose US_Fundamental.updated_at is older than that cutoff are retried.
This avoids repeating a multi-hour full recollection just to recover a small
set of missed companies.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from supabase import create_client

from collector_us_fundamental import SUPABASE_KEY, SUPABASE_URL

PAGE_SIZE = 1000
DEFAULT_CHUNK_SIZE = 100

STANDARD_SECTORS = {
    "technology", "healthcare", "consumer", "industrials",
    "energy", "materials", "communication",
}


def fetch_stale(sb, since):
    rows = []
    offset = 0
    while True:
        page = (
            sb.table("US_Companies")
            .select("ticker,cik,company_name,sector_common,scoring_profile")
            .eq("is_fundamental_eligible", True)
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

    fresh = (
        sb.table("US_Fundamental")
        .select("ticker,updated_at")
        .gte("updated_at", since)
        .execute()
        .data
        or []
    )
    refreshed = {row["ticker"] for row in fresh}
    return [row for row in rows if row["ticker"] not in refreshed]


def chunks(values, size):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def run_child(command, label):
    print(f"\n========== {label} ==========")
    print(" ".join(command), flush=True)
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {completed.returncode}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True, help="Previous recollection start time in ISO-8601 format.")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    stale = fetch_stale(sb, args.since)

    standard = [
        r["ticker"] for r in stale
        if (r.get("scoring_profile") or "standard") == "standard"
        and r.get("sector_common") in STANDARD_SECTORS
    ]
    utility = [
        r["ticker"] for r in stale
        if (r.get("scoring_profile") or "standard") == "utility"
    ]
    other = [r["ticker"] for r in stale if r["ticker"] not in set(standard) | set(utility)]

    print(
        f"[RECOVERY] stale={len(stale)} standard={len(standard)} "
        f"utility={len(utility)} other={len(other)}",
        flush=True,
    )

    root = [sys.executable]
    for n, batch in enumerate(chunks(standard, args.chunk_size), 1):
        run_child(
            root + ["collector_us_standard_fallback.py", "--tickers", ",".join(batch)],
            f"STANDARD recovery batch {n} ({len(batch)} tickers)",
        )

    for n, batch in enumerate(chunks(utility, args.chunk_size), 1):
        run_child(
            root + ["collector_us_utility_v4.py", "--tickers", ",".join(batch)],
            f"UTILITY recovery batch {n} ({len(batch)} tickers)",
        )

    if other:
        print(f"[RECOVERY] Unhandled profiles remain: {','.join(other)}", flush=True)

    print("[RECOVERY] Completed.", flush=True)


if __name__ == "__main__":
    main()
