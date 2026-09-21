"""Targeted Standard-sector ROIC recovery.

Only the inputs required to calculate ROIC are recovered from SEC Company
Facts / filing Inline XBRL. Existing metric values are preserved and no
per-ticker yfinance/downturn download is performed.
"""
from __future__ import annotations

import argparse
import math
import os
import time
from datetime import datetime, timezone

import requests
from supabase import create_client

import collector_us_fundamental as base
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8
from us_scoring import calculate_us_score, data_reliability_from_periods

URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
UA = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
PAGE_SIZE = 100
SEC_LAST = 0.0

ROIC_INPUTS = {
    "operating_income",
    "equity",
    "cash",
    "debt_current",
    "debt_noncurrent",
    "debt_total",
}
EXTRA_ALIASES = {
    "equity": [
        "PartnersCapital", "MembersEquity", "Equity",
        "EquityAttributableToOwnersOfParent", "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "cash": [
        "CashAndCashEquivalents",
        "CashAndCashEquivalentsAtCarryingValue",
    ],
    "debt_current": [
        "DebtAndFinanceLeaseLiabilitiesCurrent",
        "LeaseLiabilitiesCurrent",
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
        "LongTermDebtAndCapitalLeaseObligationsCurrent",
    ],
    "debt_noncurrent": [
        "DebtAndFinanceLeaseLiabilitiesNoncurrent",
        "LeaseLiabilitiesNoncurrent",
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
        "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
    ],
    "debt_total": [
        "TotalDebt", "Debt", "LongTermNotesPayable",
        "DebtAndFinanceLeaseLiabilities",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebtAndFinanceLeaseObligations",
    ],
    "operating_income": [
        "OperatingIncome", "OperatingProfitLoss", "IncomeFromOperations",
    ],
}


def extend_aliases():
    for metric, tags in EXTRA_ALIASES.items():
        for mapping in (base.FACT_ALIASES, base.IFRS_FACT_ALIASES):
            for tag in tags:
                if tag not in mapping.setdefault(metric, []):
                    mapping[metric].append(tag)


def sec_json(session, url):
    global SEC_LAST
    wait = 0.20 - (time.monotonic() - SEC_LAST)
    if wait > 0:
        time.sleep(wait)
    SEC_LAST = time.monotonic()

    for attempt in range(5):
        try:
            response = session.get(url, timeout=45)
            if response.status_code == 200:
                return response.json()
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(2**attempt, 16))
                continue
            response.raise_for_status()
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(min(2**attempt, 16))

    raise RuntimeError("SEC request failed")


def candidate_to_row(candidate):
    value = candidate.get("value") if isinstance(candidate, dict) else getattr(candidate, "value", None)
    if value is None:
        return None
    concept = candidate.get("concept", "") if isinstance(candidate, dict) else getattr(candidate, "concept", "")
    end = candidate.get("end") if isinstance(candidate, dict) else getattr(candidate, "end", None)
    return {
        "fy": candidate.get("fy") if isinstance(candidate, dict) else getattr(candidate, "fy", None),
        "year": int(str(end)[:4]) if end else None,
        "end": end,
        "filed": candidate.get("filed", "") if isinstance(candidate, dict) else getattr(candidate, "filed", "") or "",
        "val": float(value),
        "form": candidate.get("form") if isinstance(candidate, dict) else getattr(candidate, "form", None),
        "frame": None,
        "unit": candidate.get("unit") if isinstance(candidate, dict) else getattr(candidate, "unit", None),
        "namespace": candidate.get("namespace", "") if isinstance(candidate, dict) else getattr(candidate, "namespace", ""),
        "tag": concept,
    }


def put_if_missing(index, metric, year, candidate):
    row = candidate_to_row(candidate)
    if row is None:
        return False
    if (index.get(metric) or {}).get(year) is not None:
        return False
    index.setdefault(metric, {})[year] = row
    return True


