"""Read-only regression test for the Standard US fundamental extraction.

Tests representative companies across the five sectors that currently share the
Standard scoring model: technology, consumer, industrials, materials, and
communication.

No Supabase writes. No raw SEC data is stored.

Run in Colab:
!cd /content/Fundamental-app && python3 test_us_standard.py

Specific tickers:
!cd /content/Fundamental-app && python3 test_us_standard.py --tickers AAPL,MSFT,NVDA
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import requests


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "Fundamental-app contact@example.com"
)

TEST_GROUPS = {
    "technology": ["AAPL", "MSFT", "NVDA"],
    "consumer": ["COST", "WMT", "AMZN"],
    "industrials": ["CAT", "HON", "DE"],
    "materials": ["LIN", "APD", "NEM"],
    "communication": ["META", "VZ", "T"],
}

RAW_METRICS = [
    ("revenue", "Revenue", True),
    ("operating_income", "Operating Income", True),
    ("net_income", "Net Income", True),
    ("assets", "Assets", True),
    ("equity", "Equity", True),
    ("liabilities", "Liabilities", True),
    ("current_assets", "Current Assets", True),
    ("current_liabilities", "Current Liabilities", True),
    ("cash", "Cash", True),
    ("receivables", "Receivables", True),
    ("inventory", "Inventory", True),
    ("interest_expense", "Interest Expense", True),
    ("operating_cash_flow", "Operating Cash Flow", True),
    ("sga", "SG&A", True),
    ("eps", "EPS", False),
]

STANDARD_METRICS = [
    "revenue_growth",
    "eps_growth",
    "opm",
    "roic",
    "debt_rate",
    "quick_ratio",
    "interest_coverage",
    "ocf_ratio",
    "sga_ratio",
]


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


def load_ticker_map(session):
    data = fetch_json(session, SEC_TICKERS_URL)
    result = {}
    for row in data.values():
        ticker = str(row.get("ticker") or "").strip().upper()
        cik = row.get("cik_str")
        if ticker and cik:
            result[ticker] = str(cik)
    return result


def fmt(value, digits=2):
    if value is None:
        return "MISSING"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def fmt_money(value):
    if value is None:
        return "MISSING"
    try:
        return f"{float(value) / 1e9:,.2f}B"
    except (TypeError, ValueError):
        return str(value)


def source_label(index, metric, year):
    row = (index.get(metric) or {}).get(year)
    if not row:
        return "MISSING"
    namespace = row.get("namespace", "?")
    tag = row.get("tag", "?")
    return f"{namespace}:{tag}"


def test_company(ticker, cik, session, collector):
    print("\n" + "=" * 96)
    print(f"{ticker} | CIK {cik}")
    print("=" * 96)

    try:
        facts, submissions = collector.load_company(session, ticker, cik)
        index = collector.build_fact_index(facts)
    except Exception as exc:
        print(f"[SEC ERROR] {type(exc).__name__}: {exc}")
        return {"ticker": ticker, "status": "SEC_ERROR"}

    all_years = sorted({y for rows in index.values() for y in rows.keys()})
    flow_years = sorted(
        set((index.get("revenue") or {}).keys())
        | set((index.get("operating_income") or {}).keys())
        | set((index.get("net_income") or {}).keys())
    )

    if not all_years:
        print("[FAIL] No annual SEC facts")
        return {"ticker": ticker, "status": "NO_FACTS"}

    if not flow_years:
        print("[FAIL] No core annual flow facts")
        return {"ticker": ticker, "status": "NO_CORE_FACTS"}

    latest_year = max(flow_years)
    company_name = (
        facts.get("entityName")
        or submissions.get("name")
        or ticker
    )

    print(f"Company : {company_name}")
    print(f"Latest  : {latest_year}")
    print(f"Coverage: {all_years[0]} - {all_years[-1]}")

    print("\nLATEST RAW EXTRACTION")
    print("-" * 96)

    for metric, label, is_money in RAW_METRICS:
        value = collector.latest_annual_value(index, metric, latest_year)
        value_text = fmt_money(value) if is_money else fmt(value)
        print(f"{label:<25}{value_text:>18}   [{source_label(index, metric, latest_year)}]")

    annual = collector.annual_metrics(index, latest_year)

    print("\nCALCULATED STANDARD METRICS")
    print("-" * 96)

    for metric in STANDARD_METRICS:
        value = annual.get(metric)
        if metric in {"opm", "roic", "debt_rate", "sga_ratio"}:
            text = "MISSING" if value is None else f"{value:.2f}%"
        else:
            text = fmt(value)
        print(f"{metric:<25}{text:>18}")

    print("\nSTANDARD PERIOD COVERAGE")
    print("-" * 96)

    period_results = {}
    for period in (1, 3, 5, 10):
        try:
            used_year, metrics, base_year = collector.period_metrics(
                index, latest_year, period
            )
        except Exception as exc:
            print(f"{period:>2}Y ERROR: {exc}")
            period_results[period] = False
            continue

        if not metrics:
            print(f"{period:>2}Y MISSING BASE YEAR")
            period_results[period] = False
            continue

        missing = [m for m in STANDARD_METRICS if metrics.get(m) is None]
        print(f"{period:>2}Y base={base_year} missing={len(missing)}")
        if missing:
            print("     missing: " + ", ".join(missing))
        period_results[period] = True

    latest_missing = [
        metric for metric in STANDARD_METRICS if annual.get(metric) is None
    ]

    status = "PASS" if len(latest_missing) <= 2 else "REVIEW"
    print(f"\nRESULT: {status}")
    if latest_missing:
        print("Latest missing: " + ", ".join(latest_missing))

    return {
        "ticker": ticker,
        "status": status,
        "latest_year": latest_year,
        "missing_latest": latest_missing,
        "period_results": period_results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated tickers; omit to run all regression tickers.",
    )
    args = parser.parse_args()

    try:
        import collector_us_fundamental as collector
    except Exception as exc:
        print(f"[IMPORT ERROR] {type(exc).__name__}: {exc}")
        sys.exit(1)

    if args.tickers:
        groups = {"custom": [x.strip().upper() for x in args.tickers.split(",") if x.strip()]}
    else:
        groups = TEST_GROUPS

    tickers = list(dict.fromkeys(t for group in groups.values() for t in group))

    print("=" * 96)
    print("STANDARD MODEL EXTRACTION REGRESSION TEST")
    print("=" * 96)
    print("Read-only: YES")
    print("Supabase write: NO")
    print("Raw SEC storage: NO")
    print("\nTest groups:")
    for group, names in groups.items():
        print(f"  {group:<15}: {', '.join(names)}")

    session = requests.Session()
    session.headers.update({
        "User-Agent": SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    })

    print("\nLoading SEC ticker map...")
    ticker_map = load_ticker_map(session)
    print(f"SEC ticker map: {len(ticker_map):,} tickers")

    results = []
    for ticker in tickers:
        cik = ticker_map.get(ticker)
        if not cik:
            print(f"\n{ticker}: [SKIP] CIK not found")
            results.append({"ticker": ticker, "status": "CIK_MISSING"})
            continue
        results.append(test_company(ticker, cik, session, collector))
        time.sleep(0.15)

    print("\n" + "=" * 96)
    print("FINAL SUMMARY")
    print("=" * 96)

    counts = {}
    for result in results:
        status = result.get("status", "UNKNOWN")
        counts[status] = counts.get(status, 0) + 1

    for status in sorted(counts):
        print(f"{status:<18}{counts[status]:>4}")

    print("\nNext step: inspect every REVIEW case before changing scoring.py.")
    print("Repeated missing metrics indicate extraction/tag problems; isolated cases need company-specific review.")


if __name__ == "__main__":
    main()
