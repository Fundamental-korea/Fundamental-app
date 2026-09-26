"""Small filing-vs-Company-Facts coverage pilot.

Read-only. Does not import the production collector or scoring stack and does
not write to Supabase. It samples the current eligible universe by profile and
SEC venue, then checks whether annual raw metric families are visible in:
1) SEC Company Facts
2) the latest annual Inline XBRL filing

The key output is the filing-only recovery opportunity.
"""
from __future__ import annotations
import hashlib, json, os, re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from supabase import create_client
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8, _local_concept

URL=os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
KEY=os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY","")
UA=os.environ.get("SEC_USER_AGENT","Fundamental-app contact@example.com")
OUT=Path("artifacts"); OUT.mkdir(exist_ok=True)
PAGE=1000; PER_STRATUM=3

ALIASES={
"revenue":{"RevenueFromContractWithCustomerExcludingAssessedTax","RevenueFromContractWithCustomerIncludingAssessedTax","Revenues","SalesRevenueNet","SalesRevenueGoodsNet","Revenue","RevenueFromContractsWithCustomers"},
"operating_income":{"OperatingIncomeLoss","OperatingIncome","OperatingProfitLoss","IncomeFromOperations","ProfitLossFromOperatingActivities"},
"net_income":{"NetIncomeLoss","ProfitLoss","ProfitLossAttributableToOwnersOfParent"},
"assets":{"Assets"},
"equity":{"StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest","Equity","EquityAttributableToOwnersOfParent","ProprietaryCapital","PartnersCapital","MembersEquity"},
"liabilities":{"Liabilities"},
"current_assets":{"AssetsCurrent","CurrentAssets"},
"current_liabilities":{"LiabilitiesCurrent","CurrentLiabilities"},
"cash":{"CashAndCashEquivalentsAtCarryingValue","CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents","CashAndCashEquivalents","CashAndRestrictedCash"},
"inventory":{"InventoryNet","InventoryGross","Inventories"},
"receivables":{"AccountsReceivableNetCurrent","AccountsReceivableNet","AccountsNotesAndLoansReceivableNetCurrent","AccountsReceivableGrossCurrent","TradeAndOtherReceivables","TradeReceivables"},
"debt":{"LongTermDebtCurrent","LongTermDebtAndCapitalLeaseObligationsCurrent","LongTermDebtAndFinanceLeaseObligationsCurrent","CurrentBorrowings","CurrentPortionOfLongtermBorrowings","ShortTermBorrowings","ShorttermBorrowings","FinanceLeaseLiabilityCurrent","ConvertibleDebtCurrent","DebtCurrent","NotesPayableCurrent","NotesAndLoansPayableCurrent","CommercialPaper","LineOfCreditCurrent","RevolvingCreditFacilityCurrent","LongTermDebtNoncurrent","LongTermDebt","LongTermDebtAndCapitalLeaseObligationsNoncurrent","LongTermDebtAndFinanceLeaseObligationsNoncurrent","NoncurrentBorrowings","LongtermBorrowings","FinanceLeaseLiabilityNoncurrent","ConvertibleDebtNoncurrent","DebtNoncurrent","NotesPayableNoncurrent","NotesPayable","LongTermNotesPayable","LoansPayable","LongTermLoansPayable","OtherBorrowings","UnsecuredDebt","SecuredDebt","OtherLongTermDebt","FederalHomeLoanBankAdvances","LongTermNotesAndLoans","Borrowings","DebtLongtermAndShorttermCombinedAmount","DebtAndCapitalLeaseObligations","LongTermDebtCurrentAndNoncurrent","LongTermDebtAndCapitalLeaseObligations","LongTermDebtAndFinanceLeaseObligations","DebtAndFinanceLeaseLiabilities","Debt"},
"interest_expense":{"InterestExpenseNonoperating","InterestExpenseNonOperating","InterestExpenseDebt","InterestExpenseNonoperatingAndOther","InterestAndDebtExpense","InterestExpense","InterestExpenseOnDebtInstrumentsIssued","InterestExpenseOnBorrowings","InterestExpenseOnOtherFinancialLiabilities","InterestExpenseOnBankLoansAndOverdrafts","InterestExpenseOnBonds","InterestExpenseLongTermDebt","InterestExpenseShortTermBorrowings","InterestExpenseOtherLongTermDebt","InterestExpenseOtherShortTermBorrowings","InterestExpenseSubordinatedNotesAndDebentures","InterestCostsIncurred","FinancingInterestExpense","FinanceCosts","InterestExpenseOnDebt"},
"operating_cash_flow":{"NetCashProvidedByUsedInOperatingActivities","CashFlowsFromUsedInOperatingActivities"},
"sga":{"SellingGeneralAndAdministrativeExpense","SellingGeneralAndAdministrativeExpenseIncludingDepreciationAmortization","GeneralAndAdministrativeExpense","SellingExpense"},
"eps":{"EarningsPerShareDiluted","EarningsPerShareBasic"},
}
INSTANT={"assets","equity","liabilities","current_assets","current_liabilities","cash","inventory","receivables","debt"}
FLOW=set(ALIASES)-INSTANT
FORMS={"10-K","10-K/A","10-Q","10-Q/A","20-F","20-F/A","40-F","40-F/A"}

