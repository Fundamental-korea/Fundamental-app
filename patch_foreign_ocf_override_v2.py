from pathlib import Path


SOURCE = Path("collector_us_foreign_fallback.py")
BACKUP = SOURCE.with_name(SOURCE.name + ".pre_ocf_override_v2_backup")
MARKER = "# === FOREIGN OCF PERIOD OVERRIDE ==="
NEW_MARKER = "# === FOREIGN OCF PERIOD OVERRIDE V2 ==="

BLOCK = r'''
# === FOREIGN OCF PERIOD OVERRIDE V2 ===
# Handles foreign SEC HTML cash-flow statements whose column headers are
# flattened to integer columns. Period inference is performed across all
# financial-table candidates in the filing, not only inside the OCF table.


def _foreign_ocf_document_period_hint(
    candidates: List[Dict[str, Any]],
) -> Optional[str]:
    text = " ".join(
        _clean_text(value).lower()
        for candidate in candidates
        for value in candidate["table"].astype(str).values.flatten()
    )

    # Prefer the longer/cumulative filing periods first because foreign
    # semiannual filings commonly contain both quarter and YTD income tables.
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
        )
    ):
        return "quarter"

    if any(
        marker in text
        for marker in (
            "twelve months ended",
            "twelve months",
            "year ended",
            "fiscal year",
        )
    ):
        return "annual"

    return None


def _foreign_ocf_is_genuine_table(table: pd.DataFrame) -> bool:
    text = " ".join(
        _clean_text(value).lower()
        for value in table.astype(str).values.flatten()
    )
    return (
        "cash flows from operating activities" in text
        or "net cash provided by operating activities" in text
        or "net cash generated from operating activities" in text
        or "cash generated from operating activities" in text
        or "cash generated from operations" in text
    )


def _foreign_extract_ocf_from_candidates(
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    period_hint = _foreign_ocf_document_period_hint(candidates)

    ranked = []
    for candidate in candidates:
        table = candidate["table"]
        if not _foreign_ocf_is_genuine_table(table):
            continue

        text = " ".join(
            _clean_text(value).lower()
            for value in table.astype(str).values.flatten()
        )
        score = candidate.get("score", 0)
        if "cash flows from operating activities" in text:
            score += 30
        if "net cash provided by operating activities" in text:
            score += 20
        if "net cash generated from operating activities" in text:
            score += 20
        elif "cash generated from operating activities" in text:
            score += 10
        ranked.append((score, candidate))

    ranked.sort(key=lambda item: item[0], reverse=True)

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

        # Preserve explicit period metadata when present.
        for period in ("quarter", "ytd", "annual"):
            items = [x for x in raw_values if x["period"] == period]
            if items:
                result[period] = items[0]["value"]
                if len(items) >= 2:
                    result["prior_by_period"][period] = items[1]["value"]

        # Flattened cash-flow tables often have no usable period metadata.
        # Apply the filing-level hint to the current/prior pair only then.
        if not any(result[p] is not None for p in ("quarter", "ytd", "annual")):
            if period_hint in {"quarter", "ytd", "annual"}:
                result[period_hint] = raw_values[0]["value"]
                if len(raw_values) >= 2:
                    result["prior_by_period"][period_hint] = raw_values[1]["value"]

        result["prior"] = (
            result["prior_by_period"].get("ytd")
            if result["prior_by_period"].get("ytd") is not None
            else result["prior_by_period"].get("quarter")
            if result["prior_by_period"].get("quarter") is not None
            else result["prior_by_period"].get("annual")
        )

        return result

    return {}


_ORIGINAL_NORMALIZED_CASH_FLOW_PERIOD_V2 = _normalized_cash_flow_period


def _normalized_cash_flow_period(
    candidates: List[Dict[str, Any]],
    period: str,
) -> Dict[str, Optional[float]]:
    extracted = _foreign_extract_ocf_from_candidates(candidates)
    return {
        "operating_cash_flow": extracted.get(period),
        "prior": extracted.get("prior_by_period", {}).get(period),
    }


_ORIGINAL_CALCULATE_STANDARD_METRICS_V2 = calculate_standard_metrics


def calculate_standard_metrics(
    result: Dict[str, Any],
) -> Dict[str, Optional[float]]:
    metrics = _ORIGINAL_CALCULATE_STANDARD_METRICS_V2(result)

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

    if NEW_MARKER in text:
        print("OCF override V2 already applied; nothing to do.")
        return

    if not BACKUP.exists():
        BACKUP.write_text(text, encoding="utf-8")
        print(f"Backup created: {BACKUP}")

    marker_pos = text.find(MARKER)
    if marker_pos >= 0:
        # Existing override is appended at EOF. Replace it without touching
        # the parser code that precedes the previous marker.
        text = text[:marker_pos].rstrip() + "\n\n" + BLOCK.strip() + "\n"
    else:
        text = text.rstrip() + "\n\n" + BLOCK.strip() + "\n"

    SOURCE.write_text(text, encoding="utf-8")
    print("OCF override V2 applied successfully.")


if __name__ == "__main__":
    main()
