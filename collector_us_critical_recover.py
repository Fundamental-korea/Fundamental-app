"""Targeted US critical-metric recovery.

Uses the production collector's Company Facts first pass plus the strict
annual-filing recovery for the three stubborn 1Y metrics:
ROIC, Interest Coverage, and EPS Growth.

Utility companies are intentionally excluded because they use the specialized
utility scorer/collector with a different metric model.
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone

import requests
from supabase import create_client

from collector_us_fundamental import (
    SUPABASE_URL,
    SUPABASE_KEY,
    SEC_USER_AGENT,
    load_company,
    build_result,
    SECXBRLSearchV2_3_8,
)

PAGE_SIZE = 1000
ELIGIBLE_PROFILES = {"standard", "defense", "financial", "reit", "bdc"}
CRITICAL_KEYS = ("roic", "interest_coverage", "eps_growth")


def fetch_pages(sb, table, columns, eligible=False):
    rows = []
    offset = 0
    while True:
        q = sb.table(table).select(columns).order("ticker").range(offset, offset + PAGE_SIZE - 1)
        if eligible:
            q = q.eq("is_fundamental_eligible", True)
        page = q.execute().data or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def metric_missing(current_row, metric):
    if not current_row or current_row.get("data_unavailable"):
        return True
    period = (current_row.get("period_scores") or {}).get("1y") or {}
    avg = period.get("avg") or {}
    scores = avg.get("metric_scores") or {}
    entry = scores.get(metric)
    return not entry or entry.get("value") is None


def row_needs_recovery(row, current):
    profile = row.get("scoring_profile") or "standard"
    if profile not in ELIGIBLE_PROFILES:
        return False
    if metric_missing(current, "eps_growth"):
        return True
    if metric_missing(current, "interest_coverage"):
        return True
    if profile in {"standard", "defense"} and metric_missing(current, "roic"):
        return True
    return False


def summarize_targets(rows, existing):
    targets = []
    for row in rows:
        current = existing.get(row["ticker"])
        if row_needs_recovery(row, current):
            targets.append(row)
    return targets


def upsert_result(sb, result):
    sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--all", action="store_true", dest="all_rows")
    parser.add_argument("--ticker")
    parser.add_argument("--tickers")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    company_columns = "ticker,cik,company_name,sector_common,company_type,scoring_profile"

    if args.ticker or args.tickers:
        wanted = (
            [args.ticker.upper().strip()]
            if args.ticker
            else [x.upper().strip() for x in args.tickers.split(",") if x.strip()]
        )
        rows = (
            sb.table("US_Companies")
            .select(company_columns)
            .in_("ticker", wanted)
            .eq("is_fundamental_eligible", True)
            .execute()
            .data
            or []
        )
    else:
        rows = fetch_pages(sb, "US_Companies", company_columns, eligible=True)

    existing_rows = fetch_pages(
        sb,
        "US_Fundamental",
        "ticker,data_unavailable,period_scores",
        eligible=False,
    )
    existing = {r["ticker"]: r for r in existing_rows if r.get("ticker")}

    targets = summarize_targets(rows, existing)
    if args.ticker or args.tickers:
        targets = rows
    elif not args.all_rows:
        targets = targets[: max(args.limit, 0)]

    print(
        f"[CRITICAL RECOVERY] eligible={len(rows)} "
        f"targets={len(targets)} limit={args.limit} all={args.all_rows}"
    )
    print(
        "[CRITICAL RECOVERY] target metrics: "
        "ROIC(standard/defense), Interest Coverage(non-utility), EPS Growth(non-utility)"
    )

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    resolver = SECXBRLSearchV2_3_8(user_agent=SEC_USER_AGENT, session=session)
    filing_cache = {}
    market = None
    stock_cache = {}

    try:
        from downturn_us import _close_series, BENCHMARK
        market = _close_series(BENCHMARK)
    except Exception as exc:
        print(f"[CRITICAL RECOVERY] market data unavailable: {exc}")

    success = 0
    failed = 0
    skipped = 0

    for i, row in enumerate(targets, 1):
        ticker = row["ticker"]
        profile = row.get("scoring_profile") or "standard"
        try:
            if profile not in ELIGIBLE_PROFILES:
                skipped += 1
                print(f"[{i}/{len(targets)}] {ticker}: skipped profile={profile}")
                continue

            facts, submissions = load_company(session, ticker, row["cik"])

            if ticker not in stock_cache:
                try:
                    from downturn_us import _close_series
                    stock_cache[ticker] = _close_series(ticker)
                except Exception:
                    stock_cache[ticker] = None

            result = build_result(
                ticker,
                row["cik"],
                row.get("company_name") or submissions.get("name") or ticker,
                facts,
                submissions,
                universe_row=row,
                market_prices={"market": market, "stock": stock_cache.get(ticker)},
                filing_resolver=resolver,
                filing_recovery_cache=filing_cache,
            )
            upsert_result(sb, result)
            success += 1

            recovery = result.get("filing_recovery") or {}
            source_metrics = ",".join(sorted((recovery.get("sources") or {}).keys())) or "none"
            print(
                f"[{i}/{len(targets)}] {ticker}: profile={profile} "
                f"score={result.get('total_score')} missing={result.get('missing_metric_count')} "
                f"filing_recovery={source_metrics}"
            )
            time.sleep(0.15)
        except Exception as exc:
            failed += 1
            print(f"[{i}/{len(targets)}] {ticker}: FAILED: {exc}")

    print(
        f"[CRITICAL RECOVERY] completed success={success} "
        f"failed={failed} skipped={skipped} processed={len(targets)} "
        f"finished_at={datetime.now(timezone.utc).isoformat()}"
    )


if __name__ == "__main__":
    main()
