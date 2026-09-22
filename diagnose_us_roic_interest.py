"""Forensic diagnostic for persistent US ROIC / interest-coverage gaps.

This script deliberately DOES NOT write US_Fundamental. It samples current missing
rows, reconstructs the exact annual metric inputs used by collector_us_fundamental,
and, when an input is absent, asks the SEC XBRL resolver whether a filing-level
candidate exists. The goal is to identify the failure stage before any collector
logic is changed.
"""
from __future__ import annotations
import argparse, json, os, time
from collections import Counter
from datetime import datetime, timezone
import requests
from supabase import create_client
import collector_us_fundamental as base
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

URL=os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
KEY=os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY","")
UA=os.environ.get("SEC_USER_AGENT","Fundamental-app contact@example.com")
PAGE=100
ROIC_INPUTS={"operating_income","equity","cash","debt_current","debt_noncurrent","debt_total"}
INTEREST_INPUTS={"operating_income","interest_expense"}

def sec(session,url):
    for attempt in range(5):
        try:
            r=session.get(url,timeout=45)
            if r.status_code==200: return r.json()
            if r.status_code in (429,500,502,503,504):
                time.sleep(min(2**attempt,16)); continue
            r.raise_for_status()
        except requests.RequestException:
            if attempt==4: raise
            time.sleep(min(2**attempt,16))
    raise RuntimeError("SEC request failed")

def get_targets(sb, kind, limit):
    # Select only rows whose 1Y score metric is actually absent.
    field = "roic" if kind=="roic" else "interest_coverage"
    rows=[]; off=0
    while len(rows)<limit:
        page=(sb.table("US_Fundamental")
            .select("ticker,period_scores,base_year,data_unavailable")
            .eq("data_unavailable",False)
            .order("ticker")
            .range(off,off+PAGE-1).execute().data or [])
        if not page: break
        for f in page:
            avg=((f.get("period_scores") or {}).get("1y") or {}).get("avg") or {}
            v=((avg.get("metric_scores") or {}).get(field) or {}).get("value")
            if v is None:
                rows.append(f)
                if len(rows)>=limit: break
        off += PAGE
    return rows

def classify_roic(index,year):
    vals={m:base.latest_annual_value(index,m,year) for m in ROIC_INPUTS}
    op=vals["operating_income"]; eq=vals["equity"]
    debt=(vals["debt_current"]+vals["debt_noncurrent"]
          if vals["debt_current"] is not None and vals["debt_noncurrent"] is not None
          else vals["debt_total"])
    cash=vals["cash"]
    if op is None: return "NO_OPERATING_INCOME",vals
    if eq is None: return "NO_EQUITY",vals
    if debt is None: return "NO_DEBT",vals
    invested=eq+debt-(cash or 0.0)
    if invested<=0: return "NON_POSITIVE_INVESTED_CAPITAL",{**vals,"invested_capital":invested}
    return "CALCULABLE_FROM_COMPANY_FACTS",{**vals,"invested_capital":invested}

