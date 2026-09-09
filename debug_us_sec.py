"""Temporary SEC extraction validator for the US fundamental collector.

Usage:
  python debug_us_sec.py --tickers AAPL,JPM,GSBD,O,NEE

This script does NOT write anything to Supabase. It only fetches SEC Company Facts
and prints the annual values currently selected by collector_us_fundamental.py.
"""

import argparse
import os
import requests

from collector_us_fundamental import (
    build_fact_index,
    get_universe,
    load_company,
    annual_metrics,
    FACT_ALIASES,
)

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


RAW_METRICS = (
    "revenue",
    "operating_income",
    "net_income",
    "eps",
    "operating_cash_flow",
    "sga",
    "interest_expense",
    "assets",
    "equity",
    "liabilities",
    "current_assets",
    "current_liabilities",
)


def fmt(value):
    if value is None:
        return "MISSING"
    if isinstance(value, float):
        return f"{value:,.6g}"
    return str(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default="AAPL,JPM,GSBD,O,NEE")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_KEY is required")

    sb = __import__("supabase").create_client(SUPABASE_URL, SUPABASE_KEY)
    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    universe = get_universe(sb, tickers=tickers)
    by_ticker = {row["ticker"]: row for row in universe}

    session = requests.Session()
    session.headers.update({
        "User-Agent": os.environ.get(
            "SEC_USER_AGENT",
            "Fundamental-app contact@example.com",
        )
    })

    for ticker in tickers:
        row = by_ticker.get(ticker)
        print("\n" + "=" * 90)
        print(f"{ticker}")
        if not row:
            print("NOT FOUND in US_Companies or not fundamental-eligible")
            continue

        facts, submissions = load_company(session, ticker, row["cik"])
        index = build_fact_index(facts)

        all_years = sorted({year for rows in index.values() for year in rows.keys()})
        flow_years = sorted(
            set(index.get("revenue", {}).keys())
            | set(index.get("operating_income", {}).keys())
            | set(index.get("net_income", {}).keys())
        )
        latest_year = max(flow_years) if flow_years else (max(all_years) if all_years else None)

        print(f"Company: {row['company_name']}")
        print(f"CIK: {row['cik']}")
        print(f"SEC SIC: {submissions.get('sic')} | {submissions.get('sicDescription')}")
        print(f"Selected latest/base year: {latest_year}")
        print(f"Annual years available: {all_years[-12:]}")

        print("\nSelected SEC tag + annual record years:")
        for metric in RAW_METRICS:
            rows = index.get(metric) or {}
            tags = []
            for tag in FACT_ALIASES.get(metric, []):
                fact = (facts.get("facts") or {}).get("us-gaap", {}).get(tag)
                if fact and rows:
                    # The collector currently picks one alias internally. This
                    # line helps identify which aliases are present in SEC data.
                    tags.append(tag)
            print(f"  {metric:22} years={sorted(rows.keys())[-8:]} aliases_present={tags}")

        if latest_year is None:
            print("\nNo annual flow year available.")
            continue

        inspect_years = sorted(set(
            [latest_year, latest_year - 1, latest_year - 3, latest_year - 5, latest_year - 10]
        ) & set(all_years))

        print("\nAnnual raw values selected by collector:")
        for year in inspect_years:
            m = annual_metrics(index, year)
            print(f"\n  FY {year}")
            for metric in RAW_METRICS:
                print(f"    {metric:22} = {fmt(m.get(metric))}")

        print("\nBase-year check for periods 1/3/5/10:")
        for period in (1, 3, 5, 10):
            target = latest_year - period
            candidates = [y for y in all_years if y <= target and y < latest_year]
            base_year = max(candidates) if candidates else (min([y for y in all_years if y < latest_year], default=None))
            print(f"  {period:2}Y: target={target}, selected_base={base_year}")


if __name__ == "__main__":
    main()
