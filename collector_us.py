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

        # US_Companies has UNIQUE constraints on both ticker and CIK.
        # SEC's ticker master can contain multiple ticker records for the
        # same CIK, so keep only the first occurrence of each CIK.
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
    """Upsert the SEC company master list into US_Companies.

    US_Companies has UNIQUE constraints on ticker and CIK. To avoid a
    PostgreSQL CIK conflict when a ticker changes or multiple SEC ticker
    records point to the same issuer, existing rows are reconciled by CIK
    before the final ticker-based upsert.
    """
    supabase = get_supabase_client()

    # Reconcile each company by CIK first. This handles an existing row whose
    # CIK matches but whose ticker differs from the incoming SEC record.
    for company in companies:
        cik = company["cik"]
        existing = (
            supabase.table("US_Companies")
            .select("ticker,cik")
            .eq("cik", cik)
            .limit(1)
            .execute()
        )

        if existing.data:
            old_ticker = existing.data[0]["ticker"]
            if old_ticker != company["ticker"]:
                supabase.table("US_Companies").update(company).eq("cik", cik).execute()
            else:
                supabase.table("US_Companies").update(company).eq("ticker", company["ticker"]).execute()
        else:
            supabase.table("US_Companies").upsert(company, on_conflict="ticker").execute()

        time.sleep(0.02)


def collect_us_company_master():
    """Main Step-1 collector."""
    companies = fetch_sec_company_tickers()
    save_us_companies(companies)
    print(f"[SEC] Saved {len(companies):,} company/ticker records.")
    return len(companies)


if __name__ == "__main__":
    collect_us_company_master()
