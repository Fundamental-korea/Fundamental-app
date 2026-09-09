"""
US SEC pipeline debugger.

Purpose: locate where a fact disappears between raw SEC facts,
annual_records(), build_fact_index(), latest_annual_value(), and annual_metrics().
No Supabase writes.

Usage:
  python debug_us_pipeline.py --tickers AAPL,NEE,O,JPM,GSBD
"""

import argparse
import os

import requests

from collector_us_fundamental import (
    SEC_FACTS_URL,
    SEC_SUBMISSIONS_URL,
    FACT_ALIASES,
    annual_records,
    build_fact_index,
    latest_annual_value,
    annual_metrics,
    fetch_json,
)

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")


def universe_row(sb, ticker):
    return (
        sb.table("US_Companies")
        .select("ticker,cik,company_name")
        .eq("ticker", ticker)
        .eq("is_fundamental_eligible", True)
        .limit(1)
        .execute()
        .data
    )


def inspect_fact(facts, metric, year):
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    print(f"\n  [{metric}] target FY {year}")
    for tag in FACT_ALIASES.get(metric, []):
        fact = gaap.get(tag)
        if not fact:
            print(f"    {tag}: ABSENT")
            continue
        rows = annual_records(fact)
        row = rows.get(year)
        if row:
            print(f"    {tag}: SELECTED -> {row}")
        else:
            print(f"    {tag}: present, but annual_records[{year}] = MISSING")
            # Show nearby candidate raw rows for diagnosis.
            candidates = []
            for unit, raw_rows in (fact.get("units") or {}).items():
                for r in raw_rows if isinstance(raw_rows, list) else []:
                    if r.get("fy") == year:
                        candidates.append((unit, r))
            for unit, r in candidates[:8]:
                print(f"      RAW {unit}: {r}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", required=True)
    args = parser.parse_args()

    # Import lazily so the script can be run from the repo root.
    from supabase import create_client

    key = os.environ.get("SUPABASE_KEY", "")
    if not key:
        raise RuntimeError("SUPABASE_KEY is required")
    sb = create_client(SUPABASE_URL, key)

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"})

    for ticker in [x.upper().strip() for x in args.tickers.split(",") if x.strip()]:
        rows = universe_row(sb, ticker)
        if not rows:
            print(f"\n{'='*90}\n{ticker}: NOT FOUND / NOT ELIGIBLE")
            continue
        row = rows[0]
        cik = row["cik"]
        facts = fetch_json(session, SEC_FACTS_URL.format(cik=str(cik).zfill(10)))
        submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=str(cik).zfill(10)))
        index = build_fact_index(facts)

        years = sorted({y for rows2 in index.values() for y in rows2.keys()})
        flow_years = sorted(
            set(index.get("revenue", {}).keys())
            | set(index.get("operating_income", {}).keys())
            | set(index.get("net_income", {}).keys())
        )
        latest = max(flow_years) if flow_years else max(years)

        print(f"\n{'='*90}")
        print(f"{ticker} | {row['company_name']} | CIK {cik} | latest {latest}")
        print(f"SEC SIC: {submissions.get('sic')} | {submissions.get('sicDescription')}")

        for metric in FACT_ALIASES:
            inspect_fact(facts, metric, latest)
            indexed = (index.get(metric) or {}).get(latest)
            print(f"    INDEX {metric}[{latest}] = {indexed}")
            print(f"    latest_annual_value = {latest_annual_value(index, metric, latest)}")

        print("\n  annual_metrics(latest):")
        metrics = annual_metrics(index, latest)
        for key2, value in metrics.items():
            print(f"    {key2:22s} = {value}")


if __name__ == "__main__":
    main()
