"""Diagnostic tool for suspicious US XBRL liquidity metrics.

Prints the selected SEC facts used for quick-ratio calculation without writing
raw SEC data to Supabase. Intended for local debugging only.

Usage:
  python debug_us_xbrl.py --tickers MSFT,ARKO,AOXY,AMAT,AAPL

CIKs can be supplied through SEC_CIK_MAP=TICKER:CIK,...
"""

from __future__ import annotations

import argparse
import os

import requests
from supabase import create_client

from collector_us_fundamental import FACT_ALIASES, annual_records, build_fact_index, load_company

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
TARGETS = ("current_assets", "current_liabilities", "inventory", "cash", "receivables")


def selected_tag(facts, logical_name, latest_year):
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    candidates = []
    for priority, tag in enumerate(FACT_ALIASES[logical_name]):
        rows = annual_records(gaap.get(tag, {}))
        if not rows:
            continue
        latest_present = max(rows)
        coverage = len(rows)
        latest_distance = (latest_year - latest_present) if latest_year is not None else 999
        candidates.append((1 if latest_present == latest_year else 0, -latest_distance, coverage, -priority, tag))
    return sorted(candidates, reverse=True)[0][-1] if candidates else None


def get_cik_map(tickers):
    result = {}
    for item in os.environ.get("SEC_CIK_MAP", "").split(","):
        if ":" in item:
            ticker, cik = item.split(":", 1)
            result[ticker.strip().upper()] = cik.strip()
    if SUPABASE_KEY:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        rows = sb.table("US_Companies").select("ticker,cik").in_("ticker", tickers).execute().data
        for row in rows:
            result.setdefault(row["ticker"].upper(), str(row["cik"]))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers")
    args = parser.parse_args()
    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    cik_map = get_cik_map(tickers)

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    for ticker in tickers:
        cik = cik_map.get(ticker)
        if not cik:
            print(f"\n[{ticker}] CIK not found in Supabase or SEC_CIK_MAP")
            continue
        print("\n" + "=" * 100)
        print(f"{ticker}  CIK={cik}")
        facts, _ = load_company(session, ticker, cik)
        index = build_fact_index(facts)
        all_years = sorted({y for rows in index.values() for y in rows})
        latest_year = max(all_years) if all_years else None
        print(f"latest indexed year: {latest_year}")

        values = {}
        for logical_name in TARGETS:
            tag = selected_tag(facts, logical_name, latest_year)
            rows = index.get(logical_name) or {}
            print(f"\n{logical_name}: {tag or 'NONE'}")
            for year in sorted(rows)[-5:]:
                r = rows[year]
                print(f"  FY={year} val={r['val']} unit={r['unit']} form={r['form']} start={r.get('start')} end={r['end']} filed={r['filed']} frame={r.get('frame')}")
            if latest_year in rows:
                values[logical_name] = rows[latest_year]["val"]

        ca = values.get("current_assets")
        cl = values.get("current_liabilities")
        inv = values.get("inventory", 0.0)
        quick_assets = ca - inv if ca is not None else None
        quick_ratio = quick_assets / cl if quick_assets is not None and cl not in (None, 0) else None
        print("\nQUICK RATIO RECONSTRUCTION")
        print(f"  current_assets      = {ca}")
        print(f"  inventory           = {inv}")
        print(f"  quick_assets        = {quick_assets}")
        print(f"  current_liabilities = {cl}")
        print(f"  quick_ratio         = {quick_ratio}")


if __name__ == "__main__":
    main()
