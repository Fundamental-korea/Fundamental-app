"""Recover Standard-sector ROIC gaps using the expanded SEC debt taxonomy/fallback.

Only rows with usable data and a missing 1Y ROIC are targeted. The normal
Standard fallback collector is reused so all scoring fields remain internally
consistent after recovery; no synthetic debt estimate is introduced.
"""
from __future__ import annotations

import argparse
import subprocess
import sys

from supabase import create_client

from collector_us_fundamental import SUPABASE_KEY, SUPABASE_URL

PAGE_SIZE = 1000
BATCH_SIZE = 50
STANDARD_PROFILES = {"standard"}


def fetch_targets(sb):
    rows = []
    offset = 0
    while True:
        page = (
            sb.table("US_Fundamental")
            .select("ticker,period_scores,data_unavailable")
            .eq("data_unavailable", False)
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        if not page:
            break
        for row in page:
            score = ((row.get("period_scores") or {}).get("1y") or {})
            value = (((score.get("avg") or {}).get("metric_scores") or {}).get("roic") or {}).get("value")
            if value is None:
                rows.append(row["ticker"])
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    if not rows:
        return []

    out = []
    offset = 0
    while offset < len(rows):
        batch = rows[offset:offset + PAGE_SIZE]
        page = (
            sb.table("US_Companies")
            .select("ticker,scoring_profile,sector_common,is_fundamental_eligible")
            .in_("ticker", batch)
            .eq("is_fundamental_eligible", True)
            .execute()
            .data
            or []
        )
        out.extend(
            row["ticker"] for row in page
            if (row.get("scoring_profile") or "standard") in STANDARD_PROFILES
        )
        offset += PAGE_SIZE
    return sorted(set(out))


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
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    tickers = fetch_targets(sb)
    if args.limit is not None:
        tickers = tickers[:max(0, args.limit)]

    print(f"[ROIC] targets={len(tickers)}", flush=True)
    if args.dry_run:
        return

    root = [sys.executable]
    for n, batch in chunks(tickers, BATCH_SIZE):
        run(
            root + ["collector_us_standard_fallback.py", "--tickers", ",".join(batch)],
            f"STANDARD ROIC recovery batch {n} ({len(batch)} tickers)",
        )

    print("[ROIC] Completed.", flush=True)


if __name__ == "__main__":
    main()
