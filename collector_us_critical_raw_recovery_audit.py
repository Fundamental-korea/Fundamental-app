"""Targeted raw-source audit for currently deficient US fundamental metrics.

Read-only. No Supabase writes, no score changes.

Selection is based on the current 1y DB metric schema and intentionally targets:
- missing ROIC
- missing interest coverage
- missing EPS growth
- present-but-extreme critical values

For each selected company it compares annual raw prerequisites visible in
Company Facts with those recoverable from the latest annual Inline XBRL filing.
This is the bridge between "metric is missing" and "collector needs a source
fix".
"""
from __future__ import annotations
import hashlib,json,os,re
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
from supabase import create_client
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

URL=os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
KEY=os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY","")
UA=os.environ.get("SEC_USER_AGENT","Fundamental-app contact@example.com")
OUT=Path("artifacts"); OUT.mkdir(exist_ok=True)
PAGE=1000
PER_TARGET=25
TARGETS=("roic_missing","interest_missing","eps_growth_missing","critical_extreme")
ANNUAL_FORMS={"10-K","10-K/A","20-F","20-F/A","40-F","40-F/A"}

ALIASES={
 "equity":{"StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest","Equity","EquityAttributableToOwnersOfParent","ProprietaryCapital","PartnersCapital","MembersEquity"},
 "cash":{"CashAndCashEquivalentsAtCarryingValue","CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents","CashAndCashEquivalents","CashAndRestrictedCash"},
 "operating_income":{"OperatingIncomeLoss","OperatingIncome","OperatingProfitLoss","IncomeFromOperations","ProfitLossFromOperatingActivities"},
 "debt_total":{"LongTermDebt","LongTermDebtCurrentAndNoncurrent","DebtAndCapitalLeaseObligations","LongTermDebtAndCapitalLeaseObligations","LongTermDebtAndFinanceLeaseObligations","DebtLongtermAndShorttermCombinedAmount","Borrowings","DebtAndFinanceLeaseLiabilities","Debt"},
 "debt_current":{"LongTermDebtCurrent","LongTermDebtAndCapitalLeaseObligationsCurrent","LongTermDebtAndFinanceLeaseObligationsCurrent","CurrentBorrowings","CurrentPortionOfLongtermBorrowings","ShortTermBorrowings","ShorttermBorrowings","FinanceLeaseLiabilityCurrent","ConvertibleDebtCurrent","DebtCurrent","NotesPayableCurrent","CommercialPaper"},
 "debt_noncurrent":{"LongTermDebtNoncurrent","LongTermDebt","LongTermDebtAndCapitalLeaseObligationsNoncurrent","LongTermDebtAndFinanceLeaseObligationsNoncurrent","NoncurrentBorrowings","LongtermBorrowings","FinanceLeaseLiabilityNoncurrent","ConvertibleDebtNoncurrent","DebtNoncurrent","NotesPayableNoncurrent","NotesPayable","LongTermNotesPayable","LoansPayable","LongTermLoansPayable","OtherBorrowings","UnsecuredDebt","SecuredDebt","OtherLongTermDebt","FederalHomeLoanBankAdvances"},
 "interest_expense":{"InterestExpenseNonoperating","InterestExpenseNonOperating","InterestExpenseDebt","InterestExpenseNonoperatingAndOther","InterestAndDebtExpense","InterestExpense","InterestExpenseOnDebtInstrumentsIssued","InterestExpenseOnBorrowings","InterestExpenseOnOtherFinancialLiabilities","InterestExpenseOnBankLoansAndOverdrafts","InterestExpenseOnBonds","InterestExpenseLongTermDebt","InterestExpenseShortTermBorrowings","InterestExpenseOtherLongTermDebt","InterestExpenseOtherShortTermBorrowings","InterestExpenseSubordinatedNotesAndDebentures","InterestCostsIncurred","FinancingInterestExpense","FinanceCosts","InterestExpenseOnDebt"},
 "eps":{"EarningsPerShareDiluted","EarningsPerShareBasic"},
}

INSTANT={"equity","cash","debt_total","debt_current","debt_noncurrent"}

def norm(v): return (v or "").strip().upper()
def cik(v): return str(int(v)).zfill(10)
def stable(v): return int(hashlib.sha256(v.encode()).hexdigest()[:12],16)

def fetch_target_rows(sb):
 rows=[];off=0
 select_expr="ticker,cik,company_name,base_year,total_score,missing_metric_count,data_reliability,metric_scores:period_scores->1y->avg->metric_scores"
 while True:
  p=(sb.table("US_Fundamental")
    .select(select_expr)
    .eq("data_unavailable",False)
    .order("ticker")
    .range(off,off+PAGE-1).execute().data or [])
  if not p: break
  rows+=p
  if len(p)<PAGE: break
  off+=PAGE
 return rows

