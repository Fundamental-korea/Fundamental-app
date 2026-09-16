"""Diagnose SK hynix semi-annual financial 6-K filing with the foreign parser."""
from __future__ import annotations
import os
import requests
from collector_us_foreign_fallback import find_financial_tables, normalize_foreign_result, build_standard_metric_payload

SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
SKHY_URL = "https://www.sec.gov/Archives/edgar/data/2120882/000119312526354777/d147827d6k.htm"

def main():
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    r = session.get(SKHY_URL, timeout=60)
    print(f"[HTTP] status={r.status_code} bytes={len(r.content)}")
    r.raise_for_status()
    html = r.text
    candidates = find_financial_tables(html)
    print(f"[TABLES] candidates={len(candidates)}")
    for c in candidates[:15]:
        print(f"[TABLE] index={c['table_index']} score={c['score']} shape={c['table'].shape}")
    result = normalize_foreign_result(html, ticker="SKHY", fiscal_end="2026-06-30", form="6-K")
    print("[NORMALIZED]")
    print(result)
    print("[STANDARD]")
    print(build_standard_metric_payload(result))

if __name__ == "__main__":
    main()
