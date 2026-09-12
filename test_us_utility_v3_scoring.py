"""Utility v3 scoring dry-run for PEG/AWR.

Diagnostic only: no Supabase writes and no production collector changes.
Uses SEC Company Facts directly and utility-specific candidate selection.
"""
from __future__ import annotations

import argparse, math, os, time
from datetime import datetime
import requests

SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
FLOW_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

REVENUE_TAGS = [
    "Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
    "SalesRevenueGoodsNet",
]
INTEREST_TAGS = ["InterestExpense", "InterestExpenseNonOperating", "InterestExpenseDebt", "InterestExpenseNonOperatingNet", "FinanceCosts"]
DEBT_CURRENT = ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent", "DebtAndCapitalLeaseObligationsCurrent"]
DEBT_NONCURRENT = ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligationsNoncurrent", "DebtAndCapitalLeaseObligationsNoncurrent"]
CAPEX_TAGS = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PaymentsToAcquirePropertyPlantAndEquipmentAndOtherProductiveAssets"]
DIV_TAGS = ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock", "PaymentsOfDividendsCommonStockCash"]

WEIGHTS = {"revenue_growth":5,"eps_growth":5,"opm":10,"roa":10,"debt_capital":15,"ocf_debt":15,"fcf_debt":10,"dividend_coverage":10,"interest_coverage":10,"downturn_defense":10}

BANDS = {
 "revenue_growth":[(20,10),(15,9),(10,8),(5,7),(0,6),(-5,5),(-10,4),(-20,3),(-35,2),(-50,1)],
 "eps_growth":[(20,10),(15,9),(10,8),(5,7),(0,6),(-5,5),(-10,4),(-20,3),(-35,2),(-50,1)],
 "opm":[(35,10),(30,9),(25,8),(20,7),(15,6),(10,5),(5,4),(0,3),(-5,2),(-15,1)],
 "roa":[(8,10),(6,9),(5,8),(4,7),(3,6),(2,5),(1,4),(0,3),(-2,2),(-5,1)],
 "debt_capital":[(35,10),(40,9),(45,8),(50,7),(55,6),(60,5),(65,4),(70,3),(80,2),(90,1)],
 "ocf_debt":[(25,10),(20,9),(15,8),(12,7),(10,6),(8,5),(6,4),(4,3),(2,2),(1,1)],
 "fcf_debt":[(10,10),(8,9),(6,8),(4,7),(3,6),(2,5),(1,4),(0,3),(-2,2),(-5,1)],
 "dividend_coverage":[(2.5,10),(2,9),(1.75,8),(1.5,7),(1.25,6),(1,5),(.9,4),(.8,3),(.7,2),(.5,1)],
 "interest_coverage":[(8,10),(6,9),(5,8),(4,7),(3,6),(2.5,5),(2,4),(1.5,3),(1,2),(.5,1)],
 "downturn_defense":[(20,10),(15,9),(10,8),(5,7),(0,6),(-5,5),(-10,4),(-15,3),(-25,2),(-40,1)],
}

