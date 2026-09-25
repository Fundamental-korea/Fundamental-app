"""Controlled filing-level raw-data coverage pilot.

Read-only and scoring-agnostic. For a small stratified sample, compare SEC
Company Facts coverage against the latest annual Inline XBRL filing.

This answers a key collection question:
    "Does the metric input really not exist, or is it merely absent from
     Company Facts and present in the filing?"

It also records filing-map selections for debt/interest/ROIC inputs without
writing any values to Supabase.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from supabase import create_client

from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8, _local_concept
from sec_filing_financial_map import classify_filing_rows

# Keep this pilot independent from collector_us_fundamental.py so its QA run
# does not pull the full scoring/market-data dependency chain.
FACT_ALIASES = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "operating_income": ["OperatingIncomeLoss", "OperatingIncome", "OperatingProfitLoss", "IncomeFromOperations"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "assets": ["Assets"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "Equity", "PartnersCapital", "MembersEquity", "EquityAttributableToOwnersOfParent"],
    "liabilities": ["Liabilities"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "inventory": ["InventoryNet", "InventoryGross"],
    "receivables": ["AccountsReceivableNetCurrent", "AccountsReceivableNet", "AccountsAndNotesReceivableNetCurrent", "AccountsReceivableGrossCurrent"],
    "debt_current": ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent", "CurrentBorrowings", "CurrentPortionOfLongtermBorrowings", "ShortTermBorrowings", "ShorttermBorrowings", "FinanceLeaseLiabilityCurrent", "ConvertibleDebtCurrent", "DebtCurrent", "NotesPayableCurrent", "NotesAndLoansPayableCurrent", "ShortTermBankLoansAndNotesPayable", "CommercialPaper", "LineOfCreditCurrent", "RevolvingCreditFacilityCurrent", "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings"],
    "debt_noncurrent": ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligationsNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent", "NoncurrentBorrowings", "LongtermBorrowings", "FinanceLeaseLiabilityNoncurrent", "ConvertibleDebtNoncurrent", "DebtNoncurrent", "NotesPayableNoncurrent", "NotesPayable", "LongTermNotesPayable", "LoansPayable", "LongTermLoansPayable", "OtherBorrowings", "UnsecuredDebt", "SecuredDebt", "OtherLongTermDebt", "FederalHomeLoanBankAdvances", "LongTermNotesAndLoans", "LineOfCreditNoncurrent", "RevolvingCreditFacilityNoncurrent"],
    "debt_total": ["Borrowings", "DebtLongtermAndShorttermCombinedAmount", "DebtAndCapitalLeaseObligations", "LongTermDebtCurrentAndNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebtAndFinanceLeaseObligations", "DebtAndFinanceLeaseLiabilities", "Debt"],
    "interest_expense": ["InterestExpenseNonoperating", "InterestExpenseNonOperating", "InterestExpenseDebt", "InterestExpenseNonoperatingAndOther", "InterestAndDebtExpense", "InterestExpense", "InterestExpenseOnDebtInstrumentsIssued", "InterestExpenseOnBorrowings", "InterestExpenseOnOtherFinancialLiabilities", "InterestExpenseOnBankLoansAndOverdrafts", "InterestExpenseOnBonds", "InterestExpenseLongTermDebt", "InterestExpenseShortTermBorrowings", "InterestExpenseOtherLongTermDebt", "InterestExpenseOtherShortTermBorrowings", "InterestExpenseSubordinatedNotesAndDebentures", "InterestCostsIncurred", "FinancingInterestExpense"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "sga": ["SellingGeneralAndAdministrativeExpense", "SellingGeneralAndAdministrativeExpenseIncludingDepreciationAmortization", "GeneralAndAdministrativeExpense", "SellingExpense"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}
IFRS_FACT_ALIASES = {
    "revenue": ["Revenue", "RevenueFromContractsWithCustomers"],
    "operating_income": ["ProfitLossFromOperatingActivities", "OperatingIncomeLoss"],
    "net_income": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "assets": ["Assets"],
    "equity": ["EquityAttributableToOwnersOfParent", "Equity"],
    "liabilities": ["Liabilities"],
    "current_assets": ["CurrentAssets"],
    "current_liabilities": ["CurrentLiabilities"],
    "cash": ["CashAndCashEquivalents"],
    "inventory": ["Inventories"],
    "receivables": ["TradeAndOtherReceivables", "TradeReceivables"],
    "debt_current": ["CurrentBorrowings", "CurrentPortionOfLongtermBorrowings", "ShorttermBorrowings", "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings", "LongTermDebtCurrent", "FinanceLeaseLiabilityCurrent"],
    "debt_noncurrent": ["LongtermBorrowings", "NoncurrentBorrowings", "LongTermDebtNoncurrent", "LongTermDebt", "LongTermNotesPayable", "FinanceLeaseLiabilityNoncurrent"],
    "debt_total": ["Borrowings", "LoansAndBorrowings", "DebtLongtermAndShorttermCombinedAmount", "LongTermDebtAndFinanceLeaseObligations", "LongTermDebtAndCapitalLeaseObligations"],
    "interest_expense": ["FinanceCosts", "InterestExpense", "InterestExpenseOnBorrowings", "InterestExpenseOnDebtInstrumentsIssued", "InterestExpenseOnOtherFinancialLiabilities", "InterestExpenseOnBankLoansAndOverdrafts", "InterestExpenseOnBonds", "InterestExpenseLongTermDebt", "InterestCostsIncurred"],
    "operating_cash_flow": ["CashFlowsFromUsedInOperatingActivities"],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
PAGE_SIZE = 1000
PER_BUCKET = 5

OUT=Path("artifacts"); OUT.mkdir(exist_ok=True)

# Same logical raw families used by the production collector.
FAMILIES = {
    "revenue","operating_income","net_income","assets","equity","liabilities",
    "current_assets","current_liabilities","cash","inventory","receivables",
    "debt_current","debt_noncurrent","debt_total","interest_expense",
    "operating_cash_flow","sga","eps",
}
INSTANT = {
    "assets","equity","liabilities","current_assets","current_liabilities",
    "cash","inventory","receivables","debt_current","debt_noncurrent","debt_total",
}
FLOW = FAMILIES - INSTANT


def cik10(v):
    return str(int(v)).zfill(10) if v is not None else ""

def stable(v):
    return int(hashlib.sha256(v.encode()).hexdigest()[:12],16)

def local(v):
    return _local_concept(v or "")

def fetch_eligible(sb):
    rows=[]; off=0
    while True:
        page=(sb.table("US_Companies")
              .select("ticker,cik,company_name,company_type,scoring_profile,is_fundamental_eligible")
              .eq("is_fundamental_eligible",True)
              .order("ticker")
              .range(off,off+PAGE_SIZE-1).execute().data or [])
        if not page: break
        rows.extend(page)
        if len(page)<PAGE_SIZE: break
        off += PAGE_SIZE
    return rows

def latest_annual_target(submissions):
    recent=(submissions or {}).get("filings",{}).get("recent",{})
    forms=recent.get("form") or []
    filed=recent.get("filingDate") or []
    fys=recent.get("fy") or []
    accs=recent.get("accessionNumber") or []
    docs=recent.get("primaryDocument") or []
    best=None
    for i,form in enumerate(forms):
        if form not in {"10-K","10-K/A","20-F","20-F/A","40-F","40-F/A"}: continue
        fy=fys[i] if i<len(fys) else None
        try: fy=int(fy) if fy is not None else None
        except: fy=None
        item=(filed[i] if i<len(filed) else "", fy, accs[i] if i<len(accs) else None, docs[i] if i<len(docs) else None, form)
        if item[2] and (best is None or item[0] > best[0]): best=item
    return best

def fact_presence(companyfacts):
    inv={}
    for ns, facts in ((companyfacts or {}).get("facts") or {}).items():
        inv[ns]=set(facts)
    out={}
    for fam in FAMILIES:
        aliases=[]
        for m in (FACT_ALIASES, IFRS_FACT_ALIASES):
            aliases.extend(m.get(fam,[]))
        out[fam]=bool(any(tag in inv.get(ns,set()) for ns in {"us-gaap","ifrs-full"} for tag in aliases))
    return out

def filing_presence(rows):
    out={}
    for fam in FAMILIES:
        aliases=set(FACT_ALIASES.get(fam,[])) | set(IFRS_FACT_ALIASES.get(fam,[]))
        matched=[]
        for r in rows:
            if local(r.get("concept")) in aliases:
                if r.get("dimensioned"): continue
                if fam in INSTANT:
                    if not r.get("instant"): continue
                else:
                    start,end=r.get("start"),r.get("end")
                    if not start or not end: continue
                    try:
                        days=(datetime.fromisoformat(end[:10]).date()-datetime.fromisoformat(start[:10]).date()).days
                    except Exception:
                        continue
                    if not 300 <= days <= 380: continue
                matched.append(r)
        out[fam]=bool(matched)
    return out

def main():
    if not SUPABASE_KEY: raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")
    sb=create_client(SUPABASE_URL,SUPABASE_KEY)
    companies=fetch_eligible(sb)
    resolver=SECXBRLSearchV2_3_8(user_agent=SEC_USER_AGENT)

    # Build strata from the single official SEC ticker/CIK/exchange file.
    # This avoids fetching 7,035 submissions just to choose a sample.
    strata=defaultdict(list)
    ticker_exchange_url="https://www.sec.gov/files/company_tickers_exchange.json"
    raw=resolver._get(ticker_exchange_url).json()
    fields=raw.get("fields") or []
    idx={name:i for i,name in enumerate(fields)}
    sec_pair={}
    for row in raw.get("data") or []:
        item={k:row[i] if i<len(row) else None for k,i in idx.items()}
        sec_pair[(str(item.get("ticker") or "").strip().upper(), cik10(item.get("cik")))] = item

    for company in companies:
        key=(str(company.get("ticker") or "").strip().upper(), cik10(company.get("cik")))
        sec_item=sec_pair.get(key) or {}
        ex=str(sec_item.get("exchange") or "").strip().upper()
        venue="OTC" if ex=="OTC" else "EXCHANGE_LISTED" if ex else "NO_SEC_EXCHANGE"
        strata[(company.get("scoring_profile") or "standard",venue)].append(company)

    selected=[]
    plan={}
    for key,rows in sorted(strata.items()):
        chosen=sorted(rows,key=lambda x:stable(x["ticker"]))[:min(PER_BUCKET,len(rows))]
        plan[f"{key[0]}|{key[1]}"]=len(chosen)
        for company in chosen:
            selected.append((company,None,None,key[1]))

    results=[]
    summary=Counter()
    fam_stats=defaultdict(lambda:Counter())
    profile_stats=defaultdict(lambda:Counter())
    source_errors=Counter()

    print(f"=== US FILING RAW COVERAGE PILOT v1 ===")
    print(f"eligible={len(companies)} sampled={len(selected)} strata={len(strata)}")

    for i,(c,sub,pre_error,venue) in enumerate(selected,1):
        ticker=c["ticker"]; cik=c["cik"]
        item={
            "ticker":ticker,"cik":cik10(cik),"company_name":c["company_name"],
            "scoring_profile":c.get("scoring_profile") or "standard",
            "company_type":c.get("company_type"),"venue":venue,
        }
        if pre_error:
            item["status"]=pre_error
            source_errors[pre_error]+=1; results.append(item); continue
        try:
            facts_error = None
            try:
                facts=resolver.company_facts(cik)
            except Exception as exc:
                facts={"facts":{}}
                facts_error=f"{type(exc).__name__}:{exc}"
            annual=latest_annual_target(sub)
            accession=(annual[2] if annual else None)
            fy=(annual[1] if annual else None)
            filing_rows, meta=resolver._inline_filing_rows(cik,sub)
            facts_p=fact_presence(facts)
            filing_p=filing_presence(filing_rows)
            gained={f:(not facts_p[f] and filing_p[f]) for f in FAMILIES}

            mapped={}
            if fy is not None:
                try:
                    mapped=classify_filing_rows(filing_rows,target_year=fy) or {}
                except Exception as exc:
                    mapped={"mapping_error":f"{type(exc).__name__}:{exc}"}

            item.update({
                "status":"OK",
                "latest_annual_fy":fy,
                "latest_annual_form":annual[4] if annual else None,
                "latest_annual_filed":annual[0] if annual else None,
                "latest_accession":accession,
                "companyfacts_error":facts_error,
                "inline_row_count":len(filing_rows),
                "companyfacts_present":facts_p,
                "filing_annual_candidate_present":filing_p,
                "filing_only_gain":gained,
                "filing_map_selected_debt":mapped.get("selected_debt"),
                "filing_map_selected_interest":mapped.get("selected_interest"),
                "filing_map_roic_inputs":mapped.get("roic_inputs"),
                "filing_map_metadata":{k:v for k,v in mapped.items() if k.endswith("status") or k.endswith("reason") or k.startswith("qa_")},
            })
            profile=c.get("scoring_profile") or "standard"
            profile_stats[profile]["ok"]+=1
            for f in FAMILIES:
                if facts_p[f]: fam_stats[(profile,f)]["companyfacts"]+=1
                if filing_p[f]: fam_stats[(profile,f)]["filing"]+=1
                if gained[f]: fam_stats[(profile,f)]["filing_only_gain"]+=1
            summary["OK"]+=1
        except Exception as exc:
            err=f"{type(exc).__name__}"
            item["status"]=f"FILING_ERROR:{err}"
            source_errors[err]+=1
            results.append(item)
            continue
        results.append(item)
        if i%10==0: print(f"[{i}/{len(selected)}] {ticker} venue={venue} profile={c.get('scoring_profile')} rows={item.get('inline_row_count')}")

    profile_summary={}
    for profile in sorted({(c.get("scoring_profile") or "standard") for c in companies}):
        ok=profile_stats[profile]["ok"]
        profile_summary[profile]={
            "sample_ok":ok,
            "families":{
                f:{
                    "companyfacts":fam_stats[(profile,f)]["companyfacts"],
                    "filing":fam_stats[(profile,f)]["filing"],
                    "filing_only_gain":fam_stats[(profile,f)]["filing_only_gain"],
                    "pct_filing_of_ok":round(100*fam_stats[(profile,f)]["filing"]/ok,1) if ok else 0,
                } for f in sorted(FAMILIES)
            }
        }

    report={
        "summary":{
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "eligible":len(companies),"sampled":len(selected),
            "strata":len(strata),"per_stratum":PER_BUCKET,
            "source":"SEC Company Facts + latest annual filing Inline XBRL",
            "source_errors":dict(source_errors),
            "sample_plan":plan,
            "profile_summary":profile_summary,
        },
        "companies":results,
    }
    out=OUT/"us_filing_raw_coverage_pilot_v1.json"
    out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(f"[REPORT] {out}")

if __name__=="__main__":
    main()
