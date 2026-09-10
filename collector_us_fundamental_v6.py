"""US fundamental collector v6.

Uses the existing SEC collection pipeline but applies the refined classification
layer before scoring. This is a validation runner; it does not store raw SEC
JSON.
"""
from __future__ import annotations

import argparse

import requests
from supabase import create_client

import collector_us_fundamental as base
from us_classification_v2 import classify_company


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--tickers")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--all", action="store_true", dest="all_rows")
    args = parser.parse_args()

    if not base.SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(base.SUPABASE_URL, base.SUPABASE_KEY)
    tickers = (
        [args.ticker.upper().strip()]
        if args.ticker
        else ([x.upper().strip() for x in args.tickers.split(",") if x.strip()] if args.tickers else None)
    )
    rows = base.get_universe(sb, tickers=tickers, limit=args.limit, all_rows=args.all_rows)

    session = requests.Session()
    session.headers.update({"User-Agent": base.SEC_USER_AGENT})
    market = None
    stock_cache = {}
    try:
        from downturn_us import _close_series, BENCHMARK
        market = _close_series(BENCHMARK)
    except Exception as exc:
        print(f"[US] downturn market data unavailable: {exc}")

    for i, row in enumerate(rows, 1):
        ticker = row["ticker"]
        cik = row["cik"]
        try:
            facts, submissions = base.load_company(session, ticker, cik)
            classification = classify_company(
                ticker=ticker,
                company_name=row.get("company_name") or submissions.get("name") or ticker,
                sic=submissions.get("sic"),
                sic_desc=submissions.get("sicDescription"),
            )
            row.update(classification)

            # Persist the refined classification so subsequent normal collector
            # runs use the same profile without reclassifying every request.
            sb.table("US_Companies").update({
                "sector_source": classification.get("sector_source"),
                "sector_raw": classification.get("sector_raw"),
                "sector_common": classification.get("sector_common"),
                "sector_common_ko": classification.get("sector_common_ko"),
                "company_type": classification.get("company_type"),
                "scoring_profile": classification.get("scoring_profile"),
            }).eq("ticker", ticker).execute()

            if ticker not in stock_cache:
                try:
                    stock_cache[ticker] = _close_series(ticker)
                except Exception:
                    stock_cache[ticker] = None

            result = base.build_result(
                ticker,
                cik,
                row.get("company_name") or submissions.get("name") or ticker,
                facts,
                submissions,
                universe_row=row,
                market_prices={"market": market, "stock": stock_cache.get(ticker)},
            )
            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
            print(f"[{i}/{len(rows)}] {ticker}: {result['score'] if 'score' in result else result['total_score']} {result['grade']} profile={classification.get('scoring_profile')} sector={classification.get('sector_common')} periods={len(result['period_scores'])}")
        except Exception as exc:
            print(f"[{i}/{len(rows)}] {ticker}: FAILED: {exc}")

    print("Completed.")


if __name__ == "__main__":
    main()