def derive_eps(index, facts):
    shares = {}
    root = facts.get("facts") or {}
    tags = [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfSharesOutstandingBasic",
        "WeightedAverageNumberOfSharesOutstanding",
    ]
    for namespace in ("us-gaap", "ifrs-full"):
        ns = root.get(namespace) or {}
        for tag in tags:
            fact = ns.get(tag)
            if fact:
                for year, row in base.annual_records(fact).items():
                    shares.setdefault(year, row)

    eps = index.setdefault("eps", {})
    for year, ni in (index.get("net_income") or {}).items():
        if year in eps:
            continue
        sh = (shares.get(year) or {}).get("val")
        if sh and sh > 0:
            eps[year] = {
                **ni,
                "val": ni["val"] / sh,
                "unit": "USD/sh",
                "namespace": "derived",
                "tag": "DerivedEPSFromNetIncomeAndWeightedAverageShares",
            }


def derive_equity(index):
    equity = index.setdefault("equity", {})
    for year, assets in (index.get("assets") or {}).items():
        if year in equity:
            continue
        liabilities = (index.get("liabilities") or {}).get(year)
        if liabilities is None:
            continue
        value = assets["val"] - liabilities["val"]
        if math.isfinite(value):
            equity[year] = {
                **assets,
                "val": value,
                "namespace": "derived",
                "tag": "DerivedEquityFromAssetsMinusLiabilities",
            }


def score_with_preserved_values(old_period, pair, profile):
    old_avg = old_period.get("avg") or {}
    old_worst = old_period.get("worst") or {}
    avg = dict(pair["avg_metrics"])
    worst = dict(pair["worst_metrics"])

    old_avg_scores = old_avg.get("metric_scores") or {}
    old_worst_scores = old_worst.get("metric_scores") or {}

    for key in set(old_avg_scores) | set(avg):
        old_val = (old_avg_scores.get(key) or {}).get("value")
        if old_val is not None:
            avg[key] = old_val
            worst[key] = (old_worst_scores.get(key) or {}).get("value", old_val)

    # Market-derived downturn defense is preserved exactly.
    avg["downturn_defense"] = (old_avg_scores.get("downturn_defense") or {}).get("value")
    worst["downturn_defense"] = (old_worst_scores.get("downturn_defense") or {}).get("value")

    a = calculate_us_score(avg, profile=profile)
    w = calculate_us_score(worst, profile=profile)

    def pack(x):
        return {
            "total_score": x["total_score"],
            "grade": x["grade"],
            "metric_scores": x["metric_scores"],
            "sub_scores": x.get("sub_scores", {}),
            "financial_adjusted": False,
            "missing_metric_count": x["missing_metric_count"],
            "scoring_version": x["scoring_version"],
            "available_weight": x["available_weight"],
            "coverage_pct": x["coverage_pct"],
            "score_cap": x["score_cap"],
            "confidence_level": x["confidence_level"],
        }

    return {
        "years_used": pair["years_used"],
        "yearly_breakdown": pair["yearly_breakdown"],
        "avg": pack(a),
        "worst": pack(w),
    }


