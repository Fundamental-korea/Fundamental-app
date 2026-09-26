"""Deterministic US score recovery from the existing annual raw layer.

No SEC/network calls are made. This runner only recomputes score metrics that
are derivable from values already stored in US_Fundamental_Annual, preserving
all previously populated metric values and the market-derived downturn metric.
Utility rows are intentionally skipped because they use a separate specialized
scoring structure.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone

from supabase import create_client

import collector_us_fundamental as base
from us_scoring import calculate_us_score, data_reliability_from_periods

SUPABASE_URL = base.SUPABASE_URL
SUPABASE_KEY = base.SUPABASE_KEY
PROFILES = {"standard", "defense", "financial", "reit", "bdc"}
PAGE_SIZE = 500
TICKER_BATCH = 100


def fetch_rows(sb, table: str, columns: str, *, filters=None, order_col=None):
    rows = []
    offset = 0
    while True:
        q = sb.table(table).select(columns)
        for method, args in (filters or []):
            q = getattr(q, method)(*args)
        if order_col:
            q = q.order(order_col)
        page = q.range(offset, offset + PAGE_SIZE - 1).execute().data or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def canonical_to_index(rows):
    index = defaultdict(dict)
    for row in rows:
        year = row.get("fiscal_year")
        if year is None:
            continue
        year = int(year)
        canonical = row.get("canonical") or {}
        provenance = row.get("provenance") or {}
        for field, value in canonical.items():
            if value is None:
                continue
            p = provenance.get(field) or {}
            index[field][year] = {
                "val": value,
                "tag": p.get("concept") or "",
                "unit": p.get("unit"),
                "end": p.get("end"),
                "filed": p.get("filed"),
                "form": p.get("form"),
                "fy": p.get("fy"),
                "start": p.get("start"),
            }
    return dict(index)


def old_metric_value(period, side, metric):
    entry = ((period.get(side) or {}).get("metric_scores") or {}).get(metric) or {}
    return entry.get("value")


def merge_metric_values(calculated, old_period, side):
    old_scores = (old_period.get(side) or {}).get("metric_scores") or {}
    merged = {}
    for metric, value in calculated.items():
        old = old_scores.get(metric) or {}
        merged[metric] = value if old.get("value") is None else old.get("value")
    return merged


def period_recovery(index, old_period, period, profile):
    flow_years = sorted(
        set(index.get("revenue", {}).keys())
        | set(index.get("operating_income", {}).keys())
        | set(index.get("net_income", {}).keys())
    )
    if not flow_years:
        return old_period, False

    latest_year = max(flow_years)
    pair = base.period_metrics_pair(index, latest_year, period)
    if pair is None:
        return old_period, False

    old_period = old_period or {}
    old_avg = old_period.get("avg") or {}
    old_worst = old_period.get("worst") or {}

    avg_metrics = dict(pair["avg_metrics"])
    worst_metrics = dict(pair["worst_metrics"])

    # Preserve the existing market-derived downturn value. This runner never
    # invents price/market information from accounting data.
    downturn_avg = old_metric_value(old_period, "avg", "downturn_defense")
    downturn_worst = old_metric_value(old_period, "worst", "downturn_defense")
    avg_metrics["downturn_defense"] = downturn_avg
    worst_metrics["downturn_defense"] = downturn_worst

    merged_avg = merge_metric_values(avg_metrics, old_period, "avg")
    merged_worst = merge_metric_values(worst_metrics, old_period, "worst")

    before_missing = {
        m
        for m in ((old_avg.get("metric_scores") or {}).keys())
        if old_metric_value(old_period, "avg", m) is None
    }

    newly_available = {
        m for m, value in avg_metrics.items()
        if old_metric_value(old_period, "avg", m) is None and value is not None
    }

    if not newly_available:
        return old_period, False

    avg_score = calculate_us_score(merged_avg, profile=profile)
    worst_score = calculate_us_score(merged_worst, profile=profile)

    # Preserve previously computed yearly values and add only missing ones.
    yearly = dict(old_period.get("yearly_breakdown") or {})
    for metric, values in (pair.get("yearly_breakdown") or {}).items():
        target = dict(yearly.get(metric) or {})
        for year, value in (values or {}).items():
            if target.get(str(year)) is None and value is not None:
                target[str(year)] = value
        yearly[metric] = target

    result = {
        "years_used": pair["years_used"],
        "yearly_breakdown": yearly,
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
    return result, True


def recover_one(sb, fundamental, annual_rows, profile):
    index = canonical_to_index(annual_rows)
    if not index:
        return None

    old_periods = fundamental.get("period_scores") or {}
    new_periods = dict(old_periods)
    changed = False

    # Main collector uses 1y/3y/5y/10y keys for non-utility profiles.
    for period in (1, 3, 5, 10):
        key = f"{period}y"
        recovered, did_change = period_recovery(
            index,
            old_periods.get(key) or {},
            period,
            profile,
        )
        new_periods[key] = recovered
        changed = changed or did_change

    if not changed:
        return None

    latest = (new_periods.get("1y") or {}).get("avg") or {}
    total_score = latest.get("total_score")
    grade = latest.get("grade")
    missing_count = latest.get("missing_metric_count")

    result = dict(fundamental)
    result.update({
        "ticker": fundamental["ticker"],
        "period_scores": new_periods,
        "total_score": int(round(total_score)) if total_score is not None else fundamental.get("total_score"),
        "grade": grade or fundamental.get("grade"),
        "missing_metric_count": missing_count,
        "data_unavailable": False,
        "data_reliability": data_reliability_from_periods(new_periods),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-missing", type=int, default=1)
    parser.add_argument("--max-missing", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    fundamentals = fetch_rows(
        sb,
        "US_Fundamental",
        "*",
        filters=[
            ("eq", ("data_unavailable", False)),
            ("gte", ("missing_metric_count", args.min_missing)),
            ("lte", ("missing_metric_count", args.max_missing)),
        ],
        order_col="ticker",
    )

    meta_rows = fetch_rows(
        sb,
        "US_Companies",
        "ticker,scoring_profile,company_type",
        filters=[("eq", ("is_fundamental_eligible", True))],
        order_col="ticker",
    )
    meta = {r["ticker"]: r for r in meta_rows}

    targets = []
    for row in fundamentals:
        m = meta.get(row.get("ticker"))
        if not m:
            continue
        profile = (m.get("scoring_profile") or "standard").lower()
        if profile not in PROFILES:
            continue
        targets.append(row)

    if args.limit:
        targets = targets[: max(0, args.limit)]

    tickers = [r["ticker"] for r in targets]
    print(
        f"[DETERMINISTIC] candidates={len(fundamentals)} "
        f"targets={len(targets)} profile_scope={sorted(PROFILES)}",
        flush=True,
    )

    annual_by_ticker = defaultdict(list)
    for start in range(0, len(tickers), TICKER_BATCH):
        batch = tickers[start:start + TICKER_BATCH]
        rows = (
            sb.table("US_Fundamental_Annual")
            .select("ticker,fiscal_year,canonical,provenance")
            .in_("ticker", batch)
            .order("ticker")
            .order("fiscal_year")
            .execute()
            .data
            or []
        )
        for row in rows:
            annual_by_ticker[row["ticker"]].append(row)
        print(
            f"[DETERMINISTIC] annual_fetch={min(start + TICKER_BATCH, len(tickers))}/{len(tickers)}",
            flush=True,
        )

    updates = []
    recovered = 0
    unchanged = 0
    failed = 0

    for i, fundamental in enumerate(targets, 1):
        ticker = fundamental["ticker"]
        profile = (meta[ticker].get("scoring_profile") or "standard").lower()
        try:
            result = recover_one(
                sb,
                fundamental,
                annual_by_ticker.get(ticker) or [],
                profile,
            )
            if result:
                updates.append(result)
                recovered += 1
            else:
                unchanged += 1
        except Exception as exc:
            failed += 1
            print(f"[DETERMINISTIC] {ticker}: FAILED {type(exc).__name__}:{exc}", flush=True)

        if i % 100 == 0 or i == len(targets):
            print(
                f"[DETERMINISTIC] progress={i}/{len(targets)} "
                f"recovered={recovered} unchanged={unchanged} failed={failed}",
                flush=True,
            )

    for start in range(0, len(updates), 100):
        sb.table("US_Fundamental").upsert(
            updates[start:start + 100],
            on_conflict="ticker",
        ).execute()

    print(
        f"[DETERMINISTIC] completed targets={len(targets)} "
        f"recovered={recovered} unchanged={unchanged} failed={failed} "
        f"db_updates={len(updates)}",
        flush=True,
    )


if __name__ == "__main__":
    main()
