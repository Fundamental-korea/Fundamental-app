"""Fast deficient-universe raw-source audit.

Read-only. It deliberately avoids loading the large period_scores JSON and only
uses compact DB fields needed to form a representative deficient sample.
"""
from __future__ import annotations
import hashlib,json,os
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
from supabase import create_client
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8,_local_concept

URL=os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
KEY=os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY","")
UA=os.environ.get("SEC_USER_AGENT","Fundamental-app contact@example.com")
OUT=Path("artifacts"); OUT.mkdir(exist_ok=True)
PAGE=1000; PER_STRATUM=3

ALIASES={
 "revenue":{"RevenueFromContractWithCustomerExcludingAssessedTax","RevenueFromContractWithCustomerIncludingAssessedTax","Revenues","SalesRevenueNet","SalesRevenueGoodsNet","Revenue","RevenueFromContractsWithCustomers"},
 "operating_income":{"OperatingIncomeLoss","OperatingIncome","OperatingProfitLoss","IncomeFromOperations","ProfitLossFromOperatingActivities"},
 "net_income":{"NetIncomeLoss","ProfitLoss","ProfitLossAttributableToOwnersOfParent"},
 "assets":{"Assets"},"equity":{"StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest","Equity","EquityAttributableToOwnersOfParent","ProprietaryCapital","PartnersCapital","MembersEquity"},
 "liabilities":{"Liabilities"},"current_assets":{"AssetsCurrent","CurrentAssets"},"current_liabilities":{"LiabilitiesCurrent","CurrentLiabilities"},
 "cash":{"CashAndCashEquivalentsAtCarryingValue","CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents","CashAndCashEquivalents","CashAndRestrictedCash"},
 "inventory":{"InventoryNet","InventoryGross","Inventories"},
 "receivables":{"AccountsReceivableNetCurrent","AccountsReceivableNet","AccountsNotesAndLoansReceivableNetCurrent","AccountsReceivableGrossCurrent","TradeAndOtherReceivables","TradeReceivables"},
 "debt":{"LongTermDebtCurrent","LongTermDebtAndCapitalLeaseObligationsCurrent","LongTermDebtAndFinanceLeaseObligationsCurrent","CurrentBorrowings","CurrentPortionOfLongtermBorrowings","ShortTermBorrowings","ShorttermBorrowings","FinanceLeaseLiabilityCurrent","ConvertibleDebtCurrent","DebtCurrent","NotesPayableCurrent","NotesAndLoansPayableCurrent","CommercialPaper","LineOfCreditCurrent","RevolvingCreditFacilityCurrent","LongTermDebtNoncurrent","LongTermDebt","LongTermDebtAndCapitalLeaseObligationsNoncurrent","LongTermDebtAndFinanceLeaseObligationsNoncurrent","NoncurrentBorrowings","LongtermBorrowings","FinanceLeaseLiabilityNoncurrent","ConvertibleDebtNoncurrent","DebtNoncurrent","NotesPayableNoncurrent","NotesPayable","LongTermNotesPayable","LoansPayable","LongTermLoansPayable","OtherBorrowings","UnsecuredDebt","SecuredDebt","OtherLongTermDebt","FederalHomeLoanBankAdvances","Borrowings","DebtLongtermAndShorttermCombinedAmount","DebtAndCapitalLeaseObligations","LongTermDebtCurrentAndNoncurrent","LongTermDebtAndCapitalLeaseObligations","LongTermDebtAndFinanceLeaseObligations","DebtAndFinanceLeaseLiabilities","Debt"},
 "interest_expense":{"InterestExpenseNonoperating","InterestExpenseNonOperating","InterestExpenseDebt","InterestExpenseNonoperatingAndOther","InterestAndDebtExpense","InterestExpense","InterestExpenseOnDebtInstrumentsIssued","InterestExpenseOnBorrowings","InterestExpenseOnOtherFinancialLiabilities","InterestExpenseOnBankLoansAndOverdrafts","InterestExpenseOnBonds","InterestExpenseLongTermDebt","InterestExpenseShortTermBorrowings","InterestExpenseOtherLongTermDebt","InterestExpenseOtherShortTermBorrowings","InterestExpenseSubordinatedNotesAndDebentures","InterestCostsIncurred","FinancingInterestExpense","FinanceCosts","InterestExpenseOnDebt"},
 "operating_cash_flow":{"NetCashProvidedByUsedInOperatingActivities","CashFlowsFromUsedInOperatingActivities"},
 "sga":{"SellingGeneralAndAdministrativeExpense","SellingGeneralAndAdministrativeExpenseIncludingDepreciationAmortization","GeneralAndAdministrativeExpense","SellingExpense"},
 "eps":{"EarningsPerShareDiluted","EarningsPerShareBasic"},
}
INSTANT={"assets","equity","liabilities","current_assets","current_liabilities","cash","inventory","receivables","debt"}

def cik(v): return str(int(v)).zfill(10)
def stable(v): return int(hashlib.sha256(v.encode()).hexdigest()[:12],16)
def norm(v): return (v or "").strip().upper()

