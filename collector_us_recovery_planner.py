"""Build a targeted US recovery worklist from the latest canonical inventory.

This is a planning/classification stage:
- It does not mutate Supabase financial values.
- It separates Company Facts 404 companies from field-missing companies.
- It classifies the 404 queue by OTC / foreign-filing / ADR signals and market-cap bucket.
- It identifies deterministic component-reconstruction opportunities for the 6,215
  companies for which Company Facts was available.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yfinance as yf

SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

HIGH_IMPACT_FIELDS = (
    "revenue",
    "operating_income",
    "debt_total",
    "debt_current",
    "debt_noncurrent",
    "interest_expense",
    "capex",
    "inventory",
    "receivables",
    "current_assets",
    "current_liabilities",
    "sga",
    "cash_dividends",
    "eps",
)

OTC_EXCHANGES = {"PNK", "OTCM", "OTCMKTS", "OTC", "PINK", "OQB", "OQX", "OID"}
FOREIGN_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}
ADR_RE = re.compile(r"(/ADR\b|\bADR\b|AMERICAN DEPOSITARY|DEPOSITARY RECEIPT)", re.I)


def load_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if len(data.get("companies") or []) != int(data.get("summary", {}).get("eligible_companies", 0)):
        raise RuntimeError("Inventory report company count does not match summary eligible count")
    return data


def normalize_bucket(market_cap: float | None) -> str:
    if market_cap is None:
        return "unknown"
    if market_cap >= 5_000_000_000:
        return "over_5b"
    if market_cap >= 1_000_000_000:
        return "1b_to_5b"
    return "under_1b"


def yahoo_profile(ticker: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "yahoo_ok": False,
        "market_cap": None,
        "market_cap_bucket": "unknown",
        "yahoo_exchange": None,
        "yahoo_quote_type": None,
        "yahoo_market": None,
        "yahoo_country": None,
        "yahoo_currency": None,
    }
    try:
        info = yf.Ticker(ticker).get_info()
        mc = info.get("marketCap")
        if isinstance(mc, (int, float)) and mc > 0:
            result["market_cap"] = float(mc)
            result["market_cap_bucket"] = normalize_bucket(float(mc))
            result["yahoo_ok"] = True
        result["yahoo_exchange"] = info.get("exchange")
        result["yahoo_quote_type"] = info.get("quoteType")
        result["yahoo_market"] = info.get("market")
        result["yahoo_country"] = info.get("country")
        result["yahoo_currency"] = info.get("currency")
    except Exception as exc:
        result["yahoo_error"] = f"{type(exc).__name__}:{exc}"
    return result


def sec_profile(cik: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "sec_ok": False,
        "foreign_filing": False,
        "recent_forms": [],
        "sec_sic": None,
        "sec_entity_type": None,
        "annual_domestic_filing": False,
    }
    url = SEC_SUBMISSIONS_URL.format(cik=str(cik).zfill(10))
    try:
        r = requests.get(url, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
        r.raise_for_status()
        data = r.json()
        recent = data.get("filings", {}).get("recent", {}) or {}
        forms = list(recent.get("form") or [])
        out["recent_forms"] = forms[:20]
        out["foreign_filing"] = any(f in FOREIGN_FORMS for f in forms[:20])
        out["annual_domestic_filing"] = any(f in {"10-K", "10-K/A"} for f in forms[:20])
        out["sec_sic"] = data.get("sic")
        out["sec_entity_type"] = data.get("entityType")
        out["sec_ok"] = True
    except Exception as exc:
        out["sec_error"] = f"{type(exc).__name__}:{exc}"
    return out


def classify_404(company: dict[str, Any]) -> dict[str, Any]:
    ticker = str(company.get("ticker") or "").strip()
    name = str(company.get("company_name") or "").strip()
    y = yahoo_profile(ticker)
    s = sec_profile(str(company.get("cik") or ""))
    exchange = str(y.get("yahoo_exchange") or "").upper()
    market = str(y.get("yahoo_market") or "").lower()

    otc = exchange in OTC_EXCHANGES or "otc" in market or "pink" in market
    adr = bool(ADR_RE.search(name))
    foreign = bool(s.get("foreign_filing")) or (
        y.get("yahoo_country") not in (None, "", "United States", "USA", "US")
    )

    if y.get("market_cap_bucket") == "over_5b":
        priority = "P0"
    elif y.get("market_cap_bucket") == "1b_to_5b":
        priority = "P1"
    elif y.get("market_cap_bucket") == "under_1b":
        priority = "P2"
    else:
        priority = "P3_unknown"

    class_flags = []
    if foreign:
        class_flags.append("foreign")
    if adr:
        class_flags.append("adr")
    if otc:
        class_flags.append("otc")

    classification = "+".join(class_flags) if class_flags else "us_non_adr_non_otc_or_unknown"

    recovery_eligible = bool(
        s.get("sec_ok")
        and y.get("yahoo_ok")
        and s.get("annual_domestic_filing")
        and not foreign
        and not adr
        and not otc
    )

    if recovery_eligible:
        recovery_track = "us_domestic_sec"
    elif foreign or adr or otc:
        recovery_track = "exclude_non_domestic_or_otc"
    elif not s.get("sec_ok") or not y.get("yahoo_ok"):
        recovery_track = "unknown_insufficient_profile"
    elif not s.get("annual_domestic_filing"):
        recovery_track = "no_recent_10k_signal"
    else:
        recovery_track = "manual_review"

    return {
        "ticker": ticker,
        "cik": str(company.get("cik") or ""),
        "company_name": name,
        "market_cap": y.get("market_cap"),
        "market_cap_bucket": y.get("market_cap_bucket"),
        "priority": priority,
        "is_otc": otc,
        "is_adr": adr,
        "is_foreign": foreign,
        "classification": classification,
        "recovery_track": recovery_track,
        "recovery_eligible": recovery_eligible,
        "annual_domestic_filing": bool(s.get("annual_domestic_filing")),
        "yahoo_exchange": y.get("yahoo_exchange"),
        "yahoo_country": y.get("yahoo_country"),
        "recent_forms": s.get("recent_forms") or [],
        "sec_ok": s.get("sec_ok"),
        "yahoo_ok": y.get("yahoo_ok"),
        "market_cap_source": "yfinance",
    }


def deterministic_recovery(company: dict[str, Any]) -> list[dict[str, Any]]:
    exact = company.get("exact") or {}
    ready: list[dict[str, Any]] = []

    def add(field: str, method: str, inputs: list[str]):
        ready.append({
            "ticker": company.get("ticker"),
            "field": field,
            "method": method,
            "inputs": inputs,
            "confidence": "high",
        })

    if not exact.get("debt_total") and exact.get("debt_current") and exact.get("debt_noncurrent"):
        add("debt_total", "sum_current_plus_noncurrent", ["debt_current", "debt_noncurrent"])

    if not exact.get("sga") and exact.get("general_and_administrative_expense") and exact.get("selling_expense"):
        add("sga", "sum_g_and_a_plus_selling", ["general_and_administrative_expense", "selling_expense"])

    # Metric-level deterministic readiness from currently trusted exact fields.
    logical_debt = bool(
        exact.get("debt_total")
        or (exact.get("debt_current") and exact.get("debt_noncurrent"))
    )
    if not exact.get("operating_income") and exact.get("revenue") and exact.get("net_income"):
        # No safe operating-income reconstruction from these components alone.
        pass
    if not exact.get("interest_expense") and exact.get("operating_income"):
        # Interest must be sourced, not inferred from other statements.
        pass
    if not exact.get("roic") and exact.get("operating_income") and exact.get("equity") and exact.get("cash") and logical_debt:
        add("roic", "derive_from_trusted_inputs", ["operating_income", "equity", "cash", "debt"])
    if not exact.get("debt_capital") and logical_debt and exact.get("equity"):
        add("debt_capital", "derive_from_trusted_inputs", ["debt", "equity"])
    if not exact.get("ocf_debt") and exact.get("operating_cash_flow") and logical_debt:
        add("ocf_debt", "derive_from_trusted_inputs", ["operating_cash_flow", "debt"])
    if not exact.get("fcf_debt") and exact.get("operating_cash_flow") and exact.get("capex") and logical_debt:
        add("fcf_debt", "derive_from_trusted_inputs", ["operating_cash_flow", "capex", "debt"])
    if not exact.get("quick_ratio") and exact.get("current_assets") and exact.get("inventory") and exact.get("current_liabilities"):
        add("quick_ratio", "derive_from_trusted_inputs", ["current_assets", "inventory", "current_liabilities"])
    return ready


def field_missing(company: dict[str, Any]) -> list[str]:
    exact = company.get("exact") or {}
    return [f for f in HIGH_IMPACT_FIELDS if not exact.get(f)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory-json", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = load_report(Path(args.inventory_json))
    companies = report.get("companies") or []

    missing_counts = Counter()
    recovery_rows = []
    component_counts = Counter()
    error_companies = [c for c in companies if c.get("error")]

    for c in companies:
        if c.get("error"):
            continue
        missing = field_missing(c)
        for f in missing:
            missing_counts[f] += 1
        for r in deterministic_recovery(c):
            recovery_rows.append(r)
            component_counts[(r["field"], r["method"])] += 1

    classification_rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(classify_404, c): c for c in error_companies}
        for i, fut in enumerate(as_completed(futures), 1):
            c = futures[fut]
            try:
                classification_rows.append(fut.result())
            except Exception as exc:
                classification_rows.append({
                    "ticker": c.get("ticker"),
                    "cik": c.get("cik"),
                    "company_name": c.get("company_name"),
                    "market_cap": None,
                    "market_cap_bucket": "unknown",
                    "priority": "P3_unknown",
                    "is_otc": False,
                    "is_adr": False,
                    "is_foreign": False,
                    "classification": "classification_error",
                    "recovery_track": "classification_error",
                    "recovery_eligible": False,
                    "annual_domestic_filing": False,
                    "classification_error": f"{type(exc).__name__}:{exc}",
                })
            if i % 50 == 0 or i == len(error_companies):
                print(f"[RECOVERY-PLANNER] 404 classified {i}/{len(error_companies)}")

    classification_rows.sort(key=lambda x: (
        {"P0": 0, "P1": 1, "P2": 2, "P3_unknown": 3}.get(x.get("priority"), 9),
        -(x.get("market_cap") or 0),
        x.get("ticker") or "",
    ))

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eligible_companies": len(companies),
        "companyfacts_available": len(companies) - len(error_companies),
        "companyfacts_404": len(error_companies),
        "high_impact_missing_counts": dict(missing_counts.most_common()),
        "deterministic_recovery_count": len(recovery_rows),
        "deterministic_recovery_by_field": dict(Counter(r["field"] for r in recovery_rows)),
        "component_recovery_methods": {
            f"{k[0]}:{k[1]}": v for k, v in component_counts.items()
        },
        "404_priority_counts": dict(Counter(r["priority"] for r in classification_rows)),
        "404_classification_counts": dict(Counter(r["classification"] for r in classification_rows)),
        "404_recovery_track_counts": dict(Counter(r["recovery_track"] for r in classification_rows)),
        "404_recovery_eligible": sum(1 for r in classification_rows if r.get("recovery_eligible")),
        "404_bucket_counts": dict(Counter(r["market_cap_bucket"] for r in classification_rows)),
        "404_market_cap_known": sum(1 for r in classification_rows if r.get("market_cap")),
        "404_market_cap_unknown": sum(1 for r in classification_rows if not r.get("market_cap")),
    }

    (out / "us_recovery_planner_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "us_recovery_planner_summary.txt").write_text(
        "\n".join([
            "US TARGETED RECOVERY PLANNER",
            "=" * 72,
            f"Eligible companies: {summary['eligible_companies']:,}",
            f"Company Facts available: {summary['companyfacts_available']:,}",
            f"Company Facts 404: {summary['companyfacts_404']:,}",
            "",
            "HIGH-IMPACT MISSING COUNTS",
            *[f"{k:28s} {v:6d}" for k, v in summary["high_impact_missing_counts"].items()],
            "",
            f"Deterministic recovery opportunities: {summary['deterministic_recovery_count']:,}",
            "By field:",
            *[f"  {k:28s} {v:6d}" for k, v in summary["deterministic_recovery_by_field"].items()],
            "",
            "404 MARKET-CAP PRIORITY",
            *[f"{k:10s} {v:6d}" for k, v in summary["404_priority_counts"].items()],
            "",
            "404 MARKET-CAP BUCKET",
            *[f"{k:14s} {v:6d}" for k, v in summary["404_bucket_counts"].items()],
            "",
            f"404 market cap known: {summary['404_market_cap_known']:,}",
            f"404 market cap unknown: {summary['404_market_cap_unknown']:,}",
            f"SEC domestic recovery eligible: {summary['404_recovery_eligible']:,}",
            "",
            "404 RECOVERY TRACK",
            *[f"{k:40s} {v:6d}" for k, v in sorted(summary["404_recovery_track_counts"].items(), key=lambda kv: (-kv[1], kv[0]))],
            "",
            "404 CLASSIFICATION",
            *[f"{k:40s} {v:6d}" for k, v in sorted(summary["404_classification_counts"].items(), key=lambda kv: (-kv[1], kv[0]))],
        ]),
        encoding="utf-8",
    )

    with (out / "us_404_priority_queue.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        cols = [
            "priority","ticker","cik","company_name","market_cap","market_cap_bucket",
            "classification","recovery_track","recovery_eligible","annual_domestic_filing",
            "is_foreign","is_adr","is_otc",
            "yahoo_exchange","yahoo_country","yahoo_quote_type","recent_forms","market_cap_source",
        ]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in classification_rows:
            row = dict(row)
            row["recent_forms"] = "|".join(row.get("recent_forms") or [])
            w.writerow(row)

    with (out / "us_component_recovery_worklist.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        cols = ["ticker","field","method","inputs","confidence"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for row in recovery_rows:
            row = dict(row)
            row["inputs"] = "|".join(row["inputs"])
            w.writerow(row)

    with (out / "us_targeted_missing_fields.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        cols = ["ticker","cik","company_name","missing_fields","missing_count"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for c in companies:
            if c.get("error"):
                continue
            missing = field_missing(c)
            if not missing:
                continue
            w.writerow({
                "ticker": c.get("ticker"),
                "cik": c.get("cik"),
                "company_name": c.get("company_name"),
                "missing_fields": "|".join(missing),
                "missing_count": len(missing),
            })

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
