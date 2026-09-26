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
from sec_filing_financial_map import classify_filing_rows

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

OPERATING_EXCLUDED_TOKENS=(
 "segment","margin","percentage","ratio","rate","lease","revenue","sales",
 "cost","expense","cashflow","discontinued","restructur","per share",
 "pershare","weightedaverage","tax","reconciliation","forecast","budget",
 "proforma","nonoperating","non-operating",
)
OPERATING_CANONICAL={"OperatingIncomeLoss","OperatingIncome","OperatingProfitLoss","IncomeFromOperations","ProfitLossFromOperatingActivities"}

def operating_like_candidates(rows, year):
 out=[]
 signals=(
  "operatingincome","operatingprofit","operatingresult",
  "incomefromoperations","profitfromoperations",
  "profitlossfromoperatingactivities","resultfromoperatingactivities",
  "operatingearnings","operatingloss",
 )
 for r in rows or []:
  if r.get("form") not in ANNUAL_FORMS or not r.get("start") or not r.get("end") or r.get("dimensioned"):
   continue
  if str(r.get("end",""))[:4] != str(year):
   continue
  concept=str(r.get("concept") or "").rsplit("}",1)[-1].rsplit(":",1)[-1]
  label=str(r.get("label") or "")
  compact=re.sub(r"[^a-z0-9]+","",(concept+" "+label).lower())
  if concept in OPERATING_CANONICAL:
   continue
  if any(x.replace(" ","") in compact for x in OPERATING_EXCLUDED_TOKENS):
   continue
  if not any(sig in compact for sig in signals):
   continue
  try:
   value=float(r.get("value"))
  except Exception:
   continue
  out.append({
   "namespace":r.get("namespace") or "",
   "concept":concept,
   "label":label,
   "value":value,
   "unit":r.get("unit"),
   "start":r.get("start"),
   "end":r.get("end"),
   "filed":r.get("filed"),
   "contextRef":r.get("contextRef"),
  })
 return out

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
 print(f"=== US CRITICAL RAW RECOVERY AUDIT v1 === target_rows={len(rows)} selected={len(selected)}")
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
   # Pin the audit to the DB's current base_year. This is the same provenance
   # target used by production critical-metric recovery.
   fy=int(db["base_year"]) if db.get("base_year") is not None else None
   a=latest_fy(sub)
   item.update({"latest_submission_fy":a[1] if a else None,"latest_annual_form":a[2] if a else None,
                "latest_annual_filed":a[0] if a else None,"target_year":fy,"companyfacts_error":cf_err})
   stage="filing"
   cf={}
   fi={}
   details={}
   mapped=None
   if fy:
    try:
     # One filing parse + economic map is more reliable than checking each
     # concept independently because debt/interest require same-period/unit
     # compatibility guards.
     filing_rows, filing_meta = resolver._inline_filing_rows(c, sub)
     mapped = classify_filing_rows(filing_rows, target_year=fy)
     op_candidates = operating_like_candidates(filing_rows, fy)
     item["operating_like_candidates"] = op_candidates[:30]
     item["operating_like_candidate_count"] = len(op_candidates)
     item["filing_map_summary"]={
       "rows":len(filing_rows),
       "accession":filing_meta.get("accession"),
       "filed":filing_meta.get("filed"),
       "debt_status":mapped.get("debt_status"),
       "interest_status":mapped.get("interest_status"),
     }
    except Exception as exc:
     details["filing_map"]=f"{type(exc).__name__}:{exc}"
   for fam in {"equity","cash","operating_income","interest_expense","eps","debt_total","debt_current","debt_noncurrent"}:
    cf[fam]=annual_present(facts,fam,fy) if fy else False

   if mapped:
    ri=mapped.get("roic_inputs") or {}
    debt=mapped.get("selected_debt")
    intr=mapped.get("selected_interest")
    eq=ri.get("equity"); cash=ri.get("cash"); op=ri.get("operating_income")

    roic_inputs_complete=bool(eq and cash and op and debt)
    if target=="roic_missing" or target=="critical_extreme":
     if roic_inputs_complete:
      invested=float(eq["value"])+float(debt["value"])-float(cash["value"])
      if invested<=0:
       item["roic_recoverability"]="SOURCE_COMPLETE_BUT_UNDEFINED"
       item["roic_undefined_reason"]="NONPOSITIVE_INVESTED_CAPITAL"
      else:
       value=float(op["value"])*0.78/invested*100.0
       item["roic_recoverability"]="SOURCE_COMPLETE_CALCULABLE"
       item["roic_candidate_value"]=value
     else:
      item["roic_recoverability"]="SOURCE_INCOMPLETE"

    if target=="interest_missing" or target=="critical_extreme":
     interest_complete=bool(op and intr)
     if interest_complete:
      iv=float(intr["value"])
      if iv==0:
       item["interest_recoverability"]="SOURCE_COMPLETE_BUT_UNDEFINED"
       item["interest_undefined_reason"]="ZERO_REPORTED_INTEREST"
      else:
       item["interest_recoverability"]="SOURCE_COMPLETE_CALCULABLE"
       item["interest_candidate_value"]=float(op["value"])/iv
     else:
      item["interest_recoverability"]="SOURCE_INCOMPLETE"

   if target=="eps_growth_missing" or target=="critical_extreme":
    try:
     cur,_=resolver.search_filing(c,"eps",year=fy,limit=20)
     old,_=resolver.search_filing(c,"eps",year=fy-1,limit=20) if fy else ([],{})
     pairs=[]
     for cc in cur or []:
      cu=str(getattr(cc,"unit","") or "").strip().lower()
      cv=getattr(cc,"value",None)
      if cv is None or not cu: continue
      for oo in old or []:
       ou=str(getattr(oo,"unit","") or "").strip().lower()
       ov=getattr(oo,"value",None)
       if ov in (None,0) or ou!=cu: continue
       pairs.append((cc,oo))
     if pairs:
      cc,oo=pairs[0]
      growth=(float(cc.value)-float(oo.value))/abs(float(oo.value))*100.0
      item["eps_growth_recoverability"]="SOURCE_COMPLETE_CALCULABLE"
      item["eps_growth_candidate_value"]=growth
      item["eps_pair"]={"current":cc.compact(),"prior":oo.compact()}
     else:
      item["eps_growth_recoverability"]="SOURCE_INCOMPLETE"
    except Exception as exc:
     item["eps_growth_recoverability"]="SOURCE_ERROR"
     details["eps_growth"]=f"{type(exc).__name__}:{exc}"
   item.update({"companyfacts_prerequisites":cf,"filing_prerequisites":fi,"filing_errors":details})
   if target=="roic_missing": item["roic_filing_ready"]=item.get("roic_recoverability")=="SOURCE_COMPLETE_CALCULABLE"
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
 op_concept_freq=Counter()
 for r in results:
  for c in r.get("operating_like_candidates") or []:
   op_concept_freq[(c.get("concept") or "UNKNOWN", c.get("namespace") or "")]+=1
 summary={"generated_at":datetime.now(timezone.utc).isoformat(),"usable_rows":len(rows),"selected":len(selected),"plan":plan,
          "errors_by_stage":dict(errors),"target_counts":dict(target_counts),
          "operating_like_concepts":[{"concept":c[0],"namespace":c[1],"companies":n} for c,n in op_concept_freq.most_common(100)]}
 for k in ("roic_filing_ready","interest_filing_ready","eps_growth_filing_ready","companyfacts_roic_raw_ready","companyfacts_interest_raw_ready","companyfacts_eps_growth_raw_ready"):
  summary[k+"_count"]=sum(1 for r in results if r.get(k) is True)
 for metric,field in (("roic","roic_recoverability"),("interest","interest_recoverability"),("eps_growth","eps_growth_recoverability")):
  ctr=Counter(r.get(field) for r in results if r.get(field))
  summary[metric+"_recoverability"]=dict(ctr)
 out=OUT/"us_critical_raw_recovery_audit_v1.json"
 out.write_text(json.dumps({"summary":summary,"companies":results},indent=2,ensure_ascii=False),encoding="utf-8")
 print(json.dumps(summary,indent=2))
 print(f"[REPORT] {out}")

if __name__=="__main__": main()
