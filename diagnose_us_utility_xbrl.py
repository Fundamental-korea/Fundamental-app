"""Read-only SEC XBRL diagnostic for utility extraction.

This tool does not write to Supabase and does not save raw SEC JSON.
It reports the actual annual concepts available in us-gaap/ifrs-full and
compares them with the utility extractor's current candidate tags.
"""
from __future__ import annotations

import argparse
from datetime import datetime

import requests

from collector_us_fundamental import (
    SEC_FACTS_URL,
    SEC_SUBMISSIONS_URL,
    SEC_USER_AGENT,
    fetch_json,
)
from us_utility_extraction import (
    REVENUE_TAGS, INTEREST_TAGS, EPS_TAGS, OCF_TAGS,
    DEBT_CURRENT_TAGS, DEBT_NONCURRENT_TAGS, DEBT_TOTAL_TAGS,
    CAPEX_TAGS, DIVIDEND_TAGS,
)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FLOW_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
KEYWORDS = (
    "revenue", "sales", "operatingincome", "profitloss", "earningspershare",
    "weightedaverage", "assets", "liabilities", "equity", "debt",
    "interest", "operatingactivities", "dividend", "productiveassets",
    "propertyplantandequipment", "cashandcashequivalents",
)
TARGET_TAGS = set(
    REVENUE_TAGS + INTEREST_TAGS + EPS_TAGS + OCF_TAGS
    + DEBT_CURRENT_TAGS + DEBT_NONCURRENT_TAGS + DEBT_TOTAL_TAGS
    + CAPEX_TAGS + DIVIDEND_TAGS
)


def ticker_cik(session: requests.Session, ticker: str) -> str:
    data = fetch_json(session, SEC_TICKERS_URL)
    for item in data.values():
        if str(item.get("ticker", "")).upper() == ticker.upper():
            return str(item["cik_str"]).zfill(10)
    raise RuntimeError(f"CIK not found: {ticker}")


def annual_rows(fact: dict) -> list[tuple]:
    rows = []
    for unit, values in (fact.get("units") or {}).items():
        if not isinstance(values, list):
            continue
        for r in values:
            if r.get("form") not in FLOW_FORMS or not r.get("end"):
                continue
            try:
                end_date = datetime.fromisoformat(r["end"]).date()
            except ValueError:
                continue
            start = r.get("start")
            if start:
                try:
                    start_date = datetime.fromisoformat(start).date()
                except ValueError:
                    continue
                days = (end_date - start_date).days
                if not 300 <= days <= 380:
                    continue
            elif not str(r.get("frame") or "").startswith("CY") and r.get("fy") is None:
                continue
            rows.append((
                end_date.year, r.get("end"), r.get("filed"), r.get("form"),
                r.get("frame"), r.get("fy"), r.get("val"), unit,
            ))
    return rows


def diagnose(ticker: str, facts: dict, submissions: dict):
    print("\n" + "=" * 100)
    print(f"{ticker} | {submissions.get('name') or ticker} | CIK {submissions.get('cik')}")
    print(f"SIC: {submissions.get('sic')} | {submissions.get('sicDescription')}")

    root = facts.get("facts") or {}
    for namespace in ("us-gaap", "ifrs-full"):
        concepts = root.get(namespace) or {}
        print(f"\n[{namespace}] concepts={len(concepts)}")
        matched = []
        for tag, fact in concepts.items():
            low = tag.lower()
            if tag in TARGET_TAGS or any(k in low for k in KEYWORDS):
                rows = annual_rows(fact)
                if rows:
                    years = sorted({r[0] for r in rows})
                    latest = max(rows, key=lambda r: (r[0], r[2] or ""))
                    matched.append((tag, years, latest))
        for tag, years, latest in sorted(matched, key=lambda x: x[0].lower()):
            print(
                f"  {tag}: years={years[-8:]} latest={latest[0]} "
                f"value={latest[6]} unit={latest[7]} form={latest[3]} frame={latest[4]}"
            )

    print("\n[CURRENT EXTRACTION CANDIDATES]")
    for name, tags in (
        ("revenue", REVENUE_TAGS),
        ("interest", INTEREST_TAGS),
        ("eps", EPS_TAGS),
        ("ocf", OCF_TAGS),
        ("debt_current", DEBT_CURRENT_TAGS),
        ("debt_noncurrent", DEBT_NONCURRENT_TAGS),
        ("debt_total", DEBT_TOTAL_TAGS),
        ("capex", CAPEX_TAGS),
        ("dividend", DIVIDEND_TAGS),
    ):
        print(f"  {name}: {', '.join(tags)}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    for ticker in [x.strip().upper() for x in args.tickers.split(",") if x.strip()]:
        try:
            cik = ticker_cik(session, ticker)
            facts = fetch_json(session, SEC_FACTS_URL.format(cik=cik))
            submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik))
            diagnose(ticker, facts, submissions)
        except Exception as exc:
            print(f"\n{ticker}: FAILED {exc}")


if __name__ == "__main__":
    main()
