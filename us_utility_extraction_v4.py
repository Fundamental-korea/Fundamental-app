"""Utility SEC extraction v4: Company Facts + filing-level fallback facts."""
from __future__ import annotations
from datetime import datetime
import math

FLOW_FORMS={"10-K","10-K/A","20-F","20-F/A","40-F","40-F/A"}
REVENUE_TAGS=["RevenueFromContractWithCustomerExcludingAssessedTax","RevenueFromContractWithCustomerIncludingAssessedTax","RevenueFromContractsWithCustomers","RevenueFromContractsWithCustomer","Revenue","Revenues","RegulatedAndUnregulatedOperatingRevenue","RegulatedOperatingRevenue","OperatingRevenues","ElectricUtilityRevenue","ElectricUtilityOperatingRevenue","NaturalGasUtilityRevenue","NaturalGasUtilityOperatingRevenue","SalesRevenueNet","SalesRevenueGoodsNet"]
OPERATING_INCOME_TAGS=["OperatingIncomeLoss","ProfitLossFromOperatingActivities","OperatingIncomeLossFromContinuingOperations"]
NET_INCOME_TAGS=["NetIncomeLoss","ProfitLoss","ProfitLossAttributableToOwnersOfParent","NetIncomeLossAttributableToParent","NetIncomeLossAttributableToCommonStockholders"]
ASSETS_TAGS=["Assets"]
EQUITY_TAGS=["TotalProprietaryCapital","ProprietaryCapital","StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest","Equity","EquityAttributableToOwnersOfParent"]
INTEREST_TAGS=["InterestAndDebtExpense","InterestExpense","InterestExpenseBorrowings","InterestExpenseNonoperating","InterestExpenseNonOperating","InterestExpenseNonOperatingNet","InterestExpenseDebt","InterestExpenseNonOperatingAndOther","FinanceCosts","InterestExpenseOnBorrowings"]
INTEREST_FALLBACK_TAGS=["InterestPaidNet","InterestPaidClassifiedAsOperatingActivities"]
EPS_TAGS=["EarningsPerShareDiluted","EarningsPerShareBasic","EarningsPerShareBasicAndDiluted"]
EPS_NET_INCOME_TAGS=["NetIncomeLossAvailableToCommonStockholdersBasic","NetIncomeLossAttributableToCommonStockholders","NetIncomeLossAttributableToParent","ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntity","ProfitLossAttributableToOwnersOfParent","NetIncomeLoss","ProfitLoss"]
EPS_DILUTED_SHARE_TAGS=["WeightedAverageNumberOfDilutedSharesOutstanding","WeightedAverageNumberOfShareOutstandingBasicAndDiluted","WeightedAverageShares"]
OCF_TAGS=["NetCashProvidedByUsedInOperatingActivities","NetCashProvidedByUsedInOperatingActivitiesContinuingOperations","CashFlowsFromUsedInOperatingActivities"]
DEBT_CURRENT_TAGS=["LongTermDebtCurrent","LongTermDebtAndCapitalLeaseObligationsCurrent","DebtAndCapitalLeaseObligationsCurrent","CurrentBorrowings","CurrentPortionOfLongtermBorrowings"]
DEBT_NONCURRENT_TAGS=["LongTermDebtNoncurrent","LongTermDebtAndCapitalLeaseObligationsNoncurrent","NoncurrentBorrowings","LongtermBorrowings","Borrowings"]
DEBT_TOTAL_TAGS=["LongTermDebt","DebtAndCapitalLeaseObligations","LongTermDebtCurrentAndNoncurrent","DebtInstrumentCarryingAmount","LiabilitiesArisingFromFinancingActivities"]
CAPEX_TAGS=["PaymentsToAcquirePropertyPlantAndEquipment","PaymentsToAcquireProductiveAssets","PaymentsToAcquirePropertyPlantAndEquipmentAndOtherProductiveAssets","PaymentsToAcquirePropertyPlantAndEquipmentAndOtherProductiveAssetsNet","PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities","PaymentsForProceedsFromProductiveAssets"]
DIVIDEND_TAGS=["PaymentsOfDividendsCommonStockCash","DividendsCommonStockCash","PaymentsOfDividendsCommonStock","PaymentsOfOrdinaryDividends","DividendsPaid","PaymentsOfDividends","PaymentsOfDividendsMinorityInterest"]
NAMESPACE_PRIORITY={"us-gaap":3,"ifrs-full":2,"filing-xbrl":0}
FORM_PRIORITY={"10-K":4,"10-K/A":3,"20-F":2,"20-F/A":1,"40-F":2,"40-F/A":1}

