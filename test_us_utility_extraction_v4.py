"""Utility SEC extraction diagnostic v4.

Purpose
-------
Validate utility-specific SEC candidates before changing production extraction.

Focus:
- Revenue candidate selection (especially utility-specific revenue tags)
- Interest expense candidate selection
- Debt candidate selection (current + noncurrent debt)
- Capex/dividend tag discovery for the next utility scoring pass

No Supabase writes. No production scorer changes. No raw SEC JSON persistence.

Usage
-----
python test_us_utility_extraction_v4.py --tickers PEG,AWR
"""

from __future__ import annotations

import argparse
import math
import os
import time
from datetime import datetime

import requests

from collector_us_fundamental import (
    SEC_FACTS_URL,
    SEC_SUBMISSIONS_URL,
    build_fact_index,
    clean_number,
)

FLOW_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

REVENUE_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "SalesRevenueGoodsNet",
    "RegulatedOperatingRevenue",
    "RegulatedOperatingRevenueWater",
    "ElectricUtilityRevenue",
    "ElectricUtilityOperatingRevenue",
    "NaturalGasUtilityRevenue",
    "NaturalGasUtilityOperatingRevenue",
]

INTEREST_TAGS = [
    "InterestExpense",
    "InterestExpenseNonOperating",
    "InterestExpenseNonOperatingNet",
    "InterestExpenseDebt",
    "InterestExpenseNonOperatingAndOther",
    "FinanceCosts",
]

DEBT_TAGS = [
    "LongTermDebtCurrent",
    "LongTermDebtNoncurrent",
    "LongTermDebt",
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
    "LongTermDebtAndCapitalLeaseObligations",
    "DebtAndCapitalLeaseObligationsCurrent",
    "DebtAndCapitalLeaseObligationsNoncurrent",
    "DebtAndCapitalLeaseObligations",
]

CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsToAcquirePropertyPlantAndEquipmentAndOtherProductiveAssets",
]

DIVIDEND_TAGS = [
    "PaymentsOfDividends",
    "PaymentsOfDividendsCommonStock",
    "PaymentsOfDividendsMinorityInterest",
    "PaymentsOfDividendsCommonStockCash",
]


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
    ticker_upper = ticker.upper()
    for row in data.values():
        if str(row.get("ticker", "")).upper() == ticker_upper:
            return str(row["cik_str"]).zfill(10), row.get("title")
    return None, None


def annual_rows_for_tag(facts, tag):
    """Collect annual flow observations for one exact us-gaap/ifrs tag."""
    root = facts.get("facts", facts)
    rows_out = []
    for namespace in ("us-gaap", "ifrs-full"):
        fact = (root.get(namespace) or {}).get(tag)
        if not fact:
            continue
        units = fact.get("units") or {}
        for unit, rows in units.items():
            if not isinstance(rows, list):
                continue
            for r in rows:
                form, end, start = r.get("form"), r.get("end"), r.get("start")
                if form not in FLOW_FORMS or not end:
                    continue
                try:
                    end_date = datetime.fromisoformat(end).date()
                except ValueError:
                    continue
                if start:
                    try:
                        start_date = datetime.fromisoformat(start).date()
                    except ValueError:
                        continue
                    days = (end_date - start_date).days
                    if not 300 <= days <= 380:
                        continue
                value = clean_number(r.get("val"))
                if value is None:
                    continue
                rows_out.append({
                    "namespace": namespace,
                    "tag": tag,
                    "unit": unit,
                    "year": end_date.year,
                    "end": end,
                    "start": start,
                    "filed": r.get("filed") or "",
                    "form": form,
                    "frame": r.get("frame"),
                    "val": value,
                })
    return rows_out


