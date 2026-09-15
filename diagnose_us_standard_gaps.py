"""Diagnose unresolved US Standard companies via SEC submissions.

Focuses on companies with missing fiscal snapshots. It does not collect or
write financial data. It only classifies the latest relevant SEC filing so we
can decide between normal Company Facts collection and foreign HTML fallback.

Usage:
  python diagnose_us_standard_gaps.py
"""

import os
import time
from datetime import datetime

import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY") or ""
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
RELEVANT_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "6-K"}
FOREIGN_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}


def fetch_json(session, url):
    for attempt in range(4):
        r = session.get(url, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue
        r.raise_for_status()
    raise RuntimeError(f"SEC request failed: {url}")


def load_missing(sb):
    # Current Standard universe is small enough for the configured limit.
    return (
        sb.table("US_Fundamental")
        .select("ticker,company_name,snapshot_filed,data_unavailable,data_reliability")
        .is_("snapshot_filed", "null")
        .limit(10000)
        .execute()
        .data
    )


def latest_filings(submissions):
    forms = submissions.get("filings", {}).get("recent", {}).get("form", [])
    filing_dates = submissions.get("filings", {}).get("recent", {}).get("filingDate", [])
    accessions = submissions.get("filings", {}).get("recent", {}).get("accessionNumber", [])
    primary_docs = submissions.get("filings", {}).get("recent", {}).get("primaryDocument", [])
    out = []
    for form, filed, accession, primary in zip(forms, filing_dates, accessions, primary_docs):
        if form not in RELEVANT_FORMS:
            continue
        out.append({"form": form, "filed": filed, "accession": accession, "primary": primary})
    out.sort(key=lambda x: (x.get("filed") or "", x.get("accession") or ""), reverse=True)
    return out


def main():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows = load_missing(sb)
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    print(f"[INPUT] snapshot-missing rows={len(rows)}")
    print("ticker|company|latest_form|filed|kind|accession|primary")

    counts = {"domestic": 0, "foreign": 0, "no_relevant_filing": 0, "error": 0}
    for i, row in enumerate(rows, 1):
        ticker = row["ticker"]
        try:
            cik = str(row.get("cik") or "").zfill(10)
            if not cik.strip("0"):
                counts["error"] += 1
                print(f"{ticker}|{row.get('company_name','')}|ERROR|missing CIK")
                continue
            submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik))
            filings = latest_filings(submissions)
            if not filings:
                counts["no_relevant_filing"] += 1
                print(f"{ticker}|{row.get('company_name','')}|NONE|||no relevant filing")
                continue
            f = filings[0]
            kind = "foreign" if f["form"] in FOREIGN_FORMS else "domestic"
            counts[kind] += 1
            print("|".join([ticker, row.get("company_name") or "", f["form"], f["filed"], kind, f["accession"], f["primary"]]))
        except Exception as exc:
            counts["error"] += 1
            print(f"{ticker}|{row.get('company_name','')}|ERROR|{exc}")
        time.sleep(0.12)

    print(f"[SUMMARY] domestic={counts['domestic']} foreign={counts['foreign']} no_relevant_filing={counts['no_relevant_filing']} error={counts['error']}")


if __name__ == "__main__":
    main()
