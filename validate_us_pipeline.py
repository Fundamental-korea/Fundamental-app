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
    missing_snapshot_count = missing_snapshot.count or 0
    print(f"usable rows missing snapshot_fiscal_end: {missing_snapshot_count}")

    score_null = (
        sb.table("US_Fundamental")
        .select("ticker", count="exact", head=True)
        .eq("data_unavailable", False)
        .is_("total_score", "null")
        .execute()
    )
    score_null_count = score_null.count or 0
    print(f"usable rows missing total_score: {score_null_count}")

    valuation_missing = 0
    valuation_price_missing = 0
    valuation_shares_missing = 0
    valuation_period_shares_missing = 0
    market_cap_math_errors = 0
    per_math_errors = 0
    pbr_math_errors = 0

    score_mismatch = 0
    missing_mismatch = 0
    score_out_of_range = 0
    coverage_null = 0
    coverage_out_of_range = 0
    canonical_1y_missing = 0
    scanned = 0

    offset = 0
    while True:
        rows = (
            sb.table("US_Fundamental")
            .select(
                "ticker,total_score,missing_metric_count,period_scores,"
                "snapshot"
            )
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

            periods = row.get("period_scores") or {}
            avg = (
                ((periods.get("1y") or {})
                 if isinstance(periods, dict) else {})
                .get("avg") or {}
            )

            if "1y" not in periods:
                canonical_1y_missing += 1

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

            if coverage is None and "1y" in periods:
                coverage_null += 1
            elif coverage is not None:
                try:
                    if not 0 <= float(coverage) <= 100:
                        coverage_out_of_range += 1
                except (TypeError, ValueError):
                    coverage_out_of_range += 1

            snapshot = row.get("snapshot") or {}
            valuation = snapshot.get("valuation")
            if not isinstance(valuation, dict):
                valuation_missing += 1
                continue

            price = valuation.get("price")
            current_shares = valuation.get("current_shares_outstanding")
            period_shares = valuation.get("period_end_shares_outstanding")
            eps = valuation.get("eps")
            bps = valuation.get("bps")
            market_cap = valuation.get("market_cap")
            per = valuation.get("per")
            pbr = valuation.get("pbr")

            if price is None:
                valuation_price_missing += 1
            if current_shares is None or current_shares <= 0:
                valuation_shares_missing += 1
            if period_shares is None or period_shares <= 0:
                valuation_period_shares_missing += 1

            # Check derived math only when the required inputs are valid.
            if (
                price is not None and current_shares is not None
                and price > 0 and current_shares > 0
            ):
                expected_mc = float(price) * float(current_shares)
                if market_cap is None or abs(float(market_cap) - expected_mc) > max(1.0, abs(expected_mc) * 1e-6):
                    market_cap_math_errors += 1

            if (
                price is not None and eps is not None
                and price > 0 and eps > 0
            ):
                expected_per = float(price) / float(eps)
                if per is None or abs(float(per) - expected_per) > max(0.01, abs(expected_per) * 1e-4):
                    per_math_errors += 1

            if (
                price is not None and bps is not None
                and price > 0 and bps > 0
            ):
                expected_pbr = float(price) / float(bps)
                if pbr is None or abs(float(pbr) - expected_pbr) > max(0.01, abs(expected_pbr) * 1e-4):
                    pbr_math_errors += 1

        print(
            f"[INTEGRITY] scanned={scanned} "
            f"score_mismatch={score_mismatch} "
            f"missing_mismatch={missing_mismatch} "
            f"canonical_1y_missing={canonical_1y_missing}",
            flush=True,
        )

        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    print(f"score_out_of_range: {score_out_of_range}")
    print(f"coverage_null: {coverage_null}")
    print(f"coverage_out_of_range: {coverage_out_of_range}")
    print(f"valuation_missing: {valuation_missing}")
    print(f"valuation_price_missing: {valuation_price_missing}")
    print(f"valuation_shares_missing: {valuation_shares_missing}")
    print(f"valuation_period_shares_missing: {valuation_period_shares_missing}")
    print(f"market_cap_math_errors: {market_cap_math_errors}")
    print(f"per_math_errors: {per_math_errors}")
    print(f"pbr_math_errors: {pbr_math_errors}")

    failures = {
        "missing_cik": missing_cik_count,
        "missing_snapshot": missing_snapshot_count,
        "score_null": score_null_count,
        "score_mismatch": score_mismatch,
        "missing_mismatch": missing_mismatch,
        "canonical_1y_missing": canonical_1y_missing,
        "score_out_of_range": score_out_of_range,
        "coverage_null": coverage_null,
        "coverage_out_of_range": coverage_out_of_range,
        "market_cap_math_errors": market_cap_math_errors,
        "per_math_errors": per_math_errors,
        "pbr_math_errors": pbr_math_errors,
    }
    failed = {key: value for key, value in failures.items() if value}

    # Missing market data is reported but is not treated as a mathematical
    # integrity error; SEC/price providers can legitimately be unavailable for
    # a small number of instruments.
    if failed:
        raise RuntimeError(f"US pipeline integrity validation failed: {failed}")

    sample = ["AAPL", "MSFT", "NVDA", "JPM", "O", "RTX", "GSBD", "ARCC"]
    sample_rows = (
        sb.table("US_Fundamental")
        .select(
            "ticker,total_score,grade,missing_metric_count,"
            "data_reliability,snapshot_fiscal_end,snapshot_form,period_scores,snapshot"
        )
        .in_("ticker", sample)
        .execute()
        .data
        or []
    )
    by_ticker = {row["ticker"]: row for row in sample_rows}

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
        valuation = ((row.get("snapshot") or {}).get("valuation") or {})

        print(
            f"sample {ticker}: "
            f"score={row.get('total_score')} "
            f"grade={row.get('grade')} "
            f"missing={row.get('missing_metric_count')} "
            f"reliability={row.get('data_reliability')} "
            f"roic={(metrics.get('roic') or {}).get('value')} "
            f"interest_coverage={(metrics.get('interest_coverage') or {}).get('value')} "
            f"price={valuation.get('price')} "
            f"EPS={valuation.get('eps')} "
            f"BPS={valuation.get('bps')} "
            f"PER={valuation.get('per')} "
            f"PBR={valuation.get('pbr')} "
            f"shares={valuation.get('current_shares_outstanding')} "
            f"period_shares={valuation.get('period_end_shares_outstanding')} "
            f"snapshot={row.get('snapshot_fiscal_end')} "
            f"form={row.get('snapshot_form')}"
        )

    print("[VALIDATION] completed successfully.")


if __name__ == "__main__":
    main()
