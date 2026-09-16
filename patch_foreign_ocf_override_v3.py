from pathlib import Path


SOURCE = Path("collector_us_foreign_fallback.py")
MARKER_V1 = "# === FOREIGN OCF PERIOD OVERRIDE ==="
MARKER_V2 = "# === FOREIGN OCF PERIOD OVERRIDE V2 ==="
MARKER_V3 = "# === FOREIGN OCF PERIOD OVERRIDE V3 ==="
BACKUP = SOURCE.with_name(SOURCE.name + ".pre_ocf_override_v3_backup")

BLOCK = r'''
# === FOREIGN OCF PERIOD OVERRIDE V3 ===
# Foreign SEC cash-flow tables can lose their period headers during pandas
# HTML parsing. We infer the cash-flow period from the other financial tables
# in the same filing, where quarter/YTD metadata is often still recoverable.


def _foreign_ocf_table_score(table: pd.DataFrame) -> int:
    text = " ".join(
        _clean_text(value).lower()
        for value in table.astype(str).values.flatten()
    )
    score = 0
    if "cash flows from operating activities" in text:
        score += 50
    if "net cash provided by operating activities" in text:
        score += 40
    if "net cash generated from operating activities" in text:
        score += 40
    if "cash generated from operating activities" in text:
        score += 25
    if "cash generated from operations" in text:
        score += 25
    return score


def _foreign_filing_has_ytd_context(
    candidates: List[Dict[str, Any]],
) -> bool:
    """Return True when another financial table exposes a YTD value."""
    for candidate in candidates:
        table = candidate["table"]
        for metric in (
            "revenue",
            "operating_income",
            "net_income",
            "eps_basic",
            "eps_diluted",
        ):
            extracted = extract_metric_by_period(table, metric)
            if extracted.get("ytd") is not None:
                return True
    return False


def _foreign_filing_has_quarter_context(
    candidates: List[Dict[str, Any]],
) -> bool:
    for candidate in candidates:
        table = candidate["table"]
        for metric in (
            "revenue",
            "operating_income",
            "net_income",
            "eps_basic",
            "eps_diluted",
        ):
            extracted = extract_metric_by_period(table, metric)
            if extracted.get("quarter") is not None:
                return True
    return False


def _foreign_extract_ocf_from_candidates_v3(
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    ranked = []
    for candidate in candidates:
        score = candidate.get("score", 0) + _foreign_ocf_table_score(candidate["table"])
        if _foreign_ocf_table_score(candidate["table"]) > 0:
            ranked.append((score, candidate))

    ranked.sort(key=lambda item: item[0], reverse=True)

    filing_has_ytd = _foreign_filing_has_ytd_context(candidates)
    filing_has_quarter = _foreign_filing_has_quarter_context(candidates)

    for _, candidate in ranked:
        table = candidate["table"]
        row = find_metric_row(table, "operating_cash_flow")
        if row is None:
            continue

        date_map = infer_column_dates(table)
        period_map = infer_column_periods(table)
        raw_values = []

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

        if not raw_values:
            continue

        result = {
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

        # Preserve explicit period metadata if the table has it.
        for period in ("quarter", "ytd", "annual"):
            items = [x for x in raw_values if x["period"] == period]
            if items:
                result[period] = items[0]["value"]
                if len(items) >= 2:
                    result["prior_by_period"][period] = items[1]["value"]

        # Cash-flow statements in foreign quarterly/semiannual filings are
        # commonly cumulative/YTD. If parsing flattened the period headers,
        # use the filing context rather than guessing from the OCF table text.
        if not any(result[p] is not None for p in ("quarter", "ytd", "annual")):
            if filing_has_ytd:
                result["ytd"] = raw_values[0]["value"]
                if len(raw_values) >= 2:
                    result["prior_by_period"]["ytd"] = raw_values[1]["value"]
            elif filing_has_quarter:
                result["quarter"] = raw_values[0]["value"]
                if len(raw_values) >= 2:
                    result["prior_by_period"]["quarter"] = raw_values[1]["value"]

        result["prior"] = (
            result["prior_by_period"].get("ytd")
            if result["prior_by_period"].get("ytd") is not None
            else result["prior_by_period"].get("quarter")
            if result["prior_by_period"].get("quarter") is not None
            else result["prior_by_period"].get("annual")
        )
        return result

    return {}



def _normalized_cash_flow_period(
    candidates: List[Dict[str, Any]],
    period: str,
) -> Dict[str, Optional[float]]:
    extracted = _foreign_extract_ocf_from_candidates_v3(candidates)
    return {
        "operating_cash_flow": extracted.get(period),
        "prior": extracted.get("prior_by_period", {}).get(period),
    }


_ORIGINAL_CALCULATE_STANDARD_METRICS_V3 = calculate_standard_metrics


def calculate_standard_metrics(
    result: Dict[str, Any],
) -> Dict[str, Optional[float]]:
    metrics = _ORIGINAL_CALCULATE_STANDARD_METRICS_V3(result)

    income = result.get("income_statement", {})
    quarter = income.get("quarter", {})
    ytd = income.get("ytd", {})

    quarter_ocf = (
        result.get("cash_flow", {})
        .get("quarter", {})
        .get("operating_cash_flow")
    )
    quarter_net_income = quarter.get("net_income")

    if quarter_ocf is not None and quarter_net_income not in (None, 0):
        metrics["ocf_ratio"] = quarter_ocf / quarter_net_income
    else:
        ytd_ocf = (
            result.get("cash_flow", {})
            .get("ytd", {})
            .get("operating_cash_flow")
        )
        ytd_net_income = ytd.get("net_income")
        if ytd_ocf is not None and ytd_net_income not in (None, 0):
            metrics["ocf_ratio"] = ytd_ocf / ytd_net_income

    return metrics
'''


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)

    text = SOURCE.read_text(encoding="utf-8")

    if MARKER_V3 in text:
        print("OCF override V3 already applied; nothing to do.")
        return

    if not BACKUP.exists():
        BACKUP.write_text(text, encoding="utf-8")
        print(f"Backup created: {BACKUP}")

    # Previous OCF overrides were appended at EOF. Remove the old override
    # block(s), preserving everything before the first override marker.
    marker_positions = [
        pos for marker in (MARKER_V1, MARKER_V2)
        if (pos := text.find(marker)) >= 0
    ]
    if marker_positions:
        text = text[: min(marker_positions)].rstrip()

    text = text + "\n\n" + BLOCK.strip() + "\n"
    SOURCE.write_text(text, encoding="utf-8")
    print("OCF override V3 applied successfully.")


if __name__ == "__main__":
    main()
