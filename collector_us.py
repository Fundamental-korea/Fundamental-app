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

# SEC asks automated clients to identify themselves with a descriptive User-Agent.
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
    for item in payload.values():
        ticker = str(item.get("ticker", "")).strip().upper()
        cik_int = item.get("cik_str")
        title = str(item.get("title", "")).strip()

        if not ticker or cik_int is None or not title:
            continue

        companies.append(
            {
                "ticker": ticker,
                "cik": f"{int(cik_int):010d}",
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
    """Upsert the SEC company master list into US_Companies."""
    supabase = get_supabase_client()

    for start in range(0, len(companies), batch_size):
        batch = companies[start : start + batch_size]
        supabase.table("US_Companies").upsert(batch, on_conflict="ticker").execute()
        time.sleep(0.1)


def collect_us_company_master():
    """Main Step-1 collector."""
    companies = fetch_sec_company_tickers()
    save_us_companies(companies)
    print(f"[SEC] Saved {len(companies):,} company/ticker records.")
    return len(companies)


if __name__ == "__main__":
    collect_us_company_master()
