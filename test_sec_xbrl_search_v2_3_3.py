"""Read-only regression for SEC XBRL V2.3.3.

No Supabase writes. SEC payloads stay in memory.
The test targets the exact failure found in V2.3.2: Inline XBRL concepts carry
namespace prefixes while EXACT_CONCEPTS uses local names.
"""
from __future__ import annotations

import os
import requests

from sec_xbrl_search_v2_3_3 import SECXBRLSearchV2_3_3

TICKERS = {
    "HON": "773840",
    "VZ": "732712",
    "T": "732717",
    "NEM": "1164727",
    "DE": "315189",
    "META": "1326801",
    "APD": "2969",
    "LIN": "1707925",
}

METRICS = {
    "HON": ["liabilities"],
    "VZ": ["liabilities", "receivables"],
    "T": ["liabilities", "inventory"],
    "NEM": ["interest_expense", "inventory", "sga", "operating_income"],
    "DE": ["operating_income", "current_assets", "current_liabilities"],
    "META": ["sga"],
    "APD": ["interest_expense", "operating_cash_flow"],
    "LIN": ["interest_expense"],
}


def main():
    session = requests.Session()
    session.headers.update({
        "User-Agent": os.environ.get(
            "SEC_USER_AGENT", "Fundamental-app contact@example.com"
        )
    })
    resolver = SECXBRLSearchV2_3_3(session=session)

    for ticker, cik in TICKERS.items():
        submissions = resolver.submissions(cik)
        target_year = resolver._latest_annual_fy(submissions)
        print("=" * 80)
        print(ticker, "target_fy=", target_year)
        for metric in METRICS[ticker]:
            candidates, meta = resolver.search_filing(
                cik, metric, year=target_year, submissions=submissions, limit=5
            )
            print("-", metric, "candidates=", len(candidates))
            for c in candidates[:5]:
                print(
                    "  ", c.concept,
                    "| label=", repr(c.label),
                    "| value=", c.value,
                    "| score=", c.score,
                    "| instant=", c.instant,
                    "| dim=", c.dimensioned,
                    "| reason=", c.reason,
                )
            if meta.get("derived_balance_sheet"):
                print("   derived:", meta["derived_balance_sheet"])


if __name__ == "__main__":
    main()