def classify_interest(index,year):
    op=base.latest_annual_value(index,"operating_income",year)
    interest=base.latest_annual_value(index,"interest_expense",year)
    if op is None and interest is None: return "NO_OPERATING_INCOME_AND_INTEREST_EXPENSE",{"operating_income":op,"interest_expense":interest}
    if op is None: return "NO_OPERATING_INCOME",{"operating_income":op,"interest_expense":interest}
    if interest is None: return "NO_INTEREST_EXPENSE",{"operating_income":op,"interest_expense":interest}
    if interest==0: return "ZERO_INTEREST_EXPENSE",{"operating_income":op,"interest_expense":interest}
    return "CALCULABLE_FROM_COMPANY_FACTS",{"operating_income":op,"interest_expense":interest}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--interest-limit",type=int,default=60)
    ap.add_argument("--roic-limit",type=int,default=60)
    ap.add_argument("--output",default="us_roic_interest_forensics.json")
    args=ap.parse_args()
    if not KEY: raise RuntimeError("SUPABASE key missing")
    sb=create_client(URL,KEY)
    session=requests.Session()
    session.headers.update({"User-Agent":UA,"Accept-Encoding":"gzip, deflate"})
    resolver=SECXBRLSearchV2_3_8(user_agent=UA,session=session)
    result={"generated_at":datetime.now(timezone.utc).isoformat(),"samples":{},"summary":{}}
    for kind,limit in (("interest",args.interest_limit),("roic",args.roic_limit)):
        targets=get_targets(sb,kind,limit)
        rows=[]; reasons=Counter(); resolver_reasons=Counter()
        for n,f in enumerate(targets,1):
            meta=(sb.table("US_Companies").select("ticker,cik,company_name,sector_common,scoring_profile")
                  .eq("ticker",f["ticker"]).limit(1).execute().data or [])
            if not meta: continue
            m=meta[0]
            try:
                facts=sec(session,base.SEC_FACTS_URL.format(cik=str(m["cik"]).zfill(10)))
                subs=sec(session,base.SEC_SUBMISSIONS_URL.format(cik=str(m["cik"]).zfill(10)))
                idx=base.build_fact_index(facts)
                years=sorted(set(idx.get("revenue",{}))|set(idx.get("operating_income",{}))|set(idx.get("net_income",{})))
                year=max(years) if years else None
                if year is None:
                    reason="NO_SEC_FLOW_YEAR"; detail={}
                elif kind=="interest":
                    reason,detail=classify_interest(idx,year)
                else:
                    reason,detail=classify_roic(idx,year)
                reasons[reason]+=1
                rec={"ticker":m["ticker"],"cik":m["cik"],"company_name":m.get("company_name"),
                     "sector":m.get("sector_common"),"base_year":year,"reason":reason,"inputs":detail}
                # If the Company Facts mapping says an input is absent, test filing XBRL.
                missing=[]
                needed=INTEREST_INPUTS if kind=="interest" else ROIC_INPUTS
                for metric in sorted(needed):
                    if base.latest_annual_value(idx,metric,year) is None:
                        missing.append(metric)
                candidates={}
                resolver.prime_company(m["cik"],facts,subs)
                for metric in missing:
                    try:
                        rr=resolver.resolve(m["cik"],metric,year=year,limit=5)
                        best=rr.get("best") if isinstance(rr,dict) else None
                        if best:
                            candidates[metric]={
                                "concept":best.get("concept"),"value":best.get("value"),
                                "source":best.get("source"),"reason":best.get("reason"),
                                "form":best.get("form"),"end":best.get("end"),
                            }
                            resolver_reasons[f"{metric}:FOUND"]+=1
                        else:
                            resolver_reasons[f"{metric}:NOT_FOUND"]+=1
                    except Exception as exc:
                        candidates[metric]={"error":str(exc)}
                        resolver_reasons[f"{metric}:ERROR"]+=1
                rec["missing_company_facts"]=missing
                rec["resolver_candidates"]=candidates
                rows.append(rec)
            except Exception as exc:
                reasons["FETCH_OR_PARSE_ERROR"]+=1
                rows.append({"ticker":m["ticker"],"cik":m["cik"],"reason":"FETCH_OR_PARSE_ERROR","error":str(exc)})
            if n%10==0: print(f"[{kind}] {n}/{len(targets)}")
        result["samples"][kind]=rows
        result["summary"][kind]={"sample_size":len(rows),"reason_counts":dict(reasons),"resolver_counts":dict(resolver_reasons)}
        print(f"\n[{kind}] reason_counts={dict(reasons)}")
        print(f"[{kind}] resolver_counts={dict(resolver_reasons)}")
    with open(args.output,"w",encoding="utf-8") as fh:
        json.dump(result,fh,ensure_ascii=False,indent=2)
    print(f"wrote {args.output}")

if __name__=="__main__": main()
