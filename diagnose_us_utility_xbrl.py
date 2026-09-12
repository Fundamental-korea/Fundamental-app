"""Diagnose SEC XBRL coverage before changing utility scoring.

This tool is read-only: it fetches SEC Company Facts and prints the actual
annual concept tags available for utility companies. It never writes raw SEC
JSON to Supabase or to disk.

Examples:
    py diagnose_us_utility_xbrl.py --tickers XEL,PPL,SWX,TAC,TVE
    py diagnose_us_utility_xbrl.py --tickers XEL,PPL,BEP,BEPC,NGG,KEP
"""
from __future__ import annotations

import argparse
from datetime import datetime

import requests

from collector_us_fundamental import SEC_FACTS_URL, SEC_SUBMISSIONS_URL, SEC_TICKERS, SEC_USER_AGENT, fetch_json
from us_utility_extraction import (
    REVENUE_TAGS, INTEREST_TAGS, EPS_TAGS, OCF_TAGS,
    DEBT_CURRENT_TAGS, DEBT_NONCURRENT_TAGS, DEBT_TOTAL_TAGS,
    CAPEX_TAGS, DIVIDEND_TAGS,
)

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


def ticker_cik(session, ticker: str) -> str:
    data = fetch_json(session, SEC_TICKERS)
    for item in data.values():
        if str(item.get("ticker", "")).upper() == ticker.upper():
            return str(item["cik_str"]).zfill(10)
    raise RuntimeError(f"CIK not found: {ticker}")


def annual_rows(fact):
    rows = []
    for unit, values in (fact.get("units") or {}).items():
        if not isinstance(values, list):
            continue
        for r in values:
            if r.get("form") not in {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
                continue
            if not r.get("end"):
                continue
            start = r.get("start")
            if start:
                try:
                    days = (datetime.fromisoformat(r["end"]).date() - datetime.fromisoformat(start).date()).days
                except ValueError:
                    continue
                if not 300 <= days <= 380:
                    continue
            else:
                frame = str(r.get("frame") or "")
                if not frame.startswith("CY") and r.get("fy") is None:
                    continue
            rows.append((
                datetime.fromisoformat(r["end"]).date().year,
                r.get("end"), r.get("filed"), r.get("form"), r.get("frame"),
                r.get("fy"), r.get("val"), unit,
            ))
    return rows


def diagnose(ticker: str, facts: dict, submissions: dict):
    print("\n" + "=" * 90)
    print(f"{ticker} | {submissions.get('name') or ticker} | CIK {submissions.get('cik')}")
    print(f"SIC: {submissions.get('sic')} | {submissions.get('sicDescription')}")

    root = facts.get("facts", facts)
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
            print(f"  {tag}: years={years[-8:]} latest={latest[0]} value={latest[6]} unit={latest[7]} form={latest[3]} frame={latest[4]}")

    print("\n[CURRENT EXTRACTION CANDIDATES]")
    print("  revenue:", ", ".join(REVENUE_TAGS))
    print("  interest:", ", ".join(INTEREST_TAGS))
    print("  eps:", ", ".join(EPS_TAGS))
    print("  ocf:", ", ".join(OCF_TAGS))
    print("  capex:", ", ".join(CAPEX_TAGS))
    print("  dividend:", ", ".join(DIVIDEND_TAGS))


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
