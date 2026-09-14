"""Read-only debug for exact V2.3.8 filing concepts. No writes, no SEC payload persistence."""
from __future__ import annotations

import os
import requests

from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8, _local_concept

CASES = {
    "NEM": {
        "inventory": "InventoryOtherThanOreStockpilesNetOfReserves",
        "sga": "GeneralAndAdministrativeExpense",
        "interest_expense": "InterestIncomeExpenseNonoperatingNet",
    },
}
CIKS = {"NEM": "1164727"}


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")})
    resolver = SECXBRLSearchV2_3_8(session=session)

    for ticker, metrics in CASES.items():
        cik = CIKS[ticker]
        submissions = resolver.submissions(cik)
        target = resolver._latest_annual_fy(submissions)
        rows, meta = resolver._inline_filing_rows(cik, submissions)
        print("=" * 100)
        print(f"{ticker} target_fy={target} rows={len(rows)} filing={meta.get('primary_document')}")
        for metric, wanted in metrics.items():
            print(f"\nMETRIC={metric} wanted={wanted}")
            matches = []
            for r in rows:
                local = _local_concept(r.get("concept"))
                if local.lower() != wanted.lower():
                    continue
                matches.append(r)
            print(f"EXACT ROWS FOUND={len(matches)}")
            for r in matches[:20]:
                print({
                    "concept": r.get("concept"),
                    "namespace": r.get("namespace"),
                    "value": r.get("value"),
                    "start": r.get("start"),
                    "end": r.get("end"),
                    "instant": r.get("instant"),
                    "dimensioned": r.get("dimensioned"),
                    "contextRef": r.get("contextRef"),
                    "unit": r.get("unit"),
                    "form": r.get("form"),
                    "filed": r.get("filed"),
                })
            result = resolver.search_filing(cik, metric, year=target, submissions=submissions, limit=10)
            print(f"SEARCH_FILING={len(result[0])}")
            for c in result[0]:
                print(c.compact())


if __name__ == "__main__":
    main()
