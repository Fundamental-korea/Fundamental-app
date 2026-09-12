"""Utility-specific SEC extraction helpers.

These helpers are intentionally separate from the production scorer until the
utility dry-run passes. They handle SEC annual facts that are sometimes tagged
without a start date (notably EPS and some interest facts) by accepting an
annual CY frame on a 10-K/20-F/40-F row. They never persist raw SEC JSON.
"""
from __future__ import annotations

from datetime import datetime
import math

FLOW_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "RegulatedOperatingRevenue",
    "ElectricUtilityRevenue",
    "ElectricUtilityOperatingRevenue",
    "NaturalGasUtilityRevenue",
    "NaturalGasUtilityOperatingRevenue",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
]

# SEC concept names are case-sensitive. Keep common capitalization variants.
# InterestAndDebtExpense / InterestExpenseBorrowings are especially important
# for utilities such as D, SO and EXC where generic InterestExpense is absent.
INTEREST_TAGS = [
    "InterestExpense",
    "InterestExpenseBorrowings",
    "InterestAndDebtExpense",
    "InterestExpenseNonoperating",
    "InterestExpenseNonOperating",
    "InterestExpenseNonOperatingNet",
    "InterestExpenseDebt",
    "InterestExpenseNonOperatingAndOther",
    "FinanceCosts",
]

# InterestPaidNet is a cash-interest fallback only. It is intentionally last:
# it is not identical to P&L interest expense and should not outrank expense tags.
INTEREST_FALLBACK_TAGS = ["InterestPaidNet"]

EPS_TAGS = [
    "EarningsPerShareDiluted",
    "EarningsPerShareBasic",
    "EarningsPerShareBasicAndDiluted",
]

EPS_NET_INCOME_TAGS = [
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "NetIncomeLossAttributableToParent",
    "ProfitLossAttributableToOwnersOfParent",
    "NetIncomeLoss",
    "ProfitLoss",
]

EPS_DILUTED_SHARE_TAGS = [
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
]

OCF_TAGS = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    "CashFlowsFromUsedInOperatingActivities",
]

DEBT_CURRENT_TAGS = [
    "LongTermDebtCurrent",
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "DebtAndCapitalLeaseObligationsCurrent",
]

DEBT_NONCURRENT_TAGS = [
    "LongTermDebtNoncurrent",
    "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
    "DebtAndCapitalLeaseObligationsNoncurrent",
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebt",
]

DEBT_TOTAL_TAGS = [
    "DebtAndCapitalLeaseObligations",
    "LongTermDebtCurrentAndNoncurrent",
    "DebtInstrumentCarryingAmount",
]

# Cash capex only. PaymentsForProceedsFromProductiveAssets is a net productive-
# asset cash-flow concept (purchases less proceeds), so it is a safe last-resort
# proxy when a utility does not expose a dedicated capex concept.
CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndOtherProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndOtherProductiveAssetsNet",
    "PaymentsForProceedsFromProductiveAssets",
]

DIVIDEND_TAGS = [
    "DividendsCommonStockCash",
    "PaymentsOfDividendsCommonStockCash",
    "PaymentsOfDividendsCommonStock",
    "PaymentsOfOrdinaryDividends",
    "PaymentsOfDividends",
    "PaymentsOfDividendsMinorityInterest",
]


