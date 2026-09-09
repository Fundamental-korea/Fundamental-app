"""Resumable US fundamental collector v4.

Changes from v3:
- paginates US_Companies so Supabase's default 1,000-row response limit does not
  silently truncate --all runs;
- loads already-stored tickers from US_Fundamental and skips them, allowing a
  stopped run to resume safely;
- keeps per-company Supabase upserts so completed rows survive interruptions;
- preserves v3 SEC/unavailable/downturn handling.
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone

import requests
from supabase import create_client

from collector_us_fundamental import (
    build_fact_index,
    build_result,
    load_company,
)
from downturn_us import calculate_downturn_defense, _close_series, BENCHMARK

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
PAGE_SIZE = 500


def unavailable_result(row, downturn_value=None, downturn_detail=None):
    return {
        "ticker": row["ticker"],
        "cik": str(row["cik"]),
        "company_name": row.get("company_name"),
        "sector": row.get("sector_common"),
        "base_year": None,
        "period_scores": {},
        "total_score": None,
        "grade": None,
        "data_unavailable": True,
        "data_reliability": "none",
        "missing_metric_count": 10,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "downturn_defense": downturn_value,
        "downturn_detail": downturn_detail or {"status": "sec_data_unavailable", "episodes": []},
    }


def normalize_latest_score(result):
    if result.get("total_score") is not None:
        return result
    periods = result.get("period_scores") or {}
    for period in ("1", "3", "5", "10"):
        entry = periods.get(period)
        if not entry:
            continue
        scored = entry.get("scores") or {}
        if scored.get("total_score") is not None:
            result["total_score"] = int(round(scored["total_score"]))
            result["grade"] = scored.get("grade")
            result["missing_metric_count"] = scored.get("missing_metric_count", 0)
            return result
    return result


def fetch_all_eligible(sb):
    columns = "ticker,cik,company_name,sector_common,company_type,scoring_profile"
    rows = []
    start = 0
    while True:
        page = (
            sb.table("US_Companies")
            .select(columns)
            .eq("is_fundamental_eligible", True)
            .order("ticker")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows


def fetch_existing_tickers(sb):
    existing = set()
    start = 0
    while True:
        page = (
            sb.table("US_Fundamental")
            .select("ticker")
            .order("ticker")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        existing.update(row["ticker"] for row in page if row.get("ticker"))
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return existing


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--tickers")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--all", action="store_true", dest="all_rows")
    parser.add_argument("--retry-unavailable", action="store_true")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    explicit = None
    if args.ticker:
        explicit = [args.ticker.upper().strip()]
    elif args.tickers:
        explicit = [x.upper().strip() for x in args.tickers.split(",") if x.strip()]

    columns = "ticker,cik,company_name,sector_common,company_type,scoring_profile"
    if explicit:
        rows = (
            sb.table("US_Companies")
            .select(columns)
            .in_("ticker", explicit)
            .eq("is_fundamental_eligible", True)
            .execute()
            .data
            or []
        )
    elif args.all_rows:
        rows = fetch_all_eligible(sb)
    else:
        rows = (
            sb.table("US_Companies")
            .select(columns)
            .eq("is_fundamental_eligible", True)
            .order("ticker")
            .limit(args.limit)
            .execute()
            .data
            or []
        )

    existing = fetch_existing_tickers(sb) if not explicit else fetch_existing_tickers(sb)
    original_count = len(rows)
    if not args.retry_unavailable:
        rows = [row for row in rows if row["ticker"] not in existing]

    print(
        f"[US] candidates={original_count} existing={len(existing)} "
        f"to_process={len(rows)} retry_unavailable={args.retry_unavailable}"
    )

    market = None
    try:
        market = _close_series(BENCHMARK)
    except Exception as exc:
        print(f"[US] downturn market data unavailable: {exc}")

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    success = failed = unavailable = skipped = 0
    for index, row in enumerate(rows, start=1):
        ticker = row["ticker"]
        try:
            try:
                facts, submissions = load_company(session, ticker, row["cik"])
            except requests.HTTPError as exc:
                if getattr(exc.response, "status_code", None) == 404:
                    downturn_value, downturn_detail = calculate_downturn_defense(
                        ticker, market=market, stock=None
                    )
                    result = unavailable_result(row, downturn_value, downturn_detail)
                    sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
                    print(
                        f"[{index}/{len(rows)}] [US] {ticker}: SEC 404 -> unavailable; "
                        f"downturn={downturn_value}"
                    )
                    unavailable += 1
                    continue
                raise

            try:
                stock = _close_series(ticker)
            except Exception as exc:
                print(f"[{index}/{len(rows)}] [US] {ticker}: downturn price unavailable: {exc}")
                stock = None

            index_data = build_fact_index(facts)
            if not any(index_data.values()):
                downturn_value, downturn_detail = calculate_downturn_defense(
                    ticker, market=market, stock=stock
                )
                result = unavailable_result(row, downturn_value, downturn_detail)
            else:
                result = build_result(
                    ticker,
                    row["cik"],
                    row["company_name"],
                    facts,
                    submissions,
                    universe_row=row,
                    market_prices={"market": market, "stock": stock},
                )
                result = normalize_latest_score(result)
                result.setdefault("downturn_defense", None)
                result.setdefault("downturn_detail", {"status": "unknown", "episodes": []})

            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
            print(
                f"[{index}/{len(rows)}] [US] {ticker}: "
                f"profile={row.get('scoring_profile') or 'standard'} "
                f"score={result['total_score']} grade={result['grade']} "
                f"periods={len(result['period_scores'])} "
                f"missing={result['missing_metric_count']} "
                f"downturn={result['downturn_defense']}"
            )
            if result.get("data_unavailable"):
                unavailable += 1
            else:
                success += 1
            time.sleep(0.15)
        except Exception as exc:
            failed += 1
            print(f"[{index}/{len(rows)}] [US] {ticker}: FAILED: {exc}")

    print(
        f"Completed. success={success}, unavailable={unavailable}, "
        f"failed={failed}, skipped={skipped}, processed={len(rows)}, candidates={original_count}"
    )


if __name__ == "__main__":
    main()
