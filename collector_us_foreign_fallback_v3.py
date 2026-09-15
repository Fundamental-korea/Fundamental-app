import re
from io import StringIO
from typing import Any, Dict, List, Optional

import pandas as pd


# ============================================================
# Helpers
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
    if not text or text.upper() in {"-", "—", "–", "N/A", "NA"}:
        return None

    negative = text.startswith("(") or text.startswith("-")
    text = text.strip("()[]")
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
    return -abs(number) if negative else number


def _row_numeric_values(row: pd.Series) -> List[float]:
    """Parse numeric cells, including split-parenthesis SEC HTML cells."""
    cells = [_clean_text(v) for v in row.tolist()]
    out: List[float] = []
    pending_negative = False

    for cell in cells:
        if not cell:
            continue
        if cell in {"(", "["}:
            pending_negative = True
            continue
        if cell in {")", "]", ":",
        }:
            continue

        # A SEC table commonly splits '(93.4)' into '(93.4' + ')'.
        starts_negative = cell.startswith("(")
        number = _to_number(cell)
        if number is None:
            continue

        if starts_negative or pending_negative:
            number = -abs(number)
        pending_negative = False
        out.append(number)

    return out


def _table_text(table: pd.DataFrame) -> str:
    return " ".join(normalize_label(v) for v in table.astype(str).values.flatten())


def _classify(table: pd.DataFrame) -> str:
    text = _table_text(table)
    if "total assets" in text and "total liabilities" in text:
        return "balance_sheet"
    if (
        "cash flows from operating activities" in text
        or "net cash generated (used) in operating activities" in text
        or "net cash generated used in operating activities" in text
        or "net cash provided by operating activities" in text
    ):
        return "cash_flow"
    if "revenue" in text and "operating income" in text and "net income" in text:
        return "income_statement"
    return "other"


