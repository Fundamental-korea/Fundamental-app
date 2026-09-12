"""Dry-run comparison: current US utility scoring vs Utility v2.

No Supabase writes. Fetches SEC Company Facts, calculates the existing utility
score and the proposed v2 score for the requested tickers, then prints a
side-by-side metric/contribution comparison.
"""
from __future__ import annotations

import argparse

import requests

from collector_us_fundamental import SEC_USER_AGENT, fetch_json
from collector_us_utility import (
    SEC_TICKERS,
    load_facts,
    ticker_cik,
    build_result,
)
from downturn_us import BENCHMARK, _close_series
from us_scoring import calculate_us_score
from us_scoring_v2 import calculate_us_utility_score_v2
from us_utility_extraction import pick_dividend, pick_flow

NET_INCOME_TAGS = [
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "NetIncomeLossAttributableToParent",
    "ProfitLossAttributableToOwnersOfParent",
    "NetIncomeLoss",
    "ProfitLoss",
]


def value(facts, picker, year, tags):
    row = picker(facts, tags, year)
    return row["val"] if row else None


def payout_ratio(facts, year):
    """Dividend payout ratio = cash dividends / positive net income * 100."""
    dividend = value(facts, pick_flow, year, [
        "DividendsCommonStockCash",
        "PaymentsOfDividendsCommonStockCash",
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfOrdinaryDividends",
        "PaymentsOfDividends",
    ])
    net_income = value(facts, pick_flow, year, NET_INCOME_TAGS)
    if dividend is None or net_income is None or net_income <= 0:
        return None
    return abs(dividend) / net_income * 100.0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default="AEP,AWK,CEG,D,DUK,ED,EXC,NEE,PEG,SO,VST")
    args = parser.parse_args()
    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    market = _close_series(BENCHMARK)

    print("UTILITY V2 DRY RUN — NO DB WRITE")
    print("Weights: Revenue 7 | EPS 7 | OPM 10 | ROA 8 | Debt/Capital 15 | OCF/Debt 10 | FCF/Debt 10 | Interest 10 | Dividend Coverage 6 | Payout 5 | Downturn 12")
    print()

    for ticker in tickers:
        try:
            cik = ticker_cik(session, ticker)
            facts, submissions = load_facts(session, ticker, cik)
            stock = _close_series(ticker)
            current = build_result(
                ticker, cik, submissions.get("name") or ticker,
                facts, submissions, market, stock,
            )
            if not current:
                print(f"{ticker}: no annual facts")
                continue

            latest_year = current["base_year"]
            latest = current["period_scores"].get("1")
            if not latest:
                print(f"{ticker}: no 1Y score")
                continue

            metrics = dict(latest["metrics"])
            metrics["dividend_payout"] = payout_ratio(facts, latest_year)
            v2 = calculate_us_utility_score_v2(metrics)
            old = calculate_us_score(latest["metrics"], profile="utility")

            print(f"[{ticker}] {latest_year}")
            print(f"  CURRENT : {old['total_score']:.1f} {old['grade']} | coverage={old['coverage_pct']:.1f}% | missing={old['missing_metric_count']}")
            print(f"  V2      : {v2['total_score']:.1f} {v2['grade']} | coverage={v2['coverage_pct']:.1f}% | missing={v2['missing_metric_count']}")
            print(f"  CHANGE  : {v2['total_score'] - old['total_score']:+.1f}")
            for metric in v2["metric_scores"]:
                old_e = old["metric_scores"].get(metric)
                new_e = v2["metric_scores"][metric]
                old_s = old_e["score"] if old_e else None
                new_s = new_e["score"]
                val = new_e["value"]
                print(f"    {metric:20s} value={val!r:>12} old={old_s!s:>4} new={new_s:>4} v2_weight={new_e['weight']:>2} contrib={new_e['weighted_score']:>5.2f}")
            print()
        except Exception as exc:
            print(f"{ticker}: FAILED: {exc}")


if __name__ == "__main__":
    main()
