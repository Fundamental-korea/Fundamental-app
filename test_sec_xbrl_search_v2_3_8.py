"""Read-only regression test for SEC XBRL resolver V2.3.8.

No Supabase writes and no raw SEC persistence.
"""
from __future__ import annotations

import requests

from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

SEC_HEADERS = {"User-Agent": "Fundamental-app contact@example.com"}
TICKERS = {
    "T": {"metric": "inventory"},
    "NEM": {"metric": "inventory"},
    "NEM": {"metric": "interest_expense"},
    "NEM_SGA": {"ticker": "NEM", "metric": "sga"},
    "DE_CA": {"ticker": "DE", "metric": "current_assets"},
    "DE_CL": {"ticker": "DE", "metric": "current_liabilities"},
}


def get_cik(ticker: str) -> str:
    payload = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_HEADERS, timeout=30).json()
    for row in payload.values():
        if str(row.get("ticker", "")).upper() == ticker:
            return str(row["cik_str"])
    raise KeyError(ticker)


def run(label: str, ticker: str, metric: str):
    cik = get_cik(ticker)
    resolver = SECXBRLSearchV2_3_8(SEC_HEADERS)
    result = resolver.resolve(cik, metric, year=2025)
    print("=" * 100)
    print(f"{label}: ticker={ticker} metric={metric}")
    print("BEST:", result["best"])
    print("FILING:")
    for row in result["filing_xbrl"][:5]:
        print(" ", row)


if __name__ == "__main__":
    run("T inventory", "T", "inventory")
    run("NEM interest", "NEM", "interest_expense")
    run("NEM SGA", "NEM", "sga")
    run("DE current assets", "DE", "current_assets")
    run("DE current liabilities", "DE", "current_liabilities")
