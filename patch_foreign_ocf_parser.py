import re
from pathlib import Path


SOURCE = Path("collector_us_foreign_fallback.py")
BACKUP = SOURCE.with_name(SOURCE.name + ".pre_ocf_period_fix_backup")


def replace_function(text: str, name: str, new_source: str) -> str:
    """Replace one top-level Python function without exact-body matching."""
    pattern = re.compile(rf"(?ms)^def {re.escape(name)}\(.*?(?=^def |\Z)")
    match = pattern.search(text)
    if not match:
        raise RuntimeError(f"Could not find function: {name}")
    return text[:match.start()] + new_source.rstrip() + "\n\n" + text[match.end():]


def insert_before_function(text: str, name: str, block: str) -> str:
    marker = re.search(rf"(?m)^def {re.escape(name)}\(", text)
    if not marker:
        raise RuntimeError(f"Could not find insertion point before: {name}")
    return text[:marker.start()] + block.rstrip() + "\n\n" + text[marker.start():]


PERIOD_HELPERS = '''def _infer_table_period_hint(
    table: pd.DataFrame,
    document_period_hint: Optional[str] = None,
) -> Optional[str]:
    """Infer a reporting period when SEC HTML flattened the date headers."""
    text = " ".join(
        _clean_text(value).lower()
        for value in table.astype(str).values.flatten()
    )

    if any(marker in text for marker in (
        "six months ended", "six months",
        "nine months ended", "nine months",
        "year to date", "cumulative",
    )):
        return "ytd"

    if any(marker in text for marker in (
        "three months ended", "three months",
        "quarter ended", "quarter",
        "q1", "q2", "q3", "q4",
    )):
        return "quarter"

    if any(marker in text for marker in (
        "year ended", "twelve months ended",
        "twelve months", "fiscal year",
    )):
        return "annual"

    return document_period_hint


def _infer_document_period_hint(html: str) -> Optional[str]:
    """Infer the dominant reporting period from filing text."""
    text = _clean_text(html).lower()

    if any(marker in text for marker in (
        "six months ended", "six months",
        "half-year", "half year",
        "semi-annual", "semiannual",
    )):
        return "ytd"

    if any(marker in text for marker in (
        "three months ended", "three months",
        "quarter ended",
    )):
        return "quarter"

    if any(marker in text for marker in (
        "twelve months ended", "year ended", "fiscal year",
    )):
        return "annual"

    return None
'''

NEW_EXTRACTOR = '''def _extract_metric_from_candidates(
    candidates: List[Dict[str, Any]],
    metric: str,
    document_period_hint: Optional[str] = None,
) -> Dict[str, Any]:
    for candidate in candidates:
        table = candidate["table"]
        result = extract_metric_by_period(table, metric)
        if not result:
            continue

        # SEC HTML can flatten colspan/rowspan headers into integer columns.
        # For OCF, values may exist while their period remains 'unknown'.
        if metric == "operating_cash_flow":
            unknown_values = [
                item for item in result.get("values", [])
                if item.get("period") == "unknown"
            ]
            has_period_values = any(
                result.get(period) is not None
                for period in ("quarter", "ytd", "annual")
            )

            if unknown_values and not has_period_values:
                period_hint = _infer_table_period_hint(
                    table,
                    document_period_hint,
                )
                if period_hint in {"quarter", "ytd", "annual"}:
                    result[period_hint] = unknown_values[0]["value"]
                    if len(unknown_values) >= 2:
                        result["prior_by_period"][period_hint] = unknown_values[1]["value"]
                    result["prior"] = (
                        result["prior_by_period"].get("ytd")
                        if result["prior_by_period"].get("ytd") is not None
                        else result["prior_by_period"].get("quarter")
                        if result["prior_by_period"].get("quarter") is not None
                        else result["prior_by_period"].get("annual")
                    )

        return result

    return {}
'''

