import re
from io import StringIO

import pandas as pd
import requests

import collector_us_foreign_fallback as f

FILING_URL = (
    "https://www.sec.gov/Archives/edgar/data/2120882/"
    "000119312526354777/d147827d6k.htm"
)
HEADERS = {
    "User-Agent": "Fundamental-app research contact research@example.com",
    "Accept-Encoding": "gzip, deflate",
}


def find_text(value):
    return f._clean_text(value).lower()


def row_contains_ocf(row):
    text = " ".join(find_text(v) for v in row.tolist() if find_text(v))
    return (
        "operating activities" in text
        or "operating cash flow" in text
        or "cash generated" in text
        or "cash provided" in text
    )


def score_table(table):
    score = 0
    text = " ".join(find_text(v) for v in table.astype(str).values.flatten())
    for keyword, points in (
        ("operating activities", 5),
        ("cash generated", 4),
        ("cash provided", 4),
        ("operating cash flow", 5),
        ("net cash", 3),
        ("six months", 2),
        ("three months", 2),
    ):
        if keyword in text:
            score += points
    return score


def print_table_detail(idx, table):
    print("\n[Detailed table structure]")
    print(f"table_index={idx} shape={table.shape}")
    print("columns:")
    for col_index, column in enumerate(table.columns):
        print(f"  col {col_index}: {column!r}")

    print("first 12 rows:")
    for row_idx in range(min(12, len(table))):
        row = table.iloc[row_idx]
        values = " | ".join(f._clean_text(v) for v in row.tolist())
        print(f"  row {row_idx}: {values}")

    date_map = f.infer_column_dates(table)
    period_map = f.infer_column_periods(table)
    print("common metadata:")
    for col_index, column in enumerate(table.columns):
        print(
            f"  col {col_index}: date={date_map.get(column)!r} "
            f"period={period_map.get(column)!r}"
        )


def main():
    response = requests.get(FILING_URL, headers=HEADERS, timeout=60)
    response.raise_for_status()
    tables = pd.read_html(StringIO(response.text))

    candidates = []
    for idx, table in enumerate(tables):
        if score_table(table) >= 4 or any(row_contains_ocf(row) for _, row in table.iterrows()):
            candidates.append((score_table(table), idx, table))

    candidates.sort(reverse=True, key=lambda x: x[0])

    print("=" * 72)
    print("SK hynix OCF table diagnostic")
    print("=" * 72)
    print(f"Downloaded bytes: {len(response.content):,}")
    print(f"OCF-related tables: {len(candidates)}")

    for rank, (score, idx, table) in enumerate(candidates[:15], 1):
        print("\n" + "-" * 72)
        print(f"#{rank} table_index={idx} score={score} shape={table.shape}")
        for row_idx, row in table.iterrows():
            if row_contains_ocf(row):
                print(f"row {row_idx}: " + " | ".join(f._clean_text(v) for v in row.tolist()))

    for target_idx in (382, 741):
        target = next((table for _, idx, table in candidates if idx == target_idx), None)
        if target is not None:
            print("\n" + "=" * 72)
            print_table_detail(target_idx, target)
            print("=" * 72)

    print("\n[Current common parser lookup]")
    financial_candidates = f.find_financial_tables(response.text)
    extracted = f._extract_metric_from_candidates(financial_candidates, "operating_cash_flow")
    print(extracted)
    print("=" * 72)


if __name__ == "__main__":
    main()
