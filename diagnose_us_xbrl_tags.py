"""Generic SEC XBRL tag diagnostic for US fundamental extraction.

Read-only diagnostic tool.
- SEC Company Facts only; no Supabase writes.
- Raw SEC payloads stay in memory and are never stored.
- Designed to be reusable across sectors.

Usage:
  python3 diagnose_us_xbrl_tags.py
  python3 diagnose_us_xbrl_tags.py --tickers AAPL,MSFT,NVDA
  python3 diagnose_us_xbrl_tags.py --metric interest_expense
"""
from __future__ import annotations

import argparse
import re
import time
from collections import defaultdict
from datetime import datetime

import requests

from collector_us_fundamental import FACT_ALIASES, IFRS_FACT_ALIASES, load_company, build_fact_index

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_USER_AGENT = "Fundamental-app contact@example.com"

DEFAULT_TICKERS = {
    "technology": ["AAPL", "MSFT", "NVDA"],
    "consumer": ["COST", "WMT", "AMZN"],
    "industrials": ["CAT", "HON", "DE"],
    "materials": ["LIN", "APD", "NEM"],
    "communication": ["META", "VZ", "T"],
}

METRICS = [
    "interest_expense",
    "liabilities",
    "sga",
    "operating_income",
    "operating_cash_flow",
    "current_assets",
    "current_liabilities",
    "receivables",
    "inventory",
]

KEYWORDS = {
    "interest_expense": ["interest", "financecost", "debtservice", "borrowingcost"],
    "liabilities": ["liabilit", "obligation"],
    "sga": ["selling", "general", "administrative", "sg&a", "operatingexpense"],
    "operating_income": ["operatingincome", "incomeoperations", "operatingprofit", "profitlossfromoperating"],
    "operating_cash_flow": ["operatingcash", "cashflowfromoperating", "netcashprovidedbyusedinoperating"],
    "current_assets": ["assetscurrent", "currentassets"],
    "current_liabilities": ["liabilitiescurrent", "currentliabilities"],
    "receivables": ["receivable", "tradeandotherreceivable"],
    "inventory": ["inventory", "inventor", "stockintrade"],
}


def clean(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def fetch_json(session, url, retries=3):
    for attempt in range(retries):
        r = session.get(url, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
    raise RuntimeError(f"SEC request failed: {url}")


def ticker_map(session):
    payload = fetch_json(session, SEC_TICKERS_URL)
    out = {}
    for row in payload.values():
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            out[ticker] = str(row.get("cik_str") or "").zfill(10)
    return out


def annual_rows(fact):
    units = (fact or {}).get("units") or {}
    rows = []
    for unit, values in units.items():
        if not isinstance(values, list):
            continue
        for r in values:
            if r.get("form") not in {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
                continue
            end = r.get("end")
            if not end or not r.get("fy"):
                continue
            start = r.get("start")
            if start:
                try:
                    days = (datetime.fromisoformat(end).date() - datetime.fromisoformat(start).date()).days
                except ValueError:
                    continue
                if not 300 <= days <= 380:
                    continue
            try:
                val = float(r.get("val"))
            except (TypeError, ValueError):
                continue
            rows.append((int(r["fy"]), end, val, unit, r.get("filed") or ""))
    return rows


def matching_tags(facts, metric):
    keywords = [clean(x) for x in KEYWORDS[metric]]
    aliases = set(clean(x) for x in FACT_ALIASES.get(metric, []))
    aliases |= set(clean(x) for x in IFRS_FACT_ALIASES.get(metric, []))
    hits = []
    for namespace, ns_facts in (facts.get("facts") or {}).items():
        if not isinstance(ns_facts, dict):
            continue
        for tag, fact in ns_facts.items():
            ctag = clean(tag)
            if ctag in aliases:
                reason = "CURRENT_ALIAS"
            elif any(k in ctag for k in keywords):
                reason = "KEYWORD_MATCH"
            else:
                continue
            rows = annual_rows(fact)
            if not rows:
                continue
            latest = max(rows, key=lambda x: (x[0], x[1]))
            hits.append((namespace, tag, reason, latest, len(rows)))
    hits.sort(key=lambda x: (0 if x[2] == "CURRENT_ALIAS" else 1, -x[3][0], x[0], x[1]))
    return hits


def print_metric_report(ticker, facts, metric):
    index = build_fact_index(facts)
    latest_years = sorted({y for rows in index.values() for y in rows})
    latest_year = latest_years[-1] if latest_years else None
    current = (index.get(metric) or {}).get(latest_year) if latest_year else None
    print(f"\n{metric.upper()}")
    print("-" * 90)
    if current:
        print(f"CURRENT MATCH : {current['namespace']}:{current['tag']} = {current['val']:.6g} ({latest_year})")
    else:
        print("CURRENT MATCH : MISSING")
    hits = matching_tags(facts, metric)
    if not hits:
        print("RELATED TAGS  : none found")
        return
    print("RELATED TAGS  :")
    for namespace, tag, reason, latest, count in hits[:20]:
        fy, end, val, unit, filed = latest
        print(f"  [{reason:<13}] {namespace}:{tag:<70} latest={val:.6g} fy={fy} end={end} unit={unit} rows={count}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=None, help="Comma-separated tickers")
    parser.add_argument("--metric", choices=METRICS, default=None)
    args = parser.parse_args()

    selected = []
    if args.tickers:
        selected = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    else:
        for group, tickers in DEFAULT_TICKERS.items():
            selected.extend(tickers)

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    print("=" * 100)
    print("GENERIC SEC XBRL TAG DIAGNOSTIC")
    print("=" * 100)
    print("Read-only: YES")
    print("Supabase write: NO")
    print("Raw SEC storage: NO")
    print(f"Tickers: {', '.join(selected)}")
    print(f"Metric focus: {args.metric or 'ALL DIAGNOSTIC METRICS'}")
    print("=" * 100)

    print("Loading SEC ticker map...")
    mapping = ticker_map(session)
    print(f"SEC ticker map: {len(mapping):,} tickers")

    metrics = [args.metric] if args.metric else METRICS
    for ticker in selected:
        cik = mapping.get(ticker)
        print("\n" + "=" * 100)
        print(f"{ticker} | CIK {cik or 'NOT FOUND'}")
        print("=" * 100)
        if not cik:
            print("SKIP: ticker not found in SEC ticker map")
            continue
        try:
            facts, submissions = load_company(session, ticker, cik)
            company_name = submissions.get("name") or ticker
            print(f"Company : {company_name}")
            print(f"Latest filing form : {submissions.get('filings', {}).get('recent', {}).get('form', ['?'])[0]}")
            for metric in metrics:
                print_metric_report(ticker, facts, metric)
        except Exception as exc:
            print(f"ERROR: {type(exc).__name__}: {exc}")

    print("\n" + "=" * 100)
    print("END OF DIAGNOSTIC")
    print("=" * 100)
    print("Use this report to decide which XBRL concepts/aliases are safe to add to the generic collector.")


if __name__ == "__main__":
    main()
