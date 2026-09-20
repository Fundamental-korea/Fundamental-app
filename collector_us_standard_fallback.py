"""Standard-sector collector integration layer.

Keeps the existing collector/scoring logic intact while adding the validated
SEC XBRL V2.3.8 fallback only for missing Standard-sector metrics.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

import requests

from collector_us_fundamental import (
    SUPABASE_URL, SUPABASE_KEY, SEC_USER_AGENT, PERIODS,
    build_fact_index, build_result as _build_result, build_latest_snapshot, load_company,
)
from supabase import create_client
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

STANDARD_SECTORS = (
    "technology", "healthcare", "consumer", "industrials",
    "energy", "materials", "communication",
)

STANDARD_METRICS = (
    "revenue", "eps", "operating_income", "liabilities", "current_assets",
    "current_liabilities", "cash", "receivables", "inventory",
    "interest_expense", "operating_cash_flow", "sga",
)


def _candidate_field(candidate, key, default=None):
    if candidate is None:
        return default
    if isinstance(candidate, dict):
        return candidate.get(key, default)
    return getattr(candidate, key, default)


def _candidate_to_row(candidate):
    value = _candidate_field(candidate, "value")
    if value is None:
        return None
    end = _candidate_field(candidate, "end")
    return {
        "fy": _candidate_field(candidate, "fy"), "year": int(end[:4]) if end else None,
        "end": end, "filed": _candidate_field(candidate, "filed", "") or "",
        "val": float(value), "form": _candidate_field(candidate, "form"), "frame": None,
        "unit": _candidate_field(candidate, "unit"), "namespace": _candidate_field(candidate, "namespace", ""),
        "tag": _candidate_field(candidate, "concept", ""), "source": _candidate_field(candidate, "source"),
    }


def augment_index_with_v238(index, resolver, cik, latest_year):
    target_years = {latest_year, *(latest_year - p for p in PERIODS)}
    for metric in STANDARD_METRICS:
        metric_rows = index.setdefault(metric, {})
        for year in sorted(target_years):
            if year in metric_rows:
                continue
            try:
                resolved = resolver.resolve(cik, metric, year=year)
                candidate = resolved.get("best") if isinstance(resolved, dict) else resolved
            except Exception as exc:
                print(f"[XBRL fallback] CIK={cik} metric={metric} year={year}: {exc}")
                continue
            row = _candidate_to_row(candidate)
            if row is not None:
                metric_rows[year] = row
                print(f"[XBRL fallback] CIK={cik} metric={metric} year={year}: {_candidate_field(candidate, 'concept')}={_candidate_field(candidate, 'value')} source={_candidate_field(candidate, 'source')}")
    return index


def _snapshot_fields(snapshot):
    return {
        "snapshot": snapshot,
        "snapshot_fiscal_end": snapshot.get("fiscal_end") if snapshot else None,
        "snapshot_period": snapshot.get("fiscal_period") if snapshot else None,
        "snapshot_form": snapshot.get("form") if snapshot else None,
        "snapshot_filed": snapshot.get("filed") if snapshot else None,
        "snapshot_basis": snapshot.get("basis") if snapshot else None,
        "snapshot_updated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_result_with_v238(ticker, cik, company_name, facts, submissions,
                           universe_row=None, market_prices=None, resolver=None):
    index = build_fact_index(facts)
    snapshot = build_latest_snapshot(facts)

    if resolver is not None:
        flow_years = sorted(
            set(index.get("revenue", {}).keys())
            | set(index.get("operating_income", {}).keys())
            | set(index.get("net_income", {}).keys())
        )
        if flow_years:
            augment_index_with_v238(index, resolver, cik, max(flow_years))

    # Reuse the canonical US period/scoring builder so Standard-sector fallback
    # writes the same 1y/3y/5y/10y -> avg/worst structure as the normal collector.
    from collector_us_fundamental import (
        period_metrics_pair,
        calculate_us_score,
        calculate_downturn_defense,
    )

    universe_row = universe_row or {}
    all_years = sorted({y for rows in index.values() for y in rows.keys()})
    if not all_years:
        result = _build_result(
            ticker, cik, company_name, facts, submissions,
            universe_row, market_prices,
        )
        result.update(_snapshot_fields(snapshot))
        return result

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

    period_scores = {}
    latest_score = latest_grade = None
    latest_missing = 0

    for period in PERIODS:
        pdata = period_metrics_pair(index, latest_year, period)
        if pdata is None:
            continue

        avg_metrics = dict(pdata["avg_metrics"])
        worst_metrics = dict(pdata["worst_metrics"])
        avg_metrics["downturn_defense"] = downturn_value
        worst_metrics["downturn_defense"] = downturn_value

        avg_score = calculate_us_score(avg_metrics, profile=profile)
        worst_score = calculate_us_score(worst_metrics, profile=profile)

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
            },
            "worst": {
                "total_score": worst_score["total_score"],
                "grade": worst_score["grade"],
                "metric_scores": worst_score["metric_scores"],
                "sub_scores": worst_score.get("sub_scores", {}),
                "financial_adjusted": False,
                "missing_metric_count": worst_score["missing_metric_count"],
                "scoring_version": worst_score["scoring_version"],
            },
        }

        if period == 1:
            latest_score = avg_score["total_score"]
            latest_grade = avg_score["grade"]
            latest_missing = avg_score["missing_metric_count"]

    result = {
        "ticker": ticker,
        "cik": str(cik),
        "company_name": company_name,
        "sector": universe_row.get("sector_common") or submissions.get("sicDescription"),
        "base_year": latest_year,
        "period_scores": period_scores,
        "total_score": int(round(latest_score)) if latest_score is not None else None,
        "grade": latest_grade,
        "data_unavailable": not bool(period_scores),
        "data_reliability": (
            "high" if len(period_scores) >= 3
            else ("medium" if period_scores else "low")
        ),
        "missing_metric_count": latest_missing,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "downturn_defense": downturn_value,
        "downturn_detail": downturn_detail,
    }
    result.update(_snapshot_fields(snapshot))
    return result



def get_standard_universe(sb, tickers=None, limit=None, all_rows=False):
    """Return only eligible companies in the seven Standard sectors with pagination."""
    columns = "ticker,cik,company_name,sector_common,company_type,scoring_profile"
    if tickers:
        rows = []
        page_size = 1000
        for offset in range(0, len(tickers), page_size):
            batch = tickers[offset:offset + page_size]
            rows.extend(sb.table("US_Companies").select(columns).in_("ticker", batch).eq("is_fundamental_eligible", True).in_("sector_common", STANDARD_SECTORS).execute().data)
        return rows
    if not all_rows:
        return sb.table("US_Companies").select(columns).eq("is_fundamental_eligible", True).in_("sector_common", STANDARD_SECTORS).order("ticker").limit(limit or 5).execute().data
    rows, page_size, offset = [], 1000, 0
    while True:
        batch = sb.table("US_Companies").select(columns).eq("is_fundamental_eligible", True).in_("sector_common", STANDARD_SECTORS).order("ticker").range(offset, offset + page_size - 1).execute().data
        rows.extend(batch)
        print(f"[UNIVERSE] fetched {len(batch)} rows (total={len(rows)})")
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


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
    rows = get_standard_universe(sb, tickers=tickers, limit=args.limit, all_rows=args.all_rows)
    print(f"[UNIVERSE] Standard companies selected: {len(rows)}")
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
        ticker, cik = row["ticker"], row["cik"]
        try:
            facts, submissions = load_company(session, ticker, cik)
            if ticker not in stock_cache:
                try:
                    from downturn_us import _close_series
                    stock_cache[ticker] = _close_series(ticker)
                except Exception:
                    stock_cache[ticker] = None
            result = build_result_with_v238(ticker, cik, row.get("company_name") or submissions.get("name") or ticker, facts, submissions, universe_row=row, market_prices={"market": market, "stock": stock_cache.get(ticker)}, resolver=resolver)
            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
            print(f"[{i}/{len(rows)}] {ticker}: score={result['total_score']} grade={result['grade']} periods={len(result['period_scores'])} reliability={result['data_reliability']} snapshot={result.get('snapshot_fiscal_end')}")
        except Exception as exc:
            print(f"[{i}/{len(rows)}] {ticker}: FAILED: {exc}")
    print("Completed.")


if __name__ == "__main__":
    main()
