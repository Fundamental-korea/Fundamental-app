import re
from io import StringIO
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# ============================================================
# Generic helpers
# ============================================================


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_label(value: Any) -> str:
    text = _clean_text(value).lower()
    text = text.replace("’", "'").replace("&", "and")
    return text


def _to_number(value: Any) -> Optional[float]:
    text = _clean_text(value)
    if not text:
        return None
    if text.upper() in {"-", "—", "–", "N/A", "NA"}:
        return None

    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = text.replace(",", "")
    text = text.replace("C$", "").replace("$", "")
    text = re.sub(r"\b(?:CAD|USD|EUR|GBP|AUD)\b", "", text, flags=re.I)
    text = re.sub(r"[^\d.\-]", "", text).strip()

    if not text or text in {".", "-", "-."}:
        return None

    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def _row_numeric_values(row: pd.Series) -> List[Optional[float]]:
    """Parse row cells while preserving sign markers split across cells."""
    cells = [_clean_text(v) for v in row.tolist()]
    values: List[Optional[float]] = []
    pending_negative = False

    for cell in cells:
        if not cell:
            continue

        if cell in {"(", "["}:
            pending_negative = True
            continue

        if cell in {")",
            "]",
            ":",
        }:
            continue

        number = _to_number(cell)
        if number is None:
            continue

        if pending_negative and number > 0:
            number = -number
        pending_negative = False
        values.append(number)

    return values


# ============================================================
# Labels
# ============================================================

LABEL_ALIASES = {
    "revenue": [
        "revenue",
        "revenues",
        "sales",
        "sales revenue",
        "total revenue",
        "net revenue",
        "revenue from contracts with customers",
    ],
    "operating_income": [
        "operating income",
        "income from operations",
        "operating profit",
        "profit from operations",
        "profit from operating activities",
    ],
    "net_income": [
        "net income",
        "net earnings",
        "profit for the period",
        "profit for the year",
        "net profit",
        "net income attributable to owners",
    ],
    "assets": ["total assets"],
    "liabilities": ["total liabilities"],
    "equity": ["total equity"],
    "cash": [
        "cash and cash equivalents",
        "cash and cash equivalents at end of period",
        "cash",
    ],
    "inventory": [
        "inventories",
        "inventory",
        "inventories, net",
        "inventory, net",
    ],
    "current_assets": ["total current assets"],
    "current_liabilities": ["total current liabilities"],
    "receivables": [
        "trade and other receivables",
        "trade receivables",
        "accounts receivable, net",
        "accounts receivable",
        "receivables",
    ],
    "unbilled_receivables": [
        "unbilled receivables",
        "unbilled accounts receivable",
        "contract assets",
        "contract asset",
    ],
    "operating_cash_flow": [
        "net cash provided by operating activities",
        "net cash generated from operating activities",
        "net cash generated (used) in operating activities",
        "net cash generated used in operating activities",
        "cash generated from operating activities",
        "cash provided by operating activities",
        "net cash from operating activities",
    ],
    "sga": [
        "selling, general and administrative",
        "selling, general and administration",
        "selling general and administrative",
        "selling general and administration",
        "general and administrative",
        "selling and administrative expenses",
    ],
    "finance_costs": [
        "finance costs",
        "finance cost",
        "finance costs, net",
        "interest expense",
        "interest costs",
        "finance expenses",
    ],
    "eps_basic": [
        "earnings per share - basic",
        "basic earnings per share",
        "basic earnings per share attributable to owners",
        "basic earnings per share from continuing operations",
        "basic",
    ],
    "eps_diluted": [
        "earnings per share - diluted",
        "diluted earnings per share",
        "diluted earnings per share attributable to owners",
        "diluted earnings per share from continuing operations",
        "diluted",
    ],
}


