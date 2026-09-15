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


def _to_number(value: Any) -> Optional[float]:
    text = _clean_text(value)
    if not text:
        return None

    if text.upper() in {"-", "—", "–", "N/A"}:
        return None

    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = text.replace(",", "")
    text = text.replace("C$", "").replace("$", "")
    text = re.sub(r"\bCAD\b", "", text, flags=re.I)
    text = re.sub(r"[^\d.\-]", "", text).strip()

    if not text or text in {".", "-", "-."}:
        return None

    try:
        number = float(text)
    except ValueError:
        return None

    return -number if negative else number


def _safe_divide(
    numerator: Optional[float],
    denominator: Optional[float],
) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


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
    "assets": ["total assets"],
    "liabilities": ["total liabilities"],
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
    text = text.replace("’", "'").replace("&", "and")
    return text


def _label_matches(label: Any, aliases: List[str]) -> bool:
    normalized = normalize_label(label)

    # Exact matches always win.
    for alias in aliases:
        alias = normalize_label(alias)
        if normalized == alias:
            return True

    # Prefer longer aliases to prevent 'receivables' from stealing
    # 'unbilled receivables'.
    for alias in sorted(
        (normalize_label(alias) for alias in aliases),
        key=len,
        reverse=True,
    ):
        if len(alias) >= 8 and alias in normalized:
            return True

    return False


def match_metric_label(label: Any) -> Optional[str]:
    for metric, aliases in LABEL_ALIASES.items():
        if _label_matches(label, aliases):
            return metric
    return None


# ============================================================
# HTML loading / table detection
# ============================================================


def _read_html_tables(html: str) -> List[pd.DataFrame]:
    return pd.read_html(StringIO(html))


def score_financial_table(table: pd.DataFrame) -> int:
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

    return sum(
        points
        for keyword, points in keywords.items()
        if keyword in text
    )


def find_financial_tables(
    html: str,
    minimum_score: int = 3,
) -> List[Dict[str, Any]]:
    tables = _read_html_tables(html)
    candidates = []

    for index, table in enumerate(tables):
        score = score_financial_table(table)
        if score >= minimum_score:
            candidates.append({
                "table_index": index,
                "score": score,
                "table": table,
            })

    candidates.sort(
        key=lambda item: item["score"],
        reverse=True,
    )
    return candidates


# ============================================================
# Row / date / period extraction
# ============================================================


def _row_label_candidates(row: pd.Series) -> List[str]:
    return [
        text
        for value in row.tolist()
        if (text := _clean_text(value))
    ]


def find_metric_row(
    table: pd.DataFrame,
    metric: str,
) -> Optional[pd.Series]:
    aliases = LABEL_ALIASES.get(metric, [])
    for _, row in table.iterrows():
        for value in _row_label_candidates(row):
            if _label_matches(value, aliases):
                return row
    return None


