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
    # Query in small pages: period_scores is a large JSONB field and a 1000-row
    # PostgREST request can exceed Supabase statement timeout.
    universe = []
    offset = 0
    while True:
        page = (
            sb.table("US_Companies")
            .select("ticker")
            .eq("is_fundamental_eligible", True)
            .eq("scoring_profile", "standard")
            .order("ticker")
            .range(offset, offset + 199)
            .execute()
            .data
            or []
        )
        if not page:
            break
        universe.extend(row["ticker"] for row in page if row.get("ticker"))
        if len(page) < 200:
            break
        offset += 200

    targets = []
    for i in range(0, len(universe), 100):
        batch = universe[i:i + 100]
        page = (
            sb.table("US_Fundamental")
            .select("ticker,period_scores,data_unavailable")
            .in_("ticker", batch)
            .eq("data_unavailable", False)
            .execute()
            .data
            or []
        )
        for row in page:
            score = ((row.get("period_scores") or {}).get("1y") or {})
            value = (
                ((score.get("avg") or {}).get("metric_scores") or {})
                .get("roic", {})
                .get("value")
            )
            if value is None:
                targets.append(row["ticker"])

    return sorted(set(targets))


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