def metric_scores(row):
 return row.get("metric_scores") or {}

def value_of(row,metric):
 x=metric_scores(row).get(metric) or {}
 return x.get("value")

def selector(row):
 ms=metric_scores(row)
 if not ms: return []
 v_roic=value_of(row,"roic")
 v_ic=value_of(row,"interest_coverage")
 v_eps=value_of(row,"eps_growth")
 out=[]
 if v_roic is None: out.append("roic_missing")
 if v_ic is None: out.append("interest_missing")
 if v_eps is None: out.append("eps_growth_missing")
 try:
  if any(abs(float(x))>threshold for x,threshold in [(v_roic,1000),(v_ic,100),(v_eps,500)] if x is not None):
   out.append("critical_extreme")
 except Exception:
  pass
 return out

def annual_present(facts, family, year):
 tags=ALIASES[family]
 for ns in ("us-gaap","ifrs-full"):
  for tag in tags:
   fact=((facts or {}).get("facts") or {}).get(ns,{}).get(tag)
   for unit,rows in (fact or {}).get("units",{}).items():
    for r in rows or []:
     if r.get("form") not in ANNUAL_FORMS: continue
     end=str(r.get("end") or "")
     if not end.startswith(str(year)): continue
     start=r.get("start")
     if family in INSTANT:
      if start: continue
     else:
      if not start: continue
      try:
       days=(datetime.fromisoformat(end[:10]).date()-datetime.fromisoformat(str(start)[:10]).date()).days
      except Exception: continue
      if not 300<=days<=380: continue
     try:
      val=float(r.get("val"))
      if val==val and abs(val)!=float("inf"): return True
     except Exception: pass
 return False

def filing_has(resolver,cik_value,family,year,sub):
 try:
  candidates,meta=resolver.search_filing(cik_value,family,year=year,submissions=sub,limit=1)
  return bool(candidates), (candidates[0].compact() if candidates else None), None
 except Exception as exc:
  return False,None,f"{type(exc).__name__}:{exc}"

def latest_fy(sub):
 recent=(sub or {}).get("filings",{}).get("recent",{})
 best=None
 forms=recent.get("form") or []; fys=recent.get("fy") or []; filed=recent.get("filingDate") or []
 for i,form in enumerate(forms):
  if form not in ANNUAL_FORMS: continue
  fy=fys[i] if i<len(fys) else None
  try: fy=int(fy) if fy is not None else None
  except: fy=None
  if fy is not None and (best is None or (filed[i] if i<len(filed) else "")>best[0]): best=((filed[i] if i<len(filed) else ""),fy,form)
 return best

def venue_map(resolver):
 doc=resolver._get("https://www.sec.gov/files/company_tickers_exchange.json").json()
 fields=doc.get("fields") or []; idx={k:i for i,k in enumerate(fields)}
 return {(norm((z:= {k:r[i] if i<len(r) else None for k,i in idx.items()}).get("ticker")),cik(z.get("cik"))):norm(z.get("exchange")) for r in doc.get("data") or []}

