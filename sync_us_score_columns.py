"""Synchronize denormalized US score columns with canonical 1Y score data.

period_scores.1y.avg is the canonical score record used by the UI and score
metadata. Only the denormalized total_score, grade, and missing_metric_count
columns are synchronized; metric values are never changed.
"""
from __future__ import annotations

import os

from supabase import create_client

URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
PAGE_SIZE = 100


def main():
    if not KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(URL, KEY)
    offset = 0
    scanned = 0
    updated = 0

    while True:
        rows = (
            sb.table("US_Fundamental")
            .select("ticker,total_score,grade,missing_metric_count,period_scores")
            .eq("data_unavailable", False)
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        if not rows:
            break

        for row in rows:
            scanned += 1
            avg = (
                ((row.get("period_scores") or {}).get("1y") or {})
                .get("avg") or {}
            )
            score = avg.get("total_score")
            grade = avg.get("grade")
            missing = avg.get("missing_metric_count")
            if score is None:
                continue

            new_total = int(round(float(score)))
            new_grade = grade
            new_missing = int(missing) if missing is not None else row.get("missing_metric_count")

            if (
                row.get("total_score") == new_total
                and row.get("grade") == new_grade
                and row.get("missing_metric_count") == new_missing
            ):
                continue

            sb.table("US_Fundamental").update(
                {
                    "total_score": new_total,
                    "grade": new_grade,
                    "missing_metric_count": new_missing,
                }
            ).eq("ticker", row["ticker"]).execute()
            updated += 1

        print(
            f"[SYNC] scanned={scanned} updated={updated}",
            flush=True,
        )

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    print(f"[SYNC] completed scanned={scanned} updated={updated}", flush=True)


if __name__ == "__main__":
    main()
