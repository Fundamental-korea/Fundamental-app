"""Diagnose the existing foreign filing parser against SK hynix's 2026-09-09 6-K."""

from __future__ import annotations

import os
import requests

from collector_us_foreign_fallback import (
    find_financial_tables,
    normalize_foreign_result,
    build_standard_metric_payload,
)

SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
SKHY_URL = "https://www.sec.gov/Archives/edgar/data/2120882/000119312526385841/d80436d6k.htm"


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    response = session.get(SKHY_URL, timeout=60)
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
        ticker="SKHY",
        fiscal_end="2026-06-30",
        form="6-K",
    )

    print("[NORMALIZED]")
    print(result)
    print("[STANDARD]")
    print(build_standard_metric_payload(result))


if __name__ == "__main__":
    main()
