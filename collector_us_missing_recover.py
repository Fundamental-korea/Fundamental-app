"""Recover missing US Standard metrics 1-3.

Company Facts is the fast path. For the metrics that remain structurally
missing, the latest annual filing's inline XBRL is used as a semantic fallback.
Existing populated values are never overwritten.
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
from sec_xbrl_search_v2_3_4 import SECXBRLSearchV2_3_4
from us_scoring import calculate_us_score, data_reliability_from_periods

URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
UA = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
LAST = 0.0

# Company Facts aliases that are useful for recovery but are intentionally kept
# separate from the main collector until validated there.
EXTRA = {
    "interest_expense": [
        "InterestAndDebtExpense",
        "FinanceCosts",
        "InterestExpenseNonOperatingNet",
        "InterestExpenseDebt",
        "InterestExpenseNonOperating",
        "InterestExpense",
    ],
    "sga": [
        "GeneralAndAdministrativeExpense",
        "SellingExpense",
        "SellingGeneralAndAdministrativeExpense",
    ],
    "equity": [
        "PartnersCapital",
        "MembersEquity",
        "Equity",
        "EquityAttributableToOwnersOfParent",
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "debt_total": [
        "TotalDebt",
        "Debt",
        "LongTermNotesPayable",
        "DebtAndFinanceLeaseLiabilities",
        "DebtAndFinanceLeaseLiabilitiesCurrent",
        "DebtAndFinanceLeaseLiabilitiesNoncurrent",
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
        "OperatingIncome",
        "OperatingProfitLoss",
        "IncomeFromOperations",
    ],
    "cash": [
        "CashAndCashEquivalents",
        "CashAndCashEquivalentsAtCarryingValue",
    ],
}
SHARES = [
    "WeightedAverageNumberOfDilutedSharesOutstanding",
    "WeightedAverageNumberOfSharesOutstandingBasic",
    "WeightedAverageNumberOfSharesOutstanding",
]

# Filing-level fallback is intentionally narrow. These are the inputs needed
# to repair ROIC / interest coverage, plus operating income when it is itself
# absent. One filing download is cached by the resolver, so resolving several
# metrics for a company does not redownload the filing.
FILING_METRICS = (
    "operating_income",
    "interest_expense",
    "equity",
    "cash",
    "debt_current",
    "debt_noncurrent",
    "debt_total",
)


def extend_aliases():
    for metric, tags in EXTRA.items():
        for mapping in (base.FACT_ALIASES, base.IFRS_FACT_ALIASES):
            for tag in tags:
                if tag not in mapping.setdefault(metric, []):
                    mapping[metric].append(tag)


def get_json(session, url):
    global LAST
    wait = 0.20 - (time.monotonic() - LAST)
    if wait > 0:
        time.sleep(wait)
    LAST = time.monotonic()
    for attempt in range(5):
        try:
            response = session.get(url, timeout=30)
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


def _put_candidate(index, metric, year, candidate):
    """Insert one filing candidate only when Company Facts has no value."""
    if candidate is None or candidate.value is None:
        return False
    bucket = index.setdefault(metric, {})
    if year in bucket and bucket[year].get("val") is not None:
        return False
    bucket[year] = {
        "fy": candidate.fy or year,
        "year": year,
        "end": candidate.end,
        "filed": candidate.filed or "",
        "val": float(candidate.value),
        "form": candidate.form or "",
        "frame": None,
        "unit": candidate.unit or "",
        "namespace": candidate.namespace or "filing-xbrl",
        "tag": candidate.concept,
        "source": candidate.source,
        "resolver_reason": candidate.reason,
    }
    return True


def recover_filing_inputs(resolver, facts, submissions, cik, index, year):
    """Recover only absent latest-year inputs from the latest annual filing."""
    recovered = []
    for metric in FILING_METRICS:
        if latest_annual_value(index, metric, year) is not None:
            continue
        try:
            candidates, _meta = resolver.search_filing(
                cik,
                metric,
                year=year,
                submissions=submissions,
                limit=5,
            )
        except Exception as exc:
            print(f"    [XBRL] {metric}: resolver failed: {exc}")
            continue
        for candidate in candidates:
            if _put_candidate(index, metric, year, candidate):
                recovered.append(metric)
                print(
                    f"    [XBRL] {metric}: {candidate.concept} = "
                    f"{candidate.value} ({candidate.reason})"
                )
                break
    return recovered


def recovered_index(facts, resolver=None, submissions=None, cik=None, latest_year=None):
    idx = base.build_fact_index(facts)
    root = facts.get("facts") or {}

    # Company Facts weighted-average shares are not always exposed under the
    # normal EPS aliases. Derive EPS conservatively when needed.
    share = {}
    for namespace in ("us-gaap", "ifrs-full", "filing-xbrl"):
        facts_ns = root.get(namespace) or {}
        for tag in SHARES:
            fact = facts_ns.get(tag)
            if fact:
                for year, row in base.annual_records(fact).items():
                    share.setdefault(year, row)

    eps = idx.setdefault("eps", {})
    ni = idx.get("net_income", {})
    for year, net_row in ni.items():
        if year in eps:
            continue
        shares = (share.get(year) or {}).get("val")
        if shares and shares > 0:
            eps[year] = {
                **net_row,
                "val": net_row["val"] / shares,
                "unit": "USD/sh",
                "namespace": "derived",
                "tag": "DerivedEPSFromNetIncomeAndWeightedAverageShares",
            }

    # Balance-sheet identity is acceptable only when both source facts exist.
    eq = idx.setdefault("equity", {})
    for year, assets_row in idx.get("assets", {}).items():
        if year in eq or year not in idx.get("liabilities", {}):
            continue
        value = assets_row["val"] - idx["liabilities"][year]["val"]
        if math.isfinite(value):
            eq[year] = {
                **assets_row,
                "val": value,
                "namespace": "derived",
                "tag": "DerivedEquityFromAssetsMinusLiabilities",
            }

    if resolver is not None and submissions is not None and cik is not None and latest_year is not None:
        recover_filing_inputs(resolver, facts, submissions, cik, idx, latest_year)

    return idx


def latest_annual_value(index, metric, year):
    row = (index.get(metric) or {}).get(year)
    return row["val"] if row else None


def merged_score(old_period, pair, profile):
    old_avg = old_period.get("avg") or {}
    old_worst = old_period.get("worst") or {}
    avg = dict(pair["avg_metrics"])
    worst = dict(pair["worst_metrics"])

    # Downturn defense is market-data-derived; keep the existing value.
    avg["downturn_defense"] = (
        (old_avg.get("metric_scores") or {}).get("downturn_defense") or {}
    ).get("value")
    worst["downturn_defense"] = (
        (old_worst.get("metric_scores") or {}).get("downturn_defense") or {}
    ).get("value")

    old_values = {
        key: (value or {}).get("value")
        for key, value in (old_avg.get("metric_scores") or {}).items()
    }
    for key, value in list(avg.items()):
        if old_values.get(key) is not None:
            avg[key] = old_values[key]
            worst[key] = (
                (old_worst.get("metric_scores") or {}).get(key) or {}
            ).get("value", old_values[key])

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


def recover_row(sb, session, resolver, row, company_meta):
    cik = company_meta["cik"]
    facts_url = base.SEC_FACTS_URL.format(cik=str(cik).zfill(10))
    facts = get_json(session, facts_url)

    # Submissions are fetched only for the filing-level fallback.
    submissions_url = base.SEC_SUBMISSIONS_URL.format(cik=str(cik).zfill(10))
    submissions = get_json(session, submissions_url)

    idx = base.build_fact_index(facts)
    flow_years = sorted(
        set(idx.get("revenue", {}))
        | set(idx.get("operating_income", {}))
        | set(idx.get("net_income", {}))
    )
    latest_year = max(flow_years) if flow_years else None
    if latest_year is None:
        return False, "no-sec-years"

    idx = recovered_index(
        facts,
        resolver=resolver,
        submissions=submissions,
        cik=cik,
        latest_year=latest_year,
    )
    years = sorted({year for rows in idx.values() for year in rows})
    if not years:
        return False, "no-sec-years"

    old_periods = row.get("period_scores") or {}
    new_periods = {}
    changed = False

    profile = company_meta.get("scoring_profile") or "standard"

    for period in base.PERIODS:
        key = f"{period}y"
        old_period = old_periods.get(key) or {}
        pair = base.period_metrics_pair(idx, latest_year, period)
        if not pair:
            new_periods[key] = old_period
            continue

        packed = merged_score(old_period, pair, profile)
        old_metric_scores = (old_period.get("avg") or {}).get("metric_scores") or {}
        new_metric_scores = packed["avg"]["metric_scores"]

        if any(
            (old_metric_scores.get(key) or {}).get("value") is None
            and (value or {}).get("value") is not None
            for key, value in new_metric_scores.items()
        ):
            changed = True

        # Preserve existing yearly values; only fill blanks.
        old_yearly = old_period.get("yearly_breakdown") or {}
        new_yearly = packed["yearly_breakdown"]
        for metric, values in new_yearly.items():
            if not isinstance(values, dict):
                continue
            target = old_yearly.setdefault(metric, {})
            for year, value in values.items():
                if target.get(year) is None and value is not None:
                    target[year] = value
        packed["yearly_breakdown"] = old_yearly
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
            else None
        ),
        "grade": average.get("grade"),
        "missing_metric_count": average.get("missing_metric_count", 0),
        "data_unavailable": False,
        "data_reliability": data_reliability_from_periods(new_periods),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
    return True, "recovered"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    extend_aliases()
    sb = create_client(URL, KEY)
    resolver = SECXBRLSearchV2_3_4(user_agent=UA)

    # Supabase REST can cap a response at 1000 rows. Keep JSONB pages small
    # because period_scores is large and 500-row pages can hit statement timeout.
    rows = []
    page = 0
    page_size = 100
    while True:
        query = (
            sb.table("US_Fundamental")
            .select("ticker,period_scores")
            .gte("missing_metric_count", 1)
            .lte("missing_metric_count", 3)
            .eq("data_unavailable", False)
            .range(page * page_size, (page + 1) * page_size - 1)
        )
        batch = query.execute().data or []
        rows.extend(batch)
        if len(batch) < page_size or (args.limit and len(rows) >= args.limit):
            break
        page += 1

    if args.limit:
        rows = rows[: args.limit]

    tickers = [row["ticker"] for row in rows if row.get("ticker")]
    company_map = {}
    for start in range(0, len(tickers), 500):
        batch = tickers[start : start + 500]
        data = (
            sb.table("US_Companies")
            .select("ticker,cik,scoring_profile")
            .in_("ticker", batch)
            .execute()
            .data
            or []
        )
        for item in data:
            company_map[item["ticker"]] = {
                "cik": item.get("cik"),
                "scoring_profile": item.get("scoring_profile") or "standard",
            }

    usable = []
    for row in rows:
        meta = company_map.get(row.get("ticker")) or {}
        row["_meta"] = meta
        if meta.get("cik"):
            usable.append(row)
        else:
            print(f"[SKIP] {row.get('ticker')}: no CIK in US_Companies")
    rows = usable

    session = requests.Session()
    session.headers.update(
        {"User-Agent": UA, "Accept-Encoding": "gzip, deflate"}
    )

    recovered = 0
    for index, row in enumerate(rows, 1):
        try:
            ok, message = recover_row(
                sb, session, resolver, row, row["_meta"]
            )
            recovered += int(ok)
            print(f"[{index}/{len(rows)}] {row['ticker']}: {message}")
        except Exception as exc:
            print(f"[{index}/{len(rows)}] {row['ticker']}: FAILED {exc}")

    print(
        f"Completed processed={len(rows)} recovered={recovered}"
    )


if __name__ == "__main__":
    main()
