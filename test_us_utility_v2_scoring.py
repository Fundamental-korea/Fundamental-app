"""Dry-run comparison: current US utility scoring vs Utility v2.3.
No Supabase writes. Fetches SEC Company Facts and compares the current model
with the proposed v2.3 utility model.
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
NET_INCOME_TAGS=["NetIncomeLossAvailableToCommonStockholdersBasic","NetIncomeLossAttributableToParent","ProfitLossAttributableToOwnersOfParent","NetIncomeLoss","ProfitLoss"]
DIVIDEND_TAGS=["DividendsCommonStockCash","PaymentsOfDividendsCommonStockCash","PaymentsOfDividendsCommonStock","PaymentsOfOrdinaryDividends","PaymentsOfDividends"]
OCF_TAGS=["NetCashProvidedByUsedInOperatingActivities","NetCashProvidedByUsedInOperatingActivitiesContinuingOperations","CashFlowsFromUsedInOperatingActivities","NetCashProvidedByUsedInOperatingActivitiesContinuingOperationsAndDiscontinuedOperations"]
def value(facts,year,tags):
    row=pick_flow(facts,tags,year); return row["val"] if row else None
def dividend_amount(facts,year):
    v=value(facts,year,DIVIDEND_TAGS); return abs(v) if v is not None else None
def dividend_safety_metrics(facts,year):
    dividend=dividend_amount(facts,year); ocf=value(facts,year,OCF_TAGS); net_income=value(facts,year,NET_INCOME_TAGS)
    ocf_dividend=abs(ocf)/dividend if dividend and dividend>0 and ocf is not None else None
    payout=dividend/net_income*100.0 if dividend and dividend>0 and net_income and net_income>0 else None
    return {"dividend_coverage":ocf_dividend,"ocf_dividend":ocf_dividend,"dividend_payout":payout,"dividend":dividend,"ocf":ocf,"net_income":net_income}
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--tickers",default="AEP,AWK,CEG,D,DUK,ED,EXC,NEE,PEG,SO,VST"); args=parser.parse_args()
    tickers=[x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    session=requests.Session(); session.headers.update({"User-Agent":SEC_USER_AGENT}); market=_close_series(BENCHMARK)
    print("UTILITY V2.3 DRY RUN — NO DB WRITE")
    print("Weights: Revenue 7 | EPS 7 | OPM 10 | ROA 8 | Debt/Capital 15 | OCF/Debt 10 | FCF/Debt 8 | Interest 10 | Dividend Coverage 4 | OCF/Dividend 6 | Payout 3 | Downturn 12")
    print()
    for ticker in tickers:
        try:
            cik=ticker_cik(session,ticker); facts,submissions=load_facts(session,ticker,cik); stock=_close_series(ticker)
            current=build_result(ticker,cik,submissions.get("name") or ticker,facts,submissions,market,stock)
            if not current: print(f"{ticker}: no annual facts"); continue
            latest_year=current["base_year"]; latest=current["period_scores"].get("1")
            if not latest: print(f"{ticker}: no 1Y score"); continue
            metrics=dict(latest["metrics"]); dm=dividend_safety_metrics(facts,latest_year); metrics.update({k:dm[k] for k in ("dividend_coverage","ocf_dividend","dividend_payout")})
            v2=calculate_us_utility_score_v2(metrics); old=calculate_us_score(latest["metrics"],profile="utility")
            print(f"[{ticker}] {latest_year}"); print(f"  CURRENT : {old['total_score']:.1f} {old['grade']} | coverage={old['coverage_pct']:.1f}% | missing={old['missing_metric_count']}"); print(f"  V2.3    : {v2['total_score']:.1f} {v2['grade']} | coverage={v2['coverage_pct']:.1f}% | missing={v2['missing_metric_count']}"); print(f"  CHANGE  : {v2['total_score']-old['total_score']:+.1f}"); print(f"  DIV DATA: dividend={dm['dividend']!r} ocf={dm['ocf']!r} net_income={dm['net_income']!r}")
            for metric,e in v2["metric_scores"].items():
                old_e=old["metric_scores"].get(metric); old_s=old_e["score"] if old_e else None
                print(f"    {metric:20s} value={e['value']!r:>12} old={old_s!s:>4} new={e['score']:>4} v2.3_weight={e['weight']:>2} contrib={e['weighted_score']:>5.2f}")
            print()
        except Exception as exc: print(f"{ticker}: FAILED: {exc}")
if __name__=="__main__": main()
