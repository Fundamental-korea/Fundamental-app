"""Incremental SEC filing updater for the US Standard universe.

Checks SEC submissions for the 7 Standard sectors and only re-collects
companies that already have a stored snapshot and whose latest relevant
filing is newer than that snapshot.

Initial collection is intentionally kept separate. This prevents a manual
or scheduled run from accidentally launching a 4,580-company bootstrap.
"""
from __future__ import annotations

import subprocess
import sys
import time

import requests
from supabase import create_client

from collector_us_fundamental import SEC_SUBMISSIONS_URL, SEC_USER_AGENT, SUPABASE_URL, SUPABASE_KEY

STANDARD_SECTORS = (
    "technology", "healthcare", "consumer", "industrials",
    "energy", "materials", "communication",
)
RELEVANT_FORMS = {
    "10-Q", "10-Q/A", "10-K", "10-K/A",
    "20-F", "20-F/A", "40-F", "40-F/A",
}
PAGE_SIZE = 1000
SEC_DELAY_SECONDS = 0.12


def _sec_get(session: requests.Session, url: str):
    for attempt in range(5):
        response = session.get(url, timeout=30)
        if response.status_code == 200:
            return response.json()
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                delay = max(float(retry_after), 1.0) if retry_after else 2.0 * (attempt + 1)
            except ValueError:
                delay = 2.0 * (attempt + 1)
            print(f"[SEC] 429 rate limit; sleeping {delay:.1f}s")
            time.sleep(delay)
            continue
        if response.status_code >= 500:
            time.sleep(2.0 * (attempt + 1))
            continue
        response.raise_for_status()
    raise RuntimeError(f"SEC request failed after retries: {url}")


def _load_universe(sb):
    rows = []
    offset = 0
    columns = "ticker,cik,sector_common"
    while True:
        batch = (
            sb.table("US_Companies")
            .select(columns)
            .eq("is_fundamental_eligible", True)
            .in_("sector_common", STANDARD_SECTORS)
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        )
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def _load_existing(sb):
    rows = []
    offset = 0
    while True:
        batch = (
            sb.table("US_Fundamental")
            .select("ticker,snapshot_filed,snapshot_fiscal_end")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
        )
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return {row["ticker"]: row for row in rows}


def _latest_relevant_filing(submissions):
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    filed = recent.get("filingDate", [])
    accession = recent.get("accessionNumber", [])
    report = recent.get("reportDate", [])
    candidates = []
    for i, form in enumerate(forms):
        if form not in RELEVANT_FORMS:
            continue
        candidates.append({
            "form": form,
            "filed": filed[i] if i < len(filed) else None,
            "report_date": report[i] if i < len(report) else None,
            "accession": accession[i] if i < len(accession) else None,
        })
    if not candidates:
        return None
    return max(candidates, key=lambda x: (x.get("filed") or "", x.get("accession") or ""))


def _needs_update(latest, stored):
    # No stored snapshot means the company has not completed initial collection.
    # Never bootstrap the full universe from the daily updater.
    if not stored or not stored.get("snapshot_filed"):
        return False
    if not latest:
        return False
    return (latest.get("filed") or "") > str(stored["snapshot_filed"])[:10]


def main():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")
    if not SEC_USER_AGENT or "contact@example.com" in SEC_USER_AGENT:
        print("[WARN] SEC_USER_AGENT is using the collector default. Set a real contact email in GitHub Secrets.")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    universe = _load_universe(sb)
    existing = _load_existing(sb)
    print(f"[UNIVERSE] Standard eligible companies: {len(universe)}")

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"})

    changed = []
    uninitialized = 0
    checked = 0
    errors = 0
    for row in universe:
        ticker = row["ticker"]
        stored = existing.get(ticker)
        if not stored or not stored.get("snapshot_filed"):
            uninitialized += 1
            continue

        cik = str(row["cik"]).strip().zfill(10)
        try:
            submissions = _sec_get(session, SEC_SUBMISSIONS_URL.format(cik=cik))
            latest = _latest_relevant_filing(submissions)
            if _needs_update(latest, stored):
                changed.append(ticker)
                print(f"[CHANGED] {ticker}: filed={latest.get('filed')} form={latest.get('form')} report={latest.get('report_date')}")
            checked += 1
        except Exception as exc:
            errors += 1
            print(f"[CHECK FAILED] {ticker}: {exc}")
        time.sleep(SEC_DELAY_SECONDS)

    print(f"[CHECK] initialized={checked} uninitialized={uninitialized} changed={len(changed)} errors={errors}")
    if not changed:
        if errors:
            raise RuntimeError(f"SEC filing check completed with {errors} errors and no updates")
        print("[UPDATE] No new SEC filings. Nothing to collect.")
        return

    ticker_arg = ",".join(changed)
    print(f"[UPDATE] Re-collecting {len(changed)} Standard companies...")
    result = subprocess.run(
        [sys.executable, "collector_us_standard_fallback.py", "--tickers", ticker_arg],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"collector_us_standard_fallback.py exited with {result.returncode}")

    print("[UPDATE] US Standard incremental update completed.")


if __name__ == "__main__":
    main()
