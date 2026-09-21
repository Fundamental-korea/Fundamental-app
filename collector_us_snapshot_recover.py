"""Backfill snapshots for already-usable US fundamental rows that lack them.

This runner does not broaden the universe. It only re-runs the existing
profile-specific collectors for eligible rows where data_unavailable=false and
snapshot is NULL, so the canonical snapshot construction is applied without
recollecting the whole database.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from supabase import create_client

from collector_us_fundamental import SUPABASE_KEY, SUPABASE_URL

PAGE_SIZE = 1000
BATCH_SIZE = 50
STANDARD_SECTORS = {
    "technology", "healthcare", "consumer", "industrials",
    "energy", "materials", "communication",
}


def fetch_targets(sb):
    rows = []
    offset = 0
    while True:
        page = (
            sb.table("US_Fundamental")
            .select("ticker")
            .eq("data_unavailable", False)
            .is_("snapshot", "null")
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        if not page:
            break
        rows.extend(row["ticker"] for row in page if row.get("ticker"))
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    if not rows:
        return [], [], []

    universe = []
    offset = 0
    while True:
        page = (
            sb.table("US_Companies")
            .select("ticker,scoring_profile,sector_common,is_fundamental_eligible")
            .in_("ticker", rows[offset:offset + PAGE_SIZE])
            .order("ticker")
            .execute()
            .data
            or []
        )
        universe.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    meta = {row["ticker"]: row for row in universe if row.get("ticker")}
    standard = [
        t for t in rows
        if (meta.get(t, {}).get("scoring_profile") or "standard") == "standard"
        and meta.get(t, {}).get("sector_common") in STANDARD_SECTORS
    ]
    utility = [
        t for t in rows
        if (meta.get(t, {}).get("scoring_profile") or "standard") == "utility"
    ]
    other = [t for t in rows if t not in set(standard) | set(utility)]
    return standard, utility, other


def chunks(values, size):
    for i in range(0, len(values), size):
        yield i + 1, values[i:i + size]


def run(command, label):
    print(f"\n========== {label} ==========", flush=True)
    print(" ".join(command), flush=True)
    rc = subprocess.run(command, check=False).returncode
    if rc != 0:
        raise RuntimeError(f"{label} failed with exit code {rc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    standard, utility, other = fetch_targets(sb)

    print(
        f"[SNAPSHOT] targets standard={len(standard)} utility={len(utility)} other={len(other)}",
        flush=True,
    )
    if other:
        print(f"[SNAPSHOT] unhandled profiles: {','.join(other)}", flush=True)
    if args.dry_run:
        return

    root = [sys.executable]
    for n, batch in chunks(standard, BATCH_SIZE):
        run(
            root + ["collector_us_standard_fallback.py", "--tickers", ",".join(batch)],
            f"STANDARD snapshot batch {n} ({len(batch)} tickers)",
        )
    for n, batch in chunks(utility, BATCH_SIZE):
        run(
            root + ["collector_us_utility_v4.py", "--tickers", ",".join(batch)],
            f"UTILITY snapshot batch {n} ({len(batch)} tickers)",
        )

    print("[SNAPSHOT] Completed.", flush=True)


if __name__ == "__main__":
    main()
