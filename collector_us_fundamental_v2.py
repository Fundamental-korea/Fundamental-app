"""US fundamental collector with US downturn-defense scoring.

Keeps the existing SEC fundamental pipeline and adds market-stress analysis:
S&P 500 bear episodes + stock maximum drawdown + recovery speed.
Price history is used only in memory and is never stored in Supabase.

Usage:
  python collector_us_fundamental_v2.py --ticker AAPL
  python collector_us_fundamental_v2.py --tickers AAPL,MSFT,NVDA
  python collector_us_fundamental_v2.py --limit 5
  python collector_us_fundamental_v2.py --all
"""

import argparse
from datetime import datetime, timezone

from supabase import create_client

from collector_us_fundamental import (
    SUPABASE_URL,
    SUPABASE_KEY,
    SEC_USER_AGENT,
    load_company,
    get_universe,
    build_result,
)
from scoring import calculate_fundamental_score
from downturn_us import calculate_downturn_defense, _close_series, BENCHMARK


def add_downturn_and_rescore(base_result, ticker, market_close):
    """Add structural downturn defense and recalculate every period score."""
    defense_value, defense_detail = calculate_downturn_defense(
        ticker,
        market=market_close,
    )

    period_scores = base_result.get("period_scores") or {}
    latest_score = None
    latest_grade = None
    latest_missing = 10

    for period, payload in period_scores.items():
        metrics = dict(payload.get("metrics") or {})
        metrics["downturn_defense"] = defense_value

        scored = calculate_fundamental_score(
            metrics,
            leverage_exempt=False,
            is_financial=False,
        )
        payload["metrics"] = metrics
        payload["scores"] = scored

        if period == "1":
            latest_score = scored.get("total_score")
            latest_grade = scored.get("grade")
            latest_missing = sum(
                1 for entry in (scored.get("scores") or {}).values()
                if entry.get("value") is None
            )

    # Keep diagnostic detail inside period_scores JSONB; no schema change needed.
    if "1" in period_scores:
        period_scores["1"]["downturn_defense_detail"] = defense_detail

    base_result["period_scores"] = period_scores
    base_result["total_score"] = int(round(latest_score)) if latest_score is not None else None
    base_result["grade"] = latest_grade
    base_result["missing_metric_count"] = latest_missing
    base_result["updated_at"] = datetime.now(timezone.utc).isoformat()
    return base_result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="single ticker")
    parser.add_argument("--tickers", help="comma-separated tickers")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--all", action="store_true", dest="all_rows")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    tickers = None
    if args.ticker:
        tickers = [args.ticker.upper().strip()]
    elif args.tickers:
        tickers = [x.upper().strip() for x in args.tickers.split(",") if x.strip()]

    rows = get_universe(sb, tickers=tickers, limit=args.limit, all_rows=args.all_rows)
    market_close = _close_series(BENCHMARK)
    if market_close is None:
        raise RuntimeError("S&P 500 (^GSPC) price history is unavailable")

    import requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
    })

    success = 0
    failed = 0

    for row in rows:
        ticker = row["ticker"]
        try:
            facts, submissions = load_company(session, ticker, row["cik"])
            result = build_result(
                ticker,
                row["cik"],
                row["company_name"],
                facts,
                submissions,
            )
            result = add_downturn_and_rescore(result, ticker, market_close)
            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()

            dd = None
            if result.get("period_scores"):
                dd = result["period_scores"].get("1", {}).get("metrics", {}).get("downturn_defense")
            print(
                f"[US] {ticker}: score={result['total_score']} grade={result['grade']} "
                f"downturn={dd} periods={len(result['period_scores'])} "
                f"missing={result['missing_metric_count']}"
            )
            success += 1
        except Exception as exc:
            failed += 1
            print(f"[US] {ticker}: FAILED - {type(exc).__name__}: {exc}")

    print(f"Completed. success={success}, failed={failed}, total={len(rows)}")


if __name__ == "__main__":
    main()
