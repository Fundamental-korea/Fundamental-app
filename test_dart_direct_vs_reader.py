"""Read-only OpenDART direct API vs OpenDartReader diagnostic.

Purpose:
- Verify whether the 1,000,000x LS에코에너지 share-count anomaly comes
  from OpenDART itself or from OpenDartReader / our collector pipeline.
- This script performs NO Supabase writes.

Targets:
  005930 삼성전자
  000660 SK하이닉스
  229640 LS에코에너지

The comparison uses the 2025 annual report (11011), matching the existing
stock-total diagnostic path.
"""

import os
import sys
import requests
import pandas as pd

from opendartreader import OpenDartReader


DART_API_KEY = os.environ.get("DART_API_KEY", "").strip()
DART_URL = "https://opendart.fss.or.kr/api/stockTotqySttus.json"
YEAR = "2025"
REPRT_CODE = "11011"

TARGETS = {
    "005930": ("삼성전자", "00126380"),
    "000660": ("SK하이닉스", "00164779"),
    "229640": ("LS에코에너지", "01093007"),
}

FIELDS = [
    "istc_totqy",
    "tesstk_co",
    "distb_stock_co",
    "stlm_dt",
]


def parse_num(value):
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if s in ("", "-", "nan", "None"):
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def direct_api(corp_code):
    response = requests.get(
        DART_URL,
        params={
            "crtfc_key": DART_API_KEY,
            "corp_code": corp_code,
            "bsns_year": YEAR,
            "reprt_code": REPRT_CODE,
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    status = str(payload.get("status", ""))
    if status != "000":
        raise RuntimeError(
            f"OpenDART direct API error: status={status}, "
            f"message={payload.get('message')}"
        )

    rows = payload.get("list") or []
    return pd.DataFrame(rows)


def common_row(df):
    if df is None or df.empty:
        return None
    if "se" not in df.columns:
        return None

    common = df[df["se"].astype(str).str.strip() == "보통주"]
    if common.empty:
        return None

    return common.iloc[0]


def extract_fields(df):
    row = common_row(df)
    if row is None:
        return {field: None for field in FIELDS}

    result = {}
    for field in FIELDS:
        value = row.get(field)
        result[field] = parse_num(value) if field != "stlm_dt" else value
    return result


def reader_api(reader, stock_code):
    # OpenDartReader path only; this call itself does not touch Supabase.
    return reader.report(stock_code, "주식총수", int(YEAR))


def fmt(value):
    if value is None:
        return "None"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def main():
    if not DART_API_KEY:
        print("ERROR: DART_API_KEY is not set.")
        return 1

    reader = OpenDartReader(DART_API_KEY)

    print("\n=== DART DIRECT API vs OPENDARTREADER (READ ONLY) ===")
    print(f"year={YEAR}, reprt_code={REPRT_CODE}")
    print("No Supabase reads/writes are performed by this script.")
    print()

    failures = 0

    for stock_code, (name, corp_code) in TARGETS.items():
        print(f"--- {name} ({stock_code}) ---")
        print(f"corp_code={corp_code}")

        try:
            direct_df = direct_api(corp_code)
            direct = extract_fields(direct_df)
        except Exception as exc:
            print(f"DIRECT API ERROR: {exc}")
            failures += 1
            continue

        try:
            reader_df = reader_api(reader, stock_code)
            reader = extract_fields(reader_df)
        except Exception as exc:
            print(f"OPENDARTREADER ERROR: {exc}")
            failures += 1
            continue

        print("Direct OpenDART API:")
        for field in FIELDS:
            print(f"  {field} = {fmt(direct[field])}")

        print("OpenDartReader:")
        for field in FIELDS:
            print(f"  {field} = {fmt(reader[field])}")

        comparable = [f for f in FIELDS if direct[f] is not None or reader[f] is not None]
        mismatches = [f for f in comparable if direct[f] != reader[f]]

        if mismatches:
            print(f"RESULT: MISMATCH -> {mismatches}")
        else:
            print("RESULT: MATCH")

        if direct["istc_totqy"] and reader["istc_totqy"]:
            print(
                "istc_totqy ratio (direct / reader) = "
                f"{direct['istc_totqy'] / reader['istc_totqy']:.6f}"
            )

        print()

    print("=== DIAGNOSTIC COMPLETE ===")

    # Any API/reader failure is a test failure. A MATCH, including a MATCH on
    # an anomalous LS value, is intentionally not treated as a failure:
    # the point is to identify where the anomaly originates.
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