def clean_number(v):
    try:
        x=float(v); return x if math.isfinite(x) else None
    except (TypeError,ValueError): return None

def _date(v):
    if not v:return None
    try:return datetime.fromisoformat(str(v)[:10]).date()
    except ValueError:return None

def _annual_row(r):
    end=_date(r.get("end")); val=clean_number(r.get("val"))
    if not end or val is None:return None
    dimension=bool(r.get("has_dimension"))
    if r.get("filing_annual"):
        return {"year":end.year,"val":val,"end":r.get("end"),"start":r.get("start"),"days":r.get("days"),"filed":"","form":"10-K","frame":None,"fy":None,"has_dimension":dimension,"source_tag":r.get("source_tag"),"label":r.get("label","")}
    if r.get("form") not in FLOW_FORMS:return None
    start=r.get("start")
    if start:
        s=_date(start)
        if not s:return None
        days=(end-s).days
        if not 300<=days<=380:return None
    else:
        frame=str(r.get("frame") or ""); fy=r.get("fy")
        if not ((frame.startswith("CY") and frame[2:].isdigit()) or fy is not None):return None
        days=None
    return {"year":end.year,"val":val,"end":r.get("end"),"start":start,"days":days,"filed":r.get("filed") or "","form":r.get("form"),"frame":r.get("frame"),"fy":r.get("fy"),"has_dimension":dimension,"source_tag":r.get("source_tag"),"label":r.get("label","")}

def _rows(facts,tag,instant=False):
    root=facts.get("facts",facts);out=[]
    for ns,nsfacts in root.items():
        fact=(nsfacts or {}).get(tag)
        if not fact:continue
        for unit,rows in (fact.get("units") or {}).items():
            if not isinstance(rows,list):continue
            for r in rows:
                if ns=="filing-xbrl" and not r.get("filing_annual"):continue
                if instant:
                    if ns!="filing-xbrl" and r.get("form") not in FLOW_FORMS:continue
                    end=_date(r.get("end"));val=clean_number(r.get("val"))
                    if not end or val is None:continue
                    row={"year":end.year,"val":val,"end":r.get("end"),"start":None,"days":None,"filed":r.get("filed") or "","form":r.get("form") or "10-K","frame":r.get("frame"),"fy":r.get("fy"),"has_dimension":bool(r.get("has_dimension")),"source_tag":r.get("source_tag"),"label":r.get("label","")}
                else:
                    row=_annual_row(r)
                    if row is None:continue
                row.update({"namespace":ns,"tag":tag,"unit":unit});out.append(row)
    return out

def _quality(row,tags,instant=False):
    days=row.get("days");annual=30 if instant or days is None or 340<=days<=370 else 0
    duration=10 if days is not None else 0;form=FORM_PRIORITY.get(row.get("form"),0)*2
    ns=NAMESPACE_PRIORITY.get(row.get("namespace"),0)*3;frame=1 if str(row.get("frame") or "").startswith("CY") else 0
    dimension=100 if row.get("namespace")=="filing-xbrl" and not row.get("has_dimension",False) else 0
    try:tag=len(tags)-tags.index(row.get("tag"))
    except ValueError:tag=0
    return (dimension+annual+duration+form+ns+frame+tag,row.get("filed") or "",row.get("end",""))

def _pick_best(facts,tags,year,instant=False):
    c=[r for tag in tags for r in _rows(facts,tag,instant) if r.get("year")==year]
    return max(c,key=lambda r:_quality(r,tags,instant)) if c else None

def _pick_prefer_primary(facts,tags,year,instant=False):
    c=[r for tag in tags for r in _rows(facts,tag,instant) if r.get("year")==year]
    primary=[r for r in c if r.get("namespace")!="filing-xbrl"]
    return max(primary,key=lambda r:_quality(r,tags,instant)) if primary else (max(c,key=lambda r:_quality(r,tags,instant)) if c else None)

def pick_flow(facts,tags,year):return _pick_prefer_primary(facts,tags,year,False)
def pick_instant(facts,tags,year):return _pick_prefer_primary(facts,tags,year,True)