def _parse_date_text(text: Any) -> Optional[str]:
    text = _clean_text(text)
    if not text:
        return None

    patterns = [
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s+(\d{4})\b",
        r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+(\d{1,2}),\s+(\d{4})\b",
        r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if not match:
            continue
        try:
            return pd.to_datetime(
                match.group(0)
            ).strftime("%Y-%m-%d")
        except Exception:
            pass

    return None


def infer_column_dates(
    table: pd.DataFrame,
) -> Dict[Any, Optional[str]]:
    result: Dict[Any, Optional[str]] = {}
    columns = list(table.columns)

    for column in columns:
        result[column] = _parse_date_text(column)

    for column_index, column in enumerate(columns):
        if result[column]:
            continue

        pieces = []
        for row_index in range(
            min(5, len(table))
        ):
            value = table.iloc[
                row_index,
                column_index,
            ]
            text = _clean_text(value)
            if text:
                pieces.append(text)

        result[column] = _parse_date_text(
            " ".join(pieces)
        )

    return result


def classify_period_text(text: Any) -> str:
    text = normalize_label(text)
    if not text:
        return "unknown"

    if any(
        pattern in text
        for pattern in (
            "six months",
            "nine months",
            "six month",
            "nine month",
            "year to date",
            "ytd",
            "cumulative",
            "from january",
        )
    ):
        return "ytd"

    if any(
        pattern in text
        for pattern in (
            "three months",
            "three month",
            "quarter",
            "q1",
            "q2",
            "q3",
            "q4",
        )
    ):
        return "quarter"

    if any(
        pattern in text
        for pattern in (
            "year ended",
            "twelve months",
            "12 months",
            "fiscal year",
        )
    ):
        return "annual"

    return "unknown"


def infer_column_periods(
    table: pd.DataFrame,
) -> Dict[Any, str]:
    result: Dict[Any, str] = {}
    columns = list(table.columns)

    for column in columns:
        result[column] = classify_period_text(column)

    for column_index, column in enumerate(columns):
        if result[column] != "unknown":
            continue

        pieces = []
        for row_index in range(
            min(5, len(table))
        ):
            value = table.iloc[
                row_index,
                column_index,
            ]
            text = _clean_text(value)
            if text:
                pieces.append(text)

        result[column] = classify_period_text(
            " ".join(pieces)
        )

    return result


def _period_year(
    date_text: Optional[str],
) -> Optional[int]:
    if not date_text:
        return None
    try:
        return int(date_text[:4])
    except (TypeError, ValueError):
        return None


def extract_metric_by_period(
    table: pd.DataFrame,
    metric: str,
) -> Dict[str, Any]:
    row = find_metric_row(table, metric)
    if row is None:
        return {}

    date_map = infer_column_dates(table)
    period_map = infer_column_periods(table)

    raw_values: List[Dict[str, Any]] = []

    for column in table.columns:
        number = _to_number(row[column])
        if number is None:
            continue

        raw_values.append({
            "column": _clean_text(column),
            "date": date_map.get(column),
            "period": period_map.get(column, "unknown"),
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
    }

    for period in ("quarter", "ytd", "annual"):
        items = [
            item
            for item in raw_values
            if item["period"] == period
        ]

        if not items:
            continue

        # First value is the current reporting period.
        result[period] = items[0]["value"]

        current_year = _period_year(
            items[0].get("date")
        )

        if current_year is None:
            continue

        prior_items = [
            item
            for item in items[1:]
            if _period_year(item.get("date"))
            == current_year - 1
        ]

        if prior_items:
            result["prior_by_period"][period] = (
                prior_items[0]["value"]
            )

    # Backward-compatible convenience field. For callers that still use
    # 'prior', prefer YTD, then quarter, then annual.
    result["prior"] = (
        result["prior_by_period"].get("ytd")
        if result["prior_by_period"].get("ytd") is not None
        else result["prior_by_period"].get("quarter")
        if result["prior_by_period"].get("quarter") is not None
        else result["prior_by_period"].get("annual")
    )

    return result


def _extract_metric_from_candidates(
    candidates: List[Dict[str, Any]],
    metric: str,
) -> Dict[str, Any]:
    for candidate in candidates:
        result = extract_metric_by_period(
            candidate["table"],
            metric,
        )
        if result:
            return result
    return {}


# ============================================================
# Normalized foreign filing schema
# ============================================================


def _latest_balance_row(
    candidates: List[Dict[str, Any]],
    metric: str,
) -> Tuple[
    Optional[str],
    Optional[float],
    Optional[float],
]:
    extracted = _extract_metric_from_candidates(
        candidates,
        metric,
    )

    values = extracted.get("values", [])
    dated = [
        item
        for item in values
        if item.get("date")
    ]

    if not dated:
        return None, None, None

    dated.sort(
        key=lambda item: item["date"],
        reverse=True,
    )

    latest = dated[0]
    prior = dated[1] if len(dated) > 1 else None

    return (
        latest.get("date"),
        latest.get("value"),
        prior.get("value") if prior else None,
    )


def _normalized_income_period(
    candidates: List[Dict[str, Any]],
    period: str,
) -> Dict[str, Any]:
    metric_names = (
        "revenue",
        "operating_income",
        "net_income",
        "sga",
        "finance_costs",
        "eps_basic",
        "eps_diluted",
    )

    current: Dict[str, Optional[float]] = {}
    prior: Dict[str, Optional[float]] = {}

    for metric in metric_names:
        extracted = _extract_metric_from_candidates(
            candidates,
            metric,
        )

        current[metric] = extracted.get(period)
        prior[metric] = (
            extracted
            .get("prior_by_period", {})
            .get(period)
        )

    current["_prior_year"] = prior
    return current


def _normalized_cash_flow_period(
    candidates: List[Dict[str, Any]],
    period: str,
) -> Dict[str, Optional[float]]:
    extracted = _extract_metric_from_candidates(
        candidates,
        "operating_cash_flow",
    )

    prior = (
        extracted
        .get("prior_by_period", {})
        .get(period)
    )

    return {
        "operating_cash_flow": extracted.get(period),
        "prior": prior,
    }


def _balance_values_at_date(
    candidates: List[Dict[str, Any]],
    metric: str,
    target_date: Optional[str],
) -> Tuple[Optional[float], Optional[float]]:
    extracted = _extract_metric_from_candidates(
        candidates,
        metric,
    )

    values = [
        item
        for item in extracted.get("values", [])
        if item.get("date")
    ]

    current = None
    previous = None

    for item in values:
        if item.get("date") == target_date:
            current = item.get("value")
            break

    earlier = [
        item
        for item in values
        if item.get("date") and target_date
        and item.get("date") < target_date
    ]

    if earlier:
        earlier.sort(
            key=lambda item: item["date"],
            reverse=True,
        )
        previous = earlier[0].get("value")

    return current, previous


def normalize_foreign_result(
    html: str,
    ticker: Optional[str] = None,
    currency: Optional[str] = None,
    scale: Optional[str] = None,
    fiscal_end: Optional[str] = None,
    form: Optional[str] = None,
) -> Dict[str, Any]:
    candidates = find_financial_tables(html)

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

    metric_dates = {}
    all_dates: List[str] = []

    for metric in balance_metrics:
        extracted = _extract_metric_from_candidates(
            candidates,
            metric,
        )
        dates = [
            item.get("date")
            for item in extracted.get("values", [])
            if item.get("date")
        ]
        if dates:
            metric_dates[metric] = sorted(
                set(dates),
                reverse=True,
            )
            all_dates.extend(dates)
        else:
            metric_dates[metric] = []

    latest_balance_date = fiscal_end or (
        max(all_dates) if all_dates else None
    )

    balance: Dict[str, Dict[str, Optional[float]]] = {}

    if latest_balance_date:
        current_balance: Dict[str, Optional[float]] = {}
        previous_balance: Dict[str, Optional[float]] = {}

        for metric in balance_metrics:
            current_value, previous_value = _balance_values_at_date(
                candidates,
                metric,
                latest_balance_date,
            )

            if current_value is not None:
                current_balance[metric] = current_value
            if previous_value is not None:
                previous_balance[metric] = previous_value

        # Normalize receivables without losing the component values.
        trade_receivables = current_balance.get("receivables")
        unbilled_receivables = current_balance.get(
            "unbilled_receivables"
        )

        if (
            trade_receivables is not None
            and unbilled_receivables is not None
        ):
            current_balance["receivables"] = (
                trade_receivables
                + unbilled_receivables
            )
        elif (
            trade_receivables is None
            and unbilled_receivables is not None
        ):
            current_balance["receivables"] = (
                unbilled_receivables
            )

        # Keep a clear audit-friendly component field.
        if unbilled_receivables is not None:
            current_balance[
                "unbilled_receivables"
            ] = unbilled_receivables

        balance[latest_balance_date] = current_balance

        previous_dates = [
            date
            for date in set(all_dates)
            if date < latest_balance_date
        ]

        if previous_dates:
            previous_date = max(previous_dates)
            prior_row: Dict[str, Optional[float]] = {}

            for metric in balance_metrics:
                value, _ = _balance_values_at_date(
                    candidates,
                    metric,
                    previous_date,
                )
                if value is not None:
                    prior_row[metric] = value

            if prior_row:
                balance[previous_date] = prior_row

    income_statement = {
        "quarter": _normalized_income_period(
            candidates,
            "quarter",
        ),
        "ytd": _normalized_income_period(
            candidates,
            "ytd",
        ),
        "annual": _normalized_income_period(
            candidates,
            "annual",
        ),
    }

    cash_flow = {
        "quarter": _normalized_cash_flow_period(
            candidates,
            "quarter",
        ),
        "ytd": _normalized_cash_flow_period(
            candidates,
            "ytd",
        ),
        "annual": _normalized_cash_flow_period(
            candidates,
            "annual",
        ),
    }

    return {
        "company": {
            "ticker": ticker,
            "currency": currency,
            "scale": scale,
            "fiscal_end": (
                fiscal_end
                or latest_balance_date
            ),
            "form": form,
        },
        "income_statement": income_statement,
        "balance_sheet": balance,
        "cash_flow": cash_flow,
        "parser": {
            "financial_table_count": len(candidates),
            "table_indices": [
                candidate["table_index"]
                for candidate in candidates
            ],
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
    metrics: Dict[str, Optional[float]] = {
        metric: None
        for metric in STANDARD_METRICS
    }

    income = result.get("income_statement", {})
    quarter = income.get("quarter", {})
    ytd = income.get("ytd", {})

    # Growth: current YTD vs prior-year YTD.
    ytd_prior = ytd.get("_prior_year", {})

    metrics["revenue_growth"] = _growth_percent(
        ytd.get("revenue"),
        ytd_prior.get("revenue"),
    )

    current_eps = (
        ytd.get("eps_diluted")
        if ytd.get("eps_diluted") is not None
        else ytd.get("eps_basic")
    )
    prior_eps = (
        ytd_prior.get("eps_diluted")
        if ytd_prior.get("eps_diluted") is not None
        else ytd_prior.get("eps_basic")
    )

    metrics["eps_growth"] = _growth_percent(
        current_eps,
        prior_eps,
    )

    revenue = quarter.get("revenue")
    operating_income = quarter.get("operating_income")

    if revenue not in (None, 0) and operating_income is not None:
        metrics["opm"] = (
            operating_income / revenue * 100.0
        )

    liabilities = _latest_balance_value(
        result,
        "liabilities",
    )
    equity = _latest_balance_value(
        result,
        "equity",
    )
    cash = _latest_balance_value(
        result,
        "cash",
    )

    if (
        liabilities is not None
        and equity is not None
        and equity > 0
    ):
        metrics["debt_rate"] = (
            liabilities / equity * 100.0
        )

    current_assets = _latest_balance_value(
        result,
        "current_assets",
    )
    current_liabilities = _latest_balance_value(
        result,
        "current_liabilities",
    )
    inventory = _latest_balance_value(
        result,
        "inventory",
    )
    receivables = _latest_balance_value(
        result,
        "receivables",
    )

    if current_liabilities not in (None, 0):
        if (
            current_assets is not None
            and inventory is not None
        ):
            metrics["quick_ratio"] = (
                current_assets - inventory
            ) / current_liabilities
        elif (
            cash is not None
            and receivables is not None
        ):
            metrics["quick_ratio"] = (
                cash + receivables
            ) / current_liabilities

    finance_costs = quarter.get("finance_costs")
    if (
        operating_income is not None
        and finance_costs is not None
    ):
        interest = abs(finance_costs)
        if interest > 0:
            metrics["interest_coverage"] = (
                operating_income / interest
            )

    ocf = (
        result
        .get("cash_flow", {})
        .get("quarter", {})
        .get("operating_cash_flow")
    )
    net_income = quarter.get("net_income")

    if (
        ocf is not None
        and net_income not in (None, 0)
    ):
        metrics["ocf_ratio"] = (
            ocf / net_income
        )

    sga = quarter.get("sga")
    if sga is not None and revenue not in (None, 0):
        metrics["sga_ratio"] = (
            abs(sga) / revenue * 100.0
        )

    if (
        operating_income is not None
        and equity is not None
        and liabilities is not None
        and cash is not None
    ):
        invested_capital = (
            equity + liabilities - cash
        )

        if invested_capital > 0:
            nopat = operating_income * 0.78
            metrics["roic"] = (
                nopat
                / invested_capital
                * 100.0
            )

    # Downturn defense requires multi-year evidence.
    metrics["downturn_defense"] = None

    return metrics


def build_standard_metric_payload(
    result: Dict[str, Any],
) -> Dict[str, Any]:
    metrics = calculate_standard_metrics(result)
    missing_metrics = [
        metric
        for metric, value in metrics.items()
        if value is None
    ]

    return {
        "metrics": metrics,
        "missing_metrics": missing_metrics,
        "missing_metric_count": len(missing_metrics),
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
    print(
        "missing_metric_count =",
        payload["missing_metric_count"],
    )
    print(
        "missing_metrics =",
        payload["missing_metrics"],
    )
