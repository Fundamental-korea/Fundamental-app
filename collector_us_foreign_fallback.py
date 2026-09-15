import re
import json
from io import StringIO
from typing import Any, Dict, List, Optional

import pandas as pd


# ============================================================
# Generic helpers
# ============================================================

def _clean_text(value: Any) -> str:
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def _to_number(value: Any) -> Optional[float]:
    text = _clean_text(value)

    if not text:
        return None

    if text in {"-", "—", "–", "N/A", "n/a"}:
        return None

    negative = (
        text.startswith("(")
        and text.endswith(")")
    )

    text = text.strip("()")
    text = text.replace(",", "")
    text = text.replace("$", "")
    text = text.replace("C$", "")

    text = re.sub(
        r"\bCAD\b",
        "",
        text,
        flags=re.I,
    )

    text = text.strip()

    text = re.sub(
        r"[^\d.\-]",
        "",
        text,
    )

    if not text:
        return None

    try:
        number = float(text)
    except ValueError:
        return None

    return -number if negative else number


# ============================================================
# Label aliases
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

    "assets": [
        "total assets",
    ],

    "liabilities": [
        "total liabilities",
    ],

    "equity": [
        "total equity",
        "shareholders' equity",
        "shareholders equity",
        "total shareholders' equity",
        "equity attributable to shareholders",
        "equity attributable to owners",
    ],

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

    "current_assets": [
        "total current assets",
    ],

    "current_liabilities": [
        "total current liabilities",
    ],

    "receivables": [
        "trade and other receivables",
        "trade receivables",
        "accounts receivable",
        "accounts receivable, net",
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
        "cash generated from operating activities",
        "cash provided by operating activities",
        "net cash from operating activities",
    ],

    "sga": [
        "selling, general and administrative",
        "selling general and administrative",
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
    ],

    "eps_diluted": [
        "earnings per share - diluted",
        "diluted earnings per share",
        "diluted earnings per share attributable to owners",
        "diluted earnings per share from continuing operations",
    ],
}


def normalize_label(value: Any) -> str:

    text = _clean_text(value).lower()

    text = text.replace("’", "'")
    text = text.replace("&", "and")

    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    return text.strip()


def _label_matches(
    label: Any,
    aliases: List[str],
) -> bool:

    normalized = normalize_label(label)

    for alias in aliases:

        alias = normalize_label(alias)

        if normalized == alias:
            return True

    for alias in aliases:

        alias = normalize_label(alias)

        if len(alias) >= 8 and alias in normalized:
            return True

    return False


def match_metric_label(
    label: Any,
) -> Optional[str]:

    for metric, aliases in LABEL_ALIASES.items():

        if _label_matches(
            label,
            aliases,
        ):
            return metric

    return None


# ============================================================
# HTML loading
# ============================================================

def _read_html_tables(
    html: str,
) -> List[pd.DataFrame]:

    return pd.read_html(
        StringIO(html)
    )


# ============================================================
# Financial table detection
# ============================================================

def score_financial_table(
    table: pd.DataFrame,
) -> int:

    score = 0

    text = " ".join(
        _clean_text(value).lower()
        for value in table.astype(str).values.flatten()
    )

    keywords = {
        "revenue": 3,
        "operating income": 3,
        "net income": 3,
        "total assets": 3,
        "total liabilities": 3,
        "cash and cash equivalents": 2,
        "inventories": 2,
        "equity": 2,
        "operating activities": 2,
        "finance costs": 1,
        "earnings per share": 1,
    }

    for keyword, points in keywords.items():

        if keyword in text:
            score += points

    return score


def find_financial_tables(
    html: str,
    minimum_score: int = 3,
) -> List[Dict[str, Any]]:

    tables = _read_html_tables(html)

    candidates = []

    for i, table in enumerate(tables):

        score = score_financial_table(
            table
        )

        if score >= minimum_score:

            candidates.append({
                "table_index": i,
                "score": score,
                "table": table,
            })

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    return candidates


