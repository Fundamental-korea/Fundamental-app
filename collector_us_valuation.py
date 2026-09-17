"""US fundamental collection entrypoint with market valuation snapshot.

This wraps the existing US fundamental collector without changing its scoring
logic. It only enriches the persisted snapshot with current market facts and
SEC-safe valuation metrics.
"""

from __future__ import annotations

import argparse
import os

import yfinance as yf
from supabase import create_client

from collector_us_fundamental import (
    SEC_USER_AGENT,
    SUPABASE_KEY,
    SUPABASE_URL,
    build_result,
    get_universe,
    load_company,
)
from downturn_us import BENCHMARK, _close_series
from us_valuation import build_valuation_snapshot, normalize_market_quote


def load_market_quote(ticker):
    """Load a scalar market quote plus one-year history for 52-week extremes."""
    symbol = yf.Ticker(ticker)
    try:
        info = dict(symbol.fast_info)
    except Exception:
        info = {}
    try:
        history = symbol.history(period="1y", auto_adjust=False, actions=False)
    except Exception:
        history = None
    return normalize_market_quote(info, history)


def collect_one(sb, session, row, market=None):
    ticker, cik = row["ticker"], row["cik"]
    facts, submissions = load_company(session, ticker, cik)
    stock = None
    try:
        stock = _close_series(ticker)
    except Exception:
        pass

    result = build_result(
        ticker,
        cik,
        row.get("company_name") or submissions.get("name") or ticker,
        facts,
        submissions,
        universe_row=row,
        market_prices={"market": market, "stock": stock},
    )

    snapshot = result.get("snapshot")
    if snapshot and snapshot.get("fiscal_end"):
        quote = load_market_quote(ticker)
        valuation = build_valuation_snapshot(facts, snapshot["fiscal_end"], quote)
        snapshot["market"] = quote
        snapshot["valuation"] = valuation
        result["snapshot"] = snapshot

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--tickers")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    tickers = [args.ticker.upper().strip()] if args.ticker else ([x.upper().strip() for x in args.tickers.split(",") if x.strip()] if args.tickers else None)
    rows = get_universe(sb, tickers=tickers, limit=args.limit, all_rows=bool(tickers))

    import requests
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    market = None
    try:
        market = _close_series(BENCHMARK)
    except Exception as exc:
        print(f"[US] downturn market data unavailable: {exc}")

    for i, row in enumerate(rows, 1):
        ticker = row["ticker"]
        try:
            result = collect_one(sb, session, row, market=market)
            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
            valuation = (result.get("snapshot") or {}).get("valuation") or {}
            print(
                f"[{i}/{len(rows)}] {ticker}: score={result['total_score']} "
                f"grade={result['grade']} snapshot={result.get('snapshot_fiscal_end')} "
                f"EPS={valuation.get('eps')} BPS={valuation.get('bps')} "
                f"PER={valuation.get('per')} PBR={valuation.get('pbr')}"
            )
        except Exception as exc:
            print(f"[{i}/{len(rows)}] {ticker}: FAILED: {exc}")

    print("Completed.")


if __name__ == "__main__":
    main()
