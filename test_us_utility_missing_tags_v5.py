"""Utility SEC missing-tag audit v5.

Purpose
-------
Find the actual SEC concepts behind metrics that are still None in the
utility v3 diagnostic. This intentionally searches the complete us-gaap and
ifrs-full fact dictionaries instead of guessing aliases.

No Supabase writes. No production changes. No raw SEC JSON persistence.

Usage
-----
python test_us_utility_missing_tags_v5.py --tickers D,DUK,ED,EXC,NEE,SO
"""

from __future__ import annotations

import argparse
import math
import os
import time
from datetime import datetime

import requests

from collector_us_fundamental import SEC_FACTS_URL, SEC_SUBMISSIONS_URL

FLOW_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

SEARCH_GROUPS = {
    "EPS": ["earningspershare", "eps"],
    "INTEREST": ["interestexpense", "financecost", "interest"],
    "CAPEX": ["paymentstoacquire", "capitalexpenditure", "productiveassets", "propertyplantandequipment"],
    "DIVIDEND": ["paymentsofdividend", "dividend"],
    "DEBT": ["debt", "borrowings", "notespayable", "longtermdebt"],
    "LIABILITIES": ["liabilities"],
}


def clean_number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


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


def resolve_cik(session, ticker):
    data = fetch_json(session, "https://www.sec.gov/files/company_tickers.json")
    for row in data.values():
        if str(row.get("ticker", "")).upper() == ticker.upper():
            return str(row["cik_str"]).zfill(10), row.get("title")
    return None, None


def all_fact_tags(facts):
    root = facts.get("facts", facts)
    out = []
    for namespace in ("us-gaap", "ifrs-full"):
        for tag, fact in (root.get(namespace) or {}).items():
            out.append((namespace, tag, fact))
    return out


def rows_for_fact(fact, namespace, tag, instant=False):
    out = []
    for unit, rows in (fact.get("units") or {}).items():
        if not isinstance(rows, list):
            continue
        for r in rows:
            if r.get("form") not in FLOW_FORMS or not r.get("end"):
                continue
            try:
                end = datetime.fromisoformat(r["end"]).date()
            except ValueError:
                continue
            if not instant:
                start = r.get("start")
                if start:
                    try:
                        start_date = datetime.fromisoformat(start).date()
                    except ValueError:
                        continue
                    days = (end - start_date).days
                    if not 300 <= days <= 380:
                        continue
                else:
                    frame = str(r.get("frame") or "")
                    if not (frame.startswith("CY") and frame[2:].isdigit()):
                        continue
            value = clean_number(r.get("val"))
            if value is None:
                continue
            out.append({
                "year": end.year,
                "val": value,
                "unit": unit,
                "end": r.get("end"),
                "start": r.get("start"),
                "filed": r.get("filed") or "",
                "form": r.get("form"),
                "frame": r.get("frame"),
                "namespace": namespace,
                "tag": tag,
            })
    return out


def dedupe(rows):
    out = {}
    for r in rows:
        key = (r["end"], r["filed"], 0 if r["form"].endswith("/A") else 1)
        prev = out.get(r["year"])
        if prev is None or key > prev[0]:
            out[r["year"]] = (key, r)
    return {y: r for y, (_, r) in out.items()}


def print_group(facts, group, years):
    needles = SEARCH_GROUPS[group]
    candidates = []
    for namespace, tag, fact in all_fact_tags(facts):
        low = tag.lower()
        if any(n in low for n in needles):
            rows = dedupe(rows_for_fact(fact, namespace, tag, instant=(group in {"DEBT", "LIABILITIES"})))
            if rows:
                candidates.append((namespace, tag, rows))

    print(f"\n[{group} ACTUAL SEC TAGS]")
    if not candidates:
        print("  NONE")
        return

    # Show candidates with the most recent requested-year observations first.
    candidates.sort(key=lambda x: sum(1 for y in years if y in x[2]), reverse=True)
    for namespace, tag, rows in candidates[:40]:
        vals = []
        for year in years:
            r = rows.get(year)
            if r:
                vals.append(f"{year}={r['val']:.8g}")
        print(f"  {namespace}:{tag} | " + ", ".join(vals))
    if len(candidates) > 40:
        print(f"  ... {len(candidates) - 40} more matching concepts omitted")


def run(session, ticker):
    cik, title = resolve_cik(session, ticker)
    if not cik:
        raise RuntimeError("CIK not found")
    facts = fetch_json(session, SEC_FACTS_URL.format(cik=cik))
    submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik))

    root = facts.get("facts", facts)
    years = set()
    for _, _, fact in all_fact_tags(facts):
        for unit_rows in (fact.get("units") or {}).values():
            for r in unit_rows if isinstance(unit_rows, list) else []:
                if r.get("form") in FLOW_FORMS and r.get("end"):
                    try:
                        years.add(datetime.fromisoformat(r["end"]).date().year)
                    except ValueError:
                        pass
    if not years:
        print(f"\n{ticker}: no annual years")
        return
    latest = max(years)
    target_years = list(range(latest - 4, latest + 1))

    print("\n" + "=" * 100)
    print(f"{ticker} | {title or submissions.get('name')} | CIK={cik}")
    print(f"SIC={submissions.get('sic')} | {submissions.get('sicDescription')}")
    print(f"Target years: {target_years}")

    for group in SEARCH_GROUPS:
        print_group(facts, group, target_years)

    # Also show the exact currently selected production facts where available.
    print("\n[QUICK PRODUCTION-FACT CHECK]")
    selected = {
        "revenue": "revenue",
        "eps": "eps",
        "interest_expense": "interest_expense",
        "operating_cash_flow": "operating_cash_flow",
        "liabilities": "liabilities",
    }
    # This section only reports whether exact common SEC tags exist.
    for metric, tag in selected.items():
        found = []
        for namespace, t, fact in all_fact_tags(facts):
            if t == {"revenue": "Revenue", "eps": "EarningsPerShareDiluted", "interest_expense": "InterestExpense", "operating_cash_flow": "NetCashProvidedByUsedInOperatingActivities", "liabilities": "Liabilities"}[metric]:
                found.append(f"{namespace}:{t}")
        print(f"  {metric}: {found or 'exact common tag not present'}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", required=True)
    args = parser.parse_args()
    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    if not tickers:
        parser.error("No tickers supplied")

    session = requests.Session()
    session.headers.update({
        "User-Agent": os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com"),
        "Accept-Encoding": "gzip, deflate",
    })

    print(f"Mode: DIAGNOSTIC ONLY / NO DB WRITE / NO PRODUCTION CHANGES")
    print(f"Tickers: {', '.join(tickers)}")
    failed = 0
    for ticker in tickers:
        try:
            run(session, ticker)
        except Exception as exc:
            failed += 1
            print(f"\n{'=' * 100}\n{ticker}: ERROR: {exc}")
    print(f"\nCompleted: failed={failed}/{len(tickers)}")


if __name__ == "__main__":
    main()