# ============================================================
# Row extraction
# ============================================================

def _row_label_candidates(
    row: pd.Series,
) -> List[str]:

    labels = []

    for value in row.tolist():

        text = _clean_text(value)

        if text:
            labels.append(text)

    return labels


def find_metric_row(
    table: pd.DataFrame,
    metric: str,
) -> Optional[pd.Series]:

    aliases = LABEL_ALIASES.get(
        metric,
        [],
    )

    for _, row in table.iterrows():

        for value in _row_label_candidates(row):

            if _label_matches(
                value,
                aliases,
            ):
                return row

    return None


def extract_numeric_values(
    row: pd.Series,
) -> List[float]:

    values = []

    for value in row.tolist():

        number = _to_number(value)

        if number is not None:
            values.append(number)

    return values


# ============================================================
# Period detection
# ============================================================

def classify_period_text(
    text: Any,
) -> str:

    text = normalize_label(text)

    if not text:
        return "unknown"

    ytd_patterns = [
        "six months",
        "nine months",
        "six month",
        "nine month",
        "year to date",
        "ytd",
        "cumulative",
        "from january",
    ]

    for pattern in ytd_patterns:

        if pattern in text:
            return "ytd"

    quarter_patterns = [
        "three months",
        "three month",
        "quarter",
        "q1",
        "q2",
        "q3",
        "q4",
    ]

    for pattern in quarter_patterns:

        if pattern in text:
            return "quarter"

    annual_patterns = [
        "year ended",
        "twelve months",
        "12 months",
        "fiscal year",
    ]

    for pattern in annual_patterns:

        if pattern in text:
            return "annual"

    prior_patterns = [
        "prior year",
        "previous year",
        "comparative",
    ]

    for pattern in prior_patterns:

        if pattern in text:
            return "prior"

    return "unknown"


# ============================================================
# Date detection
# ============================================================

def _parse_date_text(
    text: Any,
) -> Optional[str]:

    text = _clean_text(text)

    if not text:
        return None

    patterns = [

        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})\b",

        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(\d{1,2}),\s+(\d{4})\b",

        r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.I,
        )

        if not match:
            continue

        try:

            parsed = pd.to_datetime(
                match.group(0)
            )

            return parsed.strftime(
                "%Y-%m-%d"
            )

        except Exception:
            continue

    return None


def infer_column_dates(
    table: pd.DataFrame,
) -> Dict[Any, Optional[str]]:

    result = {}

    columns = list(
        table.columns
    )

    for column in columns:

        text = _clean_text(
            column
        )

        result[column] = _parse_date_text(
            text
        )

    for column_index, column in enumerate(columns):

        if result[column]:
            continue

        pieces = []

        for row_index in range(
            min(4, len(table))
        ):

            value = table.iloc[
                row_index,
                column_index
            ]

            text = _clean_text(value)

            if text:
                pieces.append(text)

        combined = " ".join(pieces)

        result[column] = _parse_date_text(
            combined
        )

    return result


# ============================================================
# Period-aware extraction
# ============================================================

def infer_column_periods(
    table: pd.DataFrame,
) -> Dict[Any, str]:

    result = {}

    columns = list(
        table.columns
    )

    for column in columns:

        text = _clean_text(
            column
        )

        result[column] = classify_period_text(
            text
        )

    if all(
        period == "unknown"
        for period in result.values()
    ):

        for column_index, column in enumerate(columns):

            pieces = []

            for row_index in range(
                min(4, len(table))
            ):

                value = table.iloc[
                    row_index,
                    column_index
                ]

                text = _clean_text(value)

                if text:
                    pieces.append(text)

            combined = " ".join(pieces)

            result[column] = classify_period_text(
                combined
            )

    return result


