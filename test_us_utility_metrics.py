"""Dry-run diagnostic for SEC utility metrics.

Purpose:
    Check which raw SEC Company Facts can reliably support a future Utility
    scoring profile. This script NEVER writes to Supabase and NEVER stores raw
    SEC JSON.

Run:
    python test_us_utility_metrics.py
    python test_us_utility_metrics.py --tickers DUK,NEE,VST,CEG,SO,D,AEP,EXC,ETR,PEG,AWR
"""

from __future__ import annotations

import argparse
import os
import time
from collections import defaultdict
from datetime import datetime

import requests
from supabase import create_client

from collector_us_fundamental import (
    FACT_NAMESPACE_ALIASES,
    SEC_FACTS_URL,
    SEC_SUBMISSIONS_URL,
    annual_records,
    clean_number,
)

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")

DEFAULT_TICKERS = [
    "DUK", "NEE", "VST", "CEG", "SO", "D", "AEP", "EXC", "ETR", "PEG", "AWR",
]

# Logical metrics we want to verify before designing Utility v3.
DIAGNOSTIC_METRICS = {
    "total_debt": [
        ("us-gaap", ["LongTermDebtAndFinanceLeaseObligationsCurrent", "LongTermDebtCurrent"]),
        ("us-gaap", ["LongTermDebtAndFinanceLeaseObligations", "LongTermDebtNoncurrent"]),
        ("us-gaap", ["LongTermDebtCurrent", "LongTermDebtNoncurrent"]),
    ],
    "long_term_debt": [
        ("us-gaap", ["LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent"]),
    ],
    "current_debt": [
        ("us-gaap", ["LongTermDebtCurrent", "ShortTermBorrowings", "ShortTermDebt"]),
    ],
    "assets": [(ns, tags) for ns, aliases in FACT_NAMESPACE_ALIASES.items() for tags in [aliases["assets"]]],
    "equity": [(ns, tags) for ns, aliases in FACT_NAMESPACE_ALIASES.items() for tags in [aliases["equity"]]],
    "liabilities": [(ns, tags) for ns, aliases in FACT_NAMESPACE_ALIASES.items() for tags in [aliases["liabilities"]]],
    "cash": [(ns, tags) for ns, aliases in FACT_NAMESPACE_ALIASES.items() for tags in [aliases["cash"]]],
    "operating_cash_flow": [(ns, tags) for ns, aliases in FACT_NAMESPACE_ALIASES.items() for tags in [aliases["operating_cash_flow"]]],
    "interest_expense": [(ns, tags) for ns, aliases in FACT_NAMESPACE_ALIASES.items() for tags in [aliases["interest_expense"]]],
    "operating_income": [(ns, tags) for ns, aliases in FACT_NAMESPACE_ALIASES.items() for tags in [aliases["operating_income"]]],
    "net_income": [(ns, tags) for ns, aliases in FACT_NAMESPACE_ALIASES.items() for tags in [aliases["net_income"]]],
    "revenue": [(ns, tags) for ns, aliases in FACT_NAMESPACE_ALIASES.items() for tags in [aliases["revenue"]]],
}

# Extra tags that are useful for cash-flow/dividend analysis when SEC facts expose them.
EXTRA_TAGS = {
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForProceedsFromOtherPropertyPlantAndEquipment",
    ],
    "dividends": [
        "PaymentsOfDividends",
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividendsMinorityInterest",
    ],
}


def fetch_json(session: requests.Session, url: str, retries: int = 3):
    for attempt in range(retries):
        response = session.get(url, timeout=30)
        if response.status_code == 200:
            return response.json()
        if response.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue
        response.raise_for_status()
    raise RuntimeError(f"SEC request failed after {retries} retries: {url}")


def get_ciks(tickers: list[str]) -> dict[str, str]:
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required to read US_Companies.")

    # Read-only lookup. This script performs no insert/update/upsert/delete.
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    result = (
        client.table("US_Companies")
        .select("ticker,cik,company_name,sector_common,company_type,scoring_profile")
        .in_("ticker", tickers)
        .execute()
    )
    rows = result.data or []
    return {str(row["ticker"]).upper(): row for row in rows if row.get("cik")}


def candidate_rows(companyfacts: dict, namespace: str, tag: str) -> dict[int, dict]:
    facts = (companyfacts.get("facts") or {}).get(namespace) or {}
    fact = facts.get(tag)
    return annual_records(fact) if fact else {}


def find_metric(companyfacts: dict, metric: str):
    """Find annual values for a diagnostic metric without altering collector scoring."""
    candidates = DIAGNOSTIC_METRICS.get(metric, [])
    found = []
    for ns, tags in candidates:
        for priority, tag in enumerate(tags):
            rows = candidate_rows(companyfacts, ns, tag)
            if not rows:
                continue
            for year, row in rows.items():
                found.append((year, 2 if ns == "us-gaap" else 1, -priority, row, ns, tag))

    by_year = {}
    for year, ns_rank, alias_rank, row, ns, tag in found:
        candidate = (ns_rank, alias_rank, row.get("end", ""), row.get("filed", ""), row, ns, tag)
        previous = by_year.get(year)
        if previous is None or candidate[:4] > previous[:4]:
            by_year[year] = candidate
    return {
        year: {
            "value": clean_number(selected[4].get("val")),
            "unit": selected[4].get("unit"),
            "namespace": selected[5],
            "tag": selected[6],
            "end": selected[4].get("end"),
            "filed": selected[4].get("filed"),
        }
        for year, selected in by_year.items()
    }


