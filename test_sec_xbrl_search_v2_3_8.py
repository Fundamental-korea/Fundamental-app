"""Read-only regression test for SEC XBRL resolver V2.3.8.

No Supabase writes and no raw SEC persistence.
"""
from __future__ import annotations

import os
import requests

from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

CASES = [
    ("T inventory", "T", "inventory"),
    ("NEM inventory", "NEM", "inventory"),
    ("NEM interest", "NEM", "interest_expense"),
    ("NEM SGA", "NEM", "sga"),
    ("DE current assets", "DE", "current_assets"),
    ("DE current liabilities", "DE", "current_liabilities"),
]

CIKS = {
    "T": "732717",
    "NEM": "1164727",
    "DE": "315189",
}


def run(label: str, ticker: str, metric: str):
    cik = CIKS[ticker]
    session = requests.Session()
    session.headers.update({"User-Agent": os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")})
    resolver = SECXBRLSearchV2_3_8(session=session)
    result = resolver.resolve(cik, metric, year=2025)

    print("=" * 100)
    print(f"{label}: ticker={ticker} metric={metric}")
    print("BEST:", result["best"])
    print("FILING:")
    for row in result["filing_xbrl"][:5]:
        print(" ", row)


if __name__ == "__main__":
    for case in CASES:
        run(*case)
