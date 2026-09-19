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

try:
    from us_classification import classify_company as classify_us_company
except ImportError:
    classify_us_company = None

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")

SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
PERIODS = (1, 3, 5, 10)
FLOW_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
SNAPSHOT_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

FACT_ALIASES = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "net_income_parent": ["NetIncomeLossAttributableToOwnersOfParent", "NetIncomeLossAttributableToParent", "ProfitLossAttributableToOwnersOfParent", "ProfitLossAttributableToParent"],
    "net_income_nci": ["NetIncomeLossAttributableToNoncontrollingInterest", "ProfitLossAttributableToNoncontrollingInterest"],
    "assets": ["Assets"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "equity_nci": ["MinorityInterest", "NoncontrollingInterestInConsolidatedEntity", "NoncontrollingInterestInConsolidatedEntityIncludingPortionAttributableToRedeemableNoncontrollingInterest"],
    "liabilities": ["Liabilities"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "inventory": ["InventoryNet", "InventoryGross"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "receivables": ["AccountsReceivableNetCurrent", "AccountsReceivableNet", "AccountsAndNotesReceivableNetCurrent", "AccountsReceivableGrossCurrent"],
    "interest_expense": ["InterestExpenseNonOperating", "InterestExpenseDebt", "InterestExpenseNonOperatingNet", "InterestExpenseNonOperatingAndOther"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "sga": ["SellingGeneralAndAdministrativeExpense", "SellingGeneralAndAdministrativeExpenseIncludingDepreciationAmortization"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
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
    "current_assets": ["CurrentAssets"],
    "current_liabilities": ["CurrentLiabilities"],
    "inventory": ["Inventories"],
    "cash": ["CashAndCashEquivalents"],
    "receivables": ["TradeAndOtherReceivables", "TradeReceivables"],
    "interest_expense": ["FinanceCosts", "InterestExpense"],
    "operating_cash_flow": ["CashFlowsFromUsedInOperatingActivities"],
    "sga": ["SellingGeneralAndAdministrativeExpense"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}

FACT_NAMESPACE_ALIASES = {"us-gaap": FACT_ALIASES, "ifrs-full": IFRS_FACT_ALIASES}


def clean_number(value):
    if value is None:
        return None
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def sanitize_growth(value):
    if value is None or not math.isfinite(value) or abs(value) > 500:
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
            if not fy or not end or form not in FLOW_FORMS:
                continue
            try:
                period_end_year = datetime.fromisoformat(end).date().year
            except ValueError:
                continue
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
            records.append({"fy": int(fy), "year": period_end_year, "end": end, "filed": r.get("filed") or "", "val": value, "form": form, "frame": r.get("frame"), "unit": unit})
    by_year = {}
    for record in records:
        previous = by_year.get(record["year"])
        if previous is None or _record_quality(record) > _record_quality(previous):
            by_year[record["year"]] = record
    return by_year


def build_fact_index(companyfacts):
    facts_root = companyfacts.get("facts") or {}
    candidates_by_metric = {name: [] for name in FACT_NAMESPACE_ALIASES["us-gaap"]}
    namespace_rank = {"us-gaap": 2, "ifrs-full": 1}
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
    namespace_rank = {"us-gaap": 2, "ifrs-full": 1}
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
                    if not end or r.get("form") not in SNAPSHOT_FORMS:
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
            entry = {"value": row["val"], "unit": row["unit"], "tag": row["tag"], "namespace": row["namespace"], "source": "sec-company-facts", "filed": row["filed"]}
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
            reported_entry = {"value": reported["val"], "unit": reported["unit"], "days": reported["days"], "start": reported["start"], "end": reported["end"], "tag": reported["tag"], "namespace": reported["namespace"], "filed": reported["filed"]}
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
    if liabilities is None or equity is None or equity <= 0:
        return None
    return liabilities / equity * 100.0


def annual_metrics(index, year):
    revenue = latest_annual_value(index, "revenue", year)
    opinc = latest_annual_value(index, "operating_income", year)
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
    equity = latest_annual_value(index, "equity", year)
    equity_nci = latest_annual_value(index, "equity_nci", year)
    equity_row = (index.get("equity") or {}).get(year)
    equity_tag = (equity_row or {}).get("tag") or ""
    if equity is not None and "IncludingPortionAttributableToNoncontrollingInterest" in equity_tag and equity_nci is not None:
        equity = equity - equity_nci
    liabilities = latest_annual_value(index, "liabilities", year)
    current_assets = latest_annual_value(index, "current_assets", year)
    current_liabilities = latest_annual_value(index, "current_liabilities", year)
    cash = latest_annual_value(index, "cash", year)
    receivables = latest_annual_value(index, "receivables", year)
    inventory = latest_annual_value(index, "inventory", year)
    interest = latest_annual_value(index, "interest_expense", year)
    ocf = latest_annual_value(index, "operating_cash_flow", year)
    sga = latest_annual_value(index, "sga", year)
    eps = latest_annual_value(index, "eps", year)
    nopat = opinc * 0.78 if opinc is not None else None
    invested_capital = None
    if equity is not None or liabilities is not None:
        invested_capital = (equity or 0.0) + (liabilities or 0.0) - (cash or 0.0)
        if invested_capital <= 0:
            invested_capital = None
    quick_assets = current_assets - (inventory or 0.0) if current_assets is not None else ((cash or 0.0) + (receivables or 0.0) if cash is not None or receivables is not None else None)
    return {"revenue": revenue, "eps": eps, "revenue_growth": None, "eps_growth": None, "opm": ratio(opinc, revenue, 100.0), "roic": ratio(nopat, invested_capital, 100.0), "debt_rate": debt_rate(liabilities, equity), "quick_ratio": ratio(quick_assets, current_liabilities), "interest_coverage": ratio(opinc, interest), "ocf_ratio": ratio(ocf, net_income), "sga_ratio": ratio(sga, revenue, 100.0), "downturn_defense": None, "roa": ratio(net_income, assets, 100.0), "net_income": net_income, "assets": assets}


def classify_company(submissions):
    sic = submissions.get("sic")
    sic_desc = submissions.get("sicDescription")
    return sic_desc or (f"SIC {sic}" if sic else None)


def fetch_json(session, url, retries=3):
    for attempt in range(retries):
        response = session.get(url, timeout=30)
        if response.status_code == 200:
            return response.json()
        if response.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue
        response.raise_for_status()
    raise RuntimeError(f"SEC request failed after {retries} retries: {url}")


def load_company(session, ticker, cik):
    cik10 = str(cik).zfill(10)
    return fetch_json(session, SEC_FACTS_URL.format(cik=cik10)), fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik10))


def period_metrics(index, latest_year, period):
    all_years = sorted({y for rows in index.values() for y in rows.keys()})
    if latest_year not in all_years:
        return None, {}, None
    base_year = latest_year - period
    if base_year not in all_years:
        return None, {}, None
    candidate_years = [y for y in all_years if base_year <= y <= latest_year]
    yearly = {y: annual_metrics(index, y) for y in candidate_years}
    latest, base = yearly[latest_year], yearly[base_year]
    metrics = dict(latest)
    metrics["revenue_growth"] = growth_cagr(latest.get("revenue"), base.get("revenue"), period)
    metrics["eps_growth"] = growth_cagr(latest.get("eps"), base.get("eps"), period)
    for key in ("opm", "roic", "debt_rate", "quick_ratio", "interest_coverage", "ocf_ratio", "sga_ratio", "roa"):
        metrics[key] = worst_value(key, [yearly[y].get(key) for y in candidate_years])
    return latest_year, metrics, base_year


def build_result(ticker, cik, company_name, facts, submissions, universe_row=None, market_prices=None):
    universe_row = universe_row or {}
    index = build_fact_index(facts)
    all_years = sorted({y for rows in index.values() for y in rows.keys()})
    snapshot = build_latest_snapshot(facts)
    if not all_years:
        return {"ticker": ticker, "cik": str(cik), "company_name": company_name, "sector": classify_company(submissions), "base_year": None, "period_scores": {}, "total_score": None, "grade": None, "data_unavailable": True, "data_reliability": "none", "missing_metric_count": 10, "snapshot": snapshot, "snapshot_fiscal_end": snapshot.get("fiscal_end") if snapshot else None, "snapshot_period": snapshot.get("fiscal_period") if snapshot else None, "snapshot_form": snapshot.get("form") if snapshot else None, "snapshot_filed": snapshot.get("filed") if snapshot else None, "snapshot_basis": snapshot.get("basis") if snapshot else None, "snapshot_updated_at": datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    flow_years = sorted(set(index.get("revenue", {}).keys()) | set(index.get("operating_income", {}).keys()) | set(index.get("net_income", {}).keys()))
    latest_year = max(flow_years) if flow_years else max(all_years)
    profile = universe_row.get("scoring_profile") or "standard"
    downturn_value, downturn_detail = calculate_downturn_defense(ticker, market=(market_prices or {}).get("market"), stock=(market_prices or {}).get("stock"))
    period_scores, latest_score, latest_grade, latest_missing = {}, None, None, 0
    for period in PERIODS:
        used_year, metrics, base_year = period_metrics(index, latest_year, period)
        if not metrics:
            continue
        metrics["downturn_defense"] = downturn_value
        scored = calculate_us_score(metrics, profile=profile)
        period_scores[str(period)] = {"base_year": base_year, "metrics": metrics, "scores": scored}
        if period == 1:
            latest_score, latest_grade = scored["total_score"], scored["grade"]
            latest_missing = scored["missing_metric_count"]
    reliability = "high" if len(period_scores) >= 3 else ("medium" if period_scores else "low")
    return {"ticker": ticker, "cik": str(cik), "company_name": company_name, "sector": universe_row.get("sector_common") or classify_company(submissions), "base_year": latest_year, "period_scores": period_scores, "total_score": int(round(latest_score)) if latest_score is not None else None, "grade": latest_grade, "data_unavailable": not bool(period_scores), "data_reliability": reliability, "missing_metric_count": latest_missing, "updated_at": datetime.now(timezone.utc).isoformat(), "downturn_defense": downturn_value, "downturn_detail": downturn_detail, "snapshot": snapshot, "snapshot_fiscal_end": snapshot.get("fiscal_end") if snapshot else None, "snapshot_period": snapshot.get("fiscal_period") if snapshot else None, "snapshot_form": snapshot.get("form") if snapshot else None, "snapshot_filed": snapshot.get("filed") if snapshot else None, "snapshot_basis": snapshot.get("basis") if snapshot else None, "snapshot_updated_at": datetime.now(timezone.utc).isoformat()}


def get_universe(
    sb,
    tickers=None,
    limit=None,
    all_rows=False,
    exclude_profile=None,
    shard_index=0,
    shard_count=1,
):
    columns = "ticker,cik,company_name,sector_common,company_type,scoring_profile"

    if tickers:
        query = (
            sb.table("US_Companies")
            .select(columns)
            .in_("ticker", tickers)
            .eq("is_fundamental_eligible", True)
        )
        if exclude_profile:
            query = query.neq("scoring_profile", exclude_profile)
        rows = query.execute().data
    else:
        if not all_rows:
            query = (
                sb.table("US_Companies")
                .select(columns)
                .eq("is_fundamental_eligible", True)
                .order("ticker")
            )
            if exclude_profile:
                query = query.neq("scoring_profile", exclude_profile)
            rows = query.limit(limit or 5).execute().data
        else:
            rows = []
            page_size = 1000
            offset = 0
            while True:
                query = (
                    sb.table("US_Companies")
                    .select(columns)
                    .eq("is_fundamental_eligible", True)
                    .order("ticker")
                    .range(offset, offset + page_size - 1)
                )
                if exclude_profile:
                    query = query.neq("scoring_profile", exclude_profile)
                batch = query.execute().data
                rows.extend(batch)
                print(
                    f"[UNIVERSE] fetched {len(batch)} rows "
                    f"(total={len(rows)})"
                )
                if len(batch) < page_size:
                    break
                offset += page_size

    if shard_count < 1:
        raise ValueError("shard_count must be >= 1")
    if not 0 <= shard_index < shard_count:
        raise ValueError("shard_index must be in [0, shard_count)")

    if shard_count > 1:
        rows = rows[shard_index::shard_count]

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--tickers")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--all", action="store_true", dest="all_rows")
    parser.add_argument("--exclude-profile", default=None)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    args = parser.parse_args()
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    tickers = [args.ticker.upper().strip()] if args.ticker else ([x.upper().strip() for x in args.tickers.split(",") if x.strip()] if args.tickers else None)
    rows = get_universe(
        sb,
        tickers=tickers,
        limit=args.limit,
        all_rows=args.all_rows,
        exclude_profile=args.exclude_profile,
        shard_index=args.shard_index,
        shard_count=args.shard_count,
    )
    print(
        f"[UNIVERSE] selected={len(rows)} "
        f"exclude_profile={args.exclude_profile or '-'} "
        f"shard={args.shard_index + 1}/{args.shard_count}"
    )
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
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
            result = build_result(ticker, cik, row.get("company_name") or submissions.get("name") or ticker, facts, submissions, universe_row=row, market_prices={"market": market, "stock": stock_cache.get(ticker)})
            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
            print(f"[{i}/{len(rows)}] {ticker}: score={result['total_score']} grade={result['grade']} periods={len(result['period_scores'])} reliability={result['data_reliability']} snapshot={result.get('snapshot_fiscal_end')}")
        except Exception as exc:
            print(f"[{i}/{len(rows)}] {ticker}: FAILED: {exc}")
        finally:
            # Conservative aggregate SEC request pacing for parallel shards.
            time.sleep(0.6)
    print("Completed.")


if __name__ == "__main__":
    main()
