"""US fundamental collector.

SEC Company Facts -> compact annual metrics -> US-specific scoring -> one row/company.
Raw SEC JSON is never written to Supabase.
"""

from __future__ import annotations

import argparse
import math
import os
import time
from datetime import datetime, timezone

import requests
from supabase import create_client

from scoring import worst_value
from us_scoring import calculate_us_score
from downturn_us import calculate_downturn_defense
from sec_filing_financial_map import filing_map
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

try:
    from us_classification import classify_company as classify_us_company
except ImportError:
    classify_us_company = None

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")

SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

SEC_MIN_REQUEST_INTERVAL = 0.25
_SEC_LAST_REQUEST = 0.0
PERIODS = (1, 3, 5, 10)
FLOW_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
SNAPSHOT_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

FACT_ALIASES = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "operating_income": ["OperatingIncomeLoss", "OperatingIncome", "OperatingProfitLoss", "IncomeFromOperations"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "net_income_parent": ["NetIncomeLossAttributableToOwnersOfParent", "NetIncomeLossAttributableToParent", "ProfitLossAttributableToOwnersOfParent", "ProfitLossAttributableToParent"],
    "net_income_nci": ["NetIncomeLossAttributableToNoncontrollingInterest", "ProfitLossAttributableToNoncontrollingInterest"],
    "assets": ["Assets"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "Equity", "PartnersCapital", "MembersEquity", "EquityAttributableToOwnersOfParent"],
    "equity_nci": ["MinorityInterest", "NoncontrollingInterestInConsolidatedEntity", "NoncontrollingInterestInConsolidatedEntityIncludingPortionAttributableToRedeemableNoncontrollingInterest"],
    "liabilities": ["Liabilities"],
    "debt_current": [
        "LongTermDebtCurrent",
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
        "CurrentBorrowings",
        "CurrentPortionOfLongtermBorrowings",
        "ShortTermBorrowings",
        "ShorttermBorrowings",
        "FinanceLeaseLiabilityCurrent",
        "ConvertibleDebtCurrent",
        "DebtCurrent", "NotesPayableCurrent", "NotesAndLoansPayableCurrent", "ShortTermBankLoansAndNotesPayable", "CommercialPaper",
        "LineOfCreditCurrent", "RevolvingCreditFacilityCurrent",
        "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
    ],
    "debt_noncurrent": [
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
        "NoncurrentBorrowings",
        "LongtermBorrowings",
        "FinanceLeaseLiabilityNoncurrent",
        "ConvertibleDebtNoncurrent",
        "DebtNoncurrent", "NotesPayableNoncurrent", "NotesPayable",
        "LongTermNotesPayable", "LoansPayable", "LongTermLoansPayable",
        "OtherBorrowings", "UnsecuredDebt", "SecuredDebt", "OtherLongTermDebt",
        "FederalHomeLoanBankAdvances", "LongTermNotesAndLoans",
        "LineOfCreditNoncurrent", "RevolvingCreditFacilityNoncurrent",
    ],
    "debt_total": [
        "Borrowings",
        "DebtLongtermAndShorttermCombinedAmount",
        "DebtAndCapitalLeaseObligations",
        "LongTermDebtCurrentAndNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebtAndFinanceLeaseObligations",
        "DebtAndFinanceLeaseLiabilities",
        "Debt",
    ],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "inventory": ["InventoryNet", "InventoryGross"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "receivables": ["AccountsReceivableNetCurrent", "AccountsReceivableNet", "AccountsAndNotesReceivableNetCurrent", "AccountsReceivableGrossCurrent"],
    "interest_expense": [
        "InterestExpenseNonoperating", "InterestExpenseNonOperating", "InterestExpenseDebt",
        "InterestExpenseNonoperatingAndOther", "InterestAndDebtExpense", "InterestExpense",
        "InterestExpenseOnDebtInstrumentsIssued", "InterestExpenseOnBorrowings",
        "InterestExpenseOnOtherFinancialLiabilities", "InterestExpenseOnBankLoansAndOverdrafts",
        "InterestExpenseOnBonds", "InterestExpenseLongTermDebt", "InterestExpenseShortTermBorrowings",
        "InterestExpenseOtherLongTermDebt", "InterestExpenseOtherShortTermBorrowings",
        "InterestExpenseSubordinatedNotesAndDebentures",
        "InterestExpenseFederalHomeLoanBankAndFederalReserveBankAdvancesLongTerm",
        "InterestExpenseFederalHomeLoanBankAndFederalReserveBankAdvancesShortTerm",
        "InterestCostsIncurred", "FinancingInterestExpense",
    ],
    "interest_expense_net": ["InterestIncomeExpenseNet", "InterestIncomeExpenseNonoperatingNet"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "sga": ["SellingGeneralAndAdministrativeExpense", "SellingGeneralAndAdministrativeExpenseIncludingDepreciationAmortization", "GeneralAndAdministrativeExpense", "SellingExpense"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "pretax_income": ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments", "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"],
    "other_nonoperating": ["OtherNonoperatingIncomeExpense", "OtherNonoperatingIncome", "OtherNonoperatingExpense", "NonoperatingIncomeExpense", "OtherIncomeExpenseNet"],
}

IFRS_FACT_ALIASES = {
    "revenue": ["Revenue", "RevenueFromContractsWithCustomers"],
    "operating_income": ["ProfitLossFromOperatingActivities", "OperatingIncomeLoss"],
    "net_income": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "net_income_parent": ["ProfitLossAttributableToOwnersOfParent", "ProfitLossAttributableToParent"],
    "net_income_nci": ["ProfitLossAttributableToNoncontrollingInterest"],
    "assets": ["Assets"],
    "equity": ["EquityAttributableToOwnersOfParent", "Equity"],
    "equity_nci": ["NoncontrollingInterestsInEquity", "NoncontrollingInterestInConsolidatedEntity", "MinorityInterest"],
    "liabilities": ["Liabilities"],
    "debt_current": [
        "CurrentBorrowings",
        "CurrentPortionOfLongtermBorrowings",
        "ShorttermBorrowings",
        "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
        "LongTermDebtCurrent",
        "FinanceLeaseLiabilityCurrent",
    ],
    "debt_noncurrent": [
        "LongtermBorrowings",
        "NoncurrentBorrowings",
        "LongTermDebtNoncurrent",
        "LongTermDebt",
        "LongTermNotesPayable",
        "FinanceLeaseLiabilityNoncurrent",
    ],
    "debt_total": [
        "Borrowings",
        "LoansAndBorrowings",
        "DebtLongtermAndShorttermCombinedAmount",
        "LongTermDebtAndFinanceLeaseObligations",
        "LongTermDebtAndCapitalLeaseObligations",
    ],
    "current_assets": ["CurrentAssets"],
    "current_liabilities": ["CurrentLiabilities"],
    "inventory": ["Inventories"],
    "cash": ["CashAndCashEquivalents"],
    "receivables": ["TradeAndOtherReceivables", "TradeReceivables"],
    "interest_expense": [
        "FinanceCosts", "InterestExpense",
        "InterestExpenseOnBorrowings", "InterestExpenseOnDebtInstrumentsIssued",
        "InterestExpenseOnOtherFinancialLiabilities", "InterestExpenseOnBankLoansAndOverdrafts",
        "InterestExpenseOnBonds", "InterestExpenseLongTermDebt", "InterestExpenseShortTermBorrowings",
        "InterestExpenseOtherLongTermDebt", "InterestExpenseOtherShortTermBorrowings",
        "InterestCostsIncurred",
    ],
    "interest_expense_net": ["InterestIncomeExpenseNet"],
    "operating_cash_flow": ["CashFlowsFromUsedInOperatingActivities"],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
    "pretax_income": ["ProfitLossBeforeTax", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"],
    "other_nonoperating": ["OtherNonoperatingIncomeExpense", "OtherNonoperatingIncome", "OtherNonoperatingExpense", "NonoperatingIncomeExpense", "OtherIncomeExpenseNet"],
}

FACT_NAMESPACE_ALIASES = {
    "us-gaap": FACT_ALIASES,
    "ifrs-full": IFRS_FACT_ALIASES,
    # Filing-level fallback facts are normalized into the same logical tags
    # in-memory; keep them eligible for snapshot construction while retaining
    # their provenance through the namespace field.
    "filing-xbrl": FACT_ALIASES,
}


def clean_number(value):
    if value is None:
        return None
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def sanitize_growth(value):
    # After filing-first EPS validation, very large year-over-year moves can be
    # legitimate. Keep a high guard against malformed ratios without erasing
    # real turnaround / small-base EPS growth.
    if value is None or not math.isfinite(value) or abs(value) > 5000:
        return None
    return value


def _record_quality(record):
    form = record.get("form") or ""
    form_rank = 1 if form.endswith("/A") else 2
    frame_rank = 1 if (record.get("frame") or "").startswith("CY") else 0
    return (record.get("end", ""), record.get("filed", ""), form_rank, frame_rank)


def annual_records(fact):
    """Return one best annual observation per period-end year."""
    units = fact.get("units") or {}
    records = []
    for unit, rows in units.items():
        if not isinstance(rows, list):
            continue
        for r in rows:
            fy, form, end = r.get("fy"), r.get("form"), r.get("end")
            if not end or form not in FLOW_FORMS:
                continue
            try:
                period_end_year = datetime.fromisoformat(end).date().year
            except ValueError:
                continue
            try:
                fiscal_year = int(fy) if fy is not None else period_end_year
            except (TypeError, ValueError):
                fiscal_year = period_end_year
            start = r.get("start")
            if start:
                try:
                    days = (datetime.fromisoformat(end).date() - datetime.fromisoformat(start).date()).days
                except ValueError:
                    continue
                if not 300 <= days <= 380:
                    continue
            value = clean_number(r.get("val"))
            if value is None:
                continue
            records.append({"fy": fiscal_year, "year": period_end_year, "end": end, "filed": r.get("filed") or "", "val": value, "form": form, "frame": r.get("frame"), "unit": unit})
    by_year = {}
    for record in records:
        previous = by_year.get(record["year"])
        if previous is None or _record_quality(record) > _record_quality(previous):
            by_year[record["year"]] = record
    return by_year


def build_fact_index(companyfacts):
    facts_root = companyfacts.get("facts") or {}
    candidates_by_metric = {name: [] for name in FACT_NAMESPACE_ALIASES["us-gaap"]}
    namespace_rank = {"us-gaap": 2, "ifrs-full": 1, "filing-xbrl": 0}
    for namespace, aliases_map in FACT_NAMESPACE_ALIASES.items():
        facts = facts_root.get(namespace) or {}
        for logical_name, aliases in aliases_map.items():
            for priority, tag in enumerate(aliases):
                fact = facts.get(tag)
                rows = annual_records(fact) if fact else {}
                if not rows:
                    continue
                for year, row in rows.items():
                    candidates_by_metric[logical_name].append((year, namespace_rank.get(namespace, 0), -priority, row, namespace, tag))
    index = {}
    for logical_name, candidates in candidates_by_metric.items():
        by_year = {}
        for year, ns_rank, alias_rank, row, namespace, tag in candidates:
            candidate = (ns_rank, alias_rank, _record_quality(row), row, namespace, tag)
            previous = by_year.get(year)
            if previous is None or candidate[:3] > previous[:3]:
                by_year[year] = candidate
        index[logical_name] = {year: {**selected[3], "namespace": selected[4], "tag": selected[5]} for year, selected in by_year.items()}
    return index


def latest_annual_value(index, metric, year):
    row = (index.get(metric) or {}).get(year)
    return row["val"] if row else None


def _all_fact_rows(companyfacts, metric):
    """Return normalized raw SEC observations for a logical metric."""
    facts_root = companyfacts.get("facts") or {}
    out = []
    namespace_rank = {"us-gaap": 2, "ifrs-full": 1, "filing-xbrl": 0}
    for namespace, aliases_map in FACT_NAMESPACE_ALIASES.items():
        facts = facts_root.get(namespace) or {}
        for priority, tag in enumerate(aliases_map.get(metric, [])):
            fact = facts.get(tag)
            if not fact:
                continue
            for unit, rows in (fact.get("units") or {}).items():
                if not isinstance(rows, list):
                    continue
                for r in rows:
                    end = r.get("end")
                    filed = r.get("filed") or ""
                    if not end or r.get("form") not in SNAPSHOT_FORMS:
                        continue

                    # SEC Company Facts can contain malformed/stale observations whose
                    # fiscal end is in the future or whose filing date predates the period end.
                    # Such rows must never be eligible for the "latest snapshot".
                    try:
                        end_date = datetime.fromisoformat(str(end)).date()
                    except ValueError:
                        continue
                    if end_date > datetime.now(timezone.utc).date():
                        continue
                    if filed:
                        try:
                            filed_date = datetime.fromisoformat(str(filed)).date()
                        except ValueError:
                            filed_date = None
                        if filed_date is not None and filed_date < end_date:
                            continue

                    value = clean_number(r.get("val"))
                    if value is None:
                        continue
                    start = r.get("start")
                    days = None
                    if start:
                        try:
                            days = (datetime.fromisoformat(end).date() - datetime.fromisoformat(start).date()).days
                        except ValueError:
                            continue
                    out.append({
                        "metric": metric, "namespace": namespace, "tag": tag,
                        "priority": priority, "namespace_rank": namespace_rank.get(namespace, 0),
                        "val": value, "unit": unit, "start": start, "end": end,
                        "filed": r.get("filed") or "", "form": r.get("form") or "",
                        "fy": r.get("fy"), "fp": r.get("fp"), "frame": r.get("frame"),
                        "days": days,
                    })
    return out


def _best_snapshot_instant(rows, end):
    candidates = [r for r in rows if r["end"] == end and not r["start"]]
    candidates.sort(key=lambda r: (r["namespace_rank"], -r["priority"], r["filed"], r["form"].endswith("/A")), reverse=True)
    return candidates[0] if candidates else None


def _best_duration(rows, end, fy=None, quarter_only=False):
    candidates = [r for r in rows if r["end"] == end and r["days"]]
    if fy is not None:
        same_fy = [r for r in candidates if r.get("fy") == fy]
        if same_fy:
            candidates = same_fy
    if quarter_only:
        candidates = [r for r in candidates if 70 <= r["days"] <= 110]
    else:
        candidates = [r for r in candidates if 70 <= r["days"] <= 400]
    candidates.sort(key=lambda r: (r["namespace_rank"], -r["priority"], r["filed"], r["days"] or 0), reverse=True)
    return candidates[0] if candidates else None


def _best_related_duration(rows, target):
    candidates = [r for r in rows if r.get("end") == target.get("end") and r.get("days")]
    if target.get("fy") is not None:
        same_fy = [r for r in candidates if r.get("fy") == target.get("fy")]
        if same_fy:
            candidates = same_fy
    target_days = target.get("days")
    if target_days:
        close = [r for r in candidates if abs((r.get("days") or 0) - target_days) <= 5]
        if close:
            candidates = close
    target_start = target.get("start")
    if target_start:
        same_start = [r for r in candidates if r.get("start") == target_start]
        if same_start:
            candidates = same_start
    candidates.sort(key=lambda r: (r["namespace_rank"], -r["priority"], r["filed"], r.get("days") or 0), reverse=True)
    return candidates[0] if candidates else None


def _parent_attributable_rows(rows, nci_rows):
    """Replace consolidated duration values with parent-attributable values when NCI is reported."""
    if not rows or not nci_rows:
        return rows
    adjusted = []
    for row in rows:
        nci = _best_related_duration(nci_rows, row)
        if nci is None:
            adjusted.append(row)
            continue
        adjusted.append({**row, "val": row["val"] - nci["val"], "parent_attributable": True, "nci_source_tag": nci["tag"]})
    return adjusted


def _best_related_instant(rows, target):
    candidates = [r for r in rows if r.get("end") == target.get("end") and not r.get("start")]
    if target.get("fy") is not None:
        same_fy = [r for r in candidates if r.get("fy") == target.get("fy")]
        if same_fy:
            candidates = same_fy
    candidates.sort(key=lambda r: (r["namespace_rank"], -r["priority"], r["filed"], r["form"].endswith("/A")), reverse=True)
    return candidates[0] if candidates else None


def _parent_attributable_instant(row, nci_rows):
    """Return parent-attributable instant value when the selected equity fact includes NCI."""
    if row is None or not nci_rows:
        return row
    tag = row.get("tag") or ""
    if "IncludingPortionAttributableToNoncontrollingInterest" not in tag:
        return row
    nci = _best_related_instant(nci_rows, row)
    if nci is None:
        return row
    return {**row, "val": row["val"] - nci["val"], "parent_attributable": True, "nci_source_tag": nci["tag"]}


def _derive_quarter_value(rows, end, fy):
    """Convert latest YTD duration fact to quarter-only using the prior YTD fact."""
    current = _best_duration(rows, end, fy=fy, quarter_only=False)
    if not current:
        return None
    if current["days"] and 70 <= current["days"] <= 110:
        return {"value": current["val"], "source": "reported-quarter", "start": current["start"], "end": current["end"], "days": current["days"], "parent_attributable": bool(current.get("parent_attributable"))}
    if not current["start"] or not fy or not current["days"] or current["days"] < 150:
        return None
    prior = [r for r in rows if r.get("fy") == fy and r.get("end") < end and r.get("start") == current["start"] and r.get("days") and r["days"] < current["days"]]
    if not prior:
        return None
    prior.sort(key=lambda r: (r["end"], r["filed"]), reverse=True)
    p = prior[0]
    return {"value": current["val"] - p["val"], "source": "derived-quarter-from-ytd", "start": p["end"], "end": end, "days": (current["days"] - p["days"]), "parent_attributable": bool(current.get("parent_attributable"))}


def build_latest_snapshot(companyfacts):
    """Build the latest fiscal-quarter snapshot plus reported/YTD/TTM values.

    Balance-sheet facts are taken at the latest fiscal period end. Flow facts
    expose reported quarter, YTD and TTM when the SEC facts allow derivation.
    This is intentionally separate from annual scoring data.
    """
    all_rows = {metric: _all_fact_rows(companyfacts, metric) for metric in FACT_ALIASES}
    if all_rows.get("net_income"):
        all_rows["net_income"] = _parent_attributable_rows(all_rows["net_income"], all_rows.get("net_income_nci", []))
    all_dates = []
    for metric, rows in all_rows.items():
        if metric in {"assets", "equity", "equity_nci", "liabilities", "current_assets", "current_liabilities", "cash", "receivables", "inventory"}:
            all_dates.extend(r["end"] for r in rows if not r["start"])
        else:
            all_dates.extend(r["end"] for r in rows if r["form"] in SNAPSHOT_FORMS)
    if not all_dates:
        return None

    end = max(all_dates)
    # Prefer a true quarter filing. If no 10-Q exists, fall back to latest 10-K.
    forms_at_end = [r["form"] for rows in all_rows.values() for r in rows if r["end"] == end]
    has_q = any(f in {"10-Q", "10-Q/A"} for f in forms_at_end)
    snapshot_form = "10-Q" if has_q else next((f for f in forms_at_end if f in {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}), None)
    filed_candidates = [r["filed"] for rows in all_rows.values() for r in rows if r["end"] == end and r["filed"]]
    filed = max(filed_candidates) if filed_candidates else None
    sample = next((r for rows in all_rows.values() for r in rows if r["end"] == end and r.get("fy") is not None), None)
    fy = sample.get("fy") if sample else None
    fp = sample.get("fp") if sample else None
    snapshot = {
        "fiscal_end": end,
        "fiscal_year": fy,
        "fiscal_period": fp,
        "form": snapshot_form,
        "filed": filed,
        "basis": "latest fiscal quarter" if has_q else "latest fiscal year",
        "instant": {},
        "flows": {},
    }

    instant_metrics = {"assets", "equity", "liabilities", "current_assets", "current_liabilities", "cash", "receivables", "inventory"}
    flow_metrics = {"revenue", "operating_income", "net_income", "interest_expense", "operating_cash_flow", "sga", "eps"}
    for metric in instant_metrics:
        row = _best_snapshot_instant(all_rows.get(metric, []), end)
        if metric == "equity":
            row = _parent_attributable_instant(row, all_rows.get("equity_nci", []))
        if row:
            entry = {"value": row["val"], "unit": row["unit"], "tag": row["tag"], "namespace": row["namespace"], "source": "sec-company-facts" if row["namespace"] in {"us-gaap", "ifrs-full"} else "sec-filing-xbrl", "filed": row["filed"]}
            if row.get("parent_attributable"):
                entry["basis"] = "parent-attributable"
                entry["nci_source_tag"] = row.get("nci_source_tag")
            snapshot["instant"][metric] = entry

    for metric in flow_metrics:
        rows = all_rows.get(metric, [])
        q = _derive_quarter_value(rows, end, fy)
        reported = _best_duration(rows, end, fy=fy, quarter_only=False)
        entry = {}
        if q:
            entry["quarter"] = q
        if reported:
            reported_entry = {
                "value": reported["val"], "unit": reported["unit"], "days": reported["days"],
                "start": reported["start"], "end": reported["end"], "tag": reported["tag"],
                "namespace": reported["namespace"],
                "source": "sec-company-facts" if reported["namespace"] in {"us-gaap", "ifrs-full"} else "sec-filing-xbrl",
                "filed": reported["filed"],
            }
            if reported.get("parent_attributable"):
                reported_entry["basis"] = "parent-attributable"
                reported_entry["nci_source_tag"] = reported.get("nci_source_tag")
            entry["reported"] = reported_entry
        # TTM: current YTD/quarter + prior fiscal year - prior comparable YTD.
        if fy:
            annuals = [r for r in rows if r.get("fy") == fy and r.get("form") in FLOW_FORMS and r.get("days") and 300 <= r["days"] <= 380]
            prior_annuals = [r for r in rows if r.get("fy") == fy - 1 and r.get("form") in FLOW_FORMS and r.get("days") and 300 <= r["days"] <= 380]
            if annuals and prior_annuals:
                annuals.sort(key=lambda r: (r["end"], r["filed"]), reverse=True)
                prior_annuals.sort(key=lambda r: (r["end"], r["filed"]), reverse=True)
                annual = annuals[0]
                prior_annual = prior_annuals[0]
                comparable = None
                if reported and reported.get("start"):
                    comparable_rows = [r for r in rows if r.get("fy") == fy - 1 and r.get("start") and r.get("end") and r.get("end") < prior_annual["end"] and r.get("days") and abs(r["days"] - reported["days"]) < 20]
                    if comparable_rows:
                        comparable_rows.sort(key=lambda r: (r["end"], r["filed"]), reverse=True)
                        comparable = comparable_rows[-1]
                if comparable:
                    ttm_value = reported["val"] + prior_annual["val"] - comparable["val"]
                    entry["ttm"] = {"value": ttm_value, "unit": reported["unit"], "source": "derived-ttm", "basis": "parent-attributable"} if metric == "net_income" and reported.get("parent_attributable") else {"value": ttm_value, "unit": reported["unit"], "source": "derived-ttm"}
                elif q:
                    entry["ttm"] = {"value": q["value"] + prior_annual["val"], "unit": q.get("unit") or annual["unit"], "source": "partial-ttm", "basis": "parent-attributable"} if metric == "net_income" and q.get("parent_attributable") else {"value": q["value"] + prior_annual["val"], "unit": q.get("unit") or annual["unit"], "source": "partial-ttm"}
        if entry:
            snapshot["flows"][metric] = entry

    return snapshot


def growth_cagr(current, base, years):
    current, base = clean_number(current), clean_number(base)
    if current is None or base is None or years <= 0 or base == 0:
        return None
    if current > 0 and base > 0:
        return sanitize_growth(((current / base) ** (1.0 / years) - 1.0) * 100.0)
    return sanitize_growth((current / base - 1.0) * 100.0)


def ratio(numerator, denominator, multiplier=1.0):
    numerator, denominator = clean_number(numerator), clean_number(denominator)
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * multiplier


def debt_rate(liabilities, equity):
    liabilities, equity = clean_number(liabilities), clean_number(equity)
    # Keep signed debt/equity when equity is negative so the raw metric remains
    # visible and available to downstream scoring/reporting. Only zero equity
    # makes the ratio mathematically undefined.
    if liabilities is None or equity in (None, 0):
        return None
    return liabilities / equity * 100.0


def annual_metrics(index, year, annual_overrides=None):
    overrides = (annual_overrides or {}).get(int(year), {})

    def value_for(metric):
        override = clean_number(overrides.get(metric))
        return override if override is not None else latest_annual_value(index, metric, year)

    revenue = value_for("revenue")
    opinc = value_for("operating_income")
    pretax_income = latest_annual_value(index, "pretax_income", year)
    other_nonoperating = latest_annual_value(index, "other_nonoperating", year)
    consolidated_net_income = latest_annual_value(index, "net_income", year)
    parent_net_income = latest_annual_value(index, "net_income_parent", year)
    nci_net_income = latest_annual_value(index, "net_income_nci", year)
    if parent_net_income is not None:
        net_income = parent_net_income
    elif consolidated_net_income is not None and nci_net_income is not None:
        net_income = consolidated_net_income - nci_net_income
    else:
        net_income = consolidated_net_income
    assets = latest_annual_value(index, "assets", year)
    equity = value_for("equity")
    equity_nci = latest_annual_value(index, "equity_nci", year)
    equity_row = (index.get("equity") or {}).get(year)
    equity_tag = (equity_row or {}).get("tag") or ""
    if equity is not None and "IncludingPortionAttributableToNoncontrollingInterest" in equity_tag and equity_nci is not None:
        equity = equity - equity_nci
    liabilities = latest_annual_value(index, "liabilities", year)
    current_assets = latest_annual_value(index, "current_assets", year)
    current_liabilities = latest_annual_value(index, "current_liabilities", year)
    cash = value_for("cash")
    receivables = latest_annual_value(index, "receivables", year)
    inventory = latest_annual_value(index, "inventory", year)
    interest = value_for("interest_expense")
    # Some issuers report only net interest income/(expense). For coverage,
    # a negative net expense is converted to a positive interest burden.
    if interest is None:
        net_interest = latest_annual_value(index, "interest_expense_net", year)
        if net_interest is not None:
            interest = abs(float(net_interest))
    ocf = latest_annual_value(index, "operating_cash_flow", year)
    sga = latest_annual_value(index, "sga", year)
    eps = value_for("eps")
    debt_current = latest_annual_value(index, "debt_current", year)
    debt_noncurrent = latest_annual_value(index, "debt_noncurrent", year)
    debt_total = latest_annual_value(index, "debt_total", year)
    debt_override = clean_number(overrides.get("debt"))
    if debt_override is not None:
        debt = debt_override
    elif debt_total is not None:
        # Prefer explicitly reported total debt over partial maturity buckets.
        debt = debt_total
    elif debt_current is not None or debt_noncurrent is not None:
        debt = (debt_current or 0.0) + (debt_noncurrent or 0.0)
    else:
        debt = None

    # Some issuers (for example Alcoa) do not tag an operating-income subtotal.
    # When the filing provides pretax income, interest expense, and a signed
    # non-operating income/expense line, operating income can be reconstructed
    # from the same annual context without using a synthetic balance-sheet value.
    if opinc is None and pretax_income is not None and interest is not None and other_nonoperating is not None:
        opinc = pretax_income + interest + other_nonoperating

    # ROIC uses invested operating capital rather than total liabilities:
    # equity + interest-bearing debt - cash. This avoids counting payables,
    # deferred revenue, and other operating liabilities as invested capital.
    nopat = opinc * 0.78 if opinc is not None else None
    invested_capital = None
    if equity is not None and debt is not None:
        invested_capital = equity + debt - (cash or 0.0)
        # Preserve the signed result. A negative invested-capital denominator
        # produces a signed ROIC that can be displayed and scored; only zero
        # is mathematically undefined.
        if invested_capital == 0:
            invested_capital = None
    quick_assets = current_assets - (inventory or 0.0) if current_assets is not None else ((cash or 0.0) + (receivables or 0.0) if cash is not None or receivables is not None else None)
    return {"revenue": revenue, "eps": eps, "revenue_growth": None, "eps_growth": None, "opm": ratio(opinc, revenue, 100.0), "roic": ratio(nopat, invested_capital, 100.0), "debt_rate": debt_rate(liabilities, equity), "quick_ratio": ratio(quick_assets, current_liabilities), "interest_coverage": ratio(opinc, interest), "ocf_ratio": ratio(ocf, net_income), "sga_ratio": ratio(sga, revenue, 100.0), "downturn_defense": None, "roa": ratio(net_income, assets, 100.0), "net_income": net_income, "assets": assets}


def classify_company(submissions):
    sic = submissions.get("sic")
    sic_desc = submissions.get("sicDescription")
    return sic_desc or (f"SIC {sic}" if sic else None)


def fetch_json(session, url, retries=4):
    global _SEC_LAST_REQUEST
    last_response = None
    for attempt in range(retries):
        wait = SEC_MIN_REQUEST_INTERVAL - (time.monotonic() - _SEC_LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _SEC_LAST_REQUEST = time.monotonic()
        try:
            response = session.get(url, timeout=30)
            last_response = response
            if response.status_code == 200:
                return response.json()
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt < retries - 1:
                    raw = response.headers.get("Retry-After")
                    try:
                        retry_after = float(raw)
                    except (TypeError, ValueError):
                        retry_after = None
                    delay = min(max(retry_after, 1.0), 30.0) if retry_after is not None and retry_after >= 0 else min(2.0 ** attempt, 16.0)
                    time.sleep(delay)
                    continue
            response.raise_for_status()
        except requests.RequestException as exc:
            if attempt >= retries - 1:
                raise
            time.sleep(min(2.0 ** attempt, 16.0))
    raise RuntimeError(f"SEC request failed after {retries} retries: {url}")


def load_company(session, ticker, cik):
    cik10 = str(cik).zfill(10)
    return fetch_json(session, SEC_FACTS_URL.format(cik=cik10)), fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik10))



# ============================================================
# FILING-FIRST CRITICAL METRIC RECOVERY
# ============================================================

CRITICAL_ROIC_PROFILES = {"standard", "defense"}
CRITICAL_INTEREST_PROFILES = {"standard", "reit", "bdc", "defense", "utility"}

EPS_EXCLUDED_TOKENS = (
    "textblock",
    "numerator",
    "denominator",
    "weightedaverage",
    "sharesoutstanding",
    "sharecount",
    "stocksplit",
    "splitadjustment",
    "antidilutive",
    "potentiallydilutive",
    "effectofdilution",
    "epsimpact",
    "proforma",
    "adjustment",
    "discontinued",
    "segment",
    "netincome",
    "profitloss",
    "marketprice",
    "stockprice",
    "dividend",
)

EPS_POSITIVE_TOKENS = (
    "earningspershare",
    "basicearningspershare",
    "dilutedearningspershare",
)

EPS_LABEL_POSITIVE = (
    "earnings per share",
    "earnings (loss) per share",
    "basic eps",
    "diluted eps",
)


def _candidate_attr(candidate, name, default=None):
    return getattr(candidate, name, default)


def _local_candidate_concept(candidate):
    concept = _candidate_attr(candidate, "concept", "") or ""
    return str(concept).rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _eps_candidate_rank(candidate):
    """Return a conservative ranking for a validated filing EPS candidate."""
    concept = _local_candidate_concept(candidate)
    compact = "".join(ch for ch in concept.lower() if ch.isalnum())
    label = str(_candidate_attr(candidate, "label", "") or "").lower()
    label_compact = "".join(ch for ch in label if ch.isalnum())
    combined = f"{compact} {label_compact}"
    if any(token in combined for token in EPS_EXCLUDED_TOKENS):
        return None

    exact_rank = {
        "EarningsPerShareDiluted": 0,
        "EarningsPerShareBasic": 1,
    }.get(concept)

    looks_like_eps = (
        exact_rank is not None
        or any(token in compact for token in EPS_POSITIVE_TOKENS)
        or any(phrase in label for phrase in EPS_LABEL_POSITIVE)
    )
    if not looks_like_eps:
        return None

    value = clean_number(_candidate_attr(candidate, "value"))
    if value is None:
        return None

    return (
        0 if exact_rank is None else exact_rank + 1,
        -float(_candidate_attr(candidate, "score", 0.0) or 0.0),
        str(_candidate_attr(candidate, "namespace", "") or ""),
        compact,
    )


def select_eps_pair(current_candidates, prior_candidates):
    """Select current/prior EPS facts on one security basis and one unit."""
    current = []
    prior = []
    for candidate in current_candidates or []:
        rank = _eps_candidate_rank(candidate)
        if rank is not None:
            current.append((candidate, rank))
    for candidate in prior_candidates or []:
        rank = _eps_candidate_rank(candidate)
        if rank is not None:
            prior.append((candidate, rank))

    if not current or not prior:
        return None

    pairs = []
    for cur, cur_rank in current:
        cur_unit = str(_candidate_attr(cur, "unit", "") or "").strip().lower()
        cur_concept = _local_candidate_concept(cur)
        for old, old_rank in prior:
            old_unit = str(_candidate_attr(old, "unit", "") or "").strip().lower()
            if not cur_unit or not old_unit or cur_unit != old_unit:
                continue
            old_concept = _local_candidate_concept(old)
            same_concept = cur_concept == old_concept
            same_kind = (
                ("diluted" in cur_concept.lower() and "diluted" in old_concept.lower())
                or ("basic" in cur_concept.lower() and "basic" in old_concept.lower())
            )
            concept_bonus = 3 if same_concept else (2 if same_kind else 0)
            exact_bonus = 4 if cur_concept in {"EarningsPerShareDiluted", "EarningsPerShareBasic"} else 0
            score = (
                concept_bonus + exact_bonus,
                -cur_rank[0],
                -old_rank[0],
                -cur_rank[1],
                -old_rank[1],
            )
            pairs.append((score, cur, old))

    if not pairs:
        return None

    pairs.sort(key=lambda x: x[0], reverse=True)
    _, cur, old = pairs[0]
    return {
        "current": {
            "value": clean_number(_candidate_attr(cur, "value")),
            "unit": _candidate_attr(cur, "unit"),
            "concept": _local_candidate_concept(cur),
            "namespace": _candidate_attr(cur, "namespace"),
            "start": _candidate_attr(cur, "start"),
            "end": _candidate_attr(cur, "end"),
            "filed": _candidate_attr(cur, "filed"),
            "source": "sec-filing-xbrl-inline",
        },
        "prior": {
            "value": clean_number(_candidate_attr(old, "value")),
            "unit": _candidate_attr(old, "unit"),
            "concept": _local_candidate_concept(old),
            "namespace": _candidate_attr(old, "namespace"),
            "start": _candidate_attr(old, "start"),
            "end": _candidate_attr(old, "end"),
            "filed": _candidate_attr(old, "filed"),
            "source": "sec-filing-xbrl-inline",
        },
    }


def recover_critical_filing_metrics(
    cik,
    latest_year,
    profile,
    resolver,
    cache=None,
    need_roic=False,
    need_interest=False,
    need_eps_growth=False,
):
    """Recover ROIC / Interest Coverage / EPS Growth from the annual filing."""
    cache = cache if cache is not None else {}
    key = (str(cik), int(latest_year))
    if key in cache:
        return cache[key]

    result = {
        "annual_overrides": {},
        "sources": {},
        "errors": [],
    }

    filing_mapped = None

    if need_roic or need_interest:
        try:
            filing_mapped = filing_map(cik, resolver, year=int(latest_year))
            roic_inputs = filing_mapped.get("roic_inputs") or {}
            equity = roic_inputs.get("equity")
            cash = roic_inputs.get("cash")
            opinc = roic_inputs.get("operating_income")
            debt = filing_mapped.get("selected_debt")

            if need_roic and all(x is not None for x in (equity, cash, opinc, debt)):
                result["annual_overrides"][int(latest_year)] = {
                    "equity": equity["value"],
                    "cash": cash["value"],
                    "debt": debt["value"],
                    "operating_income": opinc["value"],
                }
                result["sources"]["roic"] = {
                    "filing": filing_mapped.get("filing"),
                    "debt_status": filing_mapped.get("debt_status"),
                    "equity": equity,
                    "cash": cash,
                    "operating_income": opinc,
                    "debt": debt,
                }

            if need_interest and opinc is not None:
                selected_interest = filing_mapped.get("selected_interest")
                if selected_interest and clean_number(selected_interest.get("value")) not in (None, 0):
                    result["annual_overrides"].setdefault(int(latest_year), {})["operating_income"] = opinc["value"]
                    result["annual_overrides"][int(latest_year)]["interest_expense"] = selected_interest["value"]
                    result["sources"]["interest_coverage"] = {
                        "filing": filing_mapped.get("filing"),
                        "interest_status": filing_mapped.get("interest_status"),
                        "operating_income": opinc,
                        "interest": selected_interest,
                    }
        except Exception as exc:
            result["errors"].append({"metric": "roic_interest", "error": str(exc)})

    if need_eps_growth:
        try:
            current_candidates, _ = resolver.search_filing(cik, "eps", year=int(latest_year), limit=50)
            prior_candidates, _ = resolver.search_filing(cik, "eps", year=int(latest_year) - 1, limit=50)
            pair = select_eps_pair(current_candidates, prior_candidates)
            if pair is not None:
                result["annual_overrides"].setdefault(int(latest_year), {})["eps"] = pair["current"]["value"]
                result["annual_overrides"].setdefault(int(latest_year) - 1, {})["eps"] = pair["prior"]["value"]
                result["sources"]["eps_growth"] = pair
        except Exception as exc:
            result["errors"].append({"metric": "eps_growth", "error": str(exc)})

    cache[key] = result
    return result

def period_metrics_pair(index, latest_year, period, annual_overrides=None):
    """Build Korean-compatible US period structure: avg + worst + yearly breakdown."""
    all_years = sorted({y for rows in index.values() for y in rows.keys()})
    if latest_year not in all_years:
        return None

    base_year = latest_year - period
    if base_year not in all_years:
        # A small but important SEC edge case: some issuers skip an annual
        # fiscal year in Company Facts (restructuring, IPO, fiscal-year change,
        # foreign filer transition, etc.). For the 1Y score, do not fabricate
        # a growth period by stretching 2023 -> 2026 into "1Y". Instead build
        # a latest-year-only score: ratio metrics remain valid, while
        # revenue/eps growth stay unavailable and are transparently excluded.
        if period == 1 and latest_year in all_years:
            window_years = [latest_year]
            oldest = newest = latest_year
            actual_span = 0
        else:
            return None
    else:
        window_years = [y for y in range(base_year, latest_year + 1) if y in all_years]
        if len(window_years) < 2:
            if period == 1 and window_years:
                oldest = newest = window_years[-1]
                actual_span = 0
            else:
                return None
        else:
            oldest, newest = window_years[0], window_years[-1]
            actual_span = newest - oldest
    yearly = {y: annual_metrics(index, y, annual_overrides=annual_overrides) for y in window_years}

    latest_metrics = dict(yearly[newest])
    revenue_growth = growth_cagr(
        yearly[newest].get("revenue"),
        yearly[oldest].get("revenue"),
        actual_span,
    )
    eps_growth = growth_cagr(
        yearly[newest].get("eps"),
        yearly[oldest].get("eps"),
        actual_span,
    )

    avg_metrics = {
        "revenue_growth": revenue_growth,
        "eps_growth": eps_growth,
        "downturn_defense": None,
    }
    worst_metrics = {
        "revenue_growth": revenue_growth,
        "eps_growth": eps_growth,
        "downturn_defense": None,
    }

    ratio_keys = (
        "opm", "roic", "debt_rate", "quick_ratio",
        "interest_coverage", "ocf_ratio", "sga_ratio",
        "roa",
    )
    recent_years = window_years[1:] if len(window_years) > 1 else window_years

    for metric in ratio_keys:
        series = [
            yearly[y].get(metric)
            for y in recent_years
            if yearly[y].get(metric) is not None
        ]
        avg_metrics[metric] = (
            round(sum(series) / len(series), 4) if series else None
        )
        worst_metrics[metric] = worst_value(metric, series)

    yearly_breakdown = {}
    for metric in ratio_keys:
        yearly_breakdown[metric] = {
            str(y): yearly[y].get(metric)
            for y in recent_years
            if yearly[y].get(metric) is not None
        }

    rev_growth_by_year, eps_growth_by_year = {}, {}
    for y in recent_years:
        prev_y = y - 1
        if prev_y not in yearly or y not in yearly:
            continue
        rev_y = yearly[y].get("revenue")
        rev_prev = yearly[prev_y].get("revenue")
        eps_y = yearly[y].get("eps")
        eps_prev = yearly[prev_y].get("eps")
        if rev_prev not in (None, 0) and rev_y is not None:
            value = (rev_y - rev_prev) / abs(rev_prev) * 100.0
            rev_growth_by_year[str(y)] = sanitize_growth(value)
        if eps_prev not in (None, 0) and eps_y is not None:
            value = (eps_y - eps_prev) / abs(eps_prev) * 100.0
            eps_growth_by_year[str(y)] = sanitize_growth(value)

    yearly_breakdown["revenue_growth"] = rev_growth_by_year
    yearly_breakdown["eps_growth"] = eps_growth_by_year

    return {
        "years_used": window_years,
        "avg_metrics": avg_metrics,
        "worst_metrics": worst_metrics,
        "yearly_breakdown": yearly_breakdown,
    }


def period_metrics(index, latest_year, period):
    """Backward-compatible worst-metrics accessor used by diagnostic collectors."""
    pair = period_metrics_pair(index, latest_year, period)
    if pair is None:
        return None, {}, None
    return latest_year, pair["worst_metrics"], latest_year - period



def build_result(
    ticker,
    cik,
    company_name,
    facts,
    submissions,
    universe_row=None,
    market_prices=None,
    filing_resolver=None,
    filing_recovery_cache=None,
):
    universe_row = universe_row or {}
    index = build_fact_index(facts)
    all_years = sorted({y for rows in index.values() for y in rows.keys()})
    snapshot = build_latest_snapshot(facts)

    if not all_years:
        return {
            "ticker": ticker,
            "cik": str(cik),
            "company_name": company_name,
            "sector": universe_row.get("sector_common") or classify_company(submissions),
            "base_year": None,
            "period_scores": {},
            "total_score": None,
            "grade": None,
            "data_unavailable": True,
            "data_reliability": "none",
            "missing_metric_count": 10,
            "snapshot": snapshot,
            "snapshot_fiscal_end": snapshot.get("fiscal_end") if snapshot else None,
            "snapshot_period": snapshot.get("fiscal_period") if snapshot else None,
            "snapshot_form": snapshot.get("form") if snapshot else None,
            "snapshot_filed": snapshot.get("filed") if snapshot else None,
            "snapshot_basis": snapshot.get("basis") if snapshot else None,
            "snapshot_updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    flow_years = sorted(
        set(index.get("revenue", {}).keys())
        | set(index.get("operating_income", {}).keys())
        | set(index.get("net_income", {}).keys())
    )
    latest_year = max(flow_years) if flow_years else max(all_years)
    profile = universe_row.get("scoring_profile") or "standard"

    filing_overrides = {}
    filing_recovery_meta = {}
    if filing_resolver is not None and latest_year is not None:
        latest_probe = annual_metrics(index, latest_year)
        prior_probe = annual_metrics(index, latest_year - 1)
        need_roic = profile in CRITICAL_ROIC_PROFILES and latest_probe.get("roic") is None
        need_interest = profile in CRITICAL_INTEREST_PROFILES and latest_probe.get("interest_coverage") is None
        need_eps_growth = (
            latest_probe.get("eps") is None
            or prior_probe.get("eps") is None
        )
        if need_roic or need_interest or need_eps_growth:
            recovery = recover_critical_filing_metrics(
                cik,
                latest_year,
                profile,
                filing_resolver,
                cache=filing_recovery_cache,
                need_roic=need_roic,
                need_interest=need_interest,
                need_eps_growth=need_eps_growth,
            )
            filing_overrides = recovery.get("annual_overrides") or {}
            filing_recovery_meta = recovery

    downturn_value, downturn_detail = calculate_downturn_defense(
        ticker,
        market=(market_prices or {}).get("market"),
        stock=(market_prices or {}).get("stock"),
    )

    period_scores = {}
    latest_score = latest_grade = None
    latest_missing = 0

    for period in PERIODS:
        pdata = period_metrics_pair(index, latest_year, period, annual_overrides=filing_overrides)
        if pdata is None:
            continue

        avg_metrics = dict(pdata["avg_metrics"])
        worst_metrics = dict(pdata["worst_metrics"])
        avg_metrics["downturn_defense"] = downturn_value
        worst_metrics["downturn_defense"] = downturn_value

        avg_score = calculate_us_score(avg_metrics, profile=profile)
        worst_score = calculate_us_score(worst_metrics, profile=profile)

        for scored, metrics in ((avg_score, avg_metrics), (worst_score, worst_metrics)):
            for growth_key in ("revenue_growth", "eps_growth"):
                value = metrics.get(growth_key)
                if value is not None and abs(value) >= 100:
                    if growth_key in scored.get("metric_scores", {}):
                        scored["metric_scores"][growth_key]["is_extreme"] = True

        period_scores[f"{period}y"] = {
            "years_used": pdata["years_used"],
            "yearly_breakdown": pdata["yearly_breakdown"],
            "avg": {
                "total_score": avg_score["total_score"],
                "grade": avg_score["grade"],
                "metric_scores": avg_score["metric_scores"],
                "sub_scores": avg_score.get("sub_scores", {}),
                "financial_adjusted": False,
                "missing_metric_count": avg_score["missing_metric_count"],
                "scoring_version": avg_score["scoring_version"],
                "available_weight": avg_score["available_weight"],
                "coverage_pct": avg_score["coverage_pct"],
                "score_cap": avg_score["score_cap"],
                "confidence_level": avg_score["confidence_level"],
            },
            "worst": {
                "total_score": worst_score["total_score"],
                "grade": worst_score["grade"],
                "metric_scores": worst_score["metric_scores"],
                "sub_scores": worst_score.get("sub_scores", {}),
                "financial_adjusted": False,
                "missing_metric_count": worst_score["missing_metric_count"],
                "scoring_version": worst_score["scoring_version"],
                "available_weight": worst_score["available_weight"],
                "coverage_pct": worst_score["coverage_pct"],
                "score_cap": worst_score["score_cap"],
                "confidence_level": worst_score["confidence_level"],
            },
        }

        if period == 1:
            latest_score = avg_score["total_score"]
            latest_grade = avg_score["grade"]
            latest_missing = avg_score["missing_metric_count"]

    from us_scoring import data_reliability_from_periods

    reliability = data_reliability_from_periods(period_scores)

    return {
        "ticker": ticker,
        "cik": str(cik),
        "company_name": company_name,
        "sector": universe_row.get("sector_common") or classify_company(submissions),
        "base_year": latest_year,
        "period_scores": period_scores,
        "total_score": int(round(latest_score)) if latest_score is not None else None,
        "grade": latest_grade,
        "data_unavailable": not bool(period_scores),
        "data_reliability": reliability,
        "missing_metric_count": latest_missing,
        "filing_recovery": filing_recovery_meta,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "downturn_defense": downturn_value,
        "downturn_detail": downturn_detail,
        "snapshot": snapshot,
        "snapshot_fiscal_end": snapshot.get("fiscal_end") if snapshot else None,
        "snapshot_period": snapshot.get("fiscal_period") if snapshot else None,
        "snapshot_form": snapshot.get("form") if snapshot else None,
        "snapshot_filed": snapshot.get("filed") if snapshot else None,
        "snapshot_basis": snapshot.get("basis") if snapshot else None,
        "snapshot_updated_at": datetime.now(timezone.utc).isoformat(),
    }



def get_universe(sb, tickers=None, limit=None, all_rows=False):
    columns = "ticker,cik,company_name,sector_common,company_type,scoring_profile"
    if tickers:
        return sb.table("US_Companies").select(columns).in_("ticker", tickers).eq("is_fundamental_eligible", True).execute().data
    query = sb.table("US_Companies").select(columns).eq("is_fundamental_eligible", True).order("ticker")
    if not all_rows:
        query = query.limit(limit or 5)
    return query.execute().data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--tickers")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--all", action="store_true", dest="all_rows")
    args = parser.parse_args()
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    tickers = [args.ticker.upper().strip()] if args.ticker else ([x.upper().strip() for x in args.tickers.split(",") if x.strip()] if args.tickers else None)
    rows = get_universe(sb, tickers=tickers, limit=args.limit, all_rows=args.all_rows)
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    filing_resolver = SECXBRLSearchV2_3_8(
        user_agent=SEC_USER_AGENT,
        session=session,
    )
    filing_recovery_cache = {}
    market = None
    stock_cache = {}
    try:
        from downturn_us import _close_series, BENCHMARK
        market = _close_series(BENCHMARK)
    except Exception as exc:
        print(f"[US] downturn market data unavailable: {exc}")
    for i, row in enumerate(rows, 1):
        ticker, cik = row["ticker"], row["cik"]
        try:
            facts, submissions = load_company(session, ticker, cik)
            if ticker not in stock_cache:
                try:
                    from downturn_us import _close_series
                    stock_cache[ticker] = _close_series(ticker)
                except Exception:
                    stock_cache[ticker] = None
            result = build_result(
                ticker,
                cik,
                row.get("company_name") or submissions.get("name") or ticker,
                facts,
                submissions,
                universe_row=row,
                market_prices={"market": market, "stock": stock_cache.get(ticker)},
                filing_resolver=filing_resolver,
                filing_recovery_cache=filing_recovery_cache,
            )
            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
            print(f"[{i}/{len(rows)}] {ticker}: score={result['total_score']} grade={result['grade']} periods={len(result['period_scores'])} reliability={result['data_reliability']} snapshot={result.get('snapshot_fiscal_end')}")
        except Exception as exc:
            print(f"[{i}/{len(rows)}] {ticker}: FAILED: {exc}")
    print("Completed.")


if __name__ == "__main__":
    main()