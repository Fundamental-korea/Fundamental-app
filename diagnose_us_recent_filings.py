"""Find recent SEC filings among exact Standard-type snapshot gaps.

Scope:
  US_Companies.is_fundamental_eligible = true
  sector_common in the 7 Standard sectors
  company_type = 'standard'
  US_Fundamental.snapshot_filed IS NULL

The script makes SEC submissions requests only. No database writes and no
Company Facts requests.

By default, "recent" means filings submitted on or after 2023-09-16
(three years before 2026-09-16). Use --since YYYY-MM-DD to change it.
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import date

import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY") or ""
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

STANDARD_SECTORS = {
    "technology",
    "healthcare",
    "consumer",
    "industrials",
    "energy",
    "materials",
    "communication",
}

RELEVANT_FORMS = {
    "10-K", "10-K/A", "10-Q", "10-Q/A",
    "20-F", "20-F/A", "40-F", "40-F/A", "6-K",
}
DOMESTIC_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A"}
FOREIGN_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}
PAGE_SIZE = 1000
DEFAULT_SINCE = "2023-09-16"


def fetch_json(session: requests.Session, url: str):
    for attempt in range(4):
        response = session.get(url, timeout=30)
        if response.status_code == 200:
            return response.json()
        if response.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue
        response.raise_for_status()
    raise RuntimeError(f"SEC request failed: {url}")


def paginated_query(query_builder):
    rows = []
    offset = 0
    while True:
        page = query_builder(offset, offset + PAGE_SIZE - 1).execute().data or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def load_exact_standard_missing(sb):
    def build_missing(offset, end):
        return (
            sb.table("US_Fundamental")
            .select("ticker,company_name,cik,snapshot_filed")
            .is_("snapshot_filed", "null")
            .order("ticker")
            .range(offset, end)
        )

    missing = paginated_query(build_missing)

    def build_companies(offset, end):
        return (
            sb.table("US_Companies")
            .select("ticker,company_type,sector_common,is_fundamental_eligible")
            .eq("is_fundamental_eligible", True)
            .eq("company_type", "standard")
            .in_("sector_common", list(STANDARD_SECTORS))
            .order("ticker")
            .range(offset, end)
        )

    standard = paginated_query(build_companies)
    standard_tickers = {r["ticker"] for r in standard if r.get("ticker")}
    rows = [r for r in missing if r.get("ticker") in standard_tickers]
    rows.sort(key=lambda r: r.get("ticker") or "")
    return rows, len(standard_tickers)


def recent_relevant_filings(submissions, since):
    recent = (submissions.get("filings") or {}).get("recent") or {}
    keys = [
        "form", "filingDate", "reportDate", "accessionNumber",
        "primaryDocument", "primaryDocDescription",
    ]
    arrays = {k: recent.get(k, []) or [] for k in keys}
    count = len(arrays["form"])
    rows = []
    for i in range(count):
        row = {k: arrays[k][i] if i < len(arrays[k]) else None for k in keys}
        if row["form"] not in RELEVANT_FORMS:
            continue
        filed = row.get("filingDate") or ""
        if filed < since:
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r.get("filingDate") or "", r.get("accessionNumber") or ""), reverse=True)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=DEFAULT_SINCE, help="Minimum filing date, YYYY-MM-DD")
    args = parser.parse_args()

    try:
        date.fromisoformat(args.since)
    except ValueError as exc:
        raise SystemExit(f"Invalid --since date: {args.since}") from exc

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows, standard_total = load_exact_standard_missing(sb)

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    print(f"[INPUT] standard_type_universe={standard_total}")
    print(f"[INPUT] exact_standard_type_snapshot_missing={len(rows)}")
    print(f"[FILTER] recent_filing_since={args.since}")
    print("ticker|company|cik|sector|recent_count|domestic_recent|foreign_recent|latest_form|latest_filed|latest_report|latest_accession|latest_primary")

    counts = {
        "recent": 0,
        "no_recent": 0,
        "error": 0,
    }

    for row in rows:
        ticker = row.get("ticker") or ""
        company = row.get("company_name") or ""
        cik = str(row.get("cik") or "").strip()
        try:
            if not cik:
                counts["error"] += 1
                print(f"{ticker}|{company}||UNKNOWN|0|0|0|ERROR|missing CIK")
                continue

            company_row = (
                sb.table("US_Companies")
                .select("sector_common")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
                .data
                or []
            )
            sector = company_row[0].get("sector_common", "") if company_row else ""

            submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik.zfill(10)))
            filings = recent_relevant_filings(submissions, args.since)
            domestic = sum(r.get("form") in DOMESTIC_FORMS for r in filings)
            foreign = sum(r.get("form") in FOREIGN_FORMS for r in filings)

            if filings:
                counts["recent"] += 1
                latest = filings[0]
            else:
                counts["no_recent"] += 1
                latest = {}

            print("|".join([
                ticker,
                company,
                cik,
                sector,
                str(len(filings)),
                str(domestic),
                str(foreign),
                latest.get("form", "NONE") or "NONE",
                latest.get("filingDate", "") or "",
                latest.get("reportDate", "") or "",
                latest.get("accessionNumber", "") or "",
                latest.get("primaryDocument", "") or "",
            ]))
        except Exception as exc:
            counts["error"] += 1
            print(f"{ticker}|{company}|{cik}|||ERROR|{type(exc).__name__}:{exc}")
        time.sleep(0.12)

    print(
        f"[SUMMARY] recent={counts['recent']} "
        f"no_recent={counts['no_recent']} "
        f"error={counts['error']}"
    )


if __name__ == "__main__":
    main()
