"""Final read-only validation for the US fundamental pipeline."""
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

    counts = {}
    for n in range(0, 11):
        q = (
            sb.table("US_Fundamental")
            .select("ticker", count="exact", head=True)
            .eq("data_unavailable", False)
            .eq("missing_metric_count", n)
            .execute()
        )
        counts[n] = q.count or 0
        print(f"missing={n}: {counts[n]}")

    unavailable = (
        sb.table("US_Fundamental")
        .select("ticker", count="exact", head=True)
        .eq("data_unavailable", True)
        .execute()
    )
    print(f"data_unavailable=true: {unavailable.count or 0}")

    missing_cik = (
        sb.table("US_Fundamental")
        .select("ticker", count="exact", head=True)
        .is_("cik", "null")
        .eq("data_unavailable", False)
        .execute()
    )
    missing_cik_count = missing_cik.count or 0
    print(f"usable rows missing CIK: {missing_cik_count}")

    missing_snapshot = (
        sb.table("US_Fundamental")
        .select("ticker", count="exact", head=True)
        .eq("data_unavailable", False)
        .is_("snapshot_fiscal_end", "null")
        .execute()
    )
    print(f"usable rows missing snapshot_fiscal_end: {missing_snapshot.count or 0}")

    score_mismatch = 0
    missing_mismatch = 0
    score_out_of_range = 0
    coverage_out_of_range = 0
    scanned = 0

    offset = 0
    while True:
        rows = (
            sb.table("US_Fundamental")
            .select("ticker,total_score,missing_metric_count,period_scores")
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
            total_score = row.get("total_score")
            if total_score is not None and not 0 <= total_score <= 100:
                score_out_of_range += 1

            avg = (
                ((row.get("period_scores") or {}).get("1y") or {})
                .get("avg") or {}
            )
            ps_score = avg.get("total_score")
            ps_missing = avg.get("missing_metric_count")
            coverage = avg.get("coverage_pct")

            if ps_score is not None:
                try:
                    if total_score is None or round(float(total_score)) != round(float(ps_score)):
                        score_mismatch += 1
                except (TypeError, ValueError):
                    score_mismatch += 1

            if ps_missing is not None:
                try:
                    if row.get("missing_metric_count") is None or int(row["missing_metric_count"]) != int(ps_missing):
                        missing_mismatch += 1
                except (TypeError, ValueError):
                    missing_mismatch += 1

            if coverage is not None:
                try:
                    if not 0 <= float(coverage) <= 100:
                        coverage_out_of_range += 1
                except (TypeError, ValueError):
                    coverage_out_of_range += 1

        print(
            f"[INTEGRITY] scanned={scanned} "
            f"score_mismatch={score_mismatch} "
            f"missing_mismatch={missing_mismatch}",
            flush=True,
        )

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    print(f"score_out_of_range: {score_out_of_range}")
    print(f"coverage_out_of_range: {coverage_out_of_range}")

    if missing_cik_count or score_mismatch or missing_mismatch or score_out_of_range or coverage_out_of_range:
        raise RuntimeError(
            "US pipeline integrity validation failed: "
            f"missing_cik={missing_cik_count}, "
            f"score_mismatch={score_mismatch}, "
            f"missing_mismatch={missing_mismatch}, "
            f"score_out_of_range={score_out_of_range}, "
            f"coverage_out_of_range={coverage_out_of_range}"
        )

    sample = ["AAPL", "MSFT", "NVDA", "JPM", "O", "RTX", "GSBD", "ARCC"]
    rows = (
        sb.table("US_Fundamental")
        .select(
            "ticker,total_score,grade,missing_metric_count,"
            "data_reliability,snapshot_fiscal_end,snapshot_form,period_scores"
        )
        .in_("ticker", sample)
        .execute()
        .data
        or []
    )
    by_ticker = {row["ticker"]: row for row in rows}

    for ticker in sample:
        row = by_ticker.get(ticker)
        if not row:
            print(f"sample {ticker}: NOT FOUND")
            continue

        avg = (
            ((row.get("period_scores") or {}).get("1y") or {})
            .get("avg") or {}
        )
        metrics = avg.get("metric_scores") or {}

        print(
            f"sample {ticker}: "
            f"score={row.get('total_score')} "
            f"grade={row.get('grade')} "
            f"missing={row.get('missing_metric_count')} "
            f"reliability={row.get('data_reliability')} "
            f"roic={(metrics.get('roic') or {}).get('value')} "
            f"interest_coverage={(metrics.get('interest_coverage') or {}).get('value')} "
            f"snapshot={row.get('snapshot_fiscal_end')} "
            f"form={row.get('snapshot_form')}"
        )

    print("[VALIDATION] completed successfully.")
    

if __name__ == "__main__":
    main()
