from pathlib import Path


SOURCE = Path("collector_us_foreign_fallback.py")
BACKUP = SOURCE.with_name(SOURCE.name + ".pre_ocf_period_fix_backup")

OLD_EXTRACTOR = '''def _extract_metric_from_candidates(\n    candidates: List[Dict[str, Any]],\n    metric: str,\n) -> Dict[str, Any]:\n    for candidate in candidates:\n        result = extract_metric_by_period(\n            candidate["table"],\n            metric,\n        )\n        if result:\n            return result\n    return {}\n'''

NEW_EXTRACTOR = '''def _infer_table_period_hint(\n    table: pd.DataFrame,\n    document_period_hint: Optional[str] = None,\n) -> Optional[str]:\n    """Infer a period for flattened SEC tables that lost header semantics."""\n    text = " ".join(\n        _clean_text(value).lower()\n        for value in table.astype(str).values.flatten()\n    )\n\n    # Table-level language wins over the document-level fallback.\n    if any(\n        marker in text\n        for marker in (\n            "six months ended",\n            "six months",\n            "nine months ended",\n            "nine months",\n            "year to date",\n            "cumulative",\n        )\n    ):\n        return "ytd"\n\n    if any(\n        marker in text\n        for marker in (\n            "three months ended",\n            "three months",\n            "quarter ended",\n            "quarter",\n            "q1",\n            "q2",\n            "q3",\n            "q4",\n        )\n    ):\n        return "quarter"\n\n    if any(\n        marker in text\n        for marker in (\n            "year ended",\n            "twelve months ended",\n            "twelve months",\n            "fiscal year",\n        )\n    ):\n        return "annual"\n\n    return document_period_hint\n\n\ndef _infer_document_period_hint(html: str) -> Optional[str]:\n    """Infer the dominant reporting period from filing text."""\n    text = _clean_text(html).lower()\n\n    if any(\n        marker in text\n        for marker in (\n            "six months ended",\n            "six months",\n            "half-year",\n            "half year",\n            "semi-annual",\n            "semiannual",\n        )\n    ):\n        return "ytd"\n\n    if any(\n        marker in text\n        for marker in (\n            "three months ended",\n            "three months",\n            "quarter ended",\n        )\n    ):\n        return "quarter"\n\n    if any(\n        marker in text\n        for marker in (\n            "twelve months ended",\n            "year ended",\n            "fiscal year",\n        )\n    ):\n        return "annual"\n\n    return None\n\n\ndef _extract_metric_from_candidates(\n    candidates: List[Dict[str, Any]],\n    metric: str,\n    document_period_hint: Optional[str] = None,\n) -> Dict[str, Any]:\n    for candidate in candidates:\n        table = candidate["table"]\n        result = extract_metric_by_period(\n            table,\n            metric,\n        )\n\n        if not result:\n            continue\n\n        # SEC HTML sometimes flattens a two-year cash-flow header to plain\n        # integer columns. In that case OCF values are present but remain\n        # period='unknown'. Use table/document semantics only as a fallback.\n        if metric == "operating_cash_flow":\n            unknown_values = [\n                item\n                for item in result.get("values", [])\n                if item.get("period") == "unknown"\n            ]\n\n            has_period_values = any(\n                result.get(period) is not None\n                for period in ("quarter", "ytd", "annual")\n            )\n\n            if unknown_values and not has_period_values:\n                period_hint = _infer_table_period_hint(\n                    table,\n                    document_period_hint,\n                )\n\n                if period_hint in {"quarter", "ytd", "annual"}:\n                    result[period_hint] = unknown_values[0]["value"]\n                    if len(unknown_values) >= 2:\n                        result["prior_by_period"][period_hint] = (\n                            unknown_values[1]["value"]\n                        )\n\n                    result["prior"] = (\n                        result["prior_by_period"].get("ytd")\n                        if result["prior_by_period"].get("ytd") is not None\n                        else result["prior_by_period"].get("quarter")\n                        if result["prior_by_period"].get("quarter") is not None\n                        else result["prior_by_period"].get("annual")\n                    )\n\n        return result\n\n    return {}\n'''

OLD_NORMALIZE = '''    candidates = find_financial_tables(html)\n\n    balance_metrics = (\n'''

