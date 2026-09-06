"""
US SEC EDGAR collector

Step 1:
- Fetch the official SEC ticker -> CIK/company-name master list.
- Store the master list in Supabase.

This file intentionally does NOT collect financial statements yet.
That will be Step 2/3 after the company universe is verified.
"""

import os
import time
from datetime import datetime, timezone

import requests
from supabase import create_client


SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "Fundamental-app/1.0 qkrrjsdnd123789@gmail.com",
)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def get_supabase_client():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_KEY is not set.")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_sec_company_tickers(timeout=30):
    """Fetch the official SEC ticker/CIK/company-name master list."""
    headers = {
        "User-Agent": SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov",
    }

    response = requests.get(SEC_TICKERS_URL, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = response.json()

    companies = []
    seen_ciks = set()

    for item in payload.values():
        ticker = str(item.get("ticker", "")).strip().upper()
        cik_int = item.get("cik_str")
        title = str(item.get("title", "")).strip()

        if not ticker or cik_int is None or not title:
            continue

        cik = f"{int(cik_int):010d}"

        # US_Companies has a UNIQUE CIK, so keep one canonical ticker per issuer.
        if cik in seen_ciks:
            continue
        seen_ciks.add(cik)

        companies.append(
            {
                "ticker": ticker,
                "cik": cik,
                "company_name": title,
                "entity_type": None,
                "exchange": None,
                "is_active": True,
                "source": "SEC_EDGAR",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    return companies


def _load_existing_companies(supabase, page_size=1000):
    """Load existing ticker/CIK pairs with pagination."""
    rows = []
    offset = 0

    while True:
        response = (
            supabase.table("US_Companies")
            .select("ticker,cik")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = response.data or []
        rows.extend(page)

        if len(page) < page_size:
            break
        offset += page_size

    return rows


def save_us_companies(companies, batch_size=500):
    """Save SEC company master records efficiently and safely.

    Existing rows are reconciled in memory first. Writes are performed in
    batches. If an incoming ticker conflicts with an existing different CIK,
    that record is skipped rather than risking a UNIQUE-key failure.
    """
    supabase = get_supabase_client()

    print(f"[SEC] Preparing {len(companies):,} SEC records...")

    existing_rows = _load_existing_companies(supabase)
    by_cik = {
        str(row["cik"]): str(row["ticker"]).upper()
        for row in existing_rows
        if row.get("cik") and row.get("ticker")
    }
    by_ticker = {
        str(row["ticker"]).upper(): str(row["cik"])
        for row in existing_rows
        if row.get("ticker") and row.get("cik")
    }

    to_insert = []
    to_update = []
    skipped = 0

    for company in companies:
        ticker = company["ticker"].upper()
        cik = company["cik"]

        existing_ticker = by_cik.get(cik)
        existing_cik = by_ticker.get(ticker)

        if existing_ticker:
            # Preserve the existing ticker for this issuer. This is important
            # because changing ticker can collide with another unique ticker.
            update_payload = {k: v for k, v in company.items() if k != "ticker"}
            to_update.append((existing_ticker, update_payload))
            continue

        if existing_cik:
            skipped += 1
            continue

        to_insert.append(company)
        by_cik[cik] = ticker
        by_ticker[ticker] = cik

    print(
        f"[SEC] Plan: insert {len(to_insert):,}, "
        f"update {len(to_update):,}, skip {skipped:,}."
    )

    total_operations = len(to_insert) + len(to_update)
    completed = 0

    # New records: true bulk inserts. Chunk size is configurable and remains
    # small enough for Supabase/PostgREST request limits.
    for start in range(0, len(to_insert), batch_size):
        batch = to_insert[start:start + batch_size]
        try:
            supabase.table("US_Companies").insert(batch).execute()
        except Exception as exc:
            # If a batch has an unexpected conflict, fall back to individual
            # inserts so one problematic row does not discard the whole batch.
            print(f"[SEC][WARN] Batch insert failed at {start:,}: {exc}")
            for company in batch:
                try:
                    supabase.table("US_Companies").insert(company).execute()
                except Exception as row_exc:
                    skipped += 1
                    print(
                        f"[SEC][SKIP] {company['ticker']} / {company['cik']}: "
                        f"{row_exc}"
                    )
        completed += len(batch)
        print(
            f"[SEC] Progress: {completed:,} / {total_operations:,} "
            f"({completed / max(total_operations, 1) * 100:.1f}%)"
        )

    # Existing records: updates are grouped into batches by sending one
    # request per row because each row needs a different WHERE ticker clause.
    # This is only for already-known companies and avoids unsafe bulk upserts.
    for index, (ticker, payload) in enumerate(to_update, start=1):
        try:
            supabase.table("US_Companies").update(payload).eq("ticker", ticker).execute()
        except Exception as exc:
            skipped += 1
            print(f"[SEC][SKIP] update {ticker}: {exc}")

        if index == 1 or index % batch_size == 0 or index == len(to_update):
            completed = len(to_insert) + index
            print(
                f"[SEC] Progress: {completed:,} / {total_operations:,} "
                f"({completed / max(total_operations, 1) * 100:.1f}%)"
            )

        # Small pause to stay polite to the API without making the job crawl.
        time.sleep(0.005)

    print(
        f"[SEC] Finished. Inserted {len(to_insert):,}, "
        f"updated {len(to_update):,}, skipped {skipped:,}."
    )

    return len(companies)


def collect_us_company_master():
    """Main Step-1 collector."""
    print("🇺🇸 SEC US Company Master collection started")
    companies = fetch_sec_company_tickers()
    print(f"[SEC] Downloaded {len(companies):,} unique CIK records.")
    save_us_companies(companies)
    print(f"[SEC] Processed {len(companies):,} SEC company records.")
    return len(companies)


if __name__ == "__main__":
    collect_us_company_master()
