"""Repair root-level US_Fundamental score fields.

Some companies have valid period_scores but no 1-year period yet (for example,
newer/current-fiscal data may only have 3/5/10-year comparisons). The collector
previously left total_score/grade NULL because it only promoted period=1.
This repair promotes the shortest available period in order 1 -> 3 -> 5 -> 10.

Usage:
  python repair_us_standard_root_fields.py
"""

import os
from datetime import datetime, timezone

from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY") or ""


def main():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows = (
        sb.table("US_Fundamental")
        .select("ticker,total_score,grade,missing_metric_count,period_scores,data_unavailable")
        .is_("total_score", "null")
        .eq("data_unavailable", False)
        .limit(10000)
        .execute()
        .data
    )

    repaired = 0
    skipped = 0
    for row in rows:
        scores = row.get("period_scores") or {}
        chosen = None
        for period in ("1", "3", "5", "10"):
            block = scores.get(period) or {}
            score_block = block.get("scores") or {}
            if score_block.get("total_score") is not None:
                chosen = score_block
                break

        if chosen is None:
            skipped += 1
            continue

        update = {
            "total_score": int(round(float(chosen["total_score"]))),
            "grade": chosen.get("grade"),
            "missing_metric_count": int(chosen.get("missing_metric_count", row.get("missing_metric_count") or 0)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        sb.table("US_Fundamental").update(update).eq("ticker", row["ticker"]).execute()
        repaired += 1

    print(f"[ROOT SCORE REPAIR] repaired={repaired} skipped={skipped}")


if __name__ == "__main__":
    main()
