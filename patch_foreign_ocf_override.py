from pathlib import Path

SOURCE = Path("collector_us_foreign_fallback.py")
BACKUP = SOURCE.with_name(SOURCE.name + ".pre_ocf_override_backup")
MARKER = "# === FOREIGN OCF PERIOD OVERRIDE ==="

BLOCK = r'''
# === FOREIGN OCF PERIOD OVERRIDE ===
# Handles foreign SEC HTML where cash-flow period headers are flattened to
# integer columns (for example: 2026 | 2026 | 2025 | 2025).


def _foreign_ocf_period_hint(table: pd.DataFrame) -> Optional[str]:
    text = " ".join(
        _clean_text(value).lower()
        for value in table.astype(str).values.flatten()
    )

    if any(
        marker in text
        for marker in (
            "six months ended",
            "six months",
            "half-year",
            "half year",
            "semi-annual",
            "semiannual",
            "year to date",
            "cumulative",
        )
    ):
        return "ytd"

    if any(
        marker in text
        for marker in (
            "three months ended",
            "three months",
            "quarter ended",
            "quarter",
            "q1",
            "q2",
            "q3",
            "q4",
        )
    ):
        return "quarter"

    if any(
        marker in text
        for marker in (
            "year ended",
            "twelve months ended",
            "twelve months",
            "fiscal year",
        )
    ):
        return "annual"

    return None


def _foreign_ocf_period_hint_from_candidates(
    candidates: List[Dict[str, Any]],
) -> Optional[str]:
    # The OCF table itself may contain only flattened year cells such as
    # "2026 | 2026 | 2025 | 2025". Look across the entire filing's financial
    # table set for the reporting-period language that the OCF table lost.
    combined = " ".join(
        _clean_text(value).lower()
        for candidate in candidates
        for value in candidate["table"].astype(str).values.flatten()
    )

    if any(
        marker in combined
        for marker in (
            "six months ended",
            "six months",
            "half-year",
            "half year",
            "semi-annual",
            "semiannual",
            "year to date",
            "cumulative",
        )
    ):
        return "ytd"

    if any(
        marker in combined
        for marker in (
            "three months ended",
            "three months",
            "quarter ended",
        )
    ):
        return "quarter"

    if any(
        marker in combined
        for marker in (
            "twelve months ended",
            "year ended",
            "fiscal year",
        )
    ):
        return "annual"

    return None


def _foreign_extract_ocf_from_candidates(
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    # Prefer a genuine operating-cash-flow table rather than an arbitrary
    # table that happens to contain one of the aliases.
    ranked = []
    for candidate in candidates:
        table = candidate["table"]
        text = " ".join(
            _clean_text(value).lower()
            for value in table.astype(str).values.flatten()
        )
        score = candidate.get("score", 0)
        if "cash flows from operating activities" in text:
            score += 20
        if "net cash provided by operating activities" in text:
            score += 12
        elif "net cash generated from operating activities" in text:
            score += 12
        elif "cash generated from operating activities" in text:
            score += 8
        ranked.append((score, candidate))

    ranked.sort(key=lambda item: item[0], reverse=True)
    filing_period_hint = _foreign_ocf_period_hint_from_candidates(candidates)

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

        # First use any explicit period metadata.
        for period in ("quarter", "ytd", "annual"):
            items = [x for x in raw_values if x["period"] == period]
            if items:
                result[period] = items[0]["value"]
                if len(items) >= 2:
                    result["prior_by_period"][period] = items[1]["value"]

        # Flattened foreign filings often leave all OCF columns as unknown.
        # Use filing-wide context because the OCF table itself may contain
        # only the repeated fiscal years and no "six months" text.
        if not any(result[p] is not None for p in ("quarter", "ytd", "annual")):
            hint = _foreign_ocf_period_hint(table) or filing_period_hint
            if hint is not None:
                result[hint] = raw_values[0]["value"]
                if len(raw_values) >= 2:
                    result["prior_by_period"][hint] = raw_values[1]["value"]

        result["prior"] = (
            result["prior_by_period"].get("ytd")
            if result["prior_by_period"].get("ytd") is not None
            else result["prior_by_period"].get("quarter")
            if result["prior_by_period"].get("quarter") is not None
            else result["prior_by_period"].get("annual")
        )

        return result

    return {}


_ORIGINAL_NORMALIZED_CASH_FLOW_PERIOD = _normalized_cash_flow_period


def _normalized_cash_flow_period(
    candidates: List[Dict[str, Any]],
    period: str,
) -> Dict[str, Optional[float]]:
    extracted = _foreign_extract_ocf_from_candidates(candidates)
    return {
        "operating_cash_flow": extracted.get(period),
        "prior": extracted.get("prior_by_period", {}).get(period),
    }


_ORIGINAL_CALCULATE_STANDARD_METRICS = calculate_standard_metrics


def calculate_standard_metrics(
    result: Dict[str, Any],
) -> Dict[str, Optional[float]]:
    metrics = _ORIGINAL_CALCULATE_STANDARD_METRICS(result)

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

    if MARKER in text:
        print("OCF override already applied; nothing to do.")
        return

    if not BACKUP.exists():
        BACKUP.write_text(text, encoding="utf-8")
        print(f"Backup created: {BACKUP}")

    text = text.rstrip() + "\n\n" + BLOCK.strip() + "\n"
    SOURCE.write_text(text, encoding="utf-8")
    print("OCF override applied successfully.")


if __name__ == "__main__":
    main()