def find_extra(companyfacts: dict, logical_name: str, tags: list[str]):
    found = []
    for ns in ("us-gaap", "ifrs-full"):
        facts = (companyfacts.get("facts") or {}).get(ns) or {}
        for priority, tag in enumerate(tags):
            fact = facts.get(tag)
            rows = annual_records(fact) if fact else {}
            for year, row in rows.items():
                found.append((year, 2 if ns == "us-gaap" else 1, -priority, row, ns, tag))
    by_year = {}
    for year, ns_rank, alias_rank, row, ns, tag in found:
        candidate = (ns_rank, alias_rank, row.get("end", ""), row.get("filed", ""), row, ns, tag)
        previous = by_year.get(year)
        if previous is None or candidate[:4] > previous[:4]:
            by_year[year] = candidate
    return {
        year: {
            "value": clean_number(selected[4].get("val")),
            "unit": selected[4].get("unit"),
            "namespace": selected[5],
            "tag": selected[6],
        }
        for year, selected in by_year.items()
    }


def print_metric(metric: str, data: dict[int, dict], years: list[int]):
    values = []
    for year in years:
        item = data.get(year)
        if item is None:
            values.append(f"{year}: —")
        else:
            value = item["value"]
            value_text = "None" if value is None else f"{value:,.2f}"
            values.append(f"{year}: {value_text} [{item['unit']}; {item['namespace']}:{item['tag']}]")
    print(f"  {metric:<20} " + " | ".join(values))


def diagnose_ticker(session: requests.Session, ticker: str, row: dict):
    cik = str(row["cik"]).zfill(10)
    facts = fetch_json(session, SEC_FACTS_URL.format(cik=cik))
    submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik))

    all_years = set()
    for metric in DIAGNOSTIC_METRICS:
        all_years.update(find_metric(facts, metric).keys())
    years = sorted(all_years)[-10:]

    print("\n" + "=" * 120)
    print(f"{ticker} | {row.get('company_name')} | CIK {cik}")
    print(f"sector={row.get('sector_common')} | type={row.get('company_type')} | profile={row.get('scoring_profile')}")
    print(f"SEC sic={submissions.get('sic')} | sicDescription={submissions.get('sicDescription')}")
    print(f"annual years available in diagnostic: {years}")

    for metric in DIAGNOSTIC_METRICS:
        print_metric(metric, find_metric(facts, metric), years)

    for metric, tags in EXTRA_TAGS.items():
        print_metric(metric, find_extra(facts, metric, tags), years)

    # Compact derived checks. These are diagnostic only; no scoring is performed.
    debt = find_metric(facts, "long_term_debt")
    current_debt = find_metric(facts, "current_debt")
    assets = find_metric(facts, "assets")
    equity = find_metric(facts, "equity")
    cash = find_metric(facts, "cash")
    ocf = find_metric(facts, "operating_cash_flow")
    interest = find_metric(facts, "interest_expense")

    print("  Derived diagnostic ratios:")
    for year in years:
        def val(mapping):
            item = mapping.get(year)
            return item["value"] if item else None

        lt = val(debt)
        cd = val(current_debt)
        a = val(assets)
        e = val(equity)
        c = val(cash)
        cf = val(ocf)
        ie = val(interest)
        total_debt = None if lt is None and cd is None else (lt or 0) + (cd or 0)
        debt_cap = None if total_debt is None or e is None or (total_debt + e) == 0 else total_debt / (total_debt + e) * 100
        net_debt = None if total_debt is None else total_debt - (c or 0)
        ocf_debt = None if cf is None or total_debt in (None, 0) else cf / total_debt * 100
        interest_cov = None if ie in (None, 0) or cf is None else None
        print(
            f"    {year}: debt={total_debt!s:<16} debt/capital={debt_cap!s:<12} "
            f"net_debt={net_debt!s:<16} OCF/debt%={ocf_debt!s:<12} "
            f"cash={c!s:<16} assets={a!s:<16} equity={e!s:<16} interest={ie!s}"
        )


def main():
    parser = argparse.ArgumentParser(description="Dry-run SEC utility metric diagnostic")
    parser.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    args = parser.parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    rows = get_ciks(tickers)
    missing = [ticker for ticker in tickers if ticker not in rows]
    if missing:
        print("Not found in US_Companies:", ", ".join(missing))

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"})

    for ticker in tickers:
        row = rows.get(ticker)
        if not row:
            continue
        try:
            diagnose_ticker(session, ticker, row)
        except Exception as exc:
            print(f"\n{ticker}: ERROR {type(exc).__name__}: {exc}")
        time.sleep(0.2)

    print("\n" + "=" * 120)
    print("DONE — read-only diagnostic. No Supabase writes were performed and no raw SEC JSON was stored.")


if __name__ == "__main__":
    main()
