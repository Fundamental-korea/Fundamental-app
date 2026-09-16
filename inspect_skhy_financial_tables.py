import re
from io import StringIO

import pandas as pd
import requests

from collector_us_foreign_fallback import find_financial_tables, _clean_text


# ============================================================
# SK hynix 2026 H1 6-K financial report inspector
# ============================================================

CIK = "0002120882"
# SEC filing dated 2026-08-18 containing the semi-annual business report.
# The earlier 0001104659 URL was invalid for SK hynix and returned 404.
FILING_URL = (
    "https://www.sec.gov/Archives/edgar/data/2120882/"
    "000119312526354777/d147827d6k.htm"
)

HEADERS = {
    "User-Agent": "Fundamental-app research contact research@example.com",
    "Accept-Encoding": "gzip, deflate",
}


def clean_table_for_print(table: pd.DataFrame) -> pd.DataFrame:
    out = table.copy()
    out.columns = [
        _clean_text(col) or f"col_{i}"
        for i, col in enumerate(out.columns)
    ]
    for col in out.columns:
        out[col] = out[col].map(_clean_text)
    return out


def row_text(row: pd.Series) -> str:
    return " | ".join(
        text for value in row.tolist() if (text := _clean_text(value))
    )


def print_relevant_rows(table: pd.DataFrame, max_rows: int = 18) -> None:
    keywords = [
        "revenue",
        "sales",
        "operating",
        "profit",
        "income",
        "expense",
        "finance",
        "selling",
        "general",
        "administrative",
        "cash",
        "receivable",
        "inventory",
        "asset",
        "liabilit",
        "equity",
        "earnings per share",
        "basic",
        "diluted",
        "current",
    ]

    shown = 0
    for idx, row in table.iterrows():
        text = row_text(row)
        low = text.lower()
        if any(keyword in low for keyword in keywords):
            print(f"  row {idx}: {text}")
            shown += 1
            if shown >= max_rows:
                break

    if shown == 0:
        print("  (no obvious financial keyword rows)")


def main() -> None:
    print("=" * 72)
    print("SK hynix financial table inspector")
    print("=" * 72)
    print(f"CIK: {CIK}")
    print(f"URL: {FILING_URL}")

    response = requests.get(FILING_URL, headers=HEADERS, timeout=60)
    response.raise_for_status()
    html = response.text
    print(f"Downloaded bytes: {len(response.content):,}")

    candidates = find_financial_tables(html, minimum_score=3)
    print(f"Financial table candidates: {len(candidates)}")

    # The current parser only needs a manageable set of candidates for
    # diagnosis. Print the strongest tables first, including their full shape
    # and a filtered set of financial rows.
    for rank, candidate in enumerate(candidates[:15], start=1):
        index = candidate["table_index"]
        score = candidate["score"]
        table = clean_table_for_print(candidate["table"])

        print("\n" + "-" * 72)
        print(
            f"#{rank} table_index={index} score={score} "
            f"shape={table.shape}"
        )
        print("Columns:")
        for col in table.columns.tolist():
            print(f"  - {col}")

        print("Relevant rows:")
        print_relevant_rows(table)

        # Print the complete table for the top 3 candidates. These are usually
        # the tables that determine whether the parser is mapping periods and
        # units correctly.
        if rank <= 3:
            print("Full table:")
            print(table.to_string(index=True))

    print("\n" + "=" * 72)
    print("Inspection complete.")
    print("Do NOT write the SKHY result to Supabase yet.")
    print("Use the table indexes/rows above to verify period columns, units, and signs first.")
    print("=" * 72)


if __name__ == "__main__":
    main()
