"""Diagnose the existing foreign filing parser against MDA's 2026-08-07 6-K exhibit."""

from __future__ import annotations

import os
import requests

from collector_us_foreign_fallback import (
    find_financial_tables,
    normalize_foreign_result,
    build_standard_metric_payload,
)

SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
MDA_URL = "https://www.sec.gov/Archives/edgar/data/1857047/000110465926092383/tm2621766d1_ex99-1.htm"


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    response = session.get(MDA_URL, timeout=60)
    print(f"[HTTP] status={response.status_code} bytes={len(response.content)}")
    response.raise_for_status()

    html = response.text
    candidates = find_financial_tables(html)
    print(f"[TABLES] candidates={len(candidates)}")
    for candidate in candidates:
        table = candidate["table"]
        print(f"[TABLE] index={candidate['table_index']} score={candidate['score']} shape={table.shape}")

    result = normalize_foreign_result(
        html,
        ticker="MDA",
        fiscal_end="2026-06-30",
        form="6-K",
    )

    print("[NORMALIZED]")
    print(result)
    print("[STANDARD]")
    print(build_standard_metric_payload(result))


if __name__ == "__main__":
    main()
