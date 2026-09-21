"""Backfill US score completeness metadata without changing scores.

The script reads existing period_scores and derives available weight,
coverage, score cap, and confidence level from the already-stored metric
entries. It never recomputes or changes total_score/grade.
"""
from __future__ import annotations

import argparse
import os

from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
PAGE_SIZE = 200
UPDATE_BATCH = 50


def confidence(coverage):
    if coverage >= 90.0:
        return "high"
    if coverage >= 75.0:
        return "medium"
    if coverage >= 60.0:
        return "low"
    return "insufficient"


def cap(coverage):
    if coverage >= 90.0:
        return 100.0
    if coverage >= 75.0:
        return 92.0
    if coverage >= 60.0:
        return 82.0
    return 70.0


def reliability(period_scores):
    if not isinstance(period_scores, dict) or not period_scores:
        return "none"
    latest = period_scores.get("1y") or next(iter(period_scores.values()))
    avg = latest.get("avg") if isinstance(latest, dict) else {}
    coverage = float((avg or {}).get("coverage_pct", 0) or 0)
    periods = len(period_scores)
    if periods >= 3 and coverage >= 90:
        return "high"
    if periods >= 2 and coverage >= 75:
        return "medium"
    if coverage >= 60:
        return "low"
    return "none"


def fetch_rows(sb):
    rows = []
    offset = 0
    while True:
        page = (
            sb.table("US_Fundamental")
            .select("ticker,period_scores")
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


def enrich(period_scores):
    if not isinstance(period_scores, dict):
        return period_scores, False
    changed = False
    result = dict(period_scores)

    for period_key, period_data in period_scores.items():
        if not isinstance(period_data, dict):
            continue
        updated_period = dict(period_data)

        for view in ("avg", "worst"):
            score_data = period_data.get(view)
            if not isinstance(score_data, dict):
                continue
            metrics = score_data.get("metric_scores")
            if not isinstance(metrics, dict):
                continue

            total_weight = sum(
                float(entry.get("weight", 0) or 0)
                for entry in metrics.values()
                if isinstance(entry, dict)
            )
            available_weight = sum(
                float(entry.get("weight", 0) or 0)
                for entry in metrics.values()
                if isinstance(entry, dict) and entry.get("value") is not None
            )
            if total_weight <= 0:
                continue

            coverage = round(available_weight / total_weight * 100.0, 1)
            updated_score = dict(score_data)
            updated_score["available_weight"] = available_weight
            updated_score["coverage_pct"] = coverage
            updated_score["score_cap"] = cap(coverage)
            updated_score["confidence_level"] = confidence(coverage)

            if updated_score != score_data:
                updated_period[view] = updated_score
                changed = True

        if updated_period != period_data:
            result[period_key] = updated_period

    return result, changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows = fetch_rows(sb)
    if args.limit is not None:
        rows = rows[:max(0, args.limit)]

    updates = []
    for row in rows:
        original = row.get("period_scores")
        enriched, changed = enrich(original)
        if changed:
            updates.append((row["ticker"], enriched, reliability(enriched)))

    print(f"[SCORE META] rows={len(rows)} updates={len(updates)}")
    for ticker, _, _ in updates[:10]:
        print(f"  {ticker}")

    for i in range(0, len(updates), UPDATE_BATCH):
        for ticker, period_scores, data_reliability in updates[i:i + UPDATE_BATCH]:
            sb.table("US_Fundamental").update(
                {
                    "period_scores": period_scores,
                    "data_reliability": data_reliability,
                }
            ).eq("ticker", ticker).execute()
        print(
            f"[SCORE META] applied "
            f"{min(i + UPDATE_BATCH, len(updates))}/{len(updates)}"
        )

    print("[SCORE META] completed.")


if __name__ == "__main__":
    main()