def _label_matches(label: Any, aliases: List[str]) -> bool:
    normalized = normalize_label(label)
    for alias in sorted(
        (normalize_label(a) for a in aliases),
        key=len,
        reverse=True,
    ):
        if normalized == alias:
            return True
    for alias in sorted(
        (normalize_label(a) for a in aliases),
        key=len,
        reverse=True,
    ):
        if len(alias) >= 8 and alias in normalized:
            return True
    return False


def _first_label(table: pd.DataFrame) -> str:
    if table.empty:
        return ""
    for value in table.iloc[:, 0].tolist():
        text = _clean_text(value)
        if text:
            return text
    return ""


# ============================================================
# Date / period metadata
# ============================================================


def _parse_date_text(text: Any) -> Optional[str]:
    text = _clean_text(text)
    if not text:
        return None

    patterns = [
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b",
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(\d{1,2}),?\s+(\d{4})\b",
        r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        try:
            return pd.to_datetime(match.group(0)).strftime("%Y-%m-%d")
        except Exception:
            pass
    return None


def _period_from_text(text: Any) -> str:
    text = normalize_label(text)
    if any(p in text for p in ("six months", "six month", "nine months", "nine month", "year to date", "ytd", "cumulative")):
        return "ytd"
    if any(p in text for p in ("three months", "three month", "quarter", "q1", "q2", "q3", "q4")):
        return "quarter"
    if any(p in text for p in ("year ended", "twelve months", "12 months", "fiscal year")):
        return "annual"
    return "unknown"


def infer_column_metadata(table: pd.DataFrame) -> Dict[Any, Dict[str, Any]]:
    metadata: Dict[Any, Dict[str, Any]] = {}
    columns = list(table.columns)

    for idx, column in enumerate(columns):
        pieces: List[str] = [_clean_text(column)]
        for row_idx in range(min(4, len(table))):
            value = _clean_text(table.iloc[row_idx, idx])
            if value:
                pieces.append(value)

        combined = " ".join(pieces)
        metadata[column] = {
            "date": _parse_date_text(combined),
            "period": _period_from_text(combined),
        }

    return metadata


# ============================================================
# Table classification
# ============================================================


def _table_text(table: pd.DataFrame) -> str:
    return " ".join(
        normalize_label(v)
        for v in table.astype(str).values.flatten()
    )


def classify_financial_table(table: pd.DataFrame) -> str:
    text = _table_text(table)
    first = normalize_label(_first_label(table))

    has_income = (
        "revenue" in text
        and "operating income" in text
        and "net income" in text
    )
    has_balance = (
        "total assets" in text
        and "total liabilities" in text
        and ("total equity" in text or "shareholders' equity" in text)
    )
    has_cashflow = (
        "cash flows from operating activities" in text
        or "cash flow from operating activities" in text
        or "net cash generated (used) in operating activities" in text
    )

    if has_balance or "as at" in first:
        return "balance_sheet"
    if has_cashflow:
        return "cash_flow"
    if has_income:
        return "income_statement"
    return "other"


def find_financial_tables(html: str) -> List[Dict[str, Any]]:
    tables = pd.read_html(StringIO(html))
    candidates: List[Dict[str, Any]] = []

    for index, table in enumerate(tables):
        table_type = classify_financial_table(table)
        if table_type == "other":
            continue
        candidates.append({
            "table_index": index,
            "table_type": table_type,
            "table": table,
        })

    return candidates


# ============================================================
# Row matching with context
# ============================================================


def _row_text(table: pd.DataFrame, row_index: int) -> str:
    return " ".join(
        _clean_text(v)
        for v in table.iloc[row_index].tolist()
        if _clean_text(v)
    )


def find_metric_row(
    table: pd.DataFrame,
    metric: str,
) -> Optional[Tuple[int, pd.Series]]:
    aliases = LABEL_ALIASES.get(metric, [])

    # Exact/alias match first, but avoid broad 'basic'/'diluted' matches unless
    # the surrounding income statement indicates an EPS section.
    for row_index in range(len(table)):
        row = table.iloc[row_index]
        first_cell = _clean_text(row.iloc[0])
        if not first_cell:
            continue

        if metric in {"eps_basic", "eps_diluted"}:
            context = " ".join(
                _clean_text(table.iloc[i, 0])
                for i in range(max(0, row_index - 3), row_index + 1)
            ).lower()
            if "earnings per share" not in context:
                continue

        if _label_matches(first_cell, aliases):
            return row_index, row

    # Some tables put the label in a later cell; fall back cautiously.
    for row_index in range(len(table)):
        row = table.iloc[row_index]
        values = [_clean_text(v) for v in row.tolist()]
        for value in values:
            if not value:
                continue
            if metric in {"eps_basic", "eps_diluted"}:
                context = " ".join(
                    _clean_text(table.iloc[i, 0])
                    for i in range(max(0, row_index - 3), row_index + 1)
                ).lower()
                if "earnings per share" not in context:
                    continue
            if _label_matches(value, aliases):
                return row_index, row

    return None


# ============================================================
# Period-aware metric extraction
# ============================================================


def extract_metric_by_period(
    table: pd.DataFrame,
    metric: str,
    fiscal_year: Optional[int] = None,
) -> Dict[str, Any]:
    found = find_metric_row(table, metric)
    if found is None:
        return {}

    row_index, row = found
    metadata = infer_column_metadata(table)
    raw_values: List[Dict[str, Any]] = []

    # Keep original cell positions so sign/period metadata remain aligned.
    cells = [_clean_text(v) for v in row.tolist()]
    pending_negative = False

    for col_index, cell in enumerate(cells):
        if not cell:
            continue
        if cell in {"(", "["}:
            pending_negative = True
            continue
        if cell in {")",
            "]",
        }:
            continue

        number = _to_number(cell)
        if number is None:
            continue

        if pending_negative and number > 0:
            number = -number
        pending_negative = False

        column = table.columns[col_index]
        meta = metadata.get(column, {})
        raw_values.append({
            "column": _clean_text(column),
            "column_index": col_index,
            "date": meta.get("date"),
            "period": meta.get("period", "unknown"),
            "value": number,
        })

    result: Dict[str, Any] = {
        "quarter": None,
        "ytd": None,
        "annual": None,
        "prior": None,
        "prior_by_period": {
            "quarter": None,
            "ytd": None,
            "annual": None,
        },
        "values": raw_values,
        "row_index": row_index,
    }

    for period in ("quarter", "ytd", "annual"):
        items = [item for item in raw_values if item["period"] == period]
        if not items:
            continue

        current_items = items
        prior_items: List[Dict[str, Any]] = []

        if fiscal_year is not None:
            current_items = [
                item for item in items
                if item.get("date")
                and int(item["date"][:4]) == fiscal_year
            ] or items
            prior_items = [
                item for item in items
                if item.get("date")
                and int(item["date"][:4]) == fiscal_year - 1
            ]
        else:
            if items:
                first_year = int(items[0]["date"][:4]) if items[0].get("date") else None
                if first_year:
                    prior_items = [
                        item for item in items[1:]
                        if item.get("date")
                        and int(item["date"][:4]) == first_year - 1
                    ]

        if current_items:
            result[period] = current_items[0]["value"]
        if prior_items:
            result["prior_by_period"][period] = prior_items[0]["value"]

    result["prior"] = (
        result["prior_by_period"]["ytd"]
        if result["prior_by_period"]["ytd"] is not None
        else result["prior_by_period"]["quarter"]
        if result["prior_by_period"]["quarter"] is not None
        else result["prior_by_period"]["annual"]
    )

    return result


# ============================================================
# Balance subtotals
# ============================================================


def _first_two_numeric_values(row: pd.Series) -> List[float]:
    values = _row_numeric_values(row)
    return values[:2]


def _is_numeric_only_row(table: pd.DataFrame, row_index: int) -> bool:
    row = table.iloc[row_index]
    first = _clean_text(row.iloc[0])
    if first:
        # Rows such as 'Other current assets' are labeled, not subtotals.
        return False
    return len(_row_numeric_values(row)) >= 1


def _find_subtotal_row_after_section(
    table: pd.DataFrame,
    section_label: str,
    stop_labels: Tuple[str, ...],
) -> Optional[int]:
    section_label = normalize_label(section_label)
    in_section = False

    for idx in range(len(table)):
        label = normalize_label(table.iloc[idx, 0])

        if section_label in label:
            in_section = True
            continue

        if not in_section:
            continue

        if any(stop in label for stop in stop_labels):
            break

        if _is_numeric_only_row(table, idx):
            return idx

    return None


def _balance_values_for_row(
    table: pd.DataFrame,
    row_index: int,
) -> Tuple[Optional[float], Optional[float]]:
    values = _first_two_numeric_values(table.iloc[row_index])
    if not values:
        return None, None
    return values[0], values[1] if len(values) > 1 else None


def _balance_metric_extraction(
    table: pd.DataFrame,
    metric: str,
    fiscal_end: Optional[str],
) -> Tuple[Optional[str], Optional[float], Optional[float]]:
    if metric == "current_assets":
        row_index = _find_subtotal_row_after_section(
            table,
            "current assets:",
            ("non-current assets", "liabilities and shareholders' equity"),
        )
    elif metric == "current_liabilities":
        row_index = _find_subtotal_row_after_section(
            table,
            "current liabilities:",
            ("non-current liabilities", "shareholders' equity"),
        )
    else:
        found = find_metric_row(table, metric)
        row_index = found[0] if found else None

    if row_index is None:
        return None, None, None

    values = _balance_values_for_row(table, row_index)
    if values[0] is None:
        return None, None, None

    metadata = infer_column_metadata(table)
    # The balance sheet date is normally present in the header columns. Prefer
    # the first two actual dates discovered in the table.
    dates: List[str] = []
    for column in table.columns:
        date = metadata.get(column, {}).get("date")
        if date and date not in dates:
            dates.append(date)

    dates.sort(reverse=True)
    latest_date = fiscal_end or (dates[0] if dates else None)
    prior_date = dates[1] if len(dates) > 1 else None

    return latest_date, values[0], values[1]


# ============================================================
# Normalized schema
# ============================================================


def normalize_foreign_result(
    html: str,
    ticker: Optional[str] = None,
    currency: Optional[str] = None,
    scale: Optional[str] = None,
    fiscal_end: Optional[str] = None,
    form: Optional[str] = None,
) -> Dict[str, Any]:
    candidates = find_financial_tables(html)

    income_tables = [c["table"] for c in candidates if c["table_type"] == "income_statement"]
    balance_tables = [c["table"] for c in candidates if c["table_type"] == "balance_sheet"]
    cashflow_tables = [c["table"] for c in candidates if c["table_type"] == "cash_flow"]

    fy = None
    if fiscal_end:
        try:
            fy = int(fiscal_end[:4])
        except (TypeError, ValueError):
            fy = None

    # Prefer the first consolidated statement of each type.
    income = income_tables[0] if income_tables else None
    balance = balance_tables[0] if balance_tables else None
    cashflow = cashflow_tables[0] if cashflow_tables else None

    def income_metric(metric: str) -> Dict[str, Any]:
        if income is None:
            return {}
        return extract_metric_by_period(income, metric, fiscal_year=fy)

    quarter: Dict[str, Any] = {}
    ytd: Dict[str, Any] = {}

    for metric in (
        "revenue",
        "operating_income",
        "net_income",
        "sga",
        "finance_costs",
        "eps_basic",
        "eps_diluted",
    ):
        extracted = income_metric(metric)
        quarter[metric] = extracted.get("quarter")
        ytd[metric] = extracted.get("ytd")
        quarter.setdefault("_prior_year", {})[metric] = (
            extracted.get("prior_by_period", {}).get("quarter")
        )
        ytd.setdefault("_prior_year", {})[metric] = (
            extracted.get("prior_by_period", {}).get("ytd")
        )

    balance_result: Dict[str, Dict[str, Optional[float]]] = {}

    if balance is not None:
        balance_metrics = (
            "assets",
            "liabilities",
            "equity",
            "cash",
            "inventory",
            "current_assets",
            "current_liabilities",
            "receivables",
            "unbilled_receivables",
        )

        extracted_balance: Dict[str, Tuple[Optional[str], Optional[float], Optional[float]]] = {}
        all_dates: List[str] = []

        for metric in balance_metrics:
            date, current, prior = _balance_metric_extraction(
                balance,
                metric,
                fiscal_end,
            )
            extracted_balance[metric] = (date, current, prior)
            if date:
                all_dates.append(date)

        latest_date = fiscal_end or (max(all_dates) if all_dates else None)

        if latest_date:
            current_row: Dict[str, Optional[float]] = {}
            prior_row: Dict[str, Optional[float]] = {}

            for metric, (_, current, prior) in extracted_balance.items():
                if current is not None:
                    current_row[metric] = current
                if prior is not None:
                    prior_row[metric] = prior

            trade_receivables = current_row.get("receivables")
            unbilled = current_row.get("unbilled_receivables")
            if trade_receivables is not None and unbilled is not None:
                current_row["receivables"] = round(
                    trade_receivables + unbilled,
                    10,
                )

            balance_result[latest_date] = current_row

            # Use the second balance column as the prior comparative date.
            prior_dates = sorted(
                {date for _, _, _ in extracted_balance.values() if date and date != latest_date},
                reverse=True,
            )
            if prior_row:
                prior_date = prior_dates[0] if prior_dates else None
                if prior_date:
                    balance_result[prior_date] = prior_row

    def cashflow_metric(period: str) -> Tuple[Optional[float], Optional[float]]:
        if cashflow is None:
            return None, None
        extracted = extract_metric_by_period(
            cashflow,
            "operating_cash_flow",
            fiscal_year=fy,
        )
        return (
            extracted.get(period),
            extracted.get("prior_by_period", {}).get(period),
        )

    q_ocf, q_ocf_prior = cashflow_metric("quarter")
    y_ocf, y_ocf_prior = cashflow_metric("ytd")

    return {
        "company": {
            "ticker": ticker,
            "currency": currency,
            "scale": scale,
            "fiscal_end": fiscal_end,
            "form": form,
        },
        "income_statement": {
            "quarter": quarter,
            "ytd": ytd,
            "annual": {},
        },
        "balance_sheet": balance_result,
        "cash_flow": {
            "quarter": {
                "operating_cash_flow": q_ocf,
                "prior": q_ocf_prior,
            },
            "ytd": {
                "operating_cash_flow": y_ocf,
                "prior": y_ocf_prior,
            },
            "annual": {
                "operating_cash_flow": None,
                "prior": None,
            },
        },
        "parser": {
            "financial_table_count": len(candidates),
            "table_types": [c["table_type"] for c in candidates],
            "table_indices": [c["table_index"] for c in candidates],
        },
    }


# ============================================================
# Standard scoring adapter
# ============================================================

STANDARD_METRICS = (
    "revenue_growth",
    "eps_growth",
    "opm",
    "roic",
    "debt_rate",
    "quick_ratio",
    "interest_coverage",
    "ocf_ratio",
    "sga_ratio",
    "downturn_defense",
)


def _latest_balance_value(
    result: Dict[str, Any],
    metric: str,
) -> Optional[float]:
    balance = result.get("balance_sheet", {})
    if not balance:
        return None
    latest_date = max(balance.keys())
    return balance[latest_date].get(metric)


def _growth_percent(
    current: Optional[float],
    prior: Optional[float],
) -> Optional[float]:
    if current is None or prior is None or prior == 0:
        return None
    return (current / prior - 1.0) * 100.0


def calculate_standard_metrics(
    result: Dict[str, Any],
) -> Dict[str, Optional[float]]:
    metrics = {metric: None for metric in STANDARD_METRICS}

    income = result.get("income_statement", {})
    quarter = income.get("quarter", {})
    ytd = income.get("ytd", {})
    ytd_prior = ytd.get("_prior_year", {})

    metrics["revenue_growth"] = _growth_percent(
        ytd.get("revenue"),
        ytd_prior.get("revenue"),
    )

    current_eps = ytd.get("eps_diluted")
    if current_eps is None:
        current_eps = ytd.get("eps_basic")

    prior_eps = ytd_prior.get("eps_diluted")
    if prior_eps is None:
        prior_eps = ytd_prior.get("eps_basic")

    metrics["eps_growth"] = _growth_percent(
        current_eps,
        prior_eps,
    )

    revenue = quarter.get("revenue")
    operating_income = quarter.get("operating_income")

    if revenue not in (None, 0) and operating_income is not None:
        metrics["opm"] = operating_income / revenue * 100.0

    liabilities = _latest_balance_value(result, "liabilities")
    equity = _latest_balance_value(result, "equity")
    cash = _latest_balance_value(result, "cash")

    if liabilities is not None and equity is not None and equity > 0:
        metrics["debt_rate"] = liabilities / equity * 100.0

    current_assets = _latest_balance_value(result, "current_assets")
    current_liabilities = _latest_balance_value(result, "current_liabilities")
    inventory = _latest_balance_value(result, "inventory")
    receivables = _latest_balance_value(result, "receivables")

    if current_liabilities not in (None, 0):
        if current_assets is not None and inventory is not None:
            metrics["quick_ratio"] = (
                current_assets - inventory
            ) / current_liabilities
        elif cash is not None and receivables is not None:
            metrics["quick_ratio"] = (
                cash + receivables
            ) / current_liabilities

    finance_costs = quarter.get("finance_costs")
    if operating_income is not None and finance_costs is not None:
        interest = abs(finance_costs)
        if interest > 0:
            metrics["interest_coverage"] = operating_income / interest

    ocf = result.get("cash_flow", {}).get("quarter", {}).get(
        "operating_cash_flow"
    )
    net_income = quarter.get("net_income")

    if ocf is not None and net_income not in (None, 0):
        metrics["ocf_ratio"] = ocf / net_income

    sga = quarter.get("sga")
    if sga is not None and revenue not in (None, 0):
        metrics["sga_ratio"] = abs(sga) / revenue * 100.0

    if (
        operating_income is not None
        and equity is not None
        and liabilities is not None
        and cash is not None
    ):
        invested_capital = equity + liabilities - cash
        if invested_capital > 0:
            nopat = operating_income * 0.78
            metrics["roic"] = nopat / invested_capital * 100.0

    metrics["downturn_defense"] = None
    return metrics


def build_standard_metric_payload(
    result: Dict[str, Any],
) -> Dict[str, Any]:
    metrics = calculate_standard_metrics(result)
    missing = [metric for metric, value in metrics.items() if value is None]
    return {
        "metrics": metrics,
        "missing_metrics": missing,
        "missing_metric_count": len(missing),
        "metric_count": len(STANDARD_METRICS),
    }


def print_standard_metric_payload(
    result: Dict[str, Any],
) -> None:
    payload = build_standard_metric_payload(result)
    print("=" * 70)
    print("STANDARD METRIC ADAPTER")
    print("=" * 70)
    for metric, value in payload["metrics"].items():
        print(f"{metric:24s} = {value}")
    print()
    print("missing_metric_count =", payload["missing_metric_count"])
    print("missing_metrics =", payload["missing_metrics"])
