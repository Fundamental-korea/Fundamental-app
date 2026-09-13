"""Read-only regression test for US utility XBRL extraction v3 + utility scoring v2.4.

No Supabase writes. No raw SEC JSON is persisted.
"""
from __future__ import annotations

import argparse

import requests

from collector_us_fundamental import (
    SEC_FACTS_URL,
    SEC_SUBMISSIONS_URL,
    fetch_json,
)
from collector_us_utility import (
    PERIODS,
    SEC_TICKERS,
    SEC_USER_AGENT,
    period_metrics,
)
from downturn_us import BENCHMARK, _close_series, calculate_downturn_defense
from us_scoring_v2 import calculate_us_utility_score_v2


def ticker_cik(session, ticker: str) -> str:
    data = fetch_json(session, SEC_TICKERS)
    for item in data.values():
        if str(item.get("ticker", "")).upper() == ticker.upper():
            return str(item["cik_str"]).zfill(10)
    raise RuntimeError(f"CIK not found: {ticker}")


def load_facts(session, ticker: str):
    cik = ticker_cik(session, ticker)
    facts = fetch_json(session, SEC_FACTS_URL.format(cik=cik))
    submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik))
    return cik, facts, submissions


def run_ticker(session, ticker: str, market):
    cik, facts, submissions = load_facts(session, ticker)

    # V3 core-year selection: revenue + operating income must both exist.
    from us_utility_extraction import core_years
    years = sorted(y for y in core_years(facts) if 2018 <= y <= 2026)
    if not years:
        print(f"{ticker}: FAIL - no core annual years")
        return False

    latest_year = max(years)
    stock = _close_series(ticker)
    downturn, _detail = calculate_downturn_defense(ticker, market=market, stock=stock)

    print(f"\n=== {ticker} | CIK {cik} | latest={latest_year} ===")
    print(f"core_years={years} downturn={downturn}")

    passed = True
    for period in PERIODS:
        metrics, base_year = period_metrics(ticker, facts, latest_year, period)
        if metrics is None:
            print(f"{period}Y: WARN - unavailable (base_year={base_year})")
            continue

        metrics["downturn_defense"] = downturn
        scored = calculate_us_utility_score_v2(metrics)
        missing = scored["missing_metric_count"]
        coverage = scored["coverage_pct"]

        print(
            f"{period}Y: base={base_year} score={scored['total_score']} "
            f"grade={scored['grade']} coverage={coverage}% missing={missing}"
        )
        print("  metrics:", ", ".join(
            f"{k}={metrics.get(k)}" for k in (
                "revenue_growth", "eps_growth", "opm", "roa", "debt_capital",
                "ocf_debt", "fcf_debt", "interest_coverage",
                "dividend_coverage", "dividend_payout", "downturn_defense",
            )
        ))

        if period == 1:
            if coverage < 90:
                print("  RESULT: WARN - latest-period coverage below 90%")
                passed = False

    return passed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tickers",
        default="TVE,XEL,PPL,SWX,TAC,AEP,CEG,DUK,ED,EXC,NEE,VST",
        help="Comma-separated tickers",
    )
    args = parser.parse_args()

    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    print("US utility XBRL v3 regression test")
    print("Read-only: no Supabase writes; no raw SEC JSON saved.")

    market = _close_series(BENCHMARK)
    ok = 0
    failed = 0

    for ticker in tickers:
        try:
            if run_ticker(session, ticker, market):
                ok += 1
            else:
                failed += 1
        except Exception as exc:
            failed += 1
            print(f"\n{ticker}: FAIL - {type(exc).__name__}: {exc}")

    print(f"\nSUMMARY: PASS={ok} FAIL={failed} TOTAL={len(tickers)}")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
