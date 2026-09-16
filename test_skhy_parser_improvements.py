import re
from io import StringIO

import pandas as pd
import requests

import collector_us_foreign_fallback as f

CIK = "0002120882"
FILING_URL = (
    "https://www.sec.gov/Archives/edgar/data/2120882/"
    "000119312526354777/d147827d6k.htm"
)

HEADERS = {
    "User-Agent": "Fundamental-app research contact research@example.com",
    "Accept-Encoding": "gzip, deflate",
}


def _header_text(table, column_index, rows=8):
    parts = []
    for row_index in range(min(rows, len(table))):
        text = f._clean_text(table.iloc[row_index, column_index])
        if text:
            parts.append(text)
    return " ".join(parts)


def improved_column_metadata(table):
    columns = list(table.columns)
    dates = {column: f._parse_date_text(column) for column in columns}
    periods = {column: f.classify_period_text(column) for column in columns}

    for i, column in enumerate(columns):
        header = _header_text(table, i, rows=8)
        if dates[column] is None:
            dates[column] = f._parse_date_text(header)
        if periods[column] == "unknown":
            periods[column] = f.classify_period_text(header)

    # SEC HTML frequently flattens colspan/rowspan headers. Propagate metadata
    # from an explicit header cell across its value columns, but never overwrite
    # a later explicit date/period discovered on that column.
    current_date = None
    current_period = "unknown"
    for column in columns:
        if dates[column] is not None:
            current_date = dates[column]
            current_period = periods[column]
        elif current_date is not None:
            dates[column] = current_date
            if periods[column] == "unknown":
                periods[column] = current_period

    return dates, periods


def improved_find_metric_row(table, metric):
    aliases = [f.normalize_label(a) for a in f.LABEL_ALIASES.get(metric, [])]
    best = None
    best_rank = -1

    for _, row in table.iterrows():
        for value in row.tolist():
            text = f.normalize_label(value)
            if not text:
                continue

            for alias in aliases:
                if text == alias:
                    rank = 1000 + len(alias)
                    if rank > best_rank:
                        best = row
                        best_rank = rank

            for alias in aliases:
                if len(alias) >= 8 and alias in text:
                    rank = len(alias)
                    if rank > best_rank:
                        best = row
                        best_rank = rank

    return best


def improved_extract_metric_by_period(table, metric):
    row = improved_find_metric_row(table, metric)
    if row is None:
        return {}

    date_map, period_map = improved_column_metadata(table)
    raw_values = []
    for column in table.columns:
        number = f._to_number(row[column])
        if number is None:
            continue
        raw_values.append({
            "column": f._clean_text(column),
            "date": date_map.get(column),
            "period": period_map.get(column, "unknown"),
            "value": number,
        })

    result = {
        "quarter": None,
        "ytd": None,
        "annual": None,
        "prior": None,
        "prior_by_period": {"quarter": None, "ytd": None, "annual": None},
        "values": raw_values,
    }

    for period in ("quarter", "ytd", "annual"):
        items = [x for x in raw_values if x["period"] == period]
        if not items:
            continue

        result[period] = items[0]["value"]
        current_year = f._period_year(items[0].get("date"))

        if current_year is not None:
            prior_items = [
                x for x in items[1:]
                if f._period_year(x.get("date")) == current_year - 1
            ]
            if prior_items:
                result["prior_by_period"][period] = prior_items[0]["value"]
        elif len(items) >= 2:
            # For flattened SEC tables where the dates are attached only to
            # surrounding header cells, the second value in the same period
            # group is the prior-year comparison column.
            result["prior_by_period"][period] = items[1]["value"]

    result["prior"] = (
        result["prior_by_period"]["ytd"]
        if result["prior_by_period"]["ytd"] is not None
        else result["prior_by_period"]["quarter"]
        if result["prior_by_period"]["quarter"] is not None
        else result["prior_by_period"]["annual"]
    )
    return result


def improved_extract_from_candidates(candidates, metric):
    for candidate in candidates:
        result = improved_extract_metric_by_period(candidate["table"], metric)
        if result:
            return result
    return {}


def run():
    response = requests.get(FILING_URL, headers=HEADERS, timeout=60)
    response.raise_for_status()
    html = response.text
    candidates = f.find_financial_tables(html)

    print("=" * 72)
    print("SK hynix parser improvement test")
    print("=" * 72)
    print(f"Downloaded bytes: {len(response.content):,}")
    print(f"Financial table candidates: {len(candidates)}")

    print("\n[Income statement: table 379]")
    table_379 = next((x["table"] for x in candidates if x["table_index"] == 379), None)
    if table_379 is None:
        print("table 379 not found")
    else:
        for metric in (
            "revenue",
            "operating_income",
            "net_income",
            "sga",
            "finance_costs",
            "eps_basic",
            "eps_diluted",
        ):
            extracted = improved_extract_metric_by_period(table_379, metric)
            print(f"{metric:20s} -> {extracted}")

    print("\n[Balance sheet: table 130]")
    table_130 = next((x["table"] for x in candidates if x["table_index"] == 130), None)
    if table_130 is None:
        print("table 130 not found")
    else:
        for metric in (
            "assets",
            "liabilities",
            "equity",
            "cash",
            "inventory",
            "current_assets",
            "current_liabilities",
            "receivables",
        ):
            extracted = improved_extract_metric_by_period(table_130, metric)
            print(f"{metric:20s} -> {extracted}")

    print("\n[Derived checks]")
    revenue = improved_extract_metric_by_period(table_379, "revenue") if table_379 is not None else {}
    op = improved_extract_metric_by_period(table_379, "operating_income") if table_379 is not None else {}
    if revenue.get("quarter") and op.get("quarter"):
        print("quarter_opm          ->", op["quarter"] / revenue["quarter"] * 100.0)
    print("quarter_revenue_prior ->", revenue.get("prior_by_period", {}).get("quarter"))
    print("ytd_revenue_prior     ->", revenue.get("prior_by_period", {}).get("ytd"))

    print("\n[Expected structural checks]")
    print("- income statement should expose 2026 current + 2025 prior for quarter and YTD")
    print("- equity should select Total Equity, not Equity Attributable to Owners")
    print("- current_assets/current_liabilities should now match 'Current Assets'/'Current Liabilities'")
    print("- OPM should be calculated from table 379 only after period mapping is validated")
    print("=" * 72)


if __name__ == "__main__":
    run()
