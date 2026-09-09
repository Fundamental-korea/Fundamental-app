"""US fundamental collector v5.

Operational runner around the current collector_us_fundamental logic.
- paginates the full eligible universe;
- supports safe resume by default;
- supports --refresh-existing to rebuild existing rows with current logic;
- upserts each company immediately.
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone

import requests
from supabase import create_client

from collector_us_fundamental import build_fact_index, build_result, load_company
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
    for period in ("1", "3", "5", "10"):
        entry = (result.get("period_scores") or {}).get(period) or {}
        scored = entry.get("scores") or {}
        if scored.get("total_score") is not None:
            result["total_score"] = int(round(scored["total_score"]))
            result["grade"] = scored.get("grade")
            result["missing_metric_count"] = scored.get("missing_metric_count", 0)
            return result
    return result


def fetch_pages(sb, table, columns, eligible=True):
    rows, start = [], 0
    while True:
        q = sb.table(table).select(columns).order("ticker").range(start, start + PAGE_SIZE - 1)
        if eligible:
            q = q.eq("is_fundamental_eligible", True)
        page = q.execute().data or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def fetch_existing(sb):
    rows = fetch_pages(sb, "US_Fundamental", "ticker", eligible=False)
    return {r["ticker"] for r in rows if r.get("ticker")}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ticker")
    p.add_argument("--tickers")
    p.add_argument("--limit", type=int)
    p.add_argument("--all", action="store_true", dest="all_rows")
    p.add_argument("--refresh-existing", action="store_true")
    args = p.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    columns = "ticker,cik,company_name,sector_common,company_type,scoring_profile"

    if args.ticker or args.tickers:
        wanted = [args.ticker.upper().strip()] if args.ticker else [x.upper().strip() for x in args.tickers.split(",") if x.strip()]
        rows = (sb.table("US_Companies").select(columns).in_("ticker", wanted)
                .eq("is_fundamental_eligible", True).execute().data or [])
    else:
        rows = fetch_pages(sb, "US_Companies", columns, eligible=True)
        if args.limit:
            rows = rows[: args.limit]
        elif not args.all_rows:
            rows = rows[:50]

    existing = fetch_existing(sb)
    candidates = len(rows)
    if not args.refresh_existing:
        rows = [r for r in rows if r["ticker"] not in existing]

    print(f"[US v5] candidates={candidates} existing={len(existing)} to_process={len(rows)} refresh_existing={args.refresh_existing}")

    market = None
    try:
        market = _close_series(BENCHMARK)
    except Exception as exc:
        print(f"[US v5] market downturn data unavailable: {exc}")

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    success = unavailable = failed = 0

    for i, row in enumerate(rows, 1):
        ticker = row["ticker"]
        try:
            try:
                facts, submissions = load_company(session, ticker, row["cik"])
            except requests.HTTPError as exc:
                if getattr(exc.response, "status_code", None) == 404:
                    dv, dd = calculate_downturn_defense(ticker, market=market, stock=None)
                    result = unavailable_result(row, dv, dd)
                    sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
                    unavailable += 1
                    print(f"[{i}/{len(rows)}] {ticker}: SEC 404 -> unavailable")
                    continue
                raise

            try:
                stock = _close_series(ticker)
            except Exception as exc:
                print(f"[{i}/{len(rows)}] {ticker}: price unavailable: {exc}")
                stock = None

            index_data = build_fact_index(facts)
            if not any(index_data.values()):
                dv, dd = calculate_downturn_defense(ticker, market=market, stock=stock)
                result = unavailable_result(row, dv, dd)
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
            if result.get("data_unavailable"):
                unavailable += 1
            else:
                success += 1
            print(f"[{i}/{len(rows)}] {ticker}: profile={row.get('scoring_profile') or 'standard'} score={result.get('total_score')} grade={result.get('grade')} periods={len(result.get('period_scores') or {})} missing={result.get('missing_metric_count')} downturn={result.get('downturn_defense')}")
            time.sleep(0.15)
        except Exception as exc:
            failed += 1
            print(f"[{i}/{len(rows)}] {ticker}: FAILED: {exc}")

    print(f"Completed. success={success}, unavailable={unavailable}, failed={failed}, processed={len(rows)}, candidates={candidates}")


if __name__ == "__main__":
    main()