def extract_metric_by_period(
    table: pd.DataFrame,
    metric: str,
) -> Dict[str, Any]:

    row = find_metric_row(
        table,
        metric,
    )

    if row is None:
        return {}

    period_map = infer_column_periods(
        table
    )

    result = {
        "quarter": None,
        "ytd": None,
        "annual": None,
        "prior": None,
        "values": [],
    }

    for column in table.columns:

        number = _to_number(
            row[column]
        )

        if number is None:
            continue

        period = period_map.get(
            column,
            "unknown",
        )

        result["values"].append({
            "column": _clean_text(column),
            "period": period,
            "value": number,
        })

        if period in {
            "quarter",
            "ytd",
            "annual",
            "prior",
        }:

            if result[period] is None:
                result[period] = number

    return result


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


def _safe_divide(
    numerator: Optional[float],
    denominator: Optional[float],
) -> Optional[float]:

    if numerator is None:
        return None

    if denominator is None:
        return None

    if denominator == 0:
        return None

    return numerator / denominator


def _get_period_value(
    result: Dict[str, Any],
    statement: str,
    period: str,
    metric: str,
) -> Optional[float]:

    return (
        result
        .get(statement, {})
        .get(period, {})
        .get(metric)
    )


def _get_latest_balance(
    result: Dict[str, Any],
    metric: str,
) -> Optional[float]:

    balance = result.get(
        "balance_sheet",
        {}
    )

    if not balance:
        return None

    dates = sorted(
        balance.keys(),
        reverse=True,
    )

    for date in dates:

        value = balance[
            date
        ].get(
            metric
        )

        if value is not None:
            return value

    return None


def _get_latest_balance_date(
    result: Dict[str, Any],
) -> Optional[str]:

    balance = result.get(
        "balance_sheet",
        {}
    )

    if not balance:
        return None

    return max(
        balance.keys()
    )


def _get_previous_balance_date(
    result: Dict[str, Any],
) -> Optional[str]:

    balance = result.get(
        "balance_sheet",
        {}
    )

    dates = sorted(
        balance.keys(),
        reverse=True,
    )

    if len(dates) < 2:
        return None

    return dates[1]


def _latest_growth_pair(
    values: List[Dict[str, Any]],
) -> Optional[tuple]:

    if not values:
        return None

    usable = [
        item
        for item in values
        if item.get("value") is not None
    ]

    if len(usable) < 2:
        return None

    # Prefer the first two values from the same
    # statement period structure.
    return (
        usable[0]["value"],
        usable[1]["value"],
    )


