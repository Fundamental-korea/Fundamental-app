"""Utility SEC extraction validation v3.

Purpose
-------
Validate the *same SEC extraction logic used by collector_us_fundamental.py*
for a small utility sample before changing the production utility scorer.

No Supabase writes. No scoring changes. No raw SEC JSON persistence.

Usage
-----
python test_us_utility_extraction_v3.py --tickers PEG,AWR
python test_us_utility_extraction_v3.py --tickers NEE,DUK,SO,AEP,D,EXC,CEG,VST,PEG,AWR,ED
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

import requests

# Reuse the production collector's validated extraction functions.
from collector_us_fundamental import (
    SEC_FACTS_URL,
    SEC_SUBMISSIONS_URL,
    build_fact_index,
    annual_metrics,
    clean_number,
)

FLOW_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}


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


def load_company(session, ticker, cik):
    cik10 = str(cik).zfill(10)
    facts = fetch_json(session, SEC_FACTS_URL.format(cik=cik10))
    submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik10))
    return facts, submissions


def resolve_cik(session, ticker):
    """Resolve ticker -> CIK from SEC submissions index."""
    url = "https://www.sec.gov/files/company_tickers.json"
    data = fetch_json(session, url)
    ticker_upper = ticker.upper()
    for row in data.values():
        if str(row.get("ticker", "")).upper() == ticker_upper:
            return str(row["cik_str"]).zfill(10), row.get("title")
    return None, None


def fmt(value, digits=3):
    if value is None:
        return "MISSING"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def print_metric_years(index, metric, years):
    print(f"\n[{metric}]")
    rows = index.get(metric) or {}
    for year in years:
        row = rows.get(year)
        if not row:
            print(f"  {year}: MISSING")
            continue
        print(
            f"  {year}: {fmt(clean_number(row.get('val')))} "
            f"| {row.get('namespace')}:{row.get('tag')} "
            f"| filed={row.get('filed')} "
            f"| form={row.get('form')} "
            f"| end={row.get('end')}"
        )


def print_tag_inventory(facts, keywords):
    root = facts.get("facts", facts)
    print("\n[TAG INVENTORY]")
    for keyword in keywords:
        print(f"  {keyword}:")
        found = []
        for namespace in ("us-gaap", "ifrs-full"):
            for tag in sorted((root.get(namespace) or {}).keys()):
                if keyword.lower() in tag.lower():
                    found.append(f"{namespace}:{tag}")
        if found:
            for tag in found[:80]:
                print(f"    - {tag}")
            if len(found) > 80:
                print(f"    ... {len(found) - 80} more")
        else:
            print("    NONE")


def run_ticker(session, ticker):
    cik, sec_title = resolve_cik(session, ticker)
    if not cik:
        print(f"\n{'=' * 90}\n{ticker}: CIK NOT FOUND")
        return

    facts, submissions = load_company(session, ticker, cik)
    index = build_fact_index(facts)

    all_years = sorted({year for rows in index.values() for year in rows})
    recent_years = [y for y in all_years if y >= max(all_years, default=2025) - 4]
    if not recent_years:
        recent_years = all_years[-5:]

    print("\n" + "=" * 90)
    print(f"{ticker} | {sec_title or submissions.get('name')}")
    print(f"CIK: {cik}")
    print(f"SIC: {submissions.get('sic')} | {submissions.get('sicDescription')}")
    print(f"Available years: {all_years[-12:]}")
    print(f"Diagnostic years: {recent_years}")

    metrics = [
        "revenue", "operating_income", "net_income", "assets", "equity",
        "liabilities", "current_assets", "current_liabilities", "cash",
        "receivables", "inventory", "interest_expense", "operating_cash_flow",
        "sga", "eps",
    ]
    for metric in metrics:
        print_metric_years(index, metric, recent_years)

    print("\n[DERIVED ANNUAL METRICS - SAME annual_metrics() AS PRODUCTION]")
    for year in recent_years:
        m = annual_metrics(index, year)
        print(
            f"  {year}: "
            f"OPM={fmt(m.get('opm'))}% | "
            f"ROA={fmt(m.get('roa'))}% | "
            f"ROIC={fmt(m.get('roic'))}% | "
            f"DebtRate={fmt(m.get('debt_rate'))}% | "
            f"Quick={fmt(m.get('quick_ratio'))} | "
            f"InterestCoverage={fmt(m.get('interest_coverage'))}x | "
            f"OCF/NI={fmt(m.get('ocf_ratio'))}% | "
            f"SGA/Revenue={fmt(m.get('sga_ratio'))}%"
        )

    print_tag_inventory(
        facts,
        [
            "Revenue", "OperatingIncome", "InterestExpense", "FinanceCost",
            "Debt", "AcquireProperty", "AcquireProductive", "Dividend",
        ],
    )

    # Explicit sanity checks. These do not alter production data.
    print("\n[SANITY FLAGS]")
    latest = recent_years[-1] if recent_years else None
    if latest is not None:
        m = annual_metrics(index, latest)
        if m.get("revenue") is None:
            print("  !! Revenue missing in production extraction path")
        if m.get("opm") is not None and abs(m["opm"]) > 100:
            print(f"  !! OPM suspicious: {m['opm']:.3f}%")
        if m.get("debt_rate") is None:
            print("  !! DebtRate unavailable")
        if m.get("interest_coverage") is None:
            print("  !! InterestCoverage unavailable")
        if m.get("quick_ratio") is None:
            print("  !! Quick ratio unavailable")
        if m.get("ocf_ratio") is None:
            print("  !! OCF/NI unavailable")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers")
    args = parser.parse_args()

    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    if not tickers:
        parser.error("No tickers supplied")

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": os.environ.get(
                "SEC_USER_AGENT", "Fundamental-app contact@example.com"
            ),
            "Accept-Encoding": "gzip, deflate",
        }
    )

    print(f"Started: {datetime.now().isoformat(timespec='seconds')}")
    print("Mode: DIAGNOSTIC ONLY / NO DB WRITE / PRODUCTION EXTRACTION REUSED")
    print(f"Tickers: {', '.join(tickers)}")

    failed = 0
    for ticker in tickers:
        try:
            run_ticker(session, ticker)
        except Exception as exc:
            failed += 1
            print(f"\n{'=' * 90}\n{ticker}: ERROR: {exc}")

    print("\n" + "=" * 90)
    print(f"Completed. failed={failed}/{len(tickers)}")


if __name__ == "__main__":
    main()
