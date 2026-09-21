"""Recover useful Standard-profile US metric gaps of size 4-9.

Only SEC inputs required by the missing scoring metrics are recovered. The
market-derived downturn metric is preserved and no per-ticker yfinance request
is made by this recovery runner.
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

STANDARD_SECTORS = {
    "technology", "healthcare", "consumer", "industrials",
    "energy", "materials", "communication",
}

DEPENDENCIES = {
    "revenue_growth": {"revenue"},
    "eps_growth": {"eps"},
    "opm": {"operating_income", "revenue"},
    "roic": {"operating_income", "equity", "cash", "debt_current", "debt_noncurrent", "debt_total"},
    "debt_rate": {"liabilities", "equity"},
    "quick_ratio": {"current_assets", "current_liabilities", "inventory"},
    "interest_coverage": {"operating_income", "interest_expense"},
    "ocf_ratio": {"operating_cash_flow", "net_income"},
    "sga_ratio": {"sga", "revenue"},
}

EXTRA_ALIASES = {
    "interest_expense": [
        "InterestAndDebtExpense", "FinanceCosts", "InterestExpenseNonOperatingNet",
        "InterestExpenseDebt", "InterestExpenseNonOperating", "InterestExpense",
    ],
    "sga": [
        "GeneralAndAdministrativeExpense", "SellingExpense",
        "SellingGeneralAndAdministrativeExpense",
    ],
    "equity": [
        "PartnersCapital", "MembersEquity", "Equity",
        "EquityAttributableToOwnersOfParent", "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "debt_total": [
        "TotalDebt", "Debt", "LongTermNotesPayable",
        "DebtAndFinanceLeaseLiabilities",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebtAndFinanceLeaseObligations",
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
    "operating_income": [
        "OperatingIncome", "OperatingProfitLoss", "IncomeFromOperations",
    ],
    "cash": [
        "CashAndCashEquivalents", "CashAndCashEquivalentsAtCarryingValue",
    ],
}

SHARE_TAGS = [
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
    "WeightedAverageNumberOfSharesOutstanding",
]


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
    if not candidate:
        return None
    value = candidate.get("value")
    if value is None:
        return None
    end = candidate.get("end")
    return {
        "fy": candidate.get("fy"),
        "year": int(str(end)[:4]) if end else None,
        "end": end,
        "filed": candidate.get("filed") or "",
        "val": float(value),
        "form": candidate.get("form"),
        "frame": None,
        "unit": candidate.get("unit"),
        "namespace": candidate.get("namespace") or "filing-xbrl",
        "tag": candidate.get("concept") or "",
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
    root = facts.get("facts") or {}
    shares_by_year = {}
    for namespace in ("us-gaap", "ifrs-full"):
        namespace_facts = root.get(namespace) or {}
        for tag in SHARE_TAGS:
            fact = namespace_facts.get(tag)
            if not fact:
                continue
            for year, row in base.annual_records(fact).items():
                shares_by_year.setdefault(year, row)

    eps = index.setdefault("eps", {})
    for year, net_row in (index.get("net_income") or {}).items():
        if year in eps:
            continue
        shares = (shares_by_year.get(year) or {}).get("val")
        if shares and shares > 0:
            eps[year] = {
                **net_row,
                "val": net_row["val"] / shares,
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


def merge_scores(old_period, pair, profile):
    old_avg = old_period.get("avg") or {}
    old_worst = old_period.get("worst") or {}
    old_avg_scores = old_avg.get("metric_scores") or {}
    old_worst_scores = old_worst.get("metric_scores") or {}

    avg = dict(pair["avg_metrics"])
    worst = dict(pair["worst_metrics"])

    for key, entry in old_avg_scores.items():
        old_value = (entry or {}).get("value")
        if old_value is not None:
            avg[key] = old_value
            worst[key] = (old_worst_scores.get(key) or {}).get("value", old_value)

    # Preserve market-derived downturn defense and all existing populated values.
    avg["downturn_defense"] = (old_avg_scores.get("downturn_defense") or {}).get("value")
    worst["downturn_defense"] = (old_worst_scores.get("downturn_defense") or {}).get("value")

    average_score = calculate_us_score(avg, profile=profile)
    worst_score = calculate_us_score(worst, profile=profile)

    def pack(score):
        return {
            "total_score": score["total_score"],
            "grade": score["grade"],
            "metric_scores": score["metric_scores"],
            "sub_scores": score.get("sub_scores", {}),
            "financial_adjusted": False,
            "missing_metric_count": score["missing_metric_count"],
            "scoring_version": score["scoring_version"],
            "available_weight": score["available_weight"],
            "coverage_pct": score["coverage_pct"],
            "score_cap": score["score_cap"],
            "confidence_level": score["confidence_level"],
        }

    return {
        "years_used": pair["years_used"],
        "yearly_breakdown": pair["yearly_breakdown"],
        "avg": pack(average_score),
        "worst": pack(worst_score),
    }


def recover_row(sb, session, resolver, row, meta):
    cik = meta["cik"]
    facts = sec_json(
        session,
        base.SEC_FACTS_URL.format(cik=str(cik).zfill(10)),
    )
    submissions = sec_json(
        session,
        base.SEC_SUBMISSIONS_URL.format(cik=str(cik).zfill(10)),
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

    derive_eps(index, facts)
    derive_equity(index)
    resolver.prime_company(cik, facts, submissions)

    old_1y = (
        ((row.get("period_scores") or {}).get("1y") or {})
        .get("avg") or {}
    )
    old_scores = old_1y.get("metric_scores") or {}
    missing_metrics = {
        key for key, entry in old_scores.items()
        if isinstance(entry, dict) and entry.get("value") is None
    }
    missing_metrics.discard("downturn_defense")

    needed_inputs = set()
    for metric in missing_metrics:
        needed_inputs.update(DEPENDENCIES.get(metric, set()))

    # Growth needs two annual revenue/EPS points.
    growth_base_year = latest_year - 1
    for metric in ("revenue_growth", "eps_growth"):
        if metric in missing_metrics:
            needed = DEPENDENCIES[metric]
            for input_metric in needed:
                for year in (latest_year, growth_base_year):
                    if base.latest_annual_value(index, input_metric, year) is not None:
                        continue
                    try:
                        resolved = resolver.resolve(cik, input_metric, year=year, limit=5)
                        candidate = resolved.get("best") if isinstance(resolved, dict) else None
                        if candidate:
                            put_if_missing(index, input_metric, year, candidate)
                    except Exception as exc:
                        print(
                            f"    [MISSING-4-9-XBRL] {row['ticker']} "
                            f"{input_metric}/{year}: {exc}"
                        )

    # All non-growth dependencies are needed only for the latest annual year.
    for input_metric in sorted(needed_inputs - {"revenue", "eps"}):
        if base.latest_annual_value(index, input_metric, latest_year) is not None:
            continue
        try:
            resolved = resolver.resolve(cik, input_metric, year=latest_year, limit=5)
            candidate = resolved.get("best") if isinstance(resolved, dict) else None
            if candidate:
                put_if_missing(index, input_metric, latest_year, candidate)
                print(
                    f"    [MISSING-4-9-XBRL] {row['ticker']} "
                    f"{input_metric}/{latest_year}: {candidate.get('concept')}={candidate.get('value')}"
                )
        except Exception as exc:
            print(
                f"    [MISSING-4-9-XBRL] {row['ticker']} "
                f"{input_metric}/{latest_year}: {exc}"
            )

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

        packed = merge_scores(
            old_period,
            pair,
            meta.get("scoring_profile") or "standard",
        )
        old_avg_scores = (old_period.get("avg") or {}).get("metric_scores") or {}
        new_avg_scores = packed["avg"]["metric_scores"]

        if any(
            (old_avg_scores.get(metric) or {}).get("value") is None
            and (new_avg_scores.get(metric) or {}).get("value") is not None
            for metric in new_avg_scores
        ):
            changed = True

        old_yearly = old_period.get("yearly_breakdown") or {}
        target_yearly = dict(old_yearly)
        for metric, values in (packed.get("yearly_breakdown") or {}).items():
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
        return False, "no-recovery"

    average = (new_periods.get("1y") or {}).get("avg") or {}
    result = {
        "ticker": row["ticker"],
        "cik": cik,
        "period_scores": new_periods,
        "base_year": latest_year,
        "total_score": (
            int(round(average["total_score"]))
            if average.get("total_score") is not None
            else row.get("total_score")
        ),
        "grade": average.get("grade") or row.get("grade"),
        "missing_metric_count": average.get(
            "missing_metric_count",
            row.get("missing_metric_count"),
        ),
        "data_unavailable": False,
        "data_reliability": data_reliability_from_periods(new_periods),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
    return True, "recovered"


def fetch_targets(sb):
    rows = []
    offset = 0

    while True:
        page = (
            sb.table("US_Fundamental")
            .select("ticker,missing_metric_count,period_scores,data_unavailable")
            .gte("missing_metric_count", 4)
            .lte("missing_metric_count", 9)
            .eq("data_unavailable", False)
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    tickers = [row["ticker"] for row in rows if row.get("ticker")]
    meta_by_ticker = {}

    for start in range(0, len(tickers), PAGE_SIZE):
        batch = tickers[start:start + PAGE_SIZE]
        companies = (
            sb.table("US_Companies")
            .select("ticker,cik,scoring_profile,sector_common,company_type")
            .in_("ticker", batch)
            .execute()
            .data
            or []
        )
        for company in companies:
            if (
                (company.get("scoring_profile") or "standard") == "standard"
                and company.get("sector_common") in STANDARD_SECTORS
                and company.get("company_type") != "spac"
            ):
                meta_by_ticker[company["ticker"]] = company

    return [
        {"fundamental": row, "meta": meta_by_ticker[row["ticker"]]}
        for row in rows
        if row["ticker"] in meta_by_ticker
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    extend_aliases()
    sb = create_client(URL, KEY)
    targets = fetch_targets(sb)
    if args.limit:
        targets = targets[:max(0, args.limit)]

    print(f"[MISSING-4-9] targets={len(targets)}", flush=True)

    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    resolver = SECXBRLSearchV2_3_8(user_agent=UA, session=session)

    recovered = 0
    for index, item in enumerate(targets, 1):
        row = item["fundamental"]
        try:
            ok, message = recover_row(sb, session, resolver, row, item["meta"])
            recovered += int(ok)
            print(
                f"[{index}/{len(targets)}] {row['ticker']}: {message}",
                flush=True,
            )
        except Exception as exc:
            print(
                f"[{index}/{len(targets)}] {row['ticker']}: FAILED {exc}",
                flush=True,
            )

    print(
        f"[MISSING-4-9] Completed processed={len(targets)} recovered={recovered}",
        flush=True,
    )


if __name__ == "__main__":
    main()