def calculate_standard_metrics(
    result: Dict[str, Any],
) -> Dict[str, Optional[float]]:

    metrics = {
        metric: None
        for metric in STANDARD_METRICS
    }

    income = result.get(
        "income_statement",
        {}
    )

    quarter = income.get(
        "quarter",
        {}
    )

    ytd = income.get(
        "ytd",
        {}
    )

    # ========================================================
    # OPM
    # ========================================================

    revenue = quarter.get(
        "revenue"
    )

    operating_income = quarter.get(
        "operating_income"
    )

    if (
        revenue is not None
        and revenue != 0
        and operating_income is not None
    ):

        metrics["opm"] = (
            operating_income
            / revenue
            * 100
        )

    # ========================================================
    # Debt rate
    # ========================================================

    liabilities = _get_latest_balance(
        result,
        "liabilities",
    )

    equity = _get_latest_balance(
        result,
        "equity",
    )

    if (
        liabilities is not None
        and equity is not None
        and equity > 0
    ):

        metrics["debt_rate"] = (
            liabilities
            / equity
            * 100
        )

    # ========================================================
    # Quick ratio
    # ========================================================

    current_assets = _get_latest_balance(
        result,
        "current_assets",
    )

    current_liabilities = _get_latest_balance(
        result,
        "current_liabilities",
    )

    inventory = _get_latest_balance(
        result,
        "inventory",
    )

    cash = _get_latest_balance(
        result,
        "cash",
    )

    receivables = _get_latest_balance(
        result,
        "receivables",
    )

    if (
        current_assets is not None
        and current_liabilities is not None
        and current_liabilities != 0
    ):

        if inventory is not None:

            quick_assets = (
                current_assets
                - inventory
            )

            metrics["quick_ratio"] = (
                quick_assets
                / current_liabilities
            )

        elif (
            cash is not None
            and receivables is not None
        ):

            quick_assets = (
                cash
                + receivables
            )

            metrics["quick_ratio"] = (
                quick_assets
                / current_liabilities
            )

    # ========================================================
    # Interest coverage
    # ========================================================

    finance_costs = quarter.get(
        "finance_costs"
    )

    if (
        operating_income is not None
        and finance_costs is not None
    ):

        interest = abs(
            finance_costs
        )

        if interest > 0:

            metrics[
                "interest_coverage"
            ] = (
                operating_income
                / interest
            )

    # ========================================================
    # OCF ratio
    # ========================================================

    ocf = result.get(
        "cash_flow",
        {}
    ).get(
        "quarter",
        {}
    ).get(
        "operating_cash_flow"
    )

    net_income = quarter.get(
        "net_income"
    )

    if (
        ocf is not None
        and net_income is not None
        and net_income != 0
    ):

        metrics["ocf_ratio"] = (
            ocf
            / net_income
        )

    # ========================================================
    # SG&A ratio
    # ========================================================

    sga = quarter.get(
        "sga"
    )

    if (
        sga is not None
        and revenue is not None
        and revenue != 0
    ):

        metrics["sga_ratio"] = (
            abs(sga)
            / revenue
            * 100
        )

    # ========================================================
    # Growth metrics
    #
    # Prefer YTD current/prior-year comparison.
    # If prior-year value is unavailable from the
    # normalized object, leave the metric missing.
    # ========================================================

    def _ytd_growth(
        metric_name: str,
    ) -> Optional[float]:

        values = ytd.get(
            metric_name
        )

        if values is not None:
            return None

        return None

    # Current normalized extraction keeps raw values
    # in the candidate layer. Build growth from the
    # period-comparison records below when available.
    #
    # For the first adapter pass, do not invent growth
    # from unrelated periods.

    # ========================================================
    # ROIC
    # ========================================================

    if (
        operating_income is not None
        and equity is not None
        and liabilities is not None
        and cash is not None
    ):

        invested_capital = (
            equity
            + liabilities
            - cash
        )

        if invested_capital > 0:

            nopat = (
                operating_income
                * 0.78
            )

            metrics["roic"] = (
                nopat
                / invested_capital
                * 100
            )

    # ========================================================
    # Downturn defense
    #
    # The existing Standard scorer treats this as a
    # composite metric. At the adapter layer we only
    # provide it when sufficient historical information
    # exists. Do not fabricate a value from one quarter.
    # ========================================================

    metrics["downturn_defense"] = None

    return metrics


def build_standard_metric_payload(
    result: Dict[str, Any],
) -> Dict[str, Any]:

    metrics = calculate_standard_metrics(
        result
    )

    missing = [
        metric
        for metric, value
        in metrics.items()
        if value is None
    ]

    return {
        "metrics": metrics,
        "missing_metrics": missing,
        "missing_metric_count": len(missing),
        "metric_count": len(
            STANDARD_METRICS
        ),
    }


def print_standard_metric_payload(
    result: Dict[str, Any],
) -> None:

    payload = build_standard_metric_payload(
        result
    )

    print("=" * 70)
    print(
        "STANDARD METRIC ADAPTER"
    )
    print("=" * 70)

    for metric, value in payload[
        "metrics"
    ].items():

        if value is None:

            print(
                f"{metric:24s} = None"
            )

        else:

            print(
                f"{metric:24s} = {value:.6f}"
            )

    print()
    print(
        "missing_metric_count =",
        payload["missing_metric_count"]
    )

    print(
        "missing_metrics =",
        payload["missing_metrics"]
    )

