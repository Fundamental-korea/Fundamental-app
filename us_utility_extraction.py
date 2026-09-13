"""Utility-specific SEC extraction v3.1.

Normalizes annual utility facts across US-GAAP and IFRS by collecting all
candidate concepts first and ranking candidates independently for each year.
Raw SEC JSON is never persisted.
"""
from __future__ import annotations

from datetime import datetime
import math

FLOW_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "RevenueFromContractsWithCustomers",
    "RevenueFromContractsWithCustomer",
    "Revenue",
    "Revenues",
    "RegulatedAndUnregulatedOperatingRevenue",
    "RegulatedOperatingRevenue",
    "OperatingRevenues",
    "ElectricUtilityRevenue",
    "ElectricUtilityOperatingRevenue",
    "NaturalGasUtilityRevenue",
    "NaturalGasUtilityOperatingRevenue",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
]
OPERATING_INCOME_TAGS = [
    "OperatingIncomeLoss",
    "ProfitLossFromOperatingActivities",
    "OperatingIncomeLossFromContinuingOperations",
]
NET_INCOME_TAGS = [
    "NetIncomeLoss",
    "ProfitLoss",
    "ProfitLossAttributableToOwnersOfParent",
    "NetIncomeLossAttributableToParent",
]
ASSETS_TAGS = ["Assets"]
EQUITY_TAGS = [
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "Equity",
    "EquityAttributableToOwnersOfParent",
]

INTEREST_TAGS = [
    "InterestAndDebtExpense", "InterestExpense", "InterestExpenseBorrowings",
    "InterestExpenseNonoperating", "InterestExpenseNonOperating",
    "InterestExpenseNonOperatingNet", "InterestExpenseDebt",
    "InterestExpenseNonOperatingAndOther", "FinanceCosts",
    "InterestExpenseOnBorrowings",
]
INTEREST_FALLBACK_TAGS = ["InterestPaidNet", "InterestPaidClassifiedAsOperatingActivities"]

EPS_TAGS = [
    "EarningsPerShareDiluted", "EarningsPerShareBasic",
    "EarningsPerShareBasicAndDiluted",
]
EPS_NET_INCOME_TAGS = [
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "NetIncomeLossAttributableToCommonStockholders",
    "NetIncomeLossAttributableToParent",
    "ProfitLossAttributableToOrdinaryEquityHoldersOfParentEntity",
    "ProfitLossAttributableToOwnersOfParent", "NetIncomeLoss", "ProfitLoss",
]
EPS_DILUTED_SHARE_TAGS = [
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
    "WeightedAverageShares",
]

OCF_TAGS = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    "CashFlowsFromUsedInOperatingActivities",
]

# Debt v3.1: current/non-current components are preferred over ambiguous total
# concepts. The total list is fallback-only to avoid accidentally selecting a
# debt-like subtotal that is not comparable with equity.
DEBT_CURRENT_TAGS = [
    "LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "DebtAndCapitalLeaseObligationsCurrent", "CurrentBorrowings",
    "CurrentPortionOfLongtermBorrowings",
]
DEBT_NONCURRENT_TAGS = [
    "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
    "NoncurrentBorrowings", "LongtermBorrowings", "Borrowings",
]
DEBT_TOTAL_TAGS = [
    "LongTermDebt", "DebtAndCapitalLeaseObligations",
    "LongTermDebtCurrentAndNoncurrent", "DebtInstrumentCarryingAmount",
    "LiabilitiesArisingFromFinancingActivities",
]

CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndOtherProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndOtherProductiveAssetsNet",
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    "PaymentsForProceedsFromProductiveAssets",
]

# Dividend v3.1: prefer explicit common-stock cash distributions. Generic
# dividend concepts are fallback-only because they may include non-common or
# special distributions with a different economic meaning.
DIVIDEND_TAGS = [
    "PaymentsOfDividendsCommonStockCash",
    "DividendsCommonStockCash",
    "PaymentsOfDividendsCommonStock",
    "PaymentsOfOrdinaryDividends",
    "DividendsPaid",
    "PaymentsOfDividends",
    "PaymentsOfDividendsMinorityInterest",
]

NAMESPACE_PRIORITY = {"us-gaap": 2, "ifrs-full": 1}
FORM_PRIORITY = {"10-K": 4, "10-K/A": 3, "20-F": 2, "20-F/A": 1, "40-F": 2, "40-F/A": 1}


def clean_number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None


def _annual_row(r):
    form, end = r.get("form"), r.get("end")
    if form not in FLOW_FORMS or not end:
        return None
    end_date = _date(end)
    if end_date is None:
        return None
    start = r.get("start")
    days = None
    if start:
        start_date = _date(start)
        if start_date is None:
            return None
        days = (end_date - start_date).days
        if not 300 <= days <= 380:
            return None
    else:
        frame = str(r.get("frame") or "")
        fy = r.get("fy")
        if not ((frame.startswith("CY") and frame[2:].isdigit()) or fy is not None):
            return None
    value = clean_number(r.get("val"))
    if value is None:
        return None
    return {"year": end_date.year, "val": value, "end": end, "start": start,
            "days": days, "filed": r.get("filed") or "", "form": form,
            "frame": r.get("frame"), "fy": r.get("fy")}