NEW_NORMALIZE = '''    candidates = find_financial_tables(html)\n    document_period_hint = _infer_document_period_hint(html)\n\n    balance_metrics = (\n'''

OLD_CASH_FUNCTION = '''def _normalized_cash_flow_period(\n    candidates: List[Dict[str, Any]],\n    period: str,\n) -> Dict[str, Optional[float]]:\n    extracted = _extract_metric_from_candidates(\n        candidates,\n        "operating_cash_flow",\n    )\n\n    prior = (\n        extracted\n        .get("prior_by_period", {})\n        .get(period)\n    )\n\n    return {\n        "operating_cash_flow": extracted.get(period),\n        "prior": prior,\n    }\n'''

NEW_CASH_FUNCTION = '''def _normalized_cash_flow_period(\n    candidates: List[Dict[str, Any]],\n    period: str,\n    document_period_hint: Optional[str] = None,\n) -> Dict[str, Optional[float]]:\n    extracted = _extract_metric_from_candidates(\n        candidates,\n        "operating_cash_flow",\n        document_period_hint=document_period_hint,\n    )\n\n    prior = (\n        extracted\n        .get("prior_by_period", {})\n        .get(period)\n    )\n\n    return {\n        "operating_cash_flow": extracted.get(period),\n        "prior": prior,\n    }\n'''

OLD_CASH_CALL = '''        candidates,\n            "quarter",\n        ),\n        "ytd": _normalized_cash_flow_period(\n            candidates,\n            "ytd",\n        ),\n        "annual": _normalized_cash_flow_period(\n            candidates,\n            "annual",\n        ),\n'''

NEW_CASH_CALL = '''        candidates,\n            "quarter",\n            document_period_hint=document_period_hint,\n        ),\n        "ytd": _normalized_cash_flow_period(\n            candidates,\n            "ytd",\n            document_period_hint=document_period_hint,\n        ),\n        "annual": _normalized_cash_flow_period(\n            candidates,\n            "annual",\n            document_period_hint=document_period_hint,\n        ),\n'''

OLD_OCF = '''    ocf = (\n        result\n        .get("cash_flow", {})\n        .get("quarter", {})\n        .get("operating_cash_flow")\n    )\n    net_income = quarter.get("net_income")\n\n    if (\n        ocf is not None\n        and net_income not in (None, 0)\n    ):\n        metrics["ocf_ratio"] = (\n            ocf / net_income\n        )\n'''

NEW_OCF = '''    ocf = (\n        result\n        .get("cash_flow", {})\n        .get("quarter", {})\n        .get("operating_cash_flow")\n    )\n    net_income = quarter.get("net_income")\n\n    # Some foreign semiannual filings provide operating cash flow only on\n    # a cumulative/YTD basis. Prefer the quarter when available, otherwise\n    # use the matching YTD cash flow and YTD net income.\n    if ocf is not None and net_income not in (None, 0):\n        metrics["ocf_ratio"] = ocf / net_income\n    else:\n        ocf = (\n            result\n            .get("cash_flow", {})\n            .get("ytd", {})\n            .get("operating_cash_flow")\n        )\n        net_income = ytd.get("net_income")\n\n        if (\n            ocf is not None\n            and net_income not in (None, 0)\n        ):\n            metrics["ocf_ratio"] = ocf / net_income\n'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected exactly one match for {label}, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(f"Missing source: {SOURCE}")

    text = SOURCE.read_text(encoding="utf-8")

    if not BACKUP.exists():
        BACKUP.write_text(text, encoding="utf-8")
        print(f"Backup created: {BACKUP}")

    if "def _infer_document_period_hint(" in text:
        print("OCF period patch already applied; nothing to do.")
        return

    text = replace_once(text, OLD_EXTRACTOR, NEW_EXTRACTOR, "candidate extractor")
    text = replace_once(text, OLD_NORMALIZE, NEW_NORMALIZE, "normalize period hint")
    text = replace_once(text, OLD_CASH_FUNCTION, NEW_CASH_FUNCTION, "cash-flow function")
    text = replace_once(text, OLD_CASH_CALL, NEW_CASH_CALL, "cash-flow call sites")
    text = replace_once(text, OLD_OCF, NEW_OCF, "standard OCF ratio fallback")

    SOURCE.write_text(text, encoding="utf-8")
    print("OCF period patch applied successfully.")


if __name__ == "__main__":
    main()
