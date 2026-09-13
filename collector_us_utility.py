"""Production collector for US utility companies.

Uses the utility-specific SEC extraction v3 and utility scoring profile.
Raw SEC JSON is never persisted. Writes only compact US_Fundamental rows.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import requests
from supabase import create_client

from collector_us_fundamental import (
    SUPABASE_URL,
    SUPABASE_KEY,
    SEC_USER_AGENT,
    SEC_FACTS_URL,
    SEC_SUBMISSIONS_URL,
    fetch_json,
)
from downturn_us import BENCHMARK, _close_series, calculate_downturn_defense
from us_scoring_v2 import calculate_us_utility_score_v2
from us_utility_extraction import (
    REVENUE_TAGS,
    OPERATING_INCOME_TAGS,
    NET_INCOME_TAGS,
    ASSETS_TAGS,
    EQUITY_TAGS,
    pick_flow,
    pick_instant,
    pick_eps,
    pick_interest,
    pick_debt,
    pick_ocf,
    pick_capex,
    pick_dividend,
    core_years,
)

SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
PERIODS = (1, 3, 5, 10)

# Auditable annual filing-based capital-expenditure overrides for utilities
# whose consolidated filing presents the relevant cash-use line in a way that
# is not reliably captured by a single standard XBRL capex concept.
# Values are USD millions.
UTILITY_CAPEX_OVERRIDES = {
    "ED": {2021: 3630.0, 2022: 3824.0, 2023: 4353.0, 2024: 4770.0, 2025: 4764.0},
    "NEE": {2021: 16077.0, 2022: 19283.0, 2023: 25113.0, 2024: 24729.0, 2025: 24606.0},
}


def ticker_cik(session, ticker: str) -> str:
    data = fetch_json(session, SEC_TICKERS)
    for item in data.values():
        if str(item.get("ticker", "")).upper() == ticker.upper():
            return str(item["cik_str"]).zfill(10)
    raise RuntimeError(f"CIK not found: {ticker}")


def load_facts(session, ticker: str, cik: str):
    facts = fetch_json(session, SEC_FACTS_URL.format(cik=str(cik).zfill(10)))
    submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=str(cik).zfill(10)))
    return facts, submissions


def value(facts, picker, year, tags=None):
    row = picker(facts, tags, year) if tags is not None else picker(facts, year)
    return row["val"] if row else None


def capex_value(ticker: str, facts, year: int):
    override = UTILITY_CAPEX_OVERRIDES.get(ticker, {}).get(year)
    if override is not None:
        return override * 1_000_000.0
    return value(facts, pick_capex, year)


def growth(cur, base, years):
    if cur is None or base in (None, 0) or years <= 0:
        return None
    if cur > 0 and base > 0:
        return ((cur / base) ** (1.0 / years) - 1.0) * 100.0
    return (cur / base - 1.0) * 100.0


def period_metrics(ticker, facts, latest_year: int, period: int):
    base_year = latest_year - period
    revenue_now = value(facts, pick_flow, latest_year, REVENUE_TAGS)
    revenue_base = value(facts, pick_flow, base_year, REVENUE_TAGS)
    eps_now = value(facts, pick_eps, latest_year)
    eps_base = value(facts, pick_eps, base_year)
    op_now = value(facts, pick_flow, latest_year, OPERATING_INCOME_TAGS)
    ni_now = value(facts, pick_flow, latest_year, NET_INCOME_TAGS)
    assets_now = value(facts, pick_instant, latest_year, ASSETS_TAGS)
    equity_now = value(facts, pick_instant, latest_year, EQUITY_TAGS)
    debt_row = pick_debt(facts, latest_year)
    ocf_now = value(facts, pick_ocf, latest_year)
    capex_now = capex_value(ticker, facts, latest_year)
    div_now = value(facts, pick_dividend, latest_year)
    interest_row = pick_interest(facts, latest_year)
    interest_now = interest_row["val"] if interest_row else None

    if revenue_now is None or revenue_base is None:
        return None, None

    debt_now = debt_row["val"] if debt_row else None
    metrics = {
        "revenue_growth": growth(revenue_now, revenue_base, period),
        "eps_growth": growth(eps_now, eps_base, period),
        "opm": (op_now / revenue_now * 100.0) if op_now is not None and revenue_now else None,
        "roa": (ni_now / assets_now * 100.0) if ni_now is not None and assets_now else None,
        "debt_capital": (debt_now / (debt_now + equity_now) * 100.0) if debt_now is not None and equity_now not in (None, 0) and debt_now + equity_now > 0 else None,
        "ocf_debt": (ocf_now / debt_now * 100.0) if ocf_now is not None and debt_now not in (None, 0) else None,
        "fcf_debt": ((ocf_now - abs(capex_now)) / debt_now * 100.0) if ocf_now is not None and capex_now is not None and debt_now not in (None, 0) else None,
        "dividend_coverage": (ocf_now / abs(div_now)) if ocf_now is not None and div_now not in (None, 0) else None,
        "dividend_payout": (abs(div_now) / ni_now * 100.0) if div_now not in (None, 0) and ni_now is not None and ni_now > 0 else None,
        "interest_coverage": (op_now / abs(interest_now)) if op_now is not None and interest_now not in (None, 0) else None,
    }
    return metrics, base_year


def build_result(ticker, cik, company_name, facts, submissions, market, stock):
    valid_core_years = core_years(facts)
    candidate_years = {y for y in valid_core_years if 2018 <= y <= 2026}
    if not candidate_years:
        return None
    latest_year = max(candidate_years)
    downturn_value, downturn_detail = calculate_downturn_defense(ticker, market=market, stock=stock)

    period_scores = {}
    for period in PERIODS:
        metrics, base_year = period_metrics(ticker, facts, latest_year, period)
        if metrics is None:
            continue
        metrics["downturn_defense"] = downturn_value
        scored = calculate_us_utility_score_v2(metrics)
        period_scores[str(period)] = {"base_year": base_year, "metrics": metrics, "scores": scored}

    latest = period_scores.get("1")
    latest_score = latest["scores"]["total_score"] if latest else None
    latest_grade = latest["scores"]["grade"] if latest else None
    latest_missing = latest["scores"]["missing_metric_count"] if latest else None
    period_unavailable = latest is None
    reliability = "high" if len(period_scores) >= 3 else ("medium" if period_scores else "low")

    return {
        "ticker": ticker,
        "cik": str(cik),
        "company_name": company_name,
        "sector": "utilities",
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
        # Kept inside period_scores/result JSON rather than adding a schema
        # column so this remains backward-compatible with the compact table.
        "extraction_version": "utility_xbrl_v3",
        "period_unavailable": period_unavailable,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--tickers")
    parser.add_argument("--all", action="store_true", dest="all_rows")
    args = parser.parse_args()
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    if args.ticker:
        tickers = [args.ticker.strip().upper()]
    elif args.tickers:
        tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    elif args.all_rows:
        rows = sb.table("US_Companies").select("ticker,cik,company_name").eq("is_fundamental_eligible", True).eq("sector_common", "utilities").order("ticker").execute().data
        tickers = [r["ticker"] for r in rows]
    else:
        raise RuntimeError("Use --ticker, --tickers, or --all")

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    market = _close_series(BENCHMARK)

    for i, ticker in enumerate(tickers, 1):
        try:
            cik = ticker_cik(session, ticker)
            facts, submissions = load_facts(session, ticker, cik)
            stock = _close_series(ticker)
            result = build_result(ticker, cik, submissions.get("name") or ticker, facts, submissions, market, stock)
            if result is None:
                print(f"[{i}/{len(tickers)}] {ticker}: no core annual facts")
                continue
            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
            print(f"[{i}/{len(tickers)}] {ticker}: score={result['total_score']} grade={result['grade']} periods={len(result['period_scores'])} missing={result['missing_metric_count']}")
        except Exception as exc:
            print(f"[{i}/{len(tickers)}] {ticker}: FAILED: {exc}")

    print("Completed: utility production collector v3")


if __name__ == "__main__":
    main()
