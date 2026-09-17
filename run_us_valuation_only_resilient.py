"""Run the US valuation-only collector with SEC successor/predecessor support.

This is intentionally a thin compatibility runner. It preserves the existing
valuation collector and scoring data, while fixing two SEC archive edge cases:

1. A successor issuer can inherit a ticker while recent submissions still
   contain predecessor-CIK accessions (e.g. XOM after its July 2026
   redomiciliation).
2. SEC archive paths are keyed by the CIK embedded in the accession number,
   not necessarily the current issuer CIK passed by the collector.

The underlying collector is monkey-patched at runtime so this script can be
validated before folding the same logic into the main collector module.
"""

from __future__ import annotations

import argparse
import re
import time

import requests

import collector_us_valuation_only as valuation_collector
from collector_us_fundamental import (
    SEC_SUBMISSIONS_URL,
    SEC_USER_AGENT,
    fetch_json,
)
from collector_us_valuation import filing_text as legacy_filing_text


ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}



def accession_cik(accession):
    """Extract the filer CIK encoded in a SEC accession number."""
    text = str(accession or "").strip()
    match = re.match(r"^(\d{10})-\d{2}-\d{6}$", text)
    return match.group(1) if match else None



def resilient_filing_text(session, cik, filing, filename=None):
    """Fetch SEC archive content using the accession's filer CIK."""
    if not filing:
        return None

    accession = str(filing.get("accession") or "")
    archive_cik = accession_cik(accession) or str(cik).zfill(10)
    archive_cik = str(int(archive_cik))

    # Preserve the existing retry behavior, but build the archive URL from the
    # actual accession owner. This is essential for successor-issuer filings.
    accession_path = accession.replace("-", "")
    name = filename or filing.get("document")
    if not accession_path or not name:
        return None

    url = f"https://www.sec.gov/Archives/edgar/data/{archive_cik}/{accession_path}/{name}"
    headers = {
        "User-Agent": SEC_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
        "Connection": "close",
    }
    transient = {403, 429, 500, 502, 503, 504}

    for attempt in range(4):
        try:
            response = session.get(url, headers=headers, timeout=45)
            if response.status_code == 200:
                text = response.text
                return text or None
            if response.status_code in transient:
                time.sleep(1.0 * (attempt + 1))
                continue
            response.raise_for_status()
        except Exception:
            if attempt >= 3:
                return None
            time.sleep(1.0 * (attempt + 1))
    return None



def annual_candidates(submissions):
    """Return annual filing candidates from one SEC submissions payload."""
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    reports = recent.get("reportDate") or []
    filed = recent.get("filingDate") or []
    out = []
    for idx, form in enumerate(forms):
        if form not in ANNUAL_FORMS:
            continue
        accession = accessions[idx] if idx < len(accessions) else None
        document = documents[idx] if idx < len(documents) else None
        if not accession or not document:
            continue
        out.append(
            {
                "form": form,
                "accession": accession,
                "document": document,
                "report_date": reports[idx] if idx < len(reports) else None,
                "filing_date": filed[idx] if idx < len(filed) else "",
            }
        )
    return out



def choose_annual(candidates, fiscal_end=None):
    """Choose the annual filing whose report date is latest at/before fiscal_end."""
    if not candidates:
        return None

    if fiscal_end:
        eligible = [
            row
            for row in candidates
            if row.get("report_date") and row["report_date"] <= fiscal_end
        ]
        if eligible:
            candidates = eligible

    candidates.sort(
        key=lambda row: (
            row.get("report_date") or "",
            row.get("filing_date") or "",
        ),
        reverse=True,
    )
    return candidates[0]



def find_latest_annual_filing_with_history(session, current_cik, submissions, fiscal_end=None):
    """Find an annual filing across current and predecessor CIK submissions."""
    direct = choose_annual(annual_candidates(submissions), fiscal_end=fiscal_end)
    if direct:
        return direct

    current_cik10 = str(current_cik).zfill(10)
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    accessions = recent.get("accessionNumber") or []

    predecessor_ciks = []
    seen = set()
    for accession in accessions:
        candidate_cik = accession_cik(accession)
        if not candidate_cik or candidate_cik == current_cik10 or candidate_cik in seen:
            continue
        seen.add(candidate_cik)
        predecessor_ciks.append(candidate_cik)

    historical = []
    for predecessor_cik in predecessor_ciks:
        try:
            payload = fetch_json(
                session,
                SEC_SUBMISSIONS_URL.format(cik=predecessor_cik),
            )
        except Exception:
            continue
        historical.extend(annual_candidates(payload))

    selected = choose_annual(historical, fiscal_end=fiscal_end)
    if selected:
        selected = {**selected, "source_cik": accession_cik(selected["accession"]) or current_cik10}
    return selected



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--tickers")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.20)
    args = parser.parse_args()

    # Keep a reference to the old implementation for non-successor behavior.
    _ = legacy_filing_text

    original_find_annual = valuation_collector.find_latest_annual_filing

    def patched_find_annual(submissions, fiscal_end=None):
        # The compatibility collector's caller doesn't pass a session/current
        # CIK, so the actual history-aware resolution is performed below in
        # collect_valuation_one via this closure's attached state.
        return original_find_annual(submissions)

    # Patch archive URL handling globally for this collector run.
    valuation_collector.filing_text = resilient_filing_text

    # Replace collect_valuation_one with a thin wrapper that has the current CIK
    # and fiscal-end context needed to resolve predecessor submissions.
    original_collect = valuation_collector.collect_valuation_one

    def patched_collect(session, row):
        ticker = row["ticker"]
        snapshot = dict(row.get("snapshot") or {})
        fiscal_end = snapshot.get("fiscal_end")
        if not fiscal_end:
            return original_collect(session, row)

        # Temporarily install a history-aware annual resolver whose closure has
        # access to the current session/current CIK/fiscal end.
        def _history_aware_annual(submissions):
            return find_latest_annual_filing_with_history(
                session,
                row["cik"],
                submissions,
                fiscal_end=fiscal_end,
            )

        valuation_collector.find_latest_annual_filing = _history_aware_annual
        try:
            return original_collect(session, row)
        finally:
            valuation_collector.find_latest_annual_filing = patched_find_annual

    valuation_collector.collect_valuation_one = patched_collect
    valuation_collector.main_from_args = None

    # Re-create the collector CLI arguments because the original main parses
    # sys.argv itself. Supplying the requested values here keeps this wrapper
    # transparent while preserving the original collector implementation.
    import sys

    argv = [sys.argv[0]]
    if args.ticker:
        argv += ["--ticker", args.ticker]
    elif args.tickers:
        argv += ["--tickers", args.tickers]
    argv += ["--limit", str(args.limit)]
    if args.refresh:
        argv.append("--refresh")
    argv += ["--sleep", str(args.sleep)]

    saved_argv = sys.argv
    sys.argv = argv
    try:
        valuation_collector.main()
    finally:
        sys.argv = saved_argv


if __name__ == "__main__":
    main()
