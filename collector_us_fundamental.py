"""
US fundamental collector (MVP)

SEC Company Facts -> annual financials -> shared scoring.py -> one row/company.
Raw SEC JSON is never written to Supabase.

Usage:
  python collector_us_fundamental.py --ticker AAPL
  python collector_us_fundamental.py --tickers AAPL,MSFT,NVDA
  python collector_us_fundamental.py --limit 5
  python collector_us_fundamental.py --all
"""

import argparse
import math
import os
import time
from datetime import datetime, timezone

import requests
from supabase import create_client

from scoring import calculate_fundamental_score, worst_value

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")

SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

PERIODS = (1, 3, 5, 10)
FLOW_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

FACT_ALIASES = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "assets": ["Assets"],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "liabilities": ["Liabilities"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "inventory": ["InventoryNet", "InventoryGross"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
    "receivables": [
        "AccountsReceivableNetCurrent",
        "AccountsReceivableNet",
        "AccountsAndNotesReceivableNetCurrent",
        "AccountsReceivableGrossCurrent",
    ],
    "interest_expense": [
        "InterestExpenseNonOperating",
        "InterestExpenseDebt",
        "InterestExpenseNonOperatingNet",
        "InterestExpenseNonOperatingAndOther",
    ],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "sga": [
        "SellingGeneralAndAdministrativeExpense",
        "SellingGeneralAndAdministrativeExpenseIncludingDepreciationAmortization",
    ],
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


def annual_records(fact):
    """Return one best annual record per fiscal year."""
    units = fact.get("units") or {}
    records = []

    for unit, rows in units.items():
        if not isinstance(rows, list):
            continue
        for r in rows:
            fy = r.get("fy")
            form = r.get("form")
            end = r.get("end")
            filed = r.get("filed")
            if not fy or not end or form not in FLOW_FORMS:
                continue

            start = r.get("start")
            if start:
                try:
                    days = (
                        datetime.fromisoformat(end).date()
                        - datetime.fromisoformat(start).date()
                    ).days
                except ValueError:
                    continue
                if not 300 <= days <= 380:
                    continue

            value = clean_number(r.get("val"))
            if value is None:
                continue

            records.append({
                "fy": int(fy),
                "end": end,
                "filed": filed or "",
                "val": value,
                "form": form,
                "frame": r.get("frame"),
                "unit": unit,
            })

    # Prefer the latest filing for a fiscal year/end. If several units exist,
    # the last record is only used after sorting deterministically.
    records.sort(key=lambda x: (x["fy"], x["end"], x["filed"], x["unit"]))

    by_fy = {}
    for r in records:
        by_fy[r["fy"]] = r
    return by_fy


def build_fact_index(companyfacts):
    facts = (companyfacts.get("facts") or {}).get("us-gaap") or {}
    index = {}

    for logical_name, aliases in FACT_ALIASES.items():
        best = {}
        best_tag = None
        for tag in aliases:
            fact = facts.get(tag)
            if not fact:
                continue
            rows = annual_records(fact)
            if len(rows) > len(best):
                best = rows
                best_tag = tag
        index[logical_name] = best

    return index


def latest_annual_value(index, metric, year):
    row = (index.get(metric) or {}).get(year)
    return row["val"] if row else None


def growth_cagr(current, base, years):
    current = clean_number(current)
    base = clean_number(base)
    if current is None or base is None or years <= 0 or base == 0:
        return None

    if current > 0 and base > 0:
        return sanitize_growth(((current / base) ** (1.0 / years) - 1.0) * 100.0)

    return sanitize_growth((current / base - 1.0) * 100.0)


def ratio(numerator, denominator, multiplier=1.0):
    numerator = clean_number(numerator)
    denominator = clean_number(denominator)
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
    current_liabilities = latest_annual_value(index, "current_liabilities", year)
    cash = latest_annual_value(index, "cash", year)
    receivables = latest_annual_value(index, "receivables", year)
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

    quick_assets = None
    if cash is not None or receivables is not None:
        quick_assets = (cash or 0.0) + (receivables or 0.0)

    return {
        "revenue": revenue,
        "eps": eps,
        "revenue_growth": None,
        "eps_growth": None,
        "opm": ratio(opinc, revenue, 100.0),
        "roic": ratio(nopat, invested_capital, 100.0),
        "debt_rate": ratio(liabilities, equity, 100.0),
        "quick_ratio": ratio(quick_assets, current_liabilities, 100.0),
        "interest_coverage": ratio(opinc, interest),
        "ocf_ratio": ratio(ocf, net_income),
        "sga_ratio": ratio(sga, revenue, 100.0),
        "downturn_defense": None,
        "roa": ratio(net_income, assets, 100.0),
        "net_income": net_income,
        "assets": assets,
    }


def classify_company(submissions):
    sic = submissions.get("sic")
    sic_desc = submissions.get("sicDescription")
    return sic_desc or (f"SIC {sic}" if sic else None)


def fetch_json(session, url, retries=3):
    for attempt in range(retries):
        r = session.get(url, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
    raise RuntimeError(f"SEC request failed after {retries} retries: {url}")


def load_company(session, ticker, cik):
    cik10 = str(cik).zfill(10)
    facts = fetch_json(session, SEC_FACTS_URL.format(cik=cik10))
    submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik10))
    return facts, submissions


def period_metrics(index, latest_year, period):
    """Build a period using the nearest available annual base year.

    We do not require revenue AND EPS to exist for the same year just to create
    a period. Each growth metric is independently allowed to be missing.
    """
    all_years = sorted({y for rows in index.values() for y in rows.keys()})
    if latest_year not in all_years:
        return None, {}, None

    target_base = latest_year - period
    prior_years = [y for y in all_years if y <= target_base and y < latest_year]
    if prior_years:
        base_year = max(prior_years)
    else:
        earlier = [y for y in all_years if y < latest_year]
        if not earlier:
            base_year = None
        else:
            base_year = min(earlier)

    candidate_years = [y for y in all_years if base_year is not None and base_year <= y <= latest_year]
    if not candidate_years:
        candidate_years = [latest_year]

    yearly = {y: annual_metrics(index, y) for y in candidate_years}
    latest = yearly[latest_year]
    base = yearly.get(base_year) if base_year is not None else None

    actual_years = (latest_year - base_year) if base_year is not None else 0
    metrics = dict(latest)
    metrics["revenue_growth"] = growth_cagr(
        latest.get("revenue"),
        base.get("revenue") if base else None,
        actual_years,
    )
    metrics["eps_growth"] = growth_cagr(
        latest.get("eps"),
        base.get("eps") if base else None,
        actual_years,
    )

    ratio_keys = (
        "opm", "roic", "debt_rate", "quick_ratio",
        "interest_coverage", "ocf_ratio", "sga_ratio", "roa",
    )
    for key in ratio_keys:
        metrics[key] = worst_value(key, [yearly[y].get(key) for y in candidate_years])

    return latest_year, metrics, base_year


def build_result(ticker, cik, company_name, facts, submissions):
    index = build_fact_index(facts)
    all_years = sorted({y for rows in index.values() for y in rows.keys()})

    if not all_years:
        return {
            "ticker": ticker,
            "cik": str(cik),
            "company_name": company_name,
            "sector": classify_company(submissions),
            "base_year": None,
            "period_scores": {},
            "total_score": None,
            "grade": None,
            "data_unavailable": True,
            "data_reliability": "none",
            "missing_metric_count": 10,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    # Prefer the latest year for which an actual income-statement flow exists.
    flow_years = sorted(
        set(index.get("revenue", {}).keys())
        | set(index.get("operating_income", {}).keys())
        | set(index.get("net_income", {}).keys())
    )
    latest_year = max(flow_years) if flow_years else max(all_years)

    period_scores = {}
    latest_score = None
    latest_grade = None
    latest_missing = 10

    for period in PERIODS:
        used_year, metrics, base_year = period_metrics(index, latest_year, period)
        if not metrics:
            continue

        scored = calculate_fundamental_score(
            metrics,
            leverage_exempt=False,
            is_financial=False,
        )

        score_entries = scored.get("scores", {})
        missing = sum(
            1
            for key in score_entries
            if score_entries[key].get("value") is None
        )

        period_scores[str(period)] = {
            "base_year": base_year,
            "metrics": metrics,
            "scores": scored,
        }

        if period == 1:
            latest_score = scored.get("total_score")
            latest_grade = scored.get("grade")
            latest_missing = missing

    reliability = "high" if len(period_scores) >= 3 else ("medium" if period_scores else "low")

    return {
        "ticker": ticker,
        "cik": str(cik),
        "company_name": company_name,
        "sector": classify_company(submissions),
        "base_year": latest_year,
        "period_scores": period_scores,
        "total_score": int(round(latest_score)) if latest_score is not None else None,
        "grade": latest_grade,
        "data_unavailable": not bool(period_scores),
        "data_reliability": reliability,
        "missing_metric_count": latest_missing,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_universe(sb, tickers=None, limit=None, all_rows=False):
    if tickers:
        return (
            sb.table("US_Companies")
            .select("ticker,cik,company_name")
            .in_("ticker", tickers)
            .eq("is_fundamental_eligible", True)
            .execute()
            .data
        )

    q = (
        sb.table("US_Companies")
        .select("ticker,cik,company_name")
        .eq("is_fundamental_eligible", True)
        .order("ticker")
    )
    if not all_rows:
        q = q.limit(limit or 5)
    return q.execute().data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="single ticker")
    parser.add_argument("--tickers", help="comma-separated tickers")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--all", action="store_true", dest="all_rows")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    tickers = None
    if args.ticker:
        tickers = [args.ticker.upper().strip()]
    elif args.tickers:
        tickers = [x.upper().strip() for x in args.tickers.split(",") if x.strip()]

    rows = get_universe(sb, tickers=tickers, limit=args.limit, all_rows=args.all_rows)
    session = requests.Session()
    session.headers.update({
        "User-Agent": SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
    })

    success = 0
    failed = 0

    for row in rows:
        ticker = row["ticker"]
        try:
            facts, submissions = load_company(session, ticker, row["cik"])
            result = build_result(
                ticker,
                row["cik"],
                row["company_name"],
                facts,
                submissions,
            )

            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()

            print(
                f"[US] {ticker}: score={result['total_score']} "
                f"grade={result['grade']} periods={len(result['period_scores'])} "
                f"missing={result['missing_metric_count']}"
            )
            success += 1
        except Exception as exc:
            failed += 1
            print(f"[US] {ticker}: FAILED - {type(exc).__name__}: {exc}")

        time.sleep(0.15)

    print(f"Completed. success={success}, failed={failed}, total={len(rows)}")


if __name__ == "__main__":
    main()
