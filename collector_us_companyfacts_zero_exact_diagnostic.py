"""Diagnose Company Facts-available issuers that matched zero canonical aliases.

This is read-only. It identifies whether the 155 companies have usable SEC
facts, annual submissions, and metric-like concepts before any recovery mapping
is promoted into production.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests

SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
SEC_MIN_INTERVAL = 0.25
_LAST_REQUEST = 0.0

FIELD_TERMS = {
    "revenue": ("revenue", "sales"),
    "operating_income": ("operatingincome", "incomeoperations", "operatingprofit"),
    "net_income": ("netincome", "profitloss", "netearnings"),
    "assets": ("assets",),
    "equity": ("equity", "capital"),
    "liabilities": ("liabilities",),
    "current_assets": ("assetscurrent", "currentassets"),
    "current_liabilities": ("liabilitiescurrent", "currentliabilities"),
    "receivables": ("receivable", "tradeandotherreceivable"),
    "inventory": ("inventory", "inventories"),
    "debt": ("debt", "borrowings", "loanspayable", "notespayable"),
    "interest_expense": ("interestexpense", "financecost", "borrowingcost"),
    "operating_cash_flow": ("operatingactivities", "cashflowfromoperations"),
    "capex": ("propertyplantandequipment", "capitalexpenditure"),
    "sga": ("sellinggeneral", "administrativeexpense", "sellingexpense"),
    "pretax_income": ("beforeincometax", "incomebeforetax", "profitbeforetax"),
    "tax_expense": ("incometaxexpense", "taxexpense"),
    "eps": ("earningspershare",),
    "shares": ("weightedaveragenumberof", "sharesoutstanding"),
    "dividends": ("dividend",),
    "depreciation": ("depreciation", "amortization"),
    "stock_comp": ("sharebasedcompensation", "stockbasedcompensation"),
}

def norm(v: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(v or "").lower())

def sec_get(session: requests.Session, url: str, retries: int = 4):
    global _LAST_REQUEST
    last = None
    for attempt in range(retries):
        wait = SEC_MIN_INTERVAL - (time.monotonic() - _LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST = time.monotonic()
        try:
            r = session.get(url, timeout=45)
            if r.status_code == 200:
                return r.json()
            last = r
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(min(2 ** attempt, 16))
                continue
            r.raise_for_status()
        except requests.RequestException:
            if attempt >= retries - 1:
                raise
            time.sleep(min(2 ** attempt, 16))
    raise RuntimeError(f"SEC request failed: {url}; last={getattr(last, 'status_code', None)}")

def annual_forms(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    recent = submissions.get("filings", {}).get("recent", {}) or {}
    out = []
    for i, form in enumerate(recent.get("form", []) or []):
        if form not in ANNUAL_FORMS:
            continue
        out.append({
            "form": form,
            "accession": (recent.get("accessionNumber", []) or [None])[i],
            "primary_document": (recent.get("primaryDocument", []) or [None])[i],
            "filed": (recent.get("filingDate", []) or [None])[i],
        })
    return out

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory-json", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    data = json.loads(Path(args.inventory_json).read_text(encoding="utf-8"))
    companies = [
        c for c in data.get("companies", [])
        if not c.get("error") and not any(bool(v) for v in (c.get("exact") or {}).values())
    ]
    if len(companies) != 155:
        raise RuntimeError(f"Expected 155 zero-exact companies, got {len(companies)}")

    session = requests.Session()
    session.headers.update({
        "User-Agent": os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com"),
        "Accept-Encoding": "gzip, deflate",
    })

    results = []
    for i, company in enumerate(companies, 1):
        cik = str(company.get("cik") or "").zfill(10)
        row = {
            "ticker": company.get("ticker"),
            "cik": company.get("cik"),
            "company_name": company.get("company_name"),
            "company_type": company.get("company_type"),
            "facts_http_ok": False,
            "facts_namespaces": {},
            "facts_tag_count": 0,
            "annual_recent_count": 0,
            "annual_recent_forms": [],
            "metric_like_fields": [],
            "metric_like_examples": [],
            "classification": None,
            "error": None,
        }
        try:
            facts = sec_get(session, SEC_FACTS_URL.format(cik=cik))
            row["facts_http_ok"] = True
            facts_root = facts.get("facts") or {}
            tag_counts = {}
            metric_hits: dict[str, list[str]] = {k: [] for k in FIELD_TERMS}
            for namespace, nsfacts in facts_root.items():
                if not isinstance(nsfacts, dict):
                    continue
                tag_counts[namespace] = len(nsfacts)
                for tag in nsfacts:
                    nt = norm(tag)
                    for field, terms in FIELD_TERMS.items():
                        if any(term in nt for term in terms):
                            if len(metric_hits[field]) < 12:
                                metric_hits[field].append(tag)
            row["facts_namespaces"] = tag_counts
            row["facts_tag_count"] = sum(tag_counts.values())
            row["metric_like_fields"] = [k for k, v in metric_hits.items() if v]
            row["metric_like_examples"] = {
                k: v for k, v in metric_hits.items() if v
            }

            submissions = sec_get(session, SEC_SUBMISSIONS_URL.format(cik=cik))
            forms = annual_forms(submissions)
            row["annual_recent_count"] = len(forms)
            row["annual_recent_forms"] = forms[:8]

            if not facts_root:
                row["classification"] = "facts_empty"
            elif not row["metric_like_fields"]:
                row["classification"] = "facts_nonfinancial_or_custom_only"
            elif not forms:
                row["classification"] = "facts_present_but_no_recent_annual_form"
            elif any(ns == "ifrs-full" for ns in facts_root):
                row["classification"] = "ifrs_or_mixed_taxonomy"
            else:
                row["classification"] = "sec_facts_recoverable_mapping"
        except Exception as exc:
            row["classification"] = "fetch_error"
            row["error"] = f"{type(exc).__name__}:{exc}"
        results.append(row)
        if i % 10 == 0 or i == len(companies):
            print(f"[ZERO-EXACT-DIAG] progress={i}/{len(companies)}")

    summary = {
        "targets": len(companies),
        "classification_counts": dict(Counter(r["classification"] for r in results)),
        "facts_http_ok": sum(bool(r["facts_http_ok"]) for r in results),
        "facts_nonempty": sum(bool(r["facts_tag_count"]) for r in results),
        "with_recent_annual_form": sum(bool(r["annual_recent_count"]) for r in results),
        "with_metric_like_fields": sum(bool(r["metric_like_fields"]) for r in results),
        "by_metric_like_field": dict(Counter(
            field for r in results for field in r["metric_like_fields"]
        )),
    }

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "zero_exact_companyfacts_diagnostic.json").write_text(
        json.dumps({"summary": summary, "companies": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / "zero_exact_companyfacts_diagnostic.txt").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
