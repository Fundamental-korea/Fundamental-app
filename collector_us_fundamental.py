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

FACT_ALIASES = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "assets": ["Assets"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "liabilities": ["Liabilities"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "inventory": ["InventoryNet", "InventoryGross"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "receivables": ["AccountsReceivableNetCurrent", "AccountsReceivableNet", "AccountsAndNotesReceivableNetCurrent", "AccountsReceivableGrossCurrent"],
    "interest_expense": ["InterestExpenseNonOperating", "InterestExpenseDebt", "InterestExpenseNonOperatingNet", "InterestExpenseNonOperatingAndOther"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "sga": ["SellingGeneralAndAdministrativeExpense", "SellingGeneralAndAdministrativeExpenseIncludingDepreciationAmortization"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
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
    if value is None or not math.isfinite(value) or abs(value) > 500:
        return None
    return value


def _record_quality(record):
    form = record.get("form") or ""
    form_rank = 1 if form.endswith("/A") else 2
    frame_rank = 1 if (record.get("frame") or "").startswith("CY") else 0
    return (record.get("end", ""), record.get("filed", ""), form_rank, frame_rank)


def annual_records(fact):
    units = fact.get("units") or {}
    records = []
    for unit, rows in units.items():
        if not isinstance(rows, list):
            continue
        for r in rows:
            fy, form, end = r.get("fy"), r.get("form"), r.get("end")
            if not fy or not end or form not in FLOW_FORMS:
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
            records.append({"fy": int(fy), "end": end, "filed": r.get("filed") or "", "val": value, "form": form, "frame": r.get("frame"), "unit": unit})
    by_fy = {}
    for record in records:
        previous = by_fy.get(record["fy"])
        if previous is None or _record_quality(record) > _record_quality(previous):
            by_fy[record["fy"]] = record
    return by_fy


def build_fact_index(companyfacts):
    facts = (companyfacts.get("facts") or {}).get("us-gaap") or {}
    all_tag_rows, all_years = {}, set()
    for tag, fact in facts.items():
        rows = annual_records(fact)
        if rows:
            all_tag_rows[tag] = rows
            all_years.update(rows.keys())
    latest_year = max(all_years) if all_years else None
    index = {}
    for logical_name, aliases in FACT_ALIASES.items():
        candidates = []
        for priority, tag in enumerate(aliases):
            rows = all_tag_rows.get(tag)
            if not rows:
                continue
            latest_present = max(rows.keys())
            coverage = len(rows)
            latest_distance = (latest_year - latest_present) if latest_year is not None else 999
            candidates.append((1 if latest_present == latest_year else 0, -latest_distance, coverage, -priority, tag, rows))
        index[logical_name] = sorted(candidates, reverse=True)[0][5] if candidates else {}
    return index


def latest_annual_value(index, metric, year):
    row = (index.get(metric) or {}).get(year)
    return row["val"] if row else None


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


def annual_metrics(index, year):
    revenue = latest_annual_value(index, "revenue", year)
    opinc = latest_annual_value(index, "operating_income", year)
    net_income = latest_annual_value(index, "net_income", year)
    assets = latest_annual_value(index, "assets", year)
    equity = latest_annual_value(index, "equity", year)
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
    return {
        "revenue": revenue, "eps": eps, "revenue_growth": None, "eps_growth": None,
        "opm": ratio(opinc, revenue, 100.0), "roic": ratio(nopat, invested_capital, 100.0),
        "debt_rate": ratio(liabilities, equity, 100.0), "quick_ratio": ratio(quick_assets, current_liabilities),
        "interest_coverage": ratio(opinc, interest), "ocf_ratio": ratio(ocf, net_income),
        "sga_ratio": ratio(sga, revenue, 100.0), "downturn_defense": None,
        "roa": ratio(net_income, assets, 100.0), "net_income": net_income, "assets": assets,
    }


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
    if not all_years:
        return {"ticker": ticker, "cik": str(cik), "company_name": company_name, "sector": classify_company(submissions), "base_year": None, "period_scores": {}, "total_score": None, "grade": None, "data_unavailable": True, "data_reliability": "none", "missing_metric_count": 10, "updated_at": datetime.now(timezone.utc).isoformat()}
    flow_years = sorted(set(index.get("revenue", {}).keys()) | set(index.get("operating_income", {}).keys()) | set(index.get("net_income", {}).keys()))
    latest_year = max(flow_years) if flow_years else max(all_years)
    profile = universe_row.get("scoring_profile") or "standard"

    # Downturn is a market-price characteristic, not an SEC metric. Calculate it once
    # and reuse the same value across the 1/3/5/10-year fundamental periods.
    downturn_value, downturn_detail = calculate_downturn_defense(
        ticker,
        market=(market_prices or {}).get("market"),
        stock=(market_prices or {}).get("stock"),
    )

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
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "downturn_defense": downturn_value,
        "downturn_detail": downturn_detail,
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
    market = None
    stock_cache = {}
    try:
        import yfinance as yf
        from downturn_us import _close_series, BENCHMARK
        market = _close_series(BENCHMARK)
    except Exception as exc:
        print(f"[US] downturn market data unavailable: {exc}")

    success = failed = 0
    for row in rows:
        try:
            facts, submissions = load_company(session, row["ticker"], row["cik"])
            stock = None
            try:
                from downturn_us import _close_series
                stock = _close_series(row["ticker"])
            except Exception as exc:
                print(f"[US] {row['ticker']}: downturn price data unavailable: {exc}")
            result = build_result(
                row["ticker"], row["cik"], row["company_name"], facts, submissions,
                universe_row=row,
                market_prices={"market": market, "stock": stock},
            )
            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
            print(f"[US] {row['ticker']}: profile={row.get('scoring_profile') or 'standard'} score={result['total_score']} grade={result['grade']} periods={len(result['period_scores'])} missing={result['missing_metric_count']} downturn={result['downturn_defense']}")
            success += 1
            time.sleep(0.15)
        except Exception as exc:
            failed += 1
            print(f"[US] {row['ticker']}: FAILED: {exc}")
    print(f"Completed. success={success}, failed={failed}, total={len(rows)}")


if __name__ == "__main__":
    main()