NEW_CASH_FLOW_FUNCTION = '''def _normalized_cash_flow_period(
    candidates: List[Dict[str, Any]],
    period: str,
    document_period_hint: Optional[str] = None,
) -> Dict[str, Optional[float]]:
    extracted = _extract_metric_from_candidates(
        candidates,
        "operating_cash_flow",
        document_period_hint=document_period_hint,
    )

    return {
        "operating_cash_flow": extracted.get(period),
        "prior": extracted.get("prior_by_period", {}).get(period),
    }
'''

NEW_OCF_BLOCK = '''    ocf = (
        result
        .get("cash_flow", {})
        .get("quarter", {})
        .get("operating_cash_flow")
    )
    net_income = quarter.get("net_income")

    # Foreign semiannual filings may provide OCF only on a cumulative/YTD basis.
    # Prefer quarter data when both numerator and denominator exist; otherwise
    # use matching YTD values.
    if ocf is not None and net_income not in (None, 0):
        metrics["ocf_ratio"] = ocf / net_income
    else:
        ocf = (
            result
            .get("cash_flow", {})
            .get("ytd", {})
            .get("operating_cash_flow")
        )
        net_income = ytd.get("net_income")
        if ocf is not None and net_income not in (None, 0):
            metrics["ocf_ratio"] = ocf / net_income
'''


def replace_exact_block(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match for {label}, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(f"Missing source: {SOURCE}")

    text = SOURCE.read_text(encoding="utf-8")

    if "def _infer_document_period_hint(" in text:
        print("OCF period patch already applied; nothing to do.")
        return

    if not BACKUP.exists():
        BACKUP.write_text(text, encoding="utf-8")
        print(f"Backup created: {BACKUP}")

    text = insert_before_function(text, "_extract_metric_from_candidates", PERIOD_HELPERS)
    text = replace_function(text, "_extract_metric_from_candidates", NEW_EXTRACTOR)
    text = replace_function(text, "_normalized_cash_flow_period", NEW_CASH_FLOW_FUNCTION)

    # Match the normalize function structurally instead of depending on
    # unrelated local edits elsewhere in the file.
    normalize_match = re.search(r"(?ms)^def normalize_foreign_result\(.*?(?=^def )", text)
    if not normalize_match:
        raise RuntimeError("Could not locate normalize_foreign_result")
    normalize_block = normalize_match.group(0)
    if "document_period_hint = _infer_document_period_hint(html)" not in normalize_block:
        old = "    candidates = find_financial_tables(html)\n"
        if old not in normalize_block:
            raise RuntimeError("Could not locate candidate initialization in normalize_foreign_result")
        normalize_block = normalize_block.replace(
            old,
            old + "    document_period_hint = _infer_document_period_hint(html)\n",
            1,
        )

    old_calls = '''        "quarter": _normalized_cash_flow_period(
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
'''
    new_calls = '''        "quarter": _normalized_cash_flow_period(
            candidates,
            "quarter",
            document_period_hint=document_period_hint,
        ),
        "ytd": _normalized_cash_flow_period(
            candidates,
            "ytd",
            document_period_hint=document_period_hint,
        ),
        "annual": _normalized_cash_flow_period(
            candidates,
            "annual",
            document_period_hint=document_period_hint,
        ),
'''
    if old_calls not in normalize_block:
        raise RuntimeError("Could not locate cash-flow call sites in normalize_foreign_result")
    normalize_block = normalize_block.replace(old_calls, new_calls, 1)
    text = text[:normalize_match.start()] + normalize_block + text[normalize_match.end():]

    # Patch the Standard OCF ratio by replacing the whole local section between
    # the OCF lookup and the next independent metric calculation.
    ocf_pattern = re.compile(
        r"(?ms)^    ocf = \(\n        result\n        \.get\(\"cash_flow\", \{\}\)\n.*?(?=^    sga = quarter\.get\(\"sga\"\))"
    )
    ocf_match = ocf_pattern.search(text)
    if not ocf_match:
        raise RuntimeError("Could not find the Standard OCF calculation block")
    text = text[:ocf_match.start()] + NEW_OCF_BLOCK + "\n" + text[ocf_match.end():]

    SOURCE.write_text(text, encoding="utf-8")
    print("OCF period patch applied successfully.")


if __name__ == "__main__":
    main()