def instant_rows_for_tag(facts, tag):
    """Collect balance-sheet observations for one exact tag."""
    root = facts.get("facts", facts)
    rows_out = []
    for namespace in ("us-gaap", "ifrs-full"):
        fact = (root.get(namespace) or {}).get(tag)
        if not fact:
            continue
        units = fact.get("units") or {}
        for unit, rows in units.items():
            if not isinstance(rows, list):
                continue
            for r in rows:
                form, end = r.get("form"), r.get("end")
                if form not in FLOW_FORMS or not end:
                    continue
                try:
                    year = datetime.fromisoformat(end).date().year
                except ValueError:
                    continue
                value = clean_number(r.get("val"))
                if value is None:
                    continue
                rows_out.append({
                    "namespace": namespace,
                    "tag": tag,
                    "unit": unit,
                    "year": year,
                    "end": end,
                    "filed": r.get("filed") or "",
                    "form": form,
                    "frame": r.get("frame"),
                    "val": value,
                })
    return rows_out


def dedupe_latest(rows):
    """One latest observation per year, preferring later filed dates."""
    out = {}
    for row in rows:
        year = row["year"]
        prev = out.get(year)
        key = (row.get("end", ""), row.get("filed", ""), 0 if row.get("form", "").endswith("/A") else 1)
        if prev is None or key > prev[0]:
            out[year] = (key, row)
    return {year: row for year, (_, row) in out.items()}


def print_candidates(facts, tags, years, title, flow=True):
    print(f"\n[{title} CANDIDATES]")
    any_found = False
    by_tag = {}
    for tag in tags:
        rows = annual_rows_for_tag(facts, tag) if flow else instant_rows_for_tag(facts, tag)
        rows = dedupe_latest(rows)
        if rows:
            any_found = True
            by_tag[tag] = rows
            print(f"  {tag}")
            for year in years:
                row = rows.get(year)
                if row:
                    print(
                        f"    {year}: {row['val']:.6g} {row['unit']} "
                        f"| {row['namespace']}:{row['tag']} "
                        f"| filed={row['filed']} | form={row['form']}"
                    )
    if not any_found:
        print("  NONE")
    return by_tag


def revenue_sanity(facts, opinc_by_year, years):
    print("\n[REVENUE SANITY / OPM TEST]")
    root = facts.get("facts", facts)
    candidates = []
    for namespace in ("us-gaap", "ifrs-full"):
        for tag in sorted((root.get(namespace) or {}).keys()):
            lower = tag.lower()
            if "revenue" in lower or "revenues" in lower:
                rows = dedupe_latest(annual_rows_for_tag(facts, tag))
                if rows:
                    candidates.append((namespace, tag, rows))
    seen = 0
    for namespace, tag, rows in candidates:
        usable = []
        for year in years:
            row = rows.get(year)
            op = opinc_by_year.get(year)
            if row and op is not None and row["val"] != 0:
                opm = op / row["val"] * 100.0
                usable.append((year, row["val"], opm))
        if not usable:
            continue
        latest = usable[-1]
        latest_year, latest_rev, latest_opm = latest
        flag = "OK" if -20 <= latest_opm <= 100 else "SUSPICIOUS"
        print(
            f"  {namespace}:{tag} | {latest_year} revenue={latest_rev:.6g} "
            f"| OPM={latest_opm:.3f}% | {flag}"
        )
        seen += 1
        if seen >= 30:
            print("  ... limited to 30 candidates")
            break


def combined_debt_preview(debt_candidates, years):
    print("\n[COMBINED DEBT PREVIEW]")
    for year in years:
        current = []
        noncurrent = []
        total_like = []
        for tag, rows in debt_candidates.items():
            row = rows.get(year)
            if not row:
                continue
            value = row["val"]
            if "current" in tag.lower():
                current.append((tag, value))
            elif "noncurrent" in tag.lower():
                noncurrent.append((tag, value))
            else:
                total_like.append((tag, value))
        print(f"  {year}:")
        print(f"    current: {current[:6] or 'NONE'}")
        print(f"    noncurrent: {noncurrent[:6] or 'NONE'}")
        print(f"    total-like: {total_like[:6] or 'NONE'}")
        if current and noncurrent:
            c = current[0][1]
            nc = noncurrent[0][1]
            print(f"    preferred combined first-match: {c + nc:.6g}")


