"""Robust US fundamental collector v3.

Fixes two issues found in the 50-company validation:
1) companies with no SEC Company Facts no longer crash on missing top-level downturn_defense;
2) if the 1-year period is unavailable, the shortest available period becomes the displayed score.
SEC 404s are recorded as unavailable rather than counted as hard failures.
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
    classify_company,
    load_company,
)
from downturn_us import calculate_downturn_defense, _close_series, BENCHMARK

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")


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
    """Use 1y score when present, otherwise the shortest available period."""
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--tickers")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--all", action="store_true", dest="all_rows")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    tickers = None
    if args.ticker:
        tickers = [args.ticker.upper().strip()]
    elif args.tickers:
        tickers = [x.upper().strip() for x in args.tickers.split(",") if x.strip()]

    columns = "ticker,cik,company_name,sector_common,company_type,scoring_profile"
    if tickers:
        rows = sb.table("US_Companies").select(columns).in_("ticker", tickers).eq("is_fundamental_eligible", True).execute().data
    else:
        q = sb.table("US_Companies").select(columns).eq("is_fundamental_eligible", True).order("ticker")
        rows = q.execute().data if args.all_rows else q.limit(args.limit).execute().data

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    market = None
    try:
        market = _close_series(BENCHMARK)
    except Exception as exc:
        print(f"[US] downturn market data unavailable: {exc}")

    success = failed = unavailable = 0
    for row in rows:
        ticker = row["ticker"]
        try:
            try:
                facts, submissions = load_company(session, ticker, row["cik"])
            except requests.HTTPError as exc:
                if getattr(exc.response, "status_code", None) == 404:
                    downturn_value, downturn_detail = calculate_downturn_defense(ticker, market=market, stock=None)
                    result = unavailable_result(row, downturn_value, downturn_detail)
                    sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
                    print(f"[US] {ticker}: SEC data unavailable (404), stored as unavailable; downturn={downturn_value}")
                    unavailable += 1
                    continue
                raise

            try:
                stock = _close_series(ticker)
            except Exception as exc:
                print(f"[US] {ticker}: downturn price data unavailable: {exc}")
                stock = None

            # Explicitly handle an SEC response with no usable annual facts.
            index = build_fact_index(facts)
            if not any(index.values()):
                downturn_value, downturn_detail = calculate_downturn_defense(ticker, market=market, stock=stock)
                result = unavailable_result(row, downturn_value, downturn_detail)
            else:
                result = build_result(
                    ticker, row["cik"], row["company_name"], facts, submissions,
                    universe_row=row,
                    market_prices={"market": market, "stock": stock},
                )
                result = normalize_latest_score(result)
                result.setdefault("downturn_defense", None)
                result.setdefault("downturn_detail", {"status": "unknown", "episodes": []})

            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
            print(
                f"[US] {ticker}: profile={row.get('scoring_profile') or 'standard'} "
                f"score={result['total_score']} grade={result['grade']} "
                f"periods={len(result['period_scores'])} missing={result['missing_metric_count']} "
                f"downturn={result['downturn_defense']}"
            )
            success += 1
            time.sleep(0.15)
        except Exception as exc:
            failed += 1
            print(f"[US] {ticker}: FAILED: {exc}")

    print(f"Completed. success={success}, unavailable={unavailable}, failed={failed}, total={len(rows)}")


if __name__ == "__main__":
    main()