def _parse_date(text: Any) -> Optional[str]:
    text = _clean_text(text)
    patterns = [
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{1,2},?\s+\d{4}\b",
        r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            try:
                return pd.to_datetime(m.group(0)).strftime("%Y-%m-%d")
            except Exception:
                pass
    return None


def _period(text: Any) -> str:
    text = normalize_label(text)
    if any(x in text for x in ("six months", "nine months", "year to date", "ytd", "cumulative")):
        return "ytd"
    if any(x in text for x in ("three months", "quarter", "q1", "q2", "q3", "q4")):
        return "quarter"
    if any(x in text for x in ("year ended", "twelve months", "12 months", "fiscal year")):
        return "annual"
    return "unknown"


def _column_meta(table: pd.DataFrame) -> List[Dict[str, Any]]:
    meta: List[Dict[str, Any]] = []
    for idx, col in enumerate(table.columns):
        pieces = [_clean_text(col)]
        for r in range(min(5, len(table))):
            pieces.append(_clean_text(table.iloc[r, idx]))
        combined = " ".join(x for x in pieces if x)
        meta.append({"date": _parse_date(combined), "period": _period(combined)})
    return meta


ALIASES = {
    "revenue": ["revenue", "revenues", "sales revenue", "total revenue"],
    "operating_income": ["operating income", "income from operations", "operating profit", "profit from operations", "profit from operating activities"],
    "net_income": ["net income", "net earnings", "net profit", "profit for the period", "profit for the year"],
    "sga": ["selling, general and administrative", "selling, general and administration", "selling general and administrative", "selling general and administration", "general and administrative", "selling and administrative expenses"],
    "finance_costs": ["finance costs", "finance cost", "finance costs, net", "interest expense", "interest costs", "finance expenses"],
    "eps_basic": ["basic earnings per share", "earnings per share - basic", "basic"],
    "eps_diluted": ["diluted earnings per share", "earnings per share - diluted", "diluted"],
    "cash": ["cash and cash equivalents", "cash"],
    "inventory": ["inventories", "inventory", "inventories, net", "inventory, net"],
    "receivables": ["trade and other receivables", "trade receivables", "accounts receivable, net", "accounts receivable", "receivables"],
    "unbilled_receivables": ["unbilled receivables", "unbilled accounts receivable", "contract assets", "contract asset"],
    "assets": ["total assets"],
    "liabilities": ["total liabilities"],
    "equity": ["total equity"],
    "current_assets": ["total current assets"],
    "current_liabilities": ["total current liabilities"],
    "operating_cash_flow": ["net cash provided by operating activities", "net cash generated from operating activities", "net cash generated (used) in operating activities", "net cash generated used in operating activities", "cash generated from operating activities", "cash provided by operating activities", "net cash from operating activities"],
}


def _matches(text: Any, aliases: List[str]) -> bool:
    normalized = normalize_label(text)
    aliases_n = sorted((normalize_label(a) for a in aliases), key=len, reverse=True)
    if normalized in aliases_n:
        return True
    return any(len(a) >= 8 and a in normalized for a in aliases_n)


def _find_row(table: pd.DataFrame, metric: str) -> Optional[int]:
    aliases = ALIASES.get(metric, [])
    for i in range(len(table)):
        first = _clean_text(table.iloc[i, 0])
        if not first:
            continue
        if metric.startswith("eps_"):
            context = " ".join(_clean_text(table.iloc[j, 0]) for j in range(max(0, i - 4), i + 1)).lower()
            if "earnings per share" not in context:
                continue
        if _matches(first, aliases):
            return i
    return None


def _metric_by_period(table: pd.DataFrame, metric: str, fiscal_year: Optional[int] = None) -> Dict[str, Any]:
    idx = _find_row(table, metric)
    if idx is None:
        return {"quarter": None, "ytd": None, "annual": None, "prior_by_period": {"quarter": None, "ytd": None, "annual": None}, "prior": None}

    metas = _column_meta(table)
    cells = [_clean_text(v) for v in table.iloc[idx].tolist()]
    raw = []
    pending_negative = False
    for cidx, cell in enumerate(cells):
        if not cell:
            continue
        if cell in {"(", "["}:
            pending_negative = True
            continue
        if cell in {")", "]"}:
            continue
        val = _to_number(cell)
        if val is None:
            continue
        if cell.startswith("(") or pending_negative:
            val = -abs(val)
        pending_negative = False
        raw.append({"idx": cidx, "value": val, **metas[cidx]})

    result = {"quarter": None, "ytd": None, "annual": None, "prior_by_period": {"quarter": None, "ytd": None, "annual": None}, "prior": None}
    for p in ("quarter", "ytd", "annual"):
        items = [x for x in raw if x["period"] == p]
        if not items:
            continue
        if fiscal_year is not None:
            current = [x for x in items if x.get("date") and int(x["date"][:4]) == fiscal_year]
            prior = [x for x in items if x.get("date") and int(x["date"][:4]) == fiscal_year - 1]
        else:
            current, prior = items[:1], items[1:2]
        if current:
            result[p] = current[0]["value"]
        if prior:
            result["prior_by_period"][p] = prior[0]["value"]
    result["prior"] = result["prior_by_period"]["ytd"] if result["prior_by_period"]["ytd"] is not None else result["prior_by_period"]["quarter"] if result["prior_by_period"]["quarter"] is not None else result["prior_by_period"]["annual"]
    return result


# ============================================================
# Balance sheet extraction
# ============================================================


def _balance_metric(table: pd.DataFrame, metric: str) -> Optional[float]:
    idx = _find_row(table, metric)
    if idx is None:
        return None
    vals = _row_numeric_values(table.iloc[idx])
    return vals[-1] if vals else None


def _section_subtotal(table: pd.DataFrame, section: str) -> Optional[float]:
    target = normalize_label(section)
    active = False
    for i in range(len(table)):
        row = table.iloc[i]
        first = normalize_label(_clean_text(row.iloc[0]))
        if first == target or first.startswith(target + ":"):
            active = True
            continue
        if not active:
            continue
        if first and first.endswith(":") and first != target + ":":
            if target == "current assets":
                break
        vals = _row_numeric_values(row)
        # First unlabeled numeric row after the section header is the subtotal.
        if not first and vals:
            return vals[0]
    return None


def _balance_snapshot(table: pd.DataFrame) -> Dict[str, Any]:
    current_assets = _balance_metric(table, "current_assets") or _section_subtotal(table, "current assets")
    current_liabilities = _balance_metric(table, "current_liabilities") or _section_subtotal(table, "current liabilities")
    trade_receivables = _balance_metric(table, "receivables")
    unbilled = _balance_metric(table, "unbilled_receivables")

    if trade_receivables is None:
        trade_receivables = 0.0
    receivables = trade_receivables
    if unbilled is not None:
        receivables += unbilled

    return {
        "assets": _balance_metric(table, "assets"),
        "liabilities": _balance_metric(table, "liabilities"),
        "equity": _balance_metric(table, "equity"),
        "cash": _balance_metric(table, "cash"),
        "inventory": _balance_metric(table, "inventory"),
        "current_assets": current_assets,
        "current_liabilities": current_liabilities,
        "receivables": receivables if receivables else None,
        "unbilled_receivables": unbilled,
    }


# ============================================================
# Normalization
# ============================================================


def find_financial_tables(html: str) -> List[Dict[str, Any]]:
    tables = pd.read_html(StringIO(html))
    out = []
    for i, t in enumerate(tables):
        typ = _classify(t)
        if typ != "other":
            out.append({"table_index": i, "table_type": typ, "table": t})
    return out


def normalize_foreign_result(
    html: str,
    ticker: str = "",
    currency: str = "",
    scale: str = "",
    fiscal_end: Optional[str] = None,
    form: Optional[str] = None,
) -> Dict[str, Any]:
    candidates = find_financial_tables(html)
    income = next((x["table"] for x in candidates if x["table_type"] == "income_statement"), pd.DataFrame())
    balance = next((x["table"] for x in candidates if x["table_type"] == "balance_sheet"), pd.DataFrame())
    cashflow = next((x["table"] for x in candidates if x["table_type"] == "cash_flow"), pd.DataFrame())

    income_out = {}
    for metric in ("revenue", "operating_income", "net_income", "sga", "finance_costs", "eps_basic", "eps_diluted"):
        income_out[metric] = _metric_by_period(income, metric, fiscal_year=int(fiscal_end[:4]) if fiscal_end else None)

    cf_out = {p: None for p in ("quarter", "ytd", "annual")}
    cf_prior = {p: None for p in ("quarter", "ytd", "annual")}
    cf = _metric_by_period(cashflow, "operating_cash_flow", fiscal_year=int(fiscal_end[:4]) if fiscal_end else None) if not cashflow.empty else {}
    if cf:
        for p in cf_out:
            cf_out[p] = cf.get(p)
            cf_prior[p] = cf.get("prior_by_period", {}).get(p)

    return {
        "ticker": ticker,
        "currency": currency,
        "scale": scale,
        "fiscal_end": fiscal_end,
        "form": form,
        "income_statement": {
            "quarter": {m: income_out[m].get("quarter") for m in income_out} | {"_prior_year": {m: income_out[m].get("prior_by_period", {}).get("quarter") for m in income_out}},
            "ytd": {m: income_out[m].get("ytd") for m in income_out} | {"_prior_year": {m: income_out[m].get("prior_by_period", {}).get("ytd") for m in income_out}},
            "annual": {m: income_out[m].get("annual") for m in income_out},
        },
        "balance_sheet": {fiscal_end or "latest": _balance_snapshot(balance) if not balance.empty else {}},
        "cash_flow": {
            "quarter": {"operating_cash_flow": cf_out["quarter"], "prior": cf_prior["quarter"]},
            "ytd": {"operating_cash_flow": cf_out["ytd"], "prior": cf_prior["ytd"]},
            "annual": {"operating_cash_flow": cf_out["annual"], "prior": cf_prior["annual"]},
        },
    }


# ============================================================
# Standard metrics payload
# ============================================================


def _safe_div(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None or b == 0:
        return None
    return a / b


def build_standard_metric_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    q = result.get("income_statement", {}).get("quarter", {})
    ytd = result.get("income_statement", {}).get("ytd", {})
    b = next(iter(result.get("balance_sheet", {}).values()), {})
    cf = result.get("cash_flow", {}).get("quarter", {})
    prior = q.get("_prior_year", {})
    ytd_prior = ytd.get("_prior_year", {})

    metrics: Dict[str, Optional[float]] = {}
    rev = q.get("revenue")
    rev0 = prior.get("revenue")
    metrics["revenue_growth"] = _safe_div((ytd.get("revenue") - ytd_prior.get("revenue")) if ytd.get("revenue") is not None and ytd_prior.get("revenue") is not None else None, ytd_prior.get("revenue"))
    if metrics["revenue_growth"] is not None:
        metrics["revenue_growth"] *= 100

    eps = ytd.get("eps_diluted") if ytd.get("eps_diluted") is not None else ytd.get("eps_basic")
    eps0 = ytd_prior.get("eps_diluted") if ytd_prior.get("eps_diluted") is not None else ytd_prior.get("eps_basic")
    metrics["eps_growth"] = _safe_div((eps - eps0) if eps is not None and eps0 is not None else None, eps0)
    if metrics["eps_growth"] is not None:
        metrics["eps_growth"] *= 100

    metrics["opm"] = _safe_div(q.get("operating_income"), rev)
    if metrics["opm"] is not None:
        metrics["opm"] *= 100

    op = q.get("operating_income")
    equity, liabilities, cash = b.get("equity"), b.get("liabilities"), b.get("cash")
    invested = (equity + liabilities - cash) if None not in (equity, liabilities, cash) else None
    metrics["roic"] = _safe_div((op * 0.78) if op is not None else None, invested)
    if metrics["roic"] is not None:
        metrics["roic"] *= 100

    metrics["debt_rate"] = _safe_div(liabilities, equity)
    if metrics["debt_rate"] is not None:
        metrics["debt_rate"] *= 100

    ca, inv, cl = b.get("current_assets"), b.get("inventory"), b.get("current_liabilities")
    metrics["quick_ratio"] = _safe_div((ca - inv) if ca is not None and inv is not None else None, cl)
    if metrics["quick_ratio"] is None:
        metrics["quick_ratio"] = _safe_div((b.get("cash", 0) or 0) + (b.get("receivables", 0) or 0), cl)

    interest = q.get("finance_costs")
    metrics["interest_coverage"] = _safe_div(op, abs(interest) if interest is not None else None)

    ocf = cf.get("operating_cash_flow")
    ni = q.get("net_income")
    metrics["ocf_ratio"] = _safe_div(ocf, ni)

    sga = q.get("sga")
    metrics["sga_ratio"] = _safe_div(abs(sga) if sga is not None else None, rev)
    if metrics["sga_ratio"] is not None:
        metrics["sga_ratio"] *= 100

    metrics["downturn_defense"] = None

    missing = [k for k, v in metrics.items() if v is None]
    return {"metrics": metrics, "missing_metric_count": len(missing), "missing_metrics": missing}