def clean(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except: return None

def get_json(s,u):
    for i in range(4):
        r=s.get(u,timeout=30)
        if r.status_code==200:return r.json()
        if r.status_code in (429,500,502,503,504):time.sleep(1.5*(i+1));continue
        r.raise_for_status()
    raise RuntimeError(u)

def annual_rows(facts, tag):
    root=facts.get("facts",facts); out=[]
    for ns in ("us-gaap","ifrs-full"):
        fact=(root.get(ns) or {}).get(tag)
        for unit, rows in ((fact or {}).get("units") or {}).items():
            for r in rows:
                if r.get("form") not in FLOW_FORMS or not r.get("end") or not r.get("start"):continue
                try:
                    end=datetime.fromisoformat(r["end"]).date(); start=datetime.fromisoformat(r["start"]).date()
                except:continue
                days=(end-start).days
                if not 300<=days<=380:continue
                v=clean(r.get("val"))
                if v is not None:out.append((end.year,v,r.get("filed",''),ns,tag,unit))
    out.sort(key=lambda x:(x[0],x[2])); return {x[0]:x for x in out}

def instant_rows(facts, tag):
    root=facts.get("facts",facts); out=[]
    for ns in ("us-gaap","ifrs-full"):
        fact=(root.get(ns) or {}).get(tag)
        for unit, rows in ((fact or {}).get("units") or {}).items():
            for r in rows:
                if r.get("form") not in FLOW_FORMS or not r.get("end"):continue
                try:y=datetime.fromisoformat(r["end"]).date().year
                except:continue
                v=clean(r.get("val"))
                if v is not None:out.append((y,v,r.get("filed",''),ns,tag,unit))
    out.sort(key=lambda x:(x[0],x[2])); return {x[0]:x for x in out}

def pick_flow(facts,tags,year):
    for tag in tags:
        r=annual_rows(facts,tag).get(year)
        if r:return r
    return None

def pick_instant(facts,tags,year):
    for tag in tags:
        r=instant_rows(facts,tag).get(year)
        if r:return r
    return None

def cagr(cur,base,n):
    if cur is None or base in (None,0):return None
    if cur>0 and base>0:return ((cur/base)**(1/n)-1)*100
    return (cur/base-1)*100

def score(metric,v):
    if v is None:return None
    for threshold,s in BANDS[metric]:
        if metric=="debt_capital":
            if v<=threshold:return s
        elif v>=threshold:return s
    return 0

def calculate(m):
    raw={k:score(k,m.get(k)) for k in WEIGHTS}
    available=sum(WEIGHTS[k] for k,v in raw.items() if v is not None)
    weighted=sum(raw[k]*WEIGHTS[k]/10 for k in WEIGHTS if raw[k] is not None)
    total=weighted*100/available if available else None
    cap=100 if available>=90 else 92 if available>=75 else 82 if available>=60 else 70
    total=min(total,cap) if total is not None else None
    grade="S" if total>=76 else "A" if total>=64 else "B" if total>=53 else "C" if total>=42 else "D"
    return raw,available,total,cap,grade

def ticker_cik(s,ticker):
    data=get_json(s,SEC_TICKERS)
    for x in data.values():
        if str(x.get("ticker",'')).upper()==ticker.upper():return str(x["cik_str"]).zfill(10)
    raise RuntimeError(f"CIK not found: {ticker}")

def run(s,ticker):
    cik=ticker_cik(s,ticker); facts=get_json(s,SEC_FACTS.format(cik=cik)); sub=get_json(s,SEC_SUBMISSIONS.format(cik=cik))
    root=facts.get("facts",facts)
    # Core annual series. Prefer consolidated Revenues for utilities.
    years=set()
    for tag in ["Revenues","OperatingIncomeLoss","NetIncomeLoss","Assets","StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest","NetCashProvidedByUsedInOperatingActivities"]:
        years.update(annual_rows(facts,tag).keys())
    latest=max(years); base=latest-1
    print("\n"+"="*90); print(f"{ticker} | {sub.get('name')} | SIC {sub.get('sic')} {sub.get('sicDescription')}"); print(f"years={sorted(y for y in years if latest-5<=y<=latest)}")
    def val(tags,year,instant=False):
        r=pick_instant(facts,tags,year) if instant else pick_flow(facts,tags,year); return r[1] if r else None
    rev={y:val(REVENUE_TAGS,y) for y in years}; op={y:val(["OperatingIncomeLoss"],y) for y in years}; ni={y:val(["NetIncomeLoss","ProfitLoss"],y) for y in years}; assets={y:val(["Assets"],y,True) for y in years}; eq={y:val(["StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest","Equity"],y,True) for y in years}; ocf={y:val(["NetCashProvidedByUsedInOperatingActivities","CashFlowsFromUsedInOperatingActivities"],y) for y in years}
    debt={}
    for y in years:
        cur=pick_instant(facts,DEBT_CURRENT,y); non=pick_instant(facts,DEBT_NONCURRENT,y); debt[y]=(cur[1] if cur else 0)+(non[1] if non else 0) if cur or non else None
    interest={y:(pick_flow(facts,INTEREST_TAGS,y)[1] if pick_flow(facts,INTEREST_TAGS,y) else None) for y in years}
    capex={y:(pick_flow(facts,CAPEX_TAGS,y)[1] if pick_flow(facts,CAPEX_TAGS,y) else None) for y in years}
    div={y:(pick_flow(facts,DIV_TAGS,y)[1] if pick_flow(facts,DIV_TAGS,y) else None) for y in years}
    metrics={
      "revenue_growth":cagr(rev.get(latest),rev.get(base),1),
      "eps_growth":None,
      "opm":(op.get(latest)/rev.get(latest)*100 if op.get(latest) is not None and rev.get(latest) else None),
      "roa":(ni.get(latest)/assets.get(latest)*100 if ni.get(latest) is not None and assets.get(latest) else None),
      "debt_capital":(debt.get(latest)/(debt.get(latest)+eq.get(latest))*100 if debt.get(latest) is not None and eq.get(latest) not in (None,0) else None),
      "ocf_debt":(ocf.get(latest)/debt.get(latest)*100 if ocf.get(latest) is not None and debt.get(latest) not in (None,0) else None),
      "fcf_debt":((ocf.get(latest)-abs(capex.get(latest)))/debt.get(latest)*100 if ocf.get(latest) is not None and capex.get(latest) is not None and debt.get(latest) not in (None,0) else None),
      "dividend_coverage":(ocf.get(latest)/abs(div.get(latest)) if ocf.get(latest) is not None and div.get(latest) not in (None,0) else None),
      "interest_coverage":(op.get(latest)/abs(interest.get(latest)) if op.get(latest) is not None and interest.get(latest) not in (None,0) else None),
      "downturn_defense":None,
    }
    raw,available,total,cap,grade=calculate(metrics)
    print("\n[SELECTED 2025/Latest inputs]")
    for k,v in metrics.items():print(f"  {k:20s} {v}")
    print("\n[RAW SCORES]"); print(raw); print(f"available_weight={available}/100 | score_cap={cap} | total={total:.1f} | grade={grade}")
    print("\n[EXTRACTION CHECK]")
    for label,series in [("revenue",rev),("debt",debt),("interest",interest),("capex",capex),("dividend",div)]:
        print(f"  {label}: "+", ".join(f"{y}={series.get(y)}" for y in sorted(y for y in years if latest-4<=y<=latest)))


def main():
    p=argparse.ArgumentParser();p.add_argument('--tickers',default='PEG,AWR');a=p.parse_args(); s=requests.Session(); s.headers.update({'User-Agent':os.environ.get('SEC_USER_AGENT','Fundamental-app contact@example.com')});
    for t in [x.strip().upper() for x in a.tickers.split(',') if x.strip()]:
        try:run(s,t)
        except Exception as e:print(f"{t}: ERROR {e}")
    print("\nCompleted: DIAGNOSTIC ONLY / NO DB WRITE / NO PRODUCTION CHANGES")
if __name__=='__main__':main()
