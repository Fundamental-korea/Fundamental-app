"""Read-only diagnostic: compare Fundamental stock_code/stock_name with KRX official names."""
from __future__ import annotations

import re
import collector
from kor_market_snapshot import _request_daily_trade

TARGET_SAMPLE_CODES = {"001080", "004960", "005930", "000660"}

def norm_name(value):
    s = str(value or "").strip().upper()
    s = re.sub(r"\s+", "", s)
    s = s.replace("(주)", "").replace("㈜", "")
    return s

def load_db_rows():
    rows = []
    start = 0
    page_size = 1000
    while True:
        res = (
            collector.supabase.table("Fundamental")
            .select("stock_code,stock_name")
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows

def load_krx_names():
    names = {}
    for api_id in ("stk_bydd_trd", "ksq_bydd_trd"):
        rows = _request_daily_trade(api_id, "20260918")
        print(f"{api_id}: {len(rows):,} rows")
        for row in rows:
            code = str(row.get("ISU_CD") or row.get("isu_cd") or "").strip().zfill(6)
            name = str(row.get("ISU_NM") or row.get("isu_nm") or "").strip()
            if code and name:
                names[code] = name
    return names

def main():
    print("=== KRX / FUNDAMENTAL CODE-NAME MAPPING DIAGNOSTIC ===")
    db = load_db_rows()
    print(f"Fundamental rows: {len(db):,}")

    by_code = {}
    duplicates = []
    for row in db:
        code = str(row.get("stock_code") or "").strip().zfill(6)
        name = str(row.get("stock_name") or "").strip()
        if code in by_code:
            duplicates.append((code, by_code[code], name))
        else:
            by_code[code] = name
    print(f"DB unique codes: {len(by_code):,}")
    print(f"Duplicate code rows: {len(duplicates):,}")

    krx = load_krx_names()
    print(f"KRX official names: {len(krx):,}")

    mismatches = []
    matched = 0
    checked = 0
    for code, db_name in by_code.items():
        krx_name = krx.get(code)
        if not krx_name:
            continue
        checked += 1
        if norm_name(db_name) == norm_name(krx_name):
            matched += 1
        else:
            mismatches.append((code, db_name, krx_name))

    print("\n=== SUMMARY ===")
    print(f"Compared current KRX codes: {checked:,}")
    print(f"Exact/normalized matches: {matched:,}")
    print(f"Name mismatches: {len(mismatches):,}")

    print("\n=== TARGET SAMPLE ===")
    for code in sorted(TARGET_SAMPLE_CODES):
        print(f"{code}: DB={by_code.get(code)!r} | KRX={krx.get(code)!r}")

    print("\n=== FIRST 100 MISMATCHES ===")
    for code, db_name, krx_name in mismatches[:100]:
        print(f"{code} | DB={db_name!r} | KRX={krx_name!r}")

    if duplicates:
        print("\n=== DUPLICATE CODE SAMPLE ===")
        for item in duplicates[:50]:
            print(item)

    print("\nREAD_ONLY: no Supabase writes were executed.")

if __name__ == "__main__":
    main()