def clean_number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _annual_row(r):
    """Return normalized annual metadata or None."""
    form, end = r.get("form"), r.get("end")
    if form not in FLOW_FORMS or not end:
        return None
    try:
        end_date = datetime.fromisoformat(end).date()
    except ValueError:
        return None

    start = r.get("start")
    if start:
        try:
            days = (end_date - datetime.fromisoformat(start).date()).days
        except ValueError:
            return None
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
    return {
        "year": end_date.year,
        "val": value,
        "end": end,
        "start": start,
        "filed": r.get("filed") or "",
        "form": form,
        "frame": r.get("frame"),
        "fy": r.get("fy"),
    }


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
                    try:
                        year = datetime.fromisoformat(r["end"]).date().year
                    except ValueError:
                        continue
                    value = clean_number(r.get("val"))
                    if value is None:
                        continue
                    row = {
                        "year": year,
                        "val": value,
                        "end": r["end"],
                        "start": None,
                        "filed": r.get("filed") or "",
                        "form": r.get("form"),
                        "frame": r.get("frame"),
                        "fy": r.get("fy"),
                    }
                else:
                    row = _annual_row(r)
                    if row is None:
                        continue
                row.update({"namespace": namespace, "tag": tag, "unit": unit})
                out.append(row)
    return out


def _dedupe(rows):
    by_year = {}
    for row in rows:
        key = (
            row.get("end", ""),
            row.get("filed", ""),
            0 if str(row.get("form", "")).endswith("/A") else 1,
            1 if str(row.get("frame", "")).startswith("CY") else 0,
        )
        prev = by_year.get(row["year"])
        if prev is None or key > prev[0]:
            by_year[row["year"]] = (key, row)
    return {year: row for year, (_, row) in by_year.items()}


def pick_flow(facts, tags, year):
    for tag in tags:
        row = _dedupe(_rows(facts, tag)).get(year)
        if row:
            return row
    return None


def pick_instant(facts, tags, year):
    for tag in tags:
        row = _dedupe(_rows(facts, tag, instant=True)).get(year)
        if row:
            return row
    return None


def pick_eps(facts, year):
    direct = pick_flow(facts, EPS_TAGS, year)
    if direct:
        return direct

    # Some large utilities expose annual EPS only in filing tables while the
    # company-facts concept is sparse. A GAAP fallback can reconstruct diluted
    # EPS from attributable net income and weighted diluted shares.
    income = pick_flow(facts, EPS_NET_INCOME_TAGS, year)
    shares = pick_flow(facts, EPS_DILUTED_SHARE_TAGS, year)
    if income and shares and shares["val"] != 0:
        return {
            "year": year,
            "val": income["val"] / shares["val"],
            "end": income.get("end"),
            "start": income.get("start"),
            "filed": income.get("filed", ""),
            "form": income.get("form"),
            "frame": income.get("frame"),
            "fy": income.get("fy"),
            "namespace": income.get("namespace"),
            "tag": "derived:net_income_attributable_to_common/weighted_diluted_shares",
            "unit": "USD-per-shares",
            "derived": True,
        }
    return None


def pick_interest(facts, year):
    row = pick_flow(facts, INTEREST_TAGS, year)
    if row:
        return row
    row = pick_flow(facts, INTEREST_FALLBACK_TAGS, year)
    if row:
        row = dict(row)
        row["interest_fallback"] = True
    return row


def pick_debt(facts, year):
    """Prefer current + non-current debt; otherwise use a total-debt tag."""
    current = pick_instant(facts, DEBT_CURRENT_TAGS, year)
    noncurrent = pick_instant(facts, DEBT_NONCURRENT_TAGS, year)
    if current or noncurrent:
        current_value = current["val"] if current else 0.0
        noncurrent_value = noncurrent["val"] if noncurrent else 0.0
        return {
            "year": year,
            "val": current_value + noncurrent_value,
            "current": current,
            "noncurrent": noncurrent,
            "total": None,
        }

    total = pick_instant(facts, DEBT_TOTAL_TAGS, year)
    if total:
        return {
            "year": year,
            "val": total["val"],
            "current": None,
            "noncurrent": None,
            "total": total,
        }
    return None


def pick_ocf(facts, year):
    return pick_flow(facts, OCF_TAGS, year)


def pick_capex(facts, year):
    return pick_flow(facts, CAPEX_TAGS, year)


def pick_dividend(facts, year):
    return pick_flow(facts, DIVIDEND_TAGS, year)