def main():
 if not KEY: raise RuntimeError("Supabase key required")
 sb=create_client(URL,KEY)
 rows=fetch_target_rows(sb)
 candidates={k:[] for k in TARGETS}
 for row in rows:
  for key in selector(row):
   if key in candidates: candidates[key].append(row)
 selected=[];seen=set();plan={}
 for key in TARGETS:
  pool=sorted(candidates[key],key=lambda x:stable(x["ticker"]))
  chosen=pool[:PER_TARGET]; plan[key]=len(chosen)
  for r in chosen:
   if r["ticker"] not in seen:
    seen.add(r["ticker"]); selected.append((r,key))
 resolver=SECXBRLSearchV2_3_8(user_agent=UA)
 venues=venue_map(resolver)
 results=[]; errors=Counter(); target_counts=Counter()
 print(f"=== US CRITICAL RAW RECOVERY AUDIT v1 === deficient_scalar_candidates={len(scalar_rows)} selected={len(selected)}")
 for i,(db,target) in enumerate(selected,1):
  t=db["ticker"]; c=cik(db["cik"]); stage="submissions"
  item={"ticker":t,"cik":c,"company_name":db["company_name"],"base_year":db.get("base_year"),
        "target_reason":target,"db_missing_metric_count":db.get("missing_metric_count"),
        "db_roic":value_of(db,"roic"),"db_interest_coverage":value_of(db,"interest_coverage"),
        "db_eps_growth":value_of(db,"eps_growth"),"sec_venue":venues.get((norm(t),c))}
  try:
   sub=resolver.submissions(c); stage="companyfacts"
   try: facts=resolver.company_facts(c); cf_err=None
   except Exception as exc: facts={"facts":{}}; cf_err=f"{type(exc).__name__}:{exc}"
   a=latest_fy(sub); fy=a[1] if a else db.get("base_year")
   item.update({"latest_annual_fy":fy,"latest_annual_form":a[2] if a else None,"latest_annual_filed":a[0] if a else None,"companyfacts_error":cf_err})
   stage="filing"
   prereq={
    "roic":["equity","cash","operating_income"],
    "interest_coverage":["operating_income","interest_expense"],
    "eps_growth":["eps","eps_prior"],
   }
   cf={}
   fi={}
   details={}
   for fam in {"equity","cash","operating_income","interest_expense","eps","debt_total","debt_current","debt_noncurrent"}:
    cf[fam]=annual_present(facts,fam,fy) if fy else False
   if target=="roic_missing":
    for fam in ("equity","cash","operating_income","debt_total","debt_current","debt_noncurrent"):
     ok,cand,err=filing_has(resolver,c,fam,fy,sub) if fy else (False,None,"NO_FY")
     fi[fam]=ok
     if err: details[fam]=err
    roic_filing_ready=(fi["equity"] and fi["cash"] and fi["operating_income"] and (fi["debt_total"] or (fi["debt_current"] and fi["debt_noncurrent"])))
   elif target=="interest_missing":
    for fam in ("operating_income","interest_expense"):
     ok,cand,err=filing_has(resolver,c,fam,fy,sub) if fy else (False,None,"NO_FY")
     fi[fam]=ok
     if err: details[fam]=err
    roic_filing_ready=False
    item["interest_filing_ready"]=fi["operating_income"] and fi["interest_expense"]
   elif target=="eps_growth_missing":
    ok1,cand1,err1=filing_has(resolver,c,"eps",fy,sub) if fy else (False,None,"NO_FY")
    ok2,cand2,err2=filing_has(resolver,c,"eps",fy-1,sub) if fy else (False,None,"NO_FY")
    fi["eps_current"]=ok1; fi["eps_prior"]=ok2
    if err1: details["eps_current"]=err1
    if err2: details["eps_prior"]=err2
    roic_filing_ready=False
    item["eps_growth_filing_ready"]=ok1 and ok2
   else:
    for fam in ("operating_income","interest_expense","eps"):
     ok,cand,err=filing_has(resolver,c,fam,fy,sub) if fy else (False,None,"NO_FY")
     fi[fam]=ok
     if err: details[fam]=err
    roic_filing_ready=False
   item.update({"companyfacts_prerequisites":cf,"filing_prerequisites":fi,"filing_errors":details})
   if target=="roic_missing": item["roic_filing_ready"]=roic_filing_ready
   if target=="roic_missing":
    item["companyfacts_roic_raw_ready"]=cf["equity"] and cf["cash"] and cf["operating_income"] and (cf["debt_total"] or (cf["debt_current"] and cf["debt_noncurrent"]))
   elif target=="interest_missing":
    item["companyfacts_interest_raw_ready"]=cf["operating_income"] and cf["interest_expense"]
   elif target=="eps_growth_missing":
    item["companyfacts_eps_growth_raw_ready"]=cf["eps"]
   target_counts[target]+=1
  except Exception as exc:
   errors[stage]+=1; item.update({"status":"ERROR","stage":stage,"error":f"{type(exc).__name__}:{exc}"})
  else: item["status"]="OK"
  results.append(item)
  print(f"[{i}/{len(selected)}] {t} {item['status']} target={target}")
 summary={"generated_at":datetime.now(timezone.utc).isoformat(),"usable_rows":len(rows),"selected":len(selected),"plan":plan,
          "errors_by_stage":dict(errors),"target_counts":dict(target_counts)}
 for k in ("roic_filing_ready","interest_filing_ready","eps_growth_filing_ready","companyfacts_roic_raw_ready","companyfacts_interest_raw_ready","companyfacts_eps_growth_raw_ready"):
  summary[k+"_count"]=sum(1 for r in results if r.get(k) is True)
 out=OUT/"us_critical_raw_recovery_audit_v1.json"
 out.write_text(json.dumps({"summary":summary,"companies":results},indent=2,ensure_ascii=False),encoding="utf-8")
 print(json.dumps(summary,indent=2))
 print(f"[REPORT] {out}")

if __name__=="__main__": main()