def pick_equity(facts,year):
    """Select company-wide equity/proprietary capital, with TVA-style custom XBRL support."""
    exact_order=[
        "TotalProprietaryCapital",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "StockholdersEquity",
        "EquityAttributableToOwnersOfParent",
        "Equity",
        "ProprietaryCapital",
    ]
    # First let normal Company Facts win when it has a usable value.
    primary=[r for tag in exact_order for r in _rows(facts,tag,True) if r.get("year")==year and r.get("namespace")!="filing-xbrl" and r.get("val",0)>=0]
    if primary:
        return max(primary,key=lambda r:_quality(r,exact_order,True))
    # Filing fallback: rank by original concept and, critically, by the human
    # label so a custom fact labelled "Total proprietary capital" beats a
    # small component such as "Proprietary capital".
    filing=[r for tag in exact_order for r in _rows(facts,tag,True) if r.get("year")==year and r.get("namespace")=="filing-xbrl" and r.get("val",0)>=0]
    if not filing:return None
    def score(r):
        source=str(r.get("source_tag") or "").lower().replace("_","")
        label=str(r.get("label") or "").lower()
        exact=0
        if source=="totalproprietarycapital":exact=1000
        elif "totalproprietarycapital" in source:exact=900
        elif "total proprietary capital" in label:exact=950
        elif source=="stockholdersequityincludingportionattributabletononcontrollinginterest":exact=850
        elif source=="stockholdersequity":exact=800
        elif source=="equityattributabletoownersofparent":exact=750
        elif source=="equity":exact=700
        elif source=="proprietarycapital":exact=100
        return exact+_quality(r,exact_order,True)[0]
    return max(filing,key=score)

def pick_eps(facts,year):
    r=_pick_prefer_primary(facts,EPS_TAGS,year,False)
    if r:return r
    income=_pick_prefer_primary(facts,EPS_NET_INCOME_TAGS,year,False);shares=_pick_prefer_primary(facts,EPS_DILUTED_SHARE_TAGS,year,False)
    if income and shares and shares["val"]:
        return {**income,"val":income["val"]/shares["val"],"tag":"derived:net_income_attributable_to_common/weighted_diluted_shares","unit":"currency-per-share","derived":True}
    return None

def pick_interest(facts,year):
    r=_pick_prefer_primary(facts,INTEREST_TAGS,year,False)
    if r:return r
    r=_pick_prefer_primary(facts,INTEREST_FALLBACK_TAGS,year,False)
    return {**r,"interest_fallback":True} if r else None

def pick_debt(facts,year):
    cur=_pick_prefer_primary(facts,DEBT_CURRENT_TAGS,year,True);non=_pick_prefer_primary(facts,DEBT_NONCURRENT_TAGS,year,True)
    if cur is not None and cur["val"]<0:cur=None
    if non is not None and non["val"]<0:non=None
    filing_cur=_pick_best(facts,DEBT_CURRENT_TAGS,year,True) if cur is None else None
    filing_non=_pick_best(facts,DEBT_NONCURRENT_TAGS,year,True) if non is None else None
    if cur is None and filing_cur is not None and filing_cur["namespace"]=="filing-xbrl" and filing_cur["val"]>=0:cur=filing_cur
    if non is None and filing_non is not None and filing_non["namespace"]=="filing-xbrl" and filing_non["val"]>=0:non=filing_non
    if cur or non:return {"year":year,"val":(cur["val"] if cur else 0)+(non["val"] if non else 0),"current":cur,"noncurrent":non,"total":None,"method":"components"}
    total=_pick_prefer_primary(facts,DEBT_TOTAL_TAGS,year,True)
    if total:return {"year":year,"val":total["val"],"current":None,"noncurrent":None,"total":total,"method":"total_fallback"}
    return None

def pick_ocf(facts,year):return _pick_prefer_primary(facts,OCF_TAGS,year,False)
def pick_capex(facts,year):return _pick_prefer_primary(facts,CAPEX_TAGS,year,False)
def pick_dividend(facts,year):return _pick_prefer_primary(facts,DIVIDEND_TAGS,year,False)
def core_years(facts):
    rev={r["year"] for t in REVENUE_TAGS for r in _rows(facts,t)};op={r["year"] for t in OPERATING_INCOME_TAGS for r in _rows(facts,t)}
    return rev&op
