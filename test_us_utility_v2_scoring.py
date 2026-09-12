"""Dry-run comparison: current US utility scoring vs Utility v2.2.

No Supabase writes. Fetches SEC Company Facts, calculates the existing utility
score and the proposed v2.2 score for the requested tickers, then prints a
side-by-side metric/contribution comparison.
"""
from __future__ import annotations

import argparse

import requests

from collector_us_fundamental import SEC_USER_AGENT
from collector_us_utility import load_facts, ticker_cik, build_result
from downturn_us import BENCHMARK, _close_series
from us_scoring import calculate_us_score
from us_scoring_v2 import calculate_us_utility_score_v2
from us_utility_extraction import pick_flow

NET_INCOME_TAGS = [
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "NetIncomeLossAttributableToParent",
    "ProfitLossAttributableToOwnersOfParent",
    "NetIncomeLoss",
    "ProfitLoss",
]

DIVIDEND_TAGS = [
    "DividendsCommonStockCash",
    "PaymentsOfDividendsCommonStockCash",
    "PaymentsOfDividendsCommonStock",
    "PaymentsOfOrdinaryDividends",
    "PaymentsOfDividends",
]

OCF_TAGS = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    "CashFlowsFromUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperationsAndDiscontinuedOperations",
]

CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndOtherProductiveAssetsNet",
    "PaymentsForProceedsFromProductiveAssets",
]


def value(facts, year, tags):
    row = pick_flow(facts, tags, year)
    return row["val"] if row else None


def dividend_amount(facts, year):
    value_ = value(facts, year, DIVIDEND_TAGS)
    return abs(value_) if value_ is not None else None


def dividend_safety_metrics(facts, year):
    """Return OCF/dividend, FCF/dividend, and payout ratio for the latest year."""
    dividend = dividend_amount(facts, year)
    ocf = value(facts, year, OCF_TAGS)
    capex = value(facts, year, CAPEX_TAGS)
    net_income = value(facts, year, NET_INCOME_TAGS)

    ocf_dividend = None
    if dividend is not None and dividend > 0 and ocf is not None:
        ocf_dividend = abs(ocf) / dividend

    fcf_dividend = None
    if dividend is not None and dividend > 0 and ocf is not None and capex is not None:
        fcf = ocf - abs(capex)
        fcf_dividend = fcf / dividend

    payout = None
    if dividend is not None and dividend > 0 and net_income is not None and net_income > 0:
        payout = dividend / net_income * 100.0

    return {
        "dividend_coverage": ocf_dividend,
        "fcf_dividend": fcf_dividend,
        "dividend_payout": payout,
        "dividend": dividend,
        "ocf": ocf,
        "capex": capex,
        "net_income": net_income,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default="AEP,AWK,CEG,D,DUK,ED,EXC,NEE,PEG,SO,VST")
    args = parser.parse_args()
    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    market = _close_series(BENCHMARK)

    print("UTILITY V2.2 DRY RUN — NO DB WRITE")
    print("Weights: Revenue 7 | EPS 7 | OPM 10 | ROA 8 | Debt/Capital 15 | OCF/Debt 10 | FCF/Debt 10 | Interest 10 | OCF/Dividend 4 | FCF/Dividend 4 | Payout 2 | Downturn 13")
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
            dividend_metrics = dividend_safety_metrics(facts, latest_year)
            metrics.update({
                "dividend_coverage": dividend_metrics["dividend_coverage"],
                "fcf_dividend": dividend_metrics["fcf_dividend"],
                "dividend_payout": dividend_metrics["dividend_payout"],
            })

            v2 = calculate_us_utility_score_v2(metrics)
            old = calculate_us_score(latest["metrics"], profile="utility")

            print(f"[{ticker}] {latest_year}")
            print(f"  CURRENT : {old['total_score']:.1f} {old['grade']} | coverage={old['coverage_pct']:.1f}% | missing={old['missing_metric_count']}")
            print(f"  V2.2    : {v2['total_score']:.1f} {v2['grade']} | coverage={v2['coverage_pct']:.1f}% | missing={v2['missing_metric_count']}")
            print(f"  CHANGE  : {v2['total_score'] - old['total_score']:+.1f}")
            print(f"  DIV DATA: dividend={dividend_metrics['dividend']!r} ocf={dividend_metrics['ocf']!r} capex={dividend_metrics['capex']!r} net_income={dividend_metrics['net_income']!r}")
            for metric in v2["metric_scores"]:
                old_e = old["metric_scores"].get(metric)
                new_e = v2["metric_scores"][metric]
                old_s = old_e["score"] if old_e else None
                new_s = new_e["score"]
                val = new_e["value"]
                print(f"    {metric:20s} value={val!r:>12} old={old_s!s:>4} new={new_s:>4} v2.2_weight={new_e['weight']:>2} contrib={new_e['weighted_score']:>5.2f}")
            print()
        except Exception as exc:
            print(f"{ticker}: FAILED: {exc}")


if __name__ == "__main__":
    main()
