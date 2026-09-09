"""Diagnostic tool for suspicious US XBRL liquidity metrics.

Prints the selected SEC facts used for quick-ratio calculation without writing
raw SEC data to Supabase. Intended for local debugging only.

Usage:
  python debug_us_xbrl.py --tickers MSFT,ARKO,AOXY,AMAT,AAPL
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime

import requests

from collector_us_fundamental import FACT_ALIASES, annual_records, build_fact_index, annual_metrics, load_company

SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")

TARGETS = (
    "current_assets",
    "current_liabilities",
    "inventory",
    "cash",
    "receivables",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers")
    args = parser.parse_args()

    # Reuse the universe lookup locally so the diagnostic remains independent of Supabase.
    # SEC submissions are fetched from CIK values supplied via a simple ticker map below.
    # If a ticker is missing, provide --cik-map in the environment as TICKER:CIK,...
    cik_map = {}
    raw_map = os.environ.get("SEC_CIK_MAP", "")
    for item in raw_map.split(","):
        if ":" in item:
            ticker, cik = item.split(":", 1)
            cik_map[ticker.strip().upper()] = cik.strip()

    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    for ticker in tickers:
        cik = cik_map.get(ticker)
        if not cik:
            print(f"\n[{ticker}] missing CIK. Set SEC_CIK_MAP={ticker}:CIK,...")
            continue

        print("\n" + "=" * 90)
        print(f"{ticker}  CIK={cik}")
        facts, submissions = load_company(session, ticker, cik)
        index = build_fact_index(facts)
        all_years = sorted({y for rows in index.values() for y in rows.keys()})
        latest_year = max(all_years) if all_years else None
        print(f"latest indexed year: {latest_year}")

        for logical_name in TARGETS:
            aliases = FACT_ALIASES[logical_name]
            selected_rows = index.get(logical_name) or {}
            selected_tag = next((tag for tag in aliases if annual_records(((facts.get("facts") or {}).get("us-gaap") or {}).get(tag, {}))), None)
            print(f"\n{logical_name}: selected alias={selected_tag}")
            if not selected_rows:
                print("  NO ANNUAL ROWS")
                continue
            for year in sorted(selected_rows)[-5:]:
                r = selected_rows[year]
                print(
                    f"  FY={year} val={r['val']} unit={r['unit']} form={r['form']} "
                    f"start={r.get('start')} end={r['end']} filed={r['filed']} frame={r.get('frame')}"
                )

        if latest_year is not None:
            m = annual_metrics(index, latest_year)
            print("\nCOMPUTED")
            print(f"  current_assets      = {m.get('current_assets') if 'current_assets' in m else 'not exposed'}")
            print(f"  current_liabilities = {m.get('current_liabilities') if 'current_liabilities' in m else 'not exposed'}")
            print(f"  quick_ratio         = {m.get('quick_ratio')}")


if __name__ == "__main__":
    main()