def recover_row(sb, session, resolver, row, meta):
    facts = sec_json(
        session,
        base.SEC_FACTS_URL.format(cik=str(meta["cik"]).zfill(10)),
    )
    submissions = sec_json(
        session,
        base.SEC_SUBMISSIONS_URL.format(cik=str(meta["cik"]).zfill(10)),
    )

    index = base.build_fact_index(facts)
    flow_years = sorted(
        set(index.get("revenue", {}))
        | set(index.get("operating_income", {}))
        | set(index.get("net_income", {}))
    )
    latest_year = max(flow_years) if flow_years else None
    if latest_year is None:
        return False, "no-sec-years"

    # Add conservative in-memory aliases before attempting XBRL fallback.
    derive_eps(index, facts)
    derive_equity(index)

    resolver.prime_company(meta["cik"], facts, submissions)

    # Recover only ROIC dependencies that are actually absent in the latest year.
    needed = []
    for metric in sorted(ROIC_INPUTS):
        if base.latest_annual_value(index, metric, latest_year) is None:
            needed.append(metric)

    for metric in needed:
        try:
            result = resolver.resolve(meta["cik"], metric, year=latest_year, limit=5)
            candidate = result.get("best") if isinstance(result, dict) else None
            if candidate and put_if_missing(index, metric, latest_year, candidate):
                print(
                    f"    [ROIC-XBRL] {row['ticker']} {metric}: "
                    f"{candidate.get('concept')}={candidate.get('value')} "
                    f"source={candidate.get('source')}"
                )
        except Exception as exc:
            print(f"    [ROIC-XBRL] {row['ticker']} {metric}: {exc}")

    changed = False
    old_periods = row.get("period_scores") or {}
    new_periods = {}

    for period in base.PERIODS:
        key = f"{period}y"
        old_period = old_periods.get(key) or {}
        pair = base.period_metrics_pair(index, latest_year, period)
        if not pair:
            new_periods[key] = old_period
            continue

        packed = score_with_preserved_values(old_period, pair, meta.get("scoring_profile") or "standard")
        old_roic = (
            ((old_period.get("avg") or {}).get("metric_scores") or {})
            .get("roic") or {}
        ).get("value")
        new_roic = (
            ((packed.get("avg") or {}).get("metric_scores") or {})
            .get("roic") or {}
        ).get("value")

        if old_roic is None and new_roic is not None:
            changed = True

        old_yearly = old_period.get("yearly_breakdown") or {}
        yearly = packed.get("yearly_breakdown") or {}
        target_yearly = dict(old_yearly)
        for metric, values in yearly.items():
            if not isinstance(values, dict):
                continue
            target = dict(target_yearly.get(metric) or {})
            for year, value in values.items():
                if target.get(year) is None and value is not None:
                    target[year] = value
            target_yearly[metric] = target
        packed["yearly_breakdown"] = target_yearly
        new_periods[key] = packed

    if not changed:
        return False, "no-roic-recovery"

    average = (new_periods.get("1y") or {}).get("avg") or {}
    result = {
        "ticker": row["ticker"],
        "cik": meta["cik"],
        "period_scores": new_periods,
        "base_year": latest_year,
        "total_score": int(round(average["total_score"])) if average.get("total_score") is not None else row.get("total_score"),
        "grade": average.get("grade") or row.get("grade"),
        "missing_metric_count": average.get("missing_metric_count", row.get("missing_metric_count")),
        "data_unavailable": False,
        "data_reliability": data_reliability_from_periods(new_periods),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
    return True, "roic-recovered"


def fetch_targets(sb):
    universe = []
    offset = 0
    while True:
        page = (
            sb.table("US_Companies")
            .select("ticker,cik,scoring_profile,sector_common,company_type")
            .eq("is_fundamental_eligible", True)
            .eq("scoring_profile", "standard")
            .in_(
                "sector_common",
                [
                    "technology", "healthcare", "consumer", "industrials",
                    "energy", "materials", "communication",
                ],
            )
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        if not page:
            break
        universe.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    targets = []
    for row in universe:
        # This second query keeps the large period_scores JSONB out of the
        # universe pagination. It selects only rows whose 1Y ROIC is absent.
        current = (
            sb.table("US_Fundamental")
            .select("ticker,period_scores,missing_metric_count,data_unavailable")
            .eq("ticker", row["ticker"])
            .limit(1)
            .execute()
            .data
            or []
        )
        if not current:
            continue
        fundamental = current[0]
        if fundamental.get("data_unavailable"):
            continue
        avg = (
            ((fundamental.get("period_scores") or {}).get("1y") or {})
            .get("avg") or {}
        )
        roic = ((avg.get("metric_scores") or {}).get("roic") or {}).get("value")
        if roic is None:
            targets.append({**fundamental, "_meta": row})

    return targets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    extend_aliases()
    sb = create_client(URL, KEY)
    targets = fetch_targets(sb)
    if args.limit is not None:
        targets = targets[:max(0, args.limit)]

    print(f"[ROIC] targets={len(targets)}", flush=True)
    if args.dry_run:
        return

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    resolver = SECXBRLSearchV2_3_8(user_agent=UA, session=session)

    recovered = 0
    for i, row in enumerate(targets, 1):
        try:
            ok, message = recover_row(sb, session, resolver, row, row["_meta"])
            recovered += int(ok)
            print(f"[{i}/{len(targets)}] {row['ticker']}: {message}", flush=True)
        except Exception as exc:
            print(f"[{i}/{len(targets)}] {row['ticker']}: FAILED {exc}", flush=True)

    print(f"[ROIC] Completed processed={len(targets)} recovered={recovered}", flush=True)


if __name__ == "__main__":
    main()
