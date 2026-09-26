"""Collect a canonical annual raw SEC data layer for Company Facts-available issuers.

This worker is deliberately separate from scoring. It writes source values/components
plus provenance into US_Fundamental_Annual and never coerces missing values to zero.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from supabase import create_client

from collector_us_canonical_field_inventory import FIELD_SPECS, _candidate_rank, _rows_by_unit

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_MIN_REQUEST_INTERVAL = 0.25
_LAST_REQUEST = 0.0


def fetch_json(session: requests.Session, url: str, retries: int = 4):
    global _LAST_REQUEST
    last_exc = None
    for attempt in range(retries):
        wait = SEC_MIN_REQUEST_INTERVAL - (time.monotonic() - _LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST = time.monotonic()
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                delay = min(2 ** attempt, 16)
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = min(max(float(retry_after), 1.0), 30.0)
                    except ValueError:
                        pass
                time.sleep(delay)
                continue
            r.raise_for_status()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= retries - 1:
                raise
            time.sleep(min(2 ** attempt, 16))
    raise RuntimeError(f"SEC request failed after {retries} retries: {last_exc}")


def eligible_from_inventory(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    companies = [c for c in data.get("companies", []) if not c.get("error")]
    if not companies:
        raise RuntimeError("Inventory contains no Company Facts-available companies")
    return companies


def best_rows_by_year(facts_root: dict[str, Any], field: str, spec: dict[str, Any]) -> dict[int, dict[str, Any]]:
    found: dict[int, tuple[Any, str, str, str, dict[str, Any]]] = {}
    for namespace, nsfacts in (facts_root or {}).items():
        if not isinstance(nsfacts, dict):
            continue
        for alias_rank, tag in enumerate(spec["aliases"]):
            fact = nsfacts.get(tag)
            if not fact:
                continue
            for unit, row in _rows_by_unit(fact, spec["kind"]):
                end = str(row.get("end") or "")[:10]
                if len(end) != 10:
                    continue
                try:
                    year = int(end[:4])
                except ValueError:
                    continue
                key = _candidate_rank(namespace, alias_rank, row)
                prior = found.get(year)
                candidate = (key, namespace, tag, unit, row)
                if prior is None or candidate[0] > prior[0]:
                    found[year] = candidate
    return {
        year: {
            "namespace": namespace,
            "concept": tag,
            "unit": unit,
            "value": row.get("val"),
            "end": row.get("end"),
            "filed": row.get("filed"),
            "form": row.get("form"),
            "fy": row.get("fy"),
            "fp": row.get("fp"),
            "start": row.get("start"),
        }
        for year, (_, namespace, tag, unit, row) in found.items()
    }


def same_basis(values: dict[str, Any], keys: list[str]) -> bool:
    rows = [values.get(k) for k in keys]
    rows = [r for r in rows if r]
    if len(rows) != len(keys):
        return False
    ends = {r.get("end") for r in rows}
    units = {r.get("unit") for r in rows}
    return len(ends) == 1 and len(units) == 1


def build_company_rows(company: dict[str, Any], facts: dict[str, Any]) -> list[dict[str, Any]]:
    facts_root = facts.get("facts") or {}
    series = {
        field: best_rows_by_year(facts_root, field, spec)
        for field, spec in FIELD_SPECS.items()
    }

    years = sorted(set().union(*(set(rows.keys()) for rows in series.values())))
    output = []
    for year in years:
        canonical = {}
        provenance = {}
        for field, rows in series.items():
            hit = rows.get(year)
            if not hit:
                continue
            canonical[field] = hit["value"]
            provenance[field] = {
                "namespace": hit["namespace"],
                "concept": hit["concept"],
                "unit": hit["unit"],
                "end": hit["end"],
                "filed": hit["filed"],
                "form": hit["form"],
                "fy": hit["fy"],
                "fp": hit["fp"],
                "start": hit["start"],
            }

        # Logical debt: prefer a reported total, otherwise sum current+noncurrent
        # only when both are available on the same balance-sheet basis.
        debt_basis = None
        if canonical.get("debt_total") is not None:
            canonical["debt"] = canonical["debt_total"]
            debt_basis = "reported_total"
        elif same_basis(provenance, ["debt_current", "debt_noncurrent"]):
            canonical["debt"] = float(canonical["debt_current"]) + float(canonical["debt_noncurrent"])
            provenance["debt"] = {
                "method": "current_plus_noncurrent",
                "components": ["debt_current", "debt_noncurrent"],
                "end": provenance["debt_current"]["end"],
                "unit": provenance["debt_current"]["unit"],
            }
            debt_basis = "current_plus_noncurrent"

        # SG&A: direct total is preferred; G&A + selling is accepted only when the
        # filing provides both on the same basis.
        sga_basis = None
        if canonical.get("sga") is not None:
            sga_basis = "reported_total"
        elif same_basis(provenance, ["general_and_administrative_expense", "selling_expense"]):
            canonical["sga"] = (
                float(canonical["general_and_administrative_expense"])
                + float(canonical["selling_expense"])
            )
            provenance["sga"] = {
                "method": "g_and_a_plus_selling",
                "components": ["general_and_administrative_expense", "selling_expense"],
                "end": provenance["general_and_administrative_expense"]["end"],
                "unit": provenance["general_and_administrative_expense"]["unit"],
            }
            sga_basis = "g_and_a_plus_selling"

        # A negative cash-flow capex is retained exactly as sourced. Downstream FCF
        # logic can decide whether the statement convention is outflow-negative.
        completeness = {
            "field_count": len(canonical),
            "debt_basis": debt_basis,
            "sga_basis": sga_basis,
            "has_growth_pair": (
                year - 1 in series.get("revenue", {})
                or year - 1 in series.get("eps", {})
            ),
        }

        period_end = None
        filed = None
        form = None
        for priority in ("revenue", "net_income", "assets", "equity", "cash"):
            p = provenance.get(priority)
            if p:
                period_end = p.get("end")
                filed = p.get("filed")
                form = p.get("form")
                break

        output.append({
            "ticker": company.get("ticker"),
            "cik": str(company.get("cik") or ""),
            "company_name": company.get("company_name"),
            "fiscal_year": int(year),
            "period_end": period_end,
            "filed": filed,
            "form": form,
            "fiscal_period": provenance.get("revenue", {}).get("fp"),
            "source_kind": "sec_companyfacts",
            "canonical": canonical,
            "provenance": provenance,
            "completeness": completeness,
            "recovery_status": "collected",
            "recovery_notes": {},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    return output


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory-json", required=True)
    ap.add_argument("--batch", type=int, required=True)
    ap.add_argument("--batch-size", type=int, default=500)
    args = ap.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    companies = eligible_from_inventory(Path(args.inventory_json))
    start = args.batch * args.batch_size
    batch = companies[start:start + args.batch_size]
    if not batch:
        print(f"[RAW-LAYER] Empty batch {args.batch}")
        return

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    all_rows = []
    failures = []
    for i, company in enumerate(batch, 1):
        try:
            cik = str(company.get("cik") or "").zfill(10)
            facts = fetch_json(session, SEC_FACTS_URL.format(cik=cik))
            rows = build_company_rows(company, facts)
            all_rows.extend(rows)
        except Exception as exc:
            failures.append({
                "ticker": company.get("ticker"),
                "cik": company.get("cik"),
                "error": f"{type(exc).__name__}:{exc}",
            })
        if i % 25 == 0 or i == len(batch):
            print(f"[RAW-LAYER] batch={args.batch} progress={i}/{len(batch)} rows={len(all_rows)}")

    # Supabase/PostgREST payloads are kept comfortably below request-size limits.
    for chunk_start in range(0, len(all_rows), 100):
        sb.table("US_Fundamental_Annual").upsert(
            all_rows[chunk_start:chunk_start + 100],
            on_conflict="ticker,fiscal_year",
        ).execute()

    summary = {
        "batch": args.batch,
        "requested_companies": len(batch),
        "companies_with_rows": len({r["ticker"] for r in all_rows}),
        "annual_rows_upserted": len(all_rows),
        "failures": failures,
    }
    out = Path("/tmp/raw_layer")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"batch-{args.batch}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