def norm(v): return (v or "").strip().upper()
def cik(v): return str(int(v)).zfill(10)
def stable(v): return int(hashlib.sha256(v.encode()).hexdigest()[:12],16)

def fetch_all(sb):
 r=[];o=0
 while True:
  p=(sb.table("US_Companies").select("ticker,cik,company_name,company_type,scoring_profile,is_fundamental_eligible")
     .eq("is_fundamental_eligible",True).order("ticker").range(o,o+PAGE-1).execute().data or [])
  if not p: break
  r+=p
  if len(p)<PAGE: break
  o+=PAGE
 return r

def presence_facts(doc):
 inv={}
 for ns,facts in ((doc or {}).get("facts") or {}).items(): inv[ns]=set(facts)
 return {k: any(x in inv.get("us-gaap",set()) or x in inv.get("ifrs-full",set()) for x in tags) for k,tags in ALIASES.items()}

def recent_filing(sub):
 recent=(sub or {}).get("filings",{}).get("recent",{})
 best=None
 for i,form in enumerate(recent.get("form") or []):
  if form not in {"10-K","10-K/A","20-F","20-F/A","40-F","40-F/A"}: continue
  filed=(recent.get("filingDate") or [""])[i]
  item=(filed,recent.get("accessionNumber",[None])[i],recent.get("primaryDocument",[None])[i],recent.get("fy",[None])[i],form)
  if item[1] and (best is None or item[0]>(best[0] or "")): best=item
 return best

def presence_filing(rows):
 out={}
 for fam,tags in ALIASES.items():
  ok=False
  for r in rows:
   if r.get("dimensioned"): continue
   if _local_concept(r.get("concept") or "") not in tags: continue
   if fam in INSTANT:
    if r.get("instant"): ok=True; break
   else:
    if not r.get("start") or not r.get("end"): continue
    try:
     d=(datetime.fromisoformat(r["end"][:10]).date()-datetime.fromisoformat(r["start"][:10]).date()).days
    except Exception: continue
    if 300<=d<=380: ok=True; break
  out[fam]=ok
 return out

