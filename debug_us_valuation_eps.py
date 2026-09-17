"""Read-only SEC EPS pipeline diagnostic.

This script does not modify Supabase. It traces one ticker from SEC Company
Facts/submissions through the annual filing's Inline XBRL facts and reports
exactly where annual EPS is present or lost.

Usage:
  python debug_us_valuation_eps.py --ticker XOM
"""

from __future__ import annotations

import argparse
import re
import os
from datetime import date

import requests
from supabase import create_client

from collector_us_fundamental import SEC_USER_AGENT, SUPABASE_KEY, SUPABASE_URL, load_company
from collector_us_valuation import filing_text
from sec_xbrl_inline import parse_inline_xbrl
from us_valuation import _reported_eps

ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
EPS_CONCEPTS = {"earningspersharediluted", "earningspersharebasic"}


def get_company_row(sb, ticker):
    rows = (
        sb.table("US_Companies")
        .select("ticker,cik,company_name,sector_common")
        .eq("ticker", ticker)
        .limit(1)
        .execute()
        .data
        or []
    )
    return rows[0] if rows else None


def latest_annual_filing(submissions):
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    reports = recent.get("reportDate") or []
    filed = recent.get("filingDate") or []
    fy = recent.get("fy") or []
    candidates = []
    for i, form in enumerate(forms):
        if form not in ANNUAL_FORMS:
            continue
        candidates.append({
            "form": form,
            "accession": accessions[i],
            "document": documents[i],
            "report_date": reports[i] if i < len(reports) else None,
            "filing_date": filed[i] if i < len(filed) else None,
            "fy": fy[i] if i < len(fy) else None,
        })
    candidates.sort(key=lambda x: x.get("filing_date") or "", reverse=True)
    return candidates[0] if candidates else None


def sec_get(session, url):
    response = session.get(url, timeout=60)
    print(f"GET {response.status_code} {len(response.content):,} bytes {response.headers.get('content-type')} {url}")
    response.raise_for_status()
    return response.text


def companyfacts_eps_diagnostics(facts, fiscal_end):
    root = facts.get("facts") or {}
    print("\nCOMPANY FACTS EPS")
    for namespace in sorted(root):
        ns_facts = root.get(namespace) or {}
        for tag, fact in ns_facts.items():
            tag_lower = tag.lower()
            if tag_lower not in EPS_CONCEPTS:
                continue
            label = fact.get("label")
            print(f"  namespace={namespace} tag={tag} label={label}")
            found = 0
            for unit, rows in (fact.get("units") or {}).items():
                for row in rows or []:
                    if row.get("form") not in ANNUAL_FORMS:
                        continue
                    if row.get("end") != fiscal_end:
                        continue
                    print(
                        "    ",
                        {
                            "unit": unit,
                            "val": row.get("val"),
                            "start": row.get("start"),
                            "end": row.get("end"),
                            "fy": row.get("fy"),
                            "fp": row.get("fp"),
                            "form": row.get("form"),
                            "filed": row.get("filed"),
                        },
                    )
                    found += 1
            if found == 0:
                print("    NO ANNUAL FACT AT TARGET FISCAL END")


def inline_eps_diagnostics(primary_text, fiscal_end, filing_date):
    print("\nINLINE XBRL EPS")
    rows = parse_inline_xbrl(primary_text, filed=filing_date, form="10-K")
    target = date.fromisoformat(fiscal_end)
    candidates = []

    for row in rows:
        concept = (row.get("concept") or "").lower()
        if concept not in EPS_CONCEPTS:
            continue
        if row.get("end") != fiscal_end or not row.get("start"):
            continue
        try:
            days = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
        except ValueError:
            continue
        if not 300 <= days <= 380:
            continue
        rank = 0 if concept == "earningspersharediluted" else 1
        dimension_rank = 0 if not row.get("dimensioned") else 1
        candidates.append((rank, dimension_rank, days, row))

    candidates.sort(key=lambda x: (x[0], x[1], -x[2]))
    print(f"  parsed_numeric_facts={len(rows):,}")
    print(f"  annual_eps_candidates={len(candidates)}")
    for rank, dimension_rank, days, row in candidates[:20]:
        print(
            "  EPS_FACT",
            {
                "concept": row.get("concept"),
                "namespace": row.get("namespace"),
                "value": row.get("value"),
                "unit": row.get("unit"),
                "start": row.get("start"),
                "end": row.get("end"),
                "days": days,
                "dimensioned": row.get("dimensioned"),
                "contextRef": row.get("contextRef"),
                "rank": rank,
                "dimension_rank": dimension_rank,
            },
        )


def visible_text_diagnostics(primary_text):
    print("\nVISIBLE FILING TEXT")
    clean = re.sub(r"<[^>]+>", " ", primary_text or "")
    clean = re.sub(r"\s+", " ", clean)
    patterns = [
        r"Earnings[^|]{0,120}per common share[^|]{0,120}6\.70",
        r"Earnings[^|]{0,120}assuming dilution[^|]{0,120}6\.70",
    ]
    for pattern in patterns:
        match = re.search(pattern, clean, re.I)
        print(f"  pattern_found={bool(match)}")
        if match:
            print("  ", match.group(0)[:500])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="XOM")
    args = parser.parse_args()
    ticker = args.ticker.upper().strip()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    row = get_company_row(sb, ticker)
    if not row:
        raise RuntimeError(f"{ticker} not found in US_Companies")

    cik = str(row["cik"]).zfill(10)
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    facts, submissions = load_company(session, ticker, row["cik"])
    annual = latest_annual_filing(submissions)
    if not annual:
        raise RuntimeError("No annual filing found")

    print("=" * 100)
    print(f"{ticker} | CIK={cik} | company={row.get('company_name')}")
    print(f"latest annual filing={annual}")

    current_eps_row, current_eps_basis = _reported_eps(facts)
    print("\nCURRENT us_valuation._reported_eps RESULT")
    print(f"  basis={current_eps_basis}")
    print(f"  row={current_eps_row}")

    target_fiscal_end = annual.get("report_date")
    if not target_fiscal_end:
        raise RuntimeError("Annual filing has no report_date")
    print(f"\ntarget fiscal end={target_fiscal_end}")

    companyfacts_eps_diagnostics(facts, target_fiscal_end)

    accession = annual["accession"].replace("-", "")
    doc = annual["document"]
    primary_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{doc}"

    print("\nSEC PRIMARY FILING")
    primary_text = sec_get(session, primary_url)
    print(f"  html_length={len(primary_text):,}")

    inline_eps_diagnostics(primary_text, target_fiscal_end, annual.get("filing_date"))
    visible_text_diagnostics(primary_text)

    print("\nEXISTING filing_text() RESULT")
    existing = filing_text(session, int(cik), annual)
    print(f"  existing_filing_text_is_none={existing is None}")
    print(f"  existing_filing_text_length={len(existing):,}" if existing else "  existing_filing_text_length=0")

    print("\nDONE")


if __name__ == "__main__":
    main()