def run_ticker(session, ticker):
    cik, title = resolve_cik(session, ticker)
    if not cik:
        raise RuntimeError("CIK not found")
    facts = fetch_json(session, SEC_FACTS_URL.format(cik=cik))
    submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik))
    index = build_fact_index(facts)

    all_years = sorted({year for rows in index.values() for year in rows})
    if not all_years:
        print(f"\n{ticker}: no annual years")
        return
    latest = max(all_years)
    years = [y for y in all_years if latest - 4 <= y <= latest]

    print("\n" + "=" * 100)
    print(f"{ticker} | {title or submissions.get('name')}")
    print(f"CIK={cik} | SIC={submissions.get('sic')} | {submissions.get('sicDescription')}")
    print(f"Diagnostic years: {years}")

    # Production baseline.
    print("\n[PRODUCTION BASELINE SELECTED FACTS]")
    for metric in ("revenue", "operating_income", "liabilities", "equity", "interest_expense", "operating_cash_flow"):
        print(f"  {metric}:")
        for year in years:
            row = (index.get(metric) or {}).get(year)
            if row:
                print(f"    {year}: {row['val']:.6g} | {row['namespace']}:{row['tag']}")
            else:
                print(f"    {year}: MISSING")

    opinc_by_year = {}
    for year in years:
        row = (index.get("operating_income") or {}).get(year)
        opinc_by_year[year] = row["val"] if row else None

    revenue_candidates = print_candidates(facts, REVENUE_TAGS, years, "REVENUE")
    revenue_sanity(facts, opinc_by_year, years)
    interest_candidates = print_candidates(facts, INTEREST_TAGS, years, "INTEREST", flow=True)
    debt_candidates = print_candidates(facts, DEBT_TAGS, years, "DEBT", flow=False)
    combined_debt_preview(debt_candidates, years)
    print_candidates(facts, CAPEX_TAGS, years, "CAPEX", flow=True)
    print_candidates(facts, DIVIDEND_TAGS, years, "DIVIDEND", flow=True)

    # Explicit production-extraction gaps.
    print("\n[DIAGNOSTIC FLAGS]")
    latest_index = max(years)
    baseline_rev = (index.get("revenue") or {}).get(latest_index)
    if baseline_rev:
        op = opinc_by_year.get(latest_index)
        opm = op / baseline_rev["val"] * 100 if op is not None and baseline_rev["val"] else None
        if opm is not None and not -20 <= opm <= 100:
            print(f"  !! Baseline revenue creates suspicious OPM={opm:.3f}%")
    if not (index.get("interest_expense") or {}).get(latest_index):
        print("  !! Production interest_expense is missing; exact InterestExpense candidate is being inspected")
    if not (index.get("liabilities") or {}).get(latest_index):
        print("  !! Production liabilities is missing; utility debt should be tested from debt-specific tags")
    if not debt_candidates:
        print("  !! No debt-specific candidates found")
    if not revenue_candidates:
        print("  !! No utility revenue candidates found")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers")
    args = parser.parse_args()
    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    if not tickers:
        parser.error("No tickers supplied")

    session = requests.Session()
    session.headers.update({
        "User-Agent": os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com"),
        "Accept-Encoding": "gzip, deflate",
    })

    print(f"Started: {datetime.now().isoformat(timespec='seconds')}")
    print("Mode: DIAGNOSTIC ONLY / NO DB WRITE / NO PRODUCTION CHANGES")
    print(f"Tickers: {', '.join(tickers)}")

    failed = 0
    for ticker in tickers:
        try:
            run_ticker(session, ticker)
        except Exception as exc:
            failed += 1
            print(f"\n{'=' * 100}\n{ticker}: ERROR: {exc}")

    print("\n" + "=" * 100)
    print(f"Completed. failed={failed}/{len(tickers)}")


if __name__ == "__main__":
    main()