def _rows(facts, tag, instant=False):
    root = facts.get("facts", facts)
    out = []
    for namespace in ("us-gaap", "ifrs-full"):
        fact = (root.get(namespace) or {}).get(tag)
        if not fact:
            continue
        for unit, rows in (fact.get("units") or {}).items():
            if not isinstance(rows, list):
                continue
            for r in rows:
                if instant:
                    if r.get("form") not in FLOW_FORMS or not r.get("end"):
                        continue
                    end_date = _date(r.get("end")); value = clean_number(r.get("val"))
                    if end_date is None or value is None:
                        continue
                    row = {"year": end_date.year, "val": value, "end": r["end"],
                           "start": None, "days": None, "filed": r.get("filed") or "",
                           "form": r.get("form"), "frame": r.get("frame"), "fy": r.get("fy")}
                else:
                    row = _annual_row(r)
                    if row is None:
                        continue
                row.update({"namespace": namespace, "tag": tag, "unit": unit})
                out.append(row)
    return out


def _tag_rank(tags, tag):
    try:
        return len(tags) - tags.index(tag)
    except ValueError:
        return 0


def _quality(row, tags, instant=False):
    days = row.get("days")
    annual_bonus = 30 if instant or days is None or 340 <= days <= 370 else 0
    duration_bonus = 10 if days is not None else 0
    form_bonus = FORM_PRIORITY.get(row.get("form"), 0) * 2
    namespace_bonus = NAMESPACE_PRIORITY.get(row.get("namespace"), 0) * 3
    frame_bonus = 1 if str(row.get("frame") or "").startswith("CY") else 0
    tag_bonus = _tag_rank(tags, row.get("tag"))
    return (annual_bonus + duration_bonus + form_bonus + namespace_bonus + frame_bonus + tag_bonus,
            row.get("filed") or "", row.get("end", ""))


def _pick_best(facts, tags, year, instant=False):
    candidates = [r for tag in tags for r in _rows(facts, tag, instant=instant) if r.get("year") == year]
    return max(candidates, key=lambda r: _quality(r, tags, instant=instant)) if candidates else None


def pick_flow(facts, tags, year):
    return _pick_best(facts, tags, year, instant=False)


def pick_instant(facts, tags, year):
    return _pick_best(facts, tags, year, instant=True)


def pick_eps(facts, year):
    direct = pick_flow(facts, EPS_TAGS, year)
    if direct:
        return direct
    income = pick_flow(facts, EPS_NET_INCOME_TAGS, year)
    shares = pick_flow(facts, EPS_DILUTED_SHARE_TAGS, year)
    if income and shares and shares["val"] != 0:
        return {"year": year, "val": income["val"] / shares["val"],
                "end": income.get("end"), "start": income.get("start"),
                "days": income.get("days"), "filed": income.get("filed", ""),
                "form": income.get("form"), "frame": income.get("frame"),
                "fy": income.get("fy"), "namespace": income.get("namespace"),
                "tag": "derived:net_income_attributable_to_common/weighted_diluted_shares",
                "unit": "currency-per-share", "derived": True}
    return None


def pick_interest(facts, year):
    row = pick_flow(facts, INTEREST_TAGS, year)
    if row:
        return row
    row = pick_flow(facts, INTEREST_FALLBACK_TAGS, year)
    if row:
        row = dict(row); row["interest_fallback"] = True
    return row


def pick_debt(facts, year):
    current = pick_instant(facts, DEBT_CURRENT_TAGS, year)
    noncurrent = pick_instant(facts, DEBT_NONCURRENT_TAGS, year)
    if current or noncurrent:
        current_value = current["val"] if current else 0.0
        noncurrent_value = noncurrent["val"] if noncurrent else 0.0
        return {"year": year, "val": current_value + noncurrent_value,
                "current": current, "noncurrent": noncurrent, "total": None,
                "method": "components"}
    total = pick_instant(facts, DEBT_TOTAL_TAGS, year)
    if total:
        return {"year": year, "val": total["val"], "current": None,
                "noncurrent": None, "total": total, "method": "total_fallback"}
    return None


def pick_ocf(facts, year):
    return pick_flow(facts, OCF_TAGS, year)


def pick_capex(facts, year):
    return pick_flow(facts, CAPEX_TAGS, year)


def pick_dividend(facts, year):
    return pick_flow(facts, DIVIDEND_TAGS, year)


def core_years(facts):
    revenue_years = {r["year"] for tag in REVENUE_TAGS for r in _rows(facts, tag)}
    operating_years = {r["year"] for tag in OPERATING_INCOME_TAGS for r in _rows(facts, tag)}
    return revenue_years & operating_years
