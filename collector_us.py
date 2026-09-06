"""
US SEC EDGAR collector

Step 1:
- Fetch the official SEC ticker -> CIK company master list.
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

        # The SEC ticker master may contain multiple securities/tickers for
        # one issuer CIK. US_Companies requires CIK to be unique, so keep the
        # first valid record for each CIK in the master list.
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


def save_us_companies(companies, batch_size=500):
    """Save SEC company master records without violating ticker/CIK UNIQUE keys.

    Strategy:
    1. Load existing ticker/CIK pairs once.
    2. For each SEC record, determine whether the CIK already exists.
    3. If the CIK exists under another ticker, update the existing row by CIK.
    4. Otherwise upsert the new record by ticker.

    Processing one record at a time is intentional here: this is a master
    table and correctness is more important than bulk-write speed.
    """
    supabase = get_supabase_client()

    existing_rows = []
    page_size = 1000
    offset = 0

    # Read the current master table so ticker/CIK conflicts can be reconciled
    # locally before any writes are made.
    while True:
        response = (
            supabase.table("US_Companies")
            .select("ticker,cik")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = response.data or []
        existing_rows.extend(rows)

        if len(rows) < page_size:
            break
        offset += page_size

    by_cik = {str(row["cik"]): row["ticker"] for row in existing_rows if row.get("cik")}
    by_ticker = {str(row["ticker"]).upper(): row["cik"] for row in existing_rows if row.get("ticker")}

    inserted = 0
    updated = 0
    skipped = 0

    for company in companies:
        ticker = company["ticker"].upper()
        cik = company["cik"]

        existing_ticker_for_cik = by_cik.get(cik)
        existing_cik_for_ticker = by_ticker.get(ticker)

        try:
            if existing_ticker_for_cik:
                # Same issuer already exists. Update that row by its current
                # ticker, avoiding an ON CONFLICT race between two UNIQUE keys.
                supabase.table("US_Companies").update(company).eq(
                    "ticker", existing_ticker_for_cik
                ).execute()
                updated += 1

                # Keep local indexes synchronized after a possible ticker change.
                if existing_ticker_for_cik != ticker:
                    by_ticker.pop(existing_ticker_for_cik, None)
                by_ticker[ticker] = cik
                by_cik[cik] = ticker

            elif existing_cik_for_ticker:
                # Ticker exists with another CIK. Do not overwrite it blindly;
                # preserve the existing row and report the conflict.
                skipped += 1
                print(
                    f"[SEC][SKIP] ticker conflict: {ticker} already maps to "
                    f"CIK {existing_cik_for_ticker}, incoming CIK {cik}"
                )

            else:
                supabase.table("US_Companies").insert(company).execute()
                inserted += 1
                by_ticker[ticker] = cik
                by_cik[cik] = ticker

        except Exception as exc:
            # Do not abort the entire master collection because of one bad
            # record. Surface the exact ticker/CIK and continue.
            skipped += 1
            print(f"[SEC][SKIP] {ticker} / {cik}: {exc}")

        time.sleep(0.02)

    print(
        f"[SEC] Inserted {inserted:,}, updated {updated:,}, "
        f"skipped {skipped:,} records."
    )

    return len(companies)


def collect_us_company_master():
    """Main Step-1 collector."""
    companies = fetch_sec_company_tickers()
    save_us_companies(companies)
    print(f"[SEC] Processed {len(companies):,} SEC company/ticker records.")
    return len(companies)


if __name__ == "__main__":
    collect_us_company_master()
