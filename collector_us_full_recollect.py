"""One-shot US full fundamental recollection runner.

Rebuilds every eligible US company from current SEC data using the current
classification and scoring stack. The work is split by profile/sector so the
specialized Utility pipeline is preserved while Standard sectors use the
validated XBRL fallback.

This script is resumable at the process level: every child collector upserts
each company immediately, so an interrupted job can be restarted safely.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from supabase import create_client

from collector_us_fundamental import SUPABASE_KEY, SUPABASE_URL

PAGE_SIZE = 1000
CHUNK_SIZE = 250

STANDARD_SECTORS = {
    "technology",
    "healthcare",
    "consumer",
    "industrials",
    "energy",
    "materials",
    "communication",
}


def fetch_eligible(sb):
    rows = []
    offset = 0
    columns = (
        "ticker,cik,company_name,sector_common,company_type,scoring_profile"
    )
    while True:
        page = (
            sb.table("US_Companies")
            .select(columns)
            .eq("is_fundamental_eligible", True)
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def chunks(values, size=CHUNK_SIZE):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def run_child(command, label):
    print(f"\n========== {label} ==========")
    print(" ".join(command))
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"{label} failed with exit code {completed.returncode}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=CHUNK_SIZE,
        help="Tickers per child collector process.",
    )
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows = fetch_eligible(sb)

    standard = []
    utility = []
    other = []

    for row in rows:
        profile = row.get("scoring_profile") or "standard"
        sector = row.get("sector_common")
        ticker = row["ticker"]

        if profile == "utility":
            utility.append(ticker)
        elif profile == "standard" and sector in STANDARD_SECTORS:
            standard.append(ticker)
        else:
            other.append(ticker)

    print(
        f"[FULL US] eligible={len(rows)} "
        f"standard_with_fallback={len(standard)} "
        f"utility_specialized={len(utility)} "
        f"other_profiles={len(other)}"
    )

    root = [sys.executable]

    for n, batch in enumerate(chunks(standard, args.chunk_size), 1):
        run_child(
            root + ["collector_us_standard_fallback.py", "--tickers", ",".join(batch)],
            f"STANDARD batch {n} ({len(batch)} tickers)",
        )

    for n, batch in enumerate(chunks(other, args.chunk_size), 1):
        run_child(
            root + [
                "collector_us_fundamental_v5.py",
                "--tickers",
                ",".join(batch),
                "--refresh-existing",
            ],
            f"SPECIAL/OTHER batch {n} ({len(batch)} tickers)",
        )

    for n, batch in enumerate(chunks(utility, args.chunk_size), 1):
        run_child(
            root + ["collector_us_utility_v4.py", "--tickers", ",".join(batch)],
            f"UTILITY batch {n} ({len(batch)} tickers)",
        )

    print(
        f"\n[FULL US] Completed all groups. "
        f"eligible={len(rows)} standard={len(standard)} "
        f"other={len(other)} utility={len(utility)}"
    )


if __name__ == "__main__":
    main()
