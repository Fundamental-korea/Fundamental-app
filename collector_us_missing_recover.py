"""Recover missing US Standard metrics 1-3 using SEC Company Facts.

Recovery-only: existing populated metric values are preserved. Only missing
values are filled from SEC facts or conservative derivations.
"""
from __future__ import annotations
import argparse, math, os, time
from datetime import datetime, timezone
import requests
from supabase import create_client
import collector_us_fundamental as base
from us_scoring import calculate_us_score, data_reliability_from_periods

URL=os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
KEY=os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY","")
UA=os.environ.get("SEC_USER_AGENT","Fundamental-app contact@example.com")
LAST=0.0

EXTRA={
 "interest_expense":["InterestAndDebtExpense","FinanceCosts","InterestExpenseNonOperatingNet","InterestExpenseDebt","InterestExpenseNonOperating","InterestExpense"],
 "sga":["GeneralAndAdministrativeExpense","SellingExpense","SellingGeneralAndAdministrativeExpense"],
 "equity":["PartnersCapital","MembersEquity","Equity","EquityAttributableToOwnersOfParent","StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
 "debt_total":["TotalDebt","Debt","LongTermNotesPayable"],
 "operating_income":["OperatingIncome","OperatingProfitLoss","IncomeFromOperations"],
}
SHARES=["WeightedAverageNumberOfDilutedSharesOutstanding","WeightedAverageNumberOfSharesOutstandingBasic","WeightedAverageNumberOfSharesOutstanding"]

def extend_aliases():
    for metric,tags in EXTRA.items():
        for m in (base.FACT_ALIASES,base.IFRS_FACT_ALIASES):
            for tag in tags:
                if tag not in m.setdefault(metric,[]): m[metric].append(tag)

def get_json(s,url):
    global LAST
    wait=.20-(time.monotonic()-LAST)
    if wait>0: time.sleep(wait)
    LAST=time.monotonic()
    for attempt in range(5):
        try:
            r=s.get(url,timeout=30)
            if r.status_code==200:return r.json()
            if r.status_code in (429,500,502,503,504):
                time.sleep(min(2**attempt,16));continue
            r.raise_for_status()
        except requests.RequestException:
            if attempt==4:raise
            time.sleep(min(2**attempt,16))
    raise RuntimeError("SEC request failed")

def recovered_index(facts):
    idx=base.build_fact_index(facts)
    root=facts.get("facts") or {}
    share={}
    for ns in ("us-gaap","ifrs-full","filing-xbrl"):
        fs=root.get(ns) or {}
        for tag in SHARES:
            fact=fs.get(tag)
            if fact:
                for y,row in base.annual_records(fact).items():
                    share.setdefault(y,row)
    eps=idx.setdefault("eps",{})
    ni=idx.get("net_income",{})
    for y,n in ni.items():
        if y in eps: continue
        sh=share.get(y,{}).get("val")
        if sh and sh>0:
            eps[y]={**n,"val":n["val"]/sh,"unit":"USD/sh","namespace":"derived","tag":"DerivedEPSFromNetIncomeAndWeightedAverageShares"}
    eq=idx.setdefault("equity",{})
    for y,a in idx.get("assets",{}).items():
        if y in eq or y not in idx.get("liabilities",{}): continue
        v=a["val"]-idx["liabilities"][y]["val"]
        if math.isfinite(v): eq[y]={**a,"val":v,"namespace":"derived","tag":"DerivedEquityFromAssetsMinusLiabilities"}
    return idx

def merged_score(oldp,pair,profile):
    oldavg=oldp.get("avg") or {}; oldworst=oldp.get("worst") or {}
    avg=dict(pair["avg_metrics"]); worst=dict(pair["worst_metrics"])
    # Downturn defense is market-data-derived; keep the existing value.
    avg["downturn_defense"]=((oldavg.get("metric_scores") or {}).get("downturn_defense") or {}).get("value")
    worst["downturn_defense"]=((oldworst.get("metric_scores") or {}).get("downturn_defense") or {}).get("value")
    oldvals={k:(v or {}).get("value") for k,v in (oldavg.get("metric_scores") or {}).items()}
    for k,v in list(avg.items()):
        if oldvals.get(k) is not None:
            avg[k]=oldvals[k]
            worst[k]=((oldworst.get("metric_scores") or {}).get(k) or {}).get("value",oldvals[k])
    a=calculate_us_score(avg,profile=profile); w=calculate_us_score(worst,profile=profile)
    def pack(x): return {"total_score":x["total_score"],"grade":x["grade"],"metric_scores":x["metric_scores"],"sub_scores":x.get("sub_scores",{}),"financial_adjusted":False,"missing_metric_count":x["missing_metric_count"],"scoring_version":x["scoring_version"],"available_weight":x["available_weight"],"coverage_pct":x["coverage_pct"],"score_cap":x["score_cap"],"confidence_level":x["confidence_level"]}
    return {"years_used":pair["years_used"],"yearly_breakdown":pair["yearly_breakdown"],"avg":pack(a),"worst":pack(w)}

def recover_row(sb,s,row):
    facts=get_json(s,base.SEC_FACTS_URL.format(cik=str(row["_cik"]).zfill(10)))
    idx=recovered_index(facts)
    years=sorted({y for rows in idx.values() for y in rows})
    if not years:return False,"no-sec-years"
    flow=sorted(set(idx.get("revenue",{}))|set(idx.get("operating_income",{}))|set(idx.get("net_income",{})))
    latest=max(flow) if flow else max(years)
    old=row.get("period_scores") or {}; new={}; changed=False
    profile=row.get("scoring_profile") or "standard"
    for p in base.PERIODS:
        key=f"{p}y"; oldp=old.get(key) or {}
        pair=base.period_metrics_pair(idx,latest,p)
        if not pair:new[key]=oldp;continue
        packed=merged_score(oldp,pair,profile)
        oldm=(oldp.get("avg") or {}).get("metric_scores") or {}
        newm=packed["avg"]["metric_scores"]
        if any((oldm.get(k) or {}).get("value") is None and (v or {}).get("value") is not None for k,v in newm.items()):
            changed=True
        # Preserve populated yearly values and only fill blanks.
        oy=oldp.get("yearly_breakdown") or {}; ny=packed["yearly_breakdown"]
        for metric,vals in ny.items():
            if not isinstance(vals,dict): continue
            target=oy.setdefault(metric,{})
            for y,v in vals.items():
                if target.get(y) is None and v is not None: target[y]=v
        packed["yearly_breakdown"]=oy
        new[key]=packed
    if not changed:return False,"no-recovery"
    avg=(new.get("1y") or {}).get("avg") or {}
    result={"ticker":row["ticker"],"cik":row["_cik"],"period_scores":new,"base_year":latest,"total_score":int(round(avg["total_score"])) if avg.get("total_score") is not None else None,"grade":avg.get("grade"),"missing_metric_count":avg.get("missing_metric_count",0),"data_unavailable":False,"data_reliability":data_reliability_from_periods(new),"updated_at":datetime.now(timezone.utc).isoformat()}
    sb.table("US_Fundamental").upsert(result,on_conflict="ticker").execute()
    return True,"recovered"

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--limit",type=int,default=0);args=ap.parse_args()
    if not KEY:raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")
    extend_aliases();sb=create_client(URL,KEY)
    # scoring_profile and CIK live on US_Companies, not US_Fundamental.
    # Supabase REST commonly caps a response at 1000 rows, so paginate explicitly.
    rows=[]
    page=0
    page_size=100
    while True:
        # Keep REST payloads small: period_scores is a large JSONB document and
        # 500-row pages can exceed the database statement timeout.
        q=(
            sb.table("US_Fundamental")
            .select("ticker,period_scores")
            .gte("missing_metric_count",1)
            .lte("missing_metric_count",3)
            .eq("data_unavailable",False)
            .range(page*page_size,(page+1)*page_size-1)
        )
        batch=q.execute().data or []
        rows.extend(batch)
        if len(batch)<page_size or (args.limit and len(rows)>=args.limit):
            break
        page+=1
    if args.limit:
        rows=rows[:args.limit]

    tickers=[r["ticker"] for r in rows if r.get("ticker")]
    company_map={}
    for start in range(0,len(tickers),500):
        batch=tickers[start:start+500]
        data=(
            sb.table("US_Companies")
            .select("ticker,cik,scoring_profile")
            .in_("ticker",batch)
            .execute().data
            or []
        )
        for item in data:
            company_map[item["ticker"]]={
                "cik": item.get("cik"),
                "scoring_profile": item.get("scoring_profile") or "standard",
            }

    usable=[]
    for row in rows:
        meta=company_map.get(row.get("ticker")) or {}
        row["_cik"]=meta.get("cik")
        row["_scoring_profile"]=meta.get("scoring_profile") or "standard"
        if row.get("_cik"):
            usable.append(row)
        else:
            print(f"[SKIP] {row.get('ticker')}: no CIK in US_Companies")
    rows=usable
    s=requests.Session();s.headers.update({"User-Agent":UA})
    done=0
    for i,row in enumerate(rows,1):
        try:
            ok,msg=recover_row(sb,s,row);done+=int(ok)
            print(f"[{i}/{len(rows)}] {row['ticker']}: {msg}")
        except Exception as e: print(f"[{i}/{len(rows)}] {row['ticker']}: FAILED {e}")
    print(f"Completed processed={len(rows)} recovered={done}")

if __name__=="__main__":main()
