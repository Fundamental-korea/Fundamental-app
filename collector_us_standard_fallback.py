"""Standard-sector collector integration layer.

Keeps the existing collector/scoring logic intact while adding the validated
SEC XBRL V2.3.8 fallback only for missing Standard-sector metrics.
"""
from __future__ import annotations

import argparse

import requests

from collector_us_fundamental import (
    SUPABASE_URL,
    SUPABASE_KEY,
    SEC_USER_AGENT,
    PERIODS,
    build_fact_index,
    build_result as _build_result,
    fetch_json,
    get_universe,
    load_company,
)
from supabase import create_client
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

STANDARD_METRICS = (
    "revenue",
    "eps",
    "operating_income",
    "liabilities",
    "current_assets",
    "current_liabilities",
    "cash",
    "receivables",
    "inventory",
    "interest_expense",
    "operating_cash_flow",
    "sga",
)


def _candidate_to_row(candidate):
    if candidate is None or candidate.value is None:
        return None
    return {
        "fy": candidate.fy,
        "year": int(candidate.end[:4]) if candidate.end else None,
        "end": candidate.end,
        "filed": candidate.filed or "",
        "val": float(candidate.value),
        "form": candidate.form,
        "frame": None,
        "unit": candidate.unit,
        "namespace": candidate.namespace,
        "tag": candidate.concept,
        "source": candidate.source,
    }


def augment_index_with_v238(index, resolver, cik, latest_year):
    """Fill only missing Standard metrics for the years used by period scoring."""
    target_years = {latest_year}
    for period in PERIODS:
        target_years.add(latest_year - period)

    for metric in STANDARD_METRICS:
        metric_rows = index.setdefault(metric, {})
        for year in sorted(target_years):
            if year in metric_rows:
                continue
            try:
                candidate = resolver.resolve(cik, metric, year=year)
            except Exception as exc:
                print(f"[XBRL fallback] CIK={cik} metric={metric} year={year}: {exc}")
                continue
            row = _candidate_to_row(candidate)
            if row is not None:
                metric_rows[year] = row
                print(
                    f"[XBRL fallback] CIK={cik} metric={metric} year={year}: "
                    f"{candidate.concept}={candidate.value} source={candidate.source}"
                )
    return index


def build_result_with_v238(ticker, cik, company_name, facts, submissions,
                           universe_row=None, market_prices=None, resolver=None):
    index = build_fact_index(facts)
    if resolver is not None:
        flow_years = sorted(
            set(index.get("revenue", {}).keys())
            | set(index.get("operating_income", {}).keys())
            | set(index.get("net_income", {}).keys())
        )
        if flow_years:
            latest_year = max(flow_years)
            augment_index_with_v238(index, resolver, cik, latest_year)

    # Reuse the production collector's scoring/result construction without
    # duplicating scoring logic here.
    # build_result() rebuilds the index internally, so temporarily pass a
    # companyfacts object enriched with the fallback values instead.
    # Instead of mutating raw SEC payloads, construct a compact facts object
    # from the enriched index for the existing collector is unsafe; therefore
    # this integration layer currently reproduces the final period/scoring
    # stage below via the original module's pure functions.
    from collector_us_fundamental import (
        annual_metrics,
        period_metrics,
        worst_value,
    )
    from downturn_us import calculate_downturn_defense
    from us_scoring import calculate_us_score
    from datetime import datetime, timezone

    universe_row = universe_row or {}
    all_years = sorted({y for rows in index.values() for y in rows.keys()})
    if not all_years:
        return _build_result(ticker, cik, company_name, facts, submissions, universe_row, market_prices)
    flow_years = sorted(
        set(index.get("revenue", {}).keys())
        | set(index.get("operating_income", {}).keys())
        | set(index.get("net_income", {}).keys())
    )
    latest_year = max(flow_years) if flow_years else max(all_years)
    profile = universe_row.get("scoring_profile") or "standard"

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
        "sector": universe_row.get("sector_common") or submissions.get("sicDescription"),
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
    tickers = [args.ticker.upper().strip()] if args.ticker else (
        [x.upper().strip() for x in args.tickers.split(",") if x.strip()]
        if args.tickers else None
    )
    rows = get_universe(sb, tickers=tickers, limit=args.limit, all_rows=args.all_rows)

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    resolver = SECXBRLSearchV2_3_8(session=session)

    market = None
    stock_cache = {}
    try:
        from downturn_us import _close_series, BENCHMARK
        market = _close_series(BENCHMARK)
    except Exception as exc:
        print(f"[US] downturn market data unavailable: {exc}")

    for i, row in enumerate(rows, 1):
        ticker = row["ticker"]
        cik = row["cik"]
        try:
            facts, submissions = load_company(session, ticker, cik)
            if ticker not in stock_cache:
                try:
                    from downturn_us import _close_series
                    stock_cache[ticker] = _close_series(ticker)
                except Exception:
                    stock_cache[ticker] = None

            result = build_result_with_v238(
                ticker,
                cik,
                row.get("company_name") or submissions.get("name") or ticker,
                facts,
                submissions,
                universe_row=row,
                market_prices={"market": market, "stock": stock_cache.get(ticker)},
                resolver=resolver,
            )
            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
            print(
                f"[{i}/{len(rows)}] {ticker}: score={result['total_score']} "
                f"grade={result['grade']} periods={len(result['period_scores'])} "
                f"reliability={result['data_reliability']}"
            )
        except Exception as exc:
            print(f"[{i}/{len(rows)}] {ticker}: FAILED: {exc}")

    print("Completed.")


if __name__ == "__main__":
    main()
