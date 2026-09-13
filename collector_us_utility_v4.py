"""Production-ready utility collector v4.

Company Facts is the primary source. If the latest utility period has missing
or clearly suspicious balance-sheet/shareholder cash-flow facts, the latest
annual filing XBRL instance is parsed in memory as a fallback. Raw SEC JSON is
never persisted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import requests
from supabase import create_client

from collector_us_fundamental import SUPABASE_URL, SUPABASE_KEY, SEC_USER_AGENT, SEC_FACTS_URL, SEC_SUBMISSIONS_URL, fetch_json
from downturn_us import BENCHMARK, _close_series, calculate_downturn_defense
from us_scoring_v2 import calculate_us_utility_score_v2
from us_utility_extraction_v4 import REVENUE_TAGS, OPERATING_INCOME_TAGS, NET_INCOME_TAGS, ASSETS_TAGS, EQUITY_TAGS, pick_flow, pick_instant, pick_eps, pick_interest, pick_debt, pick_ocf, pick_capex, pick_dividend, core_years
from us_utility_filing_fallback import augment_with_latest_filing

SEC_TICKERS="https://www.sec.gov/files/company_tickers.json"
PERIODS=(1,3,5,10)
UTILITY_CAPEX_OVERRIDES={"ED":{2021:3630.0,2022:3824.0,2023:4353.0,2024:4770.0,2025:4764.0},"NEE":{2021:16077.0,2022:19283.0,2023:25113.0,2024:24729.0,2025:24606.0}}

def ticker_cik(session,ticker):
    data=fetch_json(session,SEC_TICKERS)
    for item in data.values():
        if str(item.get("ticker","")).upper()==ticker.upper(): return str(item["cik_str"]).zfill(10)
    raise RuntimeError(f"CIK not found: {ticker}")

def load_facts(session,ticker,cik):
    return fetch_json(session,SEC_FACTS_URL.format(cik=str(cik).zfill(10))),fetch_json(session,SEC_SUBMISSIONS_URL.format(cik=str(cik).zfill(10)))

def value(facts,picker,year,tags=None):
    row=picker(facts,tags,year) if tags is not None else picker(facts,year)
    return row["val"] if row else None

def capex_value(ticker,facts,year):
    override=UTILITY_CAPEX_OVERRIDES.get(ticker,{}).get(year)
    return override*1_000_000.0 if override is not None else value(facts,pick_capex,year)

def growth(cur,base,years):
    if cur is None or base in (None,0) or years<=0:return None
    if cur>0 and base>0:return ((cur/base)**(1.0/years)-1.0)*100.0
    return (cur/base-1.0)*100.0

def period_metrics(ticker,facts,latest_year,period):
    base_year=latest_year-period
    revenue_now=value(facts,pick_flow,latest_year,REVENUE_TAGS);revenue_base=value(facts,pick_flow,base_year,REVENUE_TAGS)
    if revenue_now is None or revenue_base is None:return None,None
    eps_now=value(facts,pick_eps,latest_year);eps_base=value(facts,pick_eps,base_year)
    op_now=value(facts,pick_flow,latest_year,OPERATING_INCOME_TAGS);ni_now=value(facts,pick_flow,latest_year,NET_INCOME_TAGS)
    assets_now=value(facts,pick_instant,latest_year,ASSETS_TAGS);equity_now=value(facts,pick_instant,latest_year,EQUITY_TAGS)
    debt_row=pick_debt(facts,latest_year);debt_now=debt_row["val"] if debt_row else None
    ocf_now=value(facts,pick_ocf,latest_year);capex_now=capex_value(ticker,facts,latest_year);div_now=value(facts,pick_dividend,latest_year)
    interest_row=pick_interest(facts,latest_year);interest_now=interest_row["val"] if interest_row else None
    return {"revenue_growth":growth(revenue_now,revenue_base,period),"eps_growth":growth(eps_now,eps_base,period),"opm":op_now/revenue_now*100.0 if op_now is not None and revenue_now else None,"roa":ni_now/assets_now*100.0 if ni_now is not None and assets_now else None,"debt_capital":debt_now/(debt_now+equity_now)*100.0 if debt_now is not None and equity_now not in (None,0) and debt_now+equity_now>0 else None,"ocf_debt":ocf_now/debt_now*100.0 if ocf_now is not None and debt_now not in (None,0) else None,"fcf_debt":(ocf_now-abs(capex_now))/debt_now*100.0 if ocf_now is not None and capex_now is not None and debt_now not in (None,0) else None,"dividend_coverage":ocf_now/abs(div_now) if ocf_now is not None and div_now not in (None,0) else None,"dividend_payout":abs(div_now)/ni_now*100.0 if div_now not in (None,0) and ni_now is not None and ni_now>0 else None,"interest_coverage":op_now/abs(interest_now) if op_now is not None and interest_now not in (None,0) else None},base_year

def _suspicious(facts,latest):
    debt=pick_debt(facts,latest);equity=value(facts,pick_instant,latest,EQUITY_TAGS);eps=pick_eps(facts,latest);div=pick_dividend(facts,latest)
    if equity is None or eps is None or div is None or debt is None:return True
    if debt.get("method")=="components" and ((debt.get("current") and debt.get("current",{}).get("namespace")=="filing-xbrl") or (debt.get("noncurrent") and debt.get("noncurrent",{}).get("namespace")=="filing-xbrl")):return True
    d=debt.get("val");ocf=value(facts,pick_ocf,latest)
    if d not in (None,0) and ocf is not None and abs(ocf/d)>2.0:return True
    return False

def build_result(ticker,cik,company_name,facts,submissions,market,stock,session):
    years={y for y in core_years(facts) if 2018<=y<=2026}
    if not years:return None
    latest=max(years);fallback_meta={"used":False}
    if _suspicious(facts,latest):facts,fallback_meta=augment_with_latest_filing(session,cik,submissions,facts)
    years={y for y in core_years(facts) if 2018<=y<=2026};latest=max(years) if years else latest
    downturn_value,downturn_detail=calculate_downturn_defense(ticker,market=market,stock=stock)
    period_scores={}
    for period in PERIODS:
        metrics,base=period_metrics(ticker,facts,latest,period)
        if metrics is None:continue
        metrics["downturn_defense"]=downturn_value
        period_scores[str(period)]={"base_year":base,"metrics":metrics,"scores":calculate_us_utility_score_v2(metrics)}
    latest_row=period_scores.get("1");score=latest_row["scores"]["total_score"] if latest_row else None
    return {"ticker":ticker,"cik":str(cik),"company_name":company_name,"sector":"utilities","base_year":latest,"period_scores":period_scores,"total_score":int(round(score)) if score is not None else None,"grade":latest_row["scores"]["grade"] if latest_row else None,"data_unavailable":not bool(period_scores),"data_reliability":"high" if len(period_scores)>=3 else ("medium" if period_scores else "low"),"missing_metric_count":latest_row["scores"]["missing_metric_count"] if latest_row else None,"updated_at":datetime.now(timezone.utc).isoformat(),"downturn_defense":downturn_value,"downturn_detail":downturn_detail}

def main():
    p=argparse.ArgumentParser();p.add_argument("--ticker");p.add_argument("--tickers");p.add_argument("--all",action="store_true",dest="all_rows");a=p.parse_args()
    if not SUPABASE_KEY:raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")
    sb=create_client(SUPABASE_URL,SUPABASE_KEY)
    if a.ticker:tickers=[a.ticker.strip().upper()]
    elif a.tickers:tickers=[x.strip().upper() for x in a.tickers.split(",") if x.strip()]
    elif a.all_rows:tickers=[r["ticker"] for r in sb.table("US_Companies").select("ticker").eq("is_fundamental_eligible",True).eq("sector_common","utilities").order("ticker").execute().data]
    else:raise RuntimeError("Use --ticker, --tickers, or --all")
    session=requests.Session();session.headers.update({"User-Agent":SEC_USER_AGENT});market=_close_series(BENCHMARK)
    for i,ticker in enumerate(tickers,1):
        try:
            cik=ticker_cik(session,ticker);facts,subs=load_facts(session,ticker,cik);stock=_close_series(ticker)
            result=build_result(ticker,cik,subs.get("name") or ticker,facts,subs,market,stock,session)
            if result is None:print(f"[{i}/{len(tickers)}] {ticker}: no core annual facts");continue
            sb.table("US_Fundamental").upsert(result,on_conflict="ticker").execute()
            print(f"[{i}/{len(tickers)}] {ticker}: score={result['total_score']} grade={result['grade']} periods={len(result['period_scores'])} missing={result['missing_metric_count']}")
        except Exception as exc:print(f"[{i}/{len(tickers)}] {ticker}: FAILED: {exc}")
    print("Completed: utility production collector v4")

if __name__=="__main__":main()