def fetch_deficient(sb):
 rows=[]; off=0
 while True:
  p=(sb.table("US_Fundamental")
     .select("ticker,cik,company_name,base_year,missing_metric_count,data_reliability")
     .gt("missing_metric_count",0)
     .eq("data_unavailable",False)
     .order("ticker").range(off,off+PAGE-1).execute().data or [])
  if not p: break
  rows += p
  if len(p)<PAGE: break
  off += PAGE
 return rows

def venue_map(resolver):
 d=resolver._get("https://www.sec.gov/files/company_tickers_exchange.json").json()
 fields=d.get("fields") or []; idx={k:i for i,k in enumerate(fields)}
 out={}
 for row in d.get("data") or []:
  z={k:row[i] if i<len(row) else None for k,i in idx.items()}
  out[(norm(z.get("ticker")),cik(z.get("cik")))] = norm(z.get("exchange"))
 return out

def latest_annual(sub):
 r=(sub or {}).get("filings",{}).get("recent",{})
 best=None
 forms=r.get("form") or []; filed=r.get("filingDate") or []; fys=r.get("fy") or []
 for i,form in enumerate(forms):
  if form not in {"10-K","10-K/A","20-F","20-F/A","40-F","40-F/A"}: continue
  fy=fys[i] if i<len(fys) else None
  try: fy=int(fy) if fy is not None else None
  except: fy=None
  item=(filed[i] if i<len(filed) else "",fy,form)
  if best is None or item[0]>(best[0] or ""): best=item
 return best

def fact_presence(doc,year):
 inv={}
 for ns,f in ((doc or {}).get("facts") or {}).items(): inv[ns]=set(f)
 out={}
 for fam,tags in ALIASES.items():
  out[fam]=any(tag in inv.get("us-gaap",set()) or tag in inv.get("ifrs-full",set()) for tag in tags)
 return out

def filing_presence(rows,year):
 out={}
 for fam,tags in ALIASES.items():
  hit=False
  for r in rows:
   if r.get("dimensioned") or _local_concept(r.get("concept") or "") not in tags: continue
   end=str(r.get("end") or "")
   if year and not end.startswith(str(year)): continue
   if fam in INSTANT:
    if r.get("instant"): hit=True; break
   else:
    start=r.get("start")
    if not start: continue
    try:
     days=(datetime.fromisoformat(end[:10]).date()-datetime.fromisoformat(start[:10]).date()).days
    except: continue
    if 300<=days<=380: hit=True; break
  out[fam]=hit
 return out

def main():
 if not KEY: raise RuntimeError("Supabase key required")
 sb=create_client(URL,KEY); resolver=SECXBRLSearchV2_3_8(user_agent=UA)
 db=fetch_deficient(sb); venues=venue_map(resolver)
 strata=defaultdict(list)
 for row in db:
  ex=venues.get((norm(row["ticker"]),cik(row["cik"])),"")
  venue="OTC" if ex=="OTC" else "EXCHANGE_LISTED" if ex else "NO_SEC_EXCHANGE"
  strata[((row.get("data_reliability") or "unknown"),venue)].append(row)
 selected=[];plan={}
 for key,rows in sorted(strata.items()):
  chosen=sorted(rows,key=lambda x:stable(x["ticker"]))[:PER_STRATUM]
  plan[f"{key[0]}|{key[1]}"]=len(chosen); selected += [(r,key[1]) for r in chosen]
 print(f"=== US DEFICIENT RAW SOURCE AUDIT v1 === deficient={len(db)} sampled={len(selected)} strata={len(strata)}")
 results=[]; errors=Counter(); gains=Counter()
 for i,(row,venue) in enumerate(selected,1):
  item={"ticker":row["ticker"],"cik":cik(row["cik"]),"company_name":row["company_name"],"base_year":row.get("base_year"),
        "missing_metric_count":row.get("missing_metric_count"),"data_reliability":row.get("data_reliability"),"venue":venue}
  try:
   sub=resolver.submissions(row["cik"]); a=latest_annual(sub); fy=a[1] if a else row.get("base_year")
   facts=resolver.company_facts(row["cik"])
   fp,meta=resolver._inline_filing_rows(row["cik"],sub)
   cf=fact_presence(facts,fy); filing=filing_presence(fp,fy)
   gain={f:(not cf[f] and filing[f]) for f in ALIASES}
   for f,v in gain.items(): gains[f]+=int(v)
   item.update({"status":"OK","latest_annual_fy":fy,"latest_annual_form":a[2] if a else None,
                "latest_annual_filed":a[0] if a else None,"companyfacts_present":cf,
                "filing_annual_present":filing,"filing_only_gain":gain,
                "inline_rows":len(fp),"inline_meta":meta})
  except Exception as exc:
   errors[type(exc).__name__]+=1; item.update({"status":"ERROR","error":f"{type(exc).__name__}:{exc}"})
  results.append(item); print(f"[{i}/{len(selected)}] {row['ticker']} {item['status']}")
 out=OUT/"us_deficient_raw_source_audit_v1.json"
 report={"summary":{"generated_at":datetime.now(timezone.utc).isoformat(),"deficient":len(db),"sampled":len(selected),"per_stratum":PER_STRATUM,
          "strata":len(strata),"plan":plan,"errors":dict(errors),"filing_only_gain":dict(gains)},"companies":results}
 out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
 print(f"[REPORT] {out}")
if __name__=="__main__": main()
