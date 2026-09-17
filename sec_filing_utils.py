"""SEC filing resolution helpers for successor/predecessor issuers."""

from __future__ import annotations

import re
import time

from collector_us_fundamental import SEC_SUBMISSIONS_URL, SEC_USER_AGENT, fetch_json

ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}


def accession_cik(accession):
    """Return the ten-digit CIK encoded in a standard SEC accession number."""
    text = str(accession or "").strip()
    match = re.match(r"^(\d{10})-\d{2}-\d{6}$", text)
    return match.group(1) if match else None


def annual_candidates(submissions):
    """Return annual filing candidates from one submissions payload."""
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
        out.append({
            "form": form,
            "accession": accession,
            "document": document,
            "report_date": reports[idx] if idx < len(reports) else None,
            "filing_date": filed[idx] if idx < len(filed) else "",
        })
    return out


def choose_annual(candidates, fiscal_end=None):
    """Choose the annual filing latest at or before the target fiscal end."""
    if not candidates:
        return None
    if fiscal_end:
        candidates = [
            row for row in candidates
            if row.get("report_date") and row["report_date"] <= fiscal_end
        ]
        if not candidates:
            return None
    candidates = list(candidates)
    candidates.sort(
        key=lambda row: (row.get("report_date") or "", row.get("filing_date") or ""),
        reverse=True,
    )
    return candidates[0]


def _submission_ciks_from_accessions(submissions, current_cik):
    """Find other CIKs represented by accessions in a submissions payload."""
    current = str(current_cik).zfill(10)
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    accessions = recent.get("accessionNumber") or []
    out = []
    seen = {current}
    for accession in accessions:
        cik = accession_cik(accession)
        if not cik or cik in seen:
            continue
        seen.add(cik)
        out.append(cik)
    return out


def find_filing_with_history(session, current_cik, submissions, fiscal_end, base_find_filing):
    """Resolve a fiscal-period filing across successor/predecessor submissions.

    The current issuer CIK is tried first. When the requested period is absent,
    CIKs embedded in recent accessions are queried as historical issuers.
    Every returned filing carries ``source_cik`` so archive retrieval can use
    the issuer whose submissions supplied that filing.
    """
    direct = base_find_filing(submissions, fiscal_end)
    if direct:
        return {**direct, "source_cik": str(current_cik).zfill(10)}

    for historical_cik in _submission_ciks_from_accessions(submissions, current_cik):
        try:
            historical = fetch_json(
                session,
                SEC_SUBMISSIONS_URL.format(cik=historical_cik),
            )
        except Exception:
            continue
        candidate = base_find_filing(historical, fiscal_end)
        if candidate:
            return {**candidate, "source_cik": historical_cik}

    return None


def find_annual_filing_with_history(session, current_cik, submissions, fiscal_end=None):
    """Resolve an annual filing across current and predecessor issuer CIKs."""
    direct = choose_annual(annual_candidates(submissions), fiscal_end=fiscal_end)
    if direct:
        return {**direct, "source_cik": str(current_cik).zfill(10)}

    for historical_cik in _submission_ciks_from_accessions(submissions, current_cik):
        try:
            historical = fetch_json(
                session,
                SEC_SUBMISSIONS_URL.format(cik=historical_cik),
            )
        except Exception:
            continue
        candidate = choose_annual(annual_candidates(historical), fiscal_end=fiscal_end)
        if candidate:
            return {**candidate, "source_cik": historical_cik}

    return None


def filing_text_resilient(session, cik, filing, filename=None):
    """Fetch filing text using several valid SEC archive CIK paths.

    Prefer the CIK whose submissions supplied the filing, then the current CIK,
    then the CIK encoded in the accession. This covers successor issuers where
    current submissions may reference legacy accessions while SEC archive
    directories can remain registered under the successor CIK.
    """
    if not filing:
        return None

    accession = str(filing.get("accession") or "")
    accession_path = accession.replace("-", "")
    name = filename or filing.get("document")
    if not accession_path or not name:
        return None

    preferred = str(filing.get("source_cik") or cik).zfill(10)
    accession_owner = accession_cik(accession)
    candidates = []
    for value in (preferred, str(cik).zfill(10), accession_owner):
        if value and value not in candidates:
            candidates.append(value)

    headers = {
        "User-Agent": SEC_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.8",
        "Connection": "close",
    }
    transient = {403, 429, 500, 502, 503, 504}

    for archive_cik in candidates:
        archive_cik = str(int(archive_cik))
        url = f"https://www.sec.gov/Archives/edgar/data/{archive_cik}/{accession_path}/{name}"
        for attempt in range(4):
            try:
                response = session.get(url, headers=headers, timeout=45)
                if response.status_code == 200 and response.text:
                    return response.text
                if response.status_code in transient:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                break
            except Exception:
                if attempt >= 3:
                    break
                time.sleep(1.0 * (attempt + 1))

    return None