def main():
 if not KEY: raise RuntimeError("Supabase key required")
 sb=create_client(URL,KEY)
 companies=fetch_all(sb)
 resolver=SECXBRLSearchV2_3_8(user_agent=UA)

 # Venue strata from the SEC ticker/CIK/exchange file (one request only).
 sec=resolver._get("https://www.sec.gov/files/company_tickers_exchange.json").json()
 fields=sec.get("fields") or []; idx={k:i for i,k in enumerate(fields)}
 pairs={(norm((r:= {k:row[i] if i<len(row) else None for k,i in idx.items()}).get("ticker")),cik(r.get("cik"))):r for row in sec.get("data") or []}
 strata=defaultdict(list)
 for c in companies:
  s=pairs.get((norm(c["ticker"]),cik(c["cik"]))) or {}
  ex=norm(s.get("exchange"))
  venue="OTC" if ex=="OTC" else "EXCHANGE_LISTED" if ex else "NO_SEC_EXCHANGE"
  strata[(c.get("scoring_profile") or "standard",venue)].append(c)

 selected=[]
 plan={}
 for key,rows in sorted(strata.items()):
  chosen=sorted(rows,key=lambda x:stable(x["ticker"]))[:PER_STRATUM]
  plan[f"{key[0]}|{key[1]}"]=len(chosen)
  selected += [(c,key[1]) for c in chosen]

 results=[]; err=Counter(); prof=defaultdict(lambda:Counter()); gains=Counter()
 print(f"=== US FILING PRESENCE PILOT v2 === eligible={len(companies)} sampled={len(selected)}")
 for i,(c,venue) in enumerate(selected,1):
  t=c["ticker"]; profile=c.get("scoring_profile") or "standard"; stage="submissions"
  item={"ticker":t,"cik":cik(c["cik"]),"company_name":c["company_name"],"profile":profile,"venue":venue}
  try:
   sub=resolver.submissions(c["cik"]); stage="companyfacts"
   try: facts=resolver.company_facts(c["cik"]); facts_error=None
   except Exception as exc: facts={"facts":{}}; facts_error=f"{type(exc).__name__}:{exc}"
   stage="filing"
   filing_rows,meta=resolver._inline_filing_rows(c["cik"],sub)
   fp=presence_filing(filing_rows); cp=presence_facts(facts)
   gain={f:(not cp[f] and fp[f]) for f in ALIASES}
   prof[profile]["sample"]+=1
   prof[profile]["facts_errors"] += bool(facts_error)
   for f in ALIASES:
    prof[profile][f+"_facts"] += int(cp[f]); prof[profile][f+"_filing"] += int(fp[f]); prof[profile][f+"_gain"] += int(gain[f])
    gains[f]+=int(gain[f])
   item.update({"status":"OK","facts_error":facts_error,"latest_annual":recent_filing(sub),
                "inline_row_count":len(filing_rows),"companyfacts_present":cp,
                "filing_annual_present":fp,"filing_only_gain":gain,
                "inline_accession":meta.get("accession"),"inline_filed":meta.get("filed"),
                "inline_primary_document":meta.get("primary_document")})
  except Exception as exc:
   msg=f"{type(exc).__name__}:{exc}"
   err[stage]+=1; item.update({"status":"ERROR","stage":stage,"error":msg})
  results.append(item)
  print(f"[{i}/{len(selected)}] {t} {item['status']} stage={item.get('stage','ok')}")
 summary={"generated_at":datetime.now(timezone.utc).isoformat(),"eligible":len(companies),"sampled":len(selected),
          "per_stratum":PER_STRATUM,"strata":len(strata),"sample_plan":plan,
          "errors_by_stage":dict(err),"filing_only_gain":dict(gains)}
 profiles={}
 for p in sorted(prof):
  d=prof[p]; n=d["sample"]
  profiles[p]={"sample":n,"facts_errors":d["facts_errors"],
   "families":{f:{"companyfacts":d[f+"_facts"],"filing":d[f+"_filing"],"filing_only_gain":d[f+"_gain"],
                    "filing_pct":round(100*d[f+"_filing"]/n,1) if n else 0} for f in sorted(ALIASES)}}
 summary["profile_summary"]=profiles
 out=OUT/"us_filing_presence_pilot_v2.json"; out.write_text(json.dumps({"summary":summary,"companies":results},indent=2,ensure_ascii=False),encoding="utf-8")
 print(f"[REPORT] {out}")
if __name__=="__main__": main()
