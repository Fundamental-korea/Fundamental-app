```python
"""
US fundamental collector (MVP)

SEC Company Facts -> annual financials -> shared scoring.py -> one row/company.

Classification:
    US_Companies.scoring_profile
        -> standard
        -> financial
        -> bdc
        -> reit
        -> utility

Raw SEC JSON is never written to Supabase.

Usage:
  python collector_us_fundamental.py --ticker AAPL
  python collector_us_fundamental.py --ticker JPM
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


# ============================================================
# CONFIG
# ============================================================

SUPABASE_URL = (
    os.environ.get("SUPABASE_URL")
    or "https://cnweggechipghcivruie.supabase.co"
)

# 기존 collector 호환
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SECRET_KEY")
    or os.environ.get("SUPABASE_KEY")
    or ""
)

SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "Fundamental-app contact@example.com",
)

SEC_FACTS_URL = (
    "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
)

SEC_SUBMISSIONS_URL = (
    "https://data.sec.gov/submissions/CIK{cik}.json"
)

PERIODS = (1, 3, 5, 10)

FLOW_FORMS = {
    "10-K",
    "10-K/A",
    "20-F",
    "20-F/A",
    "40-F",
    "40-F/A",
}


# ============================================================
# FACT ALIASES
# ============================================================

FACT_ALIASES = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
    "assets": [
        "Assets",
    ],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "liabilities": [
        "Liabilities",
    ],
    "current_assets": [
        "AssetsCurrent",
    ],
    "current_liabilities": [
        "LiabilitiesCurrent",
    ],
    "inventory": [
        "InventoryNet",
        "InventoryGross",
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
    ],
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
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
    ],
    "sga": [
        "SellingGeneralAndAdministrativeExpense",
        "SellingGeneralAndAdministrativeExpenseIncludingDepreciationAmortization",
    ],
    "eps": [
        "EarningsPerShareDiluted",
        "EarningsPerShareBasic",
    ],
}


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_number(value):
    if value is None:
        return None

    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def sanitize_growth(value):
    if value is None:
        return None

    if not math.isfinite(value):
        return None

    # Extreme values are considered unreliable.
    if abs(value) > 500:
        return None

    return value


# ============================================================
# SEC ANNUAL RECORDS
# ============================================================

def annual_records(fact):
    """
    Return one best annual record per fiscal year.
    """

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

                # Annual period only.
                if not 300 <= days <= 380:
                    continue

            value = clean_number(r.get("val"))

            if value is None:
                continue

            records.append(
                {
                    "fy": int(fy),
                    "end": end,
                    "filed": filed or "",
                    "val": value,
                    "form": form,
                    "frame": r.get("frame"),
                    "unit": unit,
                }
            )

    records.sort(
        key=lambda x: (
            x["fy"],
            x["end"],
            x["filed"],
            x["unit"],
        )
    )

    by_fy = {}

    for r in records:
        by_fy[r["fy"]] = r

    return by_fy


# ============================================================
# FACT INDEX
# ============================================================

def build_fact_index(companyfacts):

    facts = (
        companyfacts
        .get("facts", {})
        .get("us-gaap", {})
    )

    index = {}

    for logical_name, aliases in FACT_ALIASES.items():

        best = {}

        for tag in aliases:

            fact = facts.get(tag)

            if not fact:
                continue

            rows = annual_records(fact)

            if len(rows) > len(best):
                best = rows

        index[logical_name] = best

    return index


def latest_annual_value(index, metric, year):

    row = (
        index
        .get(metric, {})
        .get(year)
    )

    return row["val"] if row else None


# ============================================================
# CALCULATIONS
# ============================================================

def growth_cagr(current, base, years):

    current = clean_number(current)
    base = clean_number(base)

    if (
        current is None
        or base is None
        or years <= 0
        or base == 0
    ):
        return None

    if current > 0 and base > 0:

        value = (
            (current / base)
            ** (1.0 / years)
            - 1.0
        ) * 100.0

        return sanitize_growth(value)

    return sanitize_growth(
        (current / base - 1.0) * 100.0
    )


def ratio(
    numerator,
    denominator,
    multiplier=1.0,
):

    numerator = clean_number(numerator)
    denominator = clean_number(denominator)

    if (
        numerator is None
        or denominator in (None, 0)
    ):
        return None

    return (
        numerator
        / denominator
        * multiplier
    )


# ============================================================
# ANNUAL METRICS
# ============================================================

def annual_metrics(index, year):

    revenue = latest_annual_value(
        index,
        "revenue",
        year,
    )

    opinc = latest_annual_value(
        index,
        "operating_income",
        year,
    )

    net_income = latest_annual_value(
        index,
        "net_income",
        year,
    )

    assets = latest_annual_value(
        index,
        "assets",
        year,
    )

    equity = latest_annual_value(
        index,
        "equity",
        year,
    )

    liabilities = latest_annual_value(
        index,
        "liabilities",
        year,
    )

    current_liabilities = latest_annual_value(
        index,
        "current_liabilities",
        year,
    )

    cash = latest_annual_value(
        index,
        "cash",
        year,
    )

    receivables = latest_annual_value(
        index,
        "receivables",
        year,
    )

    interest = latest_annual_value(
        index,
        "interest_expense",
        year,
    )

    ocf = latest_annual_value(
        index,
        "operating_cash_flow",
        year,
    )

    sga = latest_annual_value(
        index,
        "sga",
        year,
    )

    eps = latest_annual_value(
        index,
        "eps",
        year,
    )

    # --------------------------------------------------------
    # NOPAT
    # --------------------------------------------------------

    nopat = (
        opinc * 0.78
        if opinc is not None
        else None
    )

    # --------------------------------------------------------
    # Invested Capital
    # --------------------------------------------------------

    invested_capital = None

    if (
        equity is not None
        or liabilities is not None
    ):

        invested_capital = (
            (equity or 0.0)
            + (liabilities or 0.0)
            - (cash or 0.0)
        )

        if invested_capital <= 0:
            invested_capital = None

    # --------------------------------------------------------
    # Quick Assets
    # --------------------------------------------------------

    quick_assets = None

    if (
        cash is not None
        or receivables is not None
    ):

        quick_assets = (
            (cash or 0.0)
            + (receivables or 0.0)
        )

    # --------------------------------------------------------
    # Metrics
    # --------------------------------------------------------

    return {
        "revenue": revenue,
        "eps": eps,

        "revenue_growth": None,
        "eps_growth": None,

        "opm": ratio(
            opinc,
            revenue,
            100.0,
        ),

        "roic": ratio(
            nopat,
            invested_capital,
            100.0,
        ),

        "debt_rate": ratio(
            liabilities,
            equity,
            100.0,
        ),

        "quick_ratio": ratio(
            quick_assets,
            current_liabilities,
            100.0,
        ),

        "interest_coverage": ratio(
            opinc,
            interest,
        ),

        "ocf_ratio": ratio(
            ocf,
            net_income,
        ),

        "sga_ratio": ratio(
            sga,
            revenue,
            100.0,
        ),

        "downturn_defense": None,

        "roa": ratio(
            net_income,
            assets,
            100.0,
        ),

        "net_income": net_income,
        "assets": assets,
    }


# ============================================================
# SEC CLASSIFICATION
# ============================================================

def classify_company(submissions):

    sic = submissions.get("sic")
    sic_desc = submissions.get(
        "sicDescription"
    )

    return (
        sic_desc
        or (
            f"SIC {sic}"
            if sic
            else None
        )
    )


# ============================================================
# SEC REQUEST
# ============================================================

def fetch_json(
    session,
    url,
    retries=3,
):

    for attempt in range(retries):

        response = session.get(
            url,
            timeout=30,
        )

        if response.status_code == 200:
            return response.json()

        if response.status_code in (
            429,
            500,
            502,
            503,
            504,
        ):

            time.sleep(
                1.5 * (attempt + 1)
            )

            continue

        response.raise_for_status()

    raise RuntimeError(
        f"SEC request failed after "
        f"{retries} retries: {url}"
    )


def load_company(
    session,
    ticker,
    cik,
):

    cik10 = str(cik).zfill(10)

    facts = fetch_json(
        session,
        SEC_FACTS_URL.format(
            cik=cik10
        ),
    )

    submissions = fetch_json(
        session,
        SEC_SUBMISSIONS_URL.format(
            cik=cik10
        ),
    )

    return facts, submissions


# ============================================================
# PERIOD METRICS
# ============================================================

def period_metrics(
    index,
    latest_year,
    period,
):

    all_years = sorted(
        {
            y
            for rows in index.values()
            for y in rows.keys()
        }
    )

    if latest_year not in all_years:
        return None, {}, None

    target_base = (
        latest_year - period
    )

    prior_years = [
        y
        for y in all_years
        if (
            y <= target_base
            and y < latest_year
        )
    ]

    if prior_years:

        base_year = max(
            prior_years
        )

    else:

        earlier = [
            y
            for y in all_years
            if y < latest_year
        ]

        if not earlier:
            base_year = None
        else:
            base_year = min(
                earlier
            )

    candidate_years = [
        y
        for y in all_years
        if (
            base_year is not None
            and base_year <= y <= latest_year
        )
    ]

    if not candidate_years:
        candidate_years = [
            latest_year
        ]

    yearly = {
        y: annual_metrics(
            index,
            y,
        )
        for y in candidate_years
    }

    latest = yearly[
        latest_year
    ]

    base = (
        yearly.get(base_year)
        if base_year is not None
        else None
    )

    actual_years = (
        latest_year - base_year
        if base_year is not None
        else 0
    )

    metrics = dict(
        latest
    )

    # --------------------------------------------------------
    # Growth
    # --------------------------------------------------------

    metrics[
        "revenue_growth"
    ] = growth_cagr(
        latest.get("revenue"),
        base.get("revenue")
        if base
        else None,
        actual_years,
    )

    metrics[
        "eps_growth"
    ] = growth_cagr(
        latest.get("eps"),
        base.get("eps")
        if base
        else None,
        actual_years,
    )

    # --------------------------------------------------------
    # Worst-year ratios
    # --------------------------------------------------------

    ratio_keys = (
        "opm",
        "roic",
        "debt_rate",
        "quick_ratio",
        "interest_coverage",
        "ocf_ratio",
        "sga_ratio",
        "roa",
    )

    for key in ratio_keys:

        metrics[key] = worst_value(
            key,
            [
                yearly[y].get(key)
                for y in candidate_years
            ],
        )

    return (
        latest_year,
        metrics,
        base_year,
    )


# ============================================================
# SCORING PROFILE -> SCORING FLAGS
# ============================================================

def scoring_flags(
    scoring_profile,
):
    """
    Convert US_Companies.scoring_profile
    into the flags currently supported by scoring.py.

    Profiles:

        standard
            normal scoring

        financial
            financial-adjusted scoring
            + leverage exemption

        bdc
            financial-style scoring
            + leverage exemption

        reit
            normal operating scoring
            + leverage exemption

        utility
            normal operating scoring
            + leverage exemption
    """

    profile = (
        scoring_profile
        or "standard"
    ).strip().lower()

    if profile == "financial":

        return {
            "leverage_exempt": True,
            "is_financial": True,
        }

    if profile == "bdc":

        return {
            "leverage_exempt": True,
            "is_financial": True,
        }

    if profile == "reit":

        return {
            "leverage_exempt": True,
            "is_financial": False,
        }

    if profile == "utility":

        return {
            "leverage_exempt": True,
            "is_financial": False,
        }

    return {
        "leverage_exempt": False,
        "is_financial": False,
    }


# ============================================================
# BUILD RESULT
# ============================================================

def build_result(
    ticker,
    cik,
    company_name,
    facts,
    submissions,
    scoring_profile="standard",
):

    index = build_fact_index(
        facts
    )

    all_years = sorted(
        {
            y
            for rows in index.values()
            for y in rows.keys()
        }
    )

    # --------------------------------------------------------
    # No usable SEC data
    # --------------------------------------------------------

    if not all_years:

        return {
            "ticker": ticker,
            "cik": str(cik),
            "company_name": company_name,

            "sector": classify_company(
                submissions
            ),

            "base_year": None,
            "period_scores": {},

            "total_score": None,
            "grade": None,

            "data_unavailable": True,
            "data_reliability": "none",

            "missing_metric_count": 10,

            "updated_at": datetime.now(
                timezone.utc
            ).isoformat(),
        }

    # --------------------------------------------------------
    # Latest flow year
    # --------------------------------------------------------

    flow_years = sorted(
        set(
            index.get(
                "revenue",
                {}
            ).keys()
        )
        | set(
            index.get(
                "operating_income",
                {}
            ).keys()
        )
        | set(
            index.get(
                "net_income",
                {}
            ).keys()
        )
    )

    latest_year = (
        max(flow_years)
        if flow_years
        else max(all_years)
    )

    # --------------------------------------------------------
    # Scoring configuration
    # --------------------------------------------------------

    flags = scoring_flags(
        scoring_profile
    )

    leverage_exempt = flags[
        "leverage_exempt"
    ]

    is_financial = flags[
        "is_financial"
    ]

    # --------------------------------------------------------
    # Period scoring
    # --------------------------------------------------------

    period_scores = {}

    latest_score = None
    latest_grade = None
    latest_missing = 10

    for period in PERIODS:

        (
            used_year,
            metrics,
            base_year,
        ) = period_metrics(
            index,
            latest_year,
            period,
        )

        if not metrics:
            continue

        scored = calculate_fundamental_score(
            metrics,
            leverage_exempt=leverage_exempt,
            is_financial=is_financial,
        )

        score_entries = scored.get(
            "scores",
            {},
        )

        missing = sum(
            1
            for key in score_entries
            if score_entries[key].get(
                "value"
            ) is None
            and not score_entries[key].get(
                "excluded_from_total",
                False,
            )
        )

        period_scores[
            str(period)
        ] = {
            "base_year": base_year,
            "metrics": metrics,
            "scores": scored,
        }

        # 1-year score = main score
        if period == 1:

            latest_score = scored.get(
                "total_score"
            )

            latest_grade = scored.get(
                "grade"
            )

            latest_missing = missing

    # --------------------------------------------------------
    # Reliability
    # --------------------------------------------------------

    if len(period_scores) >= 3:
        reliability = "high"

    elif len(period_scores) >= 1:
        reliability = "medium"

    else:
        reliability = "low"

    # --------------------------------------------------------
    # Result
    # --------------------------------------------------------

    return {
        "ticker": ticker,
        "cik": str(cik),
        "company_name": company_name,

        # Keep SEC SIC description for compatibility.
        "sector": classify_company(
            submissions
        ),

        "base_year": latest_year,

        "period_scores": period_scores,

        "total_score": (
            int(round(latest_score))
            if latest_score is not None
            else None
        ),

        "grade": latest_grade,

        "data_unavailable": not bool(
            period_scores
        ),

        "data_reliability": reliability,

        "missing_metric_count": latest_missing,

        "updated_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }


# ============================================================
# UNIVERSE
# ============================================================

def get_universe(
    sb,
    tickers=None,
    limit=None,
    all_rows=False,
):

    # --------------------------------------------------------
    # Specific ticker(s)
    # --------------------------------------------------------

    if tickers:

        return (
            sb
            .table("US_Companies")
            .select(
                "ticker,cik,company_name,"
                "sector_common,company_type,"
                "scoring_profile"
            )
            .in_(
                "ticker",
                tickers,
            )
            .eq(
                "is_fundamental_eligible",
                True,
            )
            .execute()
            .data
        )

    # --------------------------------------------------------
    # Full universe
    # --------------------------------------------------------

    q = (
        sb
        .table("US_Companies")
        .select(
            "ticker,cik,company_name,"
            "sector_common,company_type,"
            "scoring_profile"
        )
        .eq(
            "is_fundamental_eligible",
            True,
        )
        .order(
            "ticker"
        )
    )

    if not all_rows:

        q = q.limit(
            limit or 5
        )

    return q.execute().data


# ============================================================
# MAIN
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--ticker",
        help="single ticker",
    )

    parser.add_argument(
        "--tickers",
        help="comma-separated tickers",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="number of companies for testing",
    )

    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_rows",
        help="run all fundamental-eligible companies",
    )

    args = parser.parse_args()

    # --------------------------------------------------------
    # Supabase key
    # --------------------------------------------------------

    if not SUPABASE_KEY:

        raise RuntimeError(
            "SUPABASE_SECRET_KEY 또는 "
            "SUPABASE_KEY 환경변수가 필요합니다."
        )

    sb = create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )

    # --------------------------------------------------------
    # Tickers
    # --------------------------------------------------------

    tickers = None

    if args.ticker:

        tickers = [
            args.ticker
            .upper()
            .strip()
        ]

    elif args.tickers:

        tickers = [
            x.upper().strip()
            for x in args.tickers.split(",")
            if x.strip()
        ]

    # --------------------------------------------------------
    # Universe
    # --------------------------------------------------------

    rows = get_universe(
        sb,
        tickers=tickers,
        limit=args.limit,
        all_rows=args.all_rows,
    )

    print("=" * 70)
    print("US FUNDAMENTAL COLLECTOR")
    print("=" * 70)

    print(
        f"Companies to run : {len(rows):,}"
    )

    print()

    # --------------------------------------------------------
    # SEC session
    # --------------------------------------------------------

    session = requests.Session()

    session.headers.update(
        {
            "User-Agent": SEC_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        }
    )

    success = 0
    failed = 0

    # --------------------------------------------------------
    # Collect
    # --------------------------------------------------------

    for row in rows:

        ticker = (
            row.get("ticker")
            or ""
        ).strip().upper()

        scoring_profile = (
            row.get(
                "scoring_profile"
            )
            or "standard"
        )

        company_type = (
            row.get(
                "company_type"
            )
            or "standard"
        )

        sector_common = (
            row.get(
                "sector_common"
            )
            or "other"
        )

        try:

            facts, submissions = load_company(
                session,
                ticker,
                row["cik"],
            )

            result = build_result(
                ticker=ticker,
                cik=row["cik"],
                company_name=row[
                    "company_name"
                ],
                facts=facts,
                submissions=submissions,
                scoring_profile=scoring_profile,
            )

            # ------------------------------------------------
            # Store only compact result.
            # Raw SEC JSON is never stored.
            # ------------------------------------------------

            (
                sb
                .table("US_Fundamental")
                .upsert(
                    result,
                    on_conflict="ticker",
                )
                .execute()
            )

            print(
                f"[US] {ticker:<6} | "
                f"{sector_common:<12} | "
                f"{company_type:<16} | "
                f"profile={scoring_profile:<9} | "
                f"score={result['total_score']} | "
                f"grade={result['grade']} | "
                f"periods={len(result['period_scores'])} | "
                f"missing={result['missing_metric_count']}"
            )

            success += 1

        except Exception as exc:

            failed += 1

            print(
                f"[US] {ticker}: "
                f"FAILED - "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

        time.sleep(
            0.15
        )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("COMPLETED")
    print("=" * 70)

    print(
        f"Success : {success:,}"
    )

    print(
        f"Failed  : {failed:,}"
    )

    print(
        f"Total   : {len(rows):,}"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
```
