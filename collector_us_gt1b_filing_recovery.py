"""Recover the >$1B Company-Facts-404 queue from SEC annual Inline-XBRL.

Only P0/P1 companies from the recovery planner are processed (249 in the
current planner result). Values are written to the canonical annual raw layer,
never directly fabricated into scoring fields.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import create_client

from collector_us_canonical_field_inventory import FIELD_SPECS
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}


def local_concept(value: Any) -> str:
    return str(value or "").rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def valid_date(v: Any):
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except Exception:
        return None


def valid_row(row: dict[str, Any], kind: str) -> bool:
    end = valid_date(row.get("end"))
    if not end or end > datetime.now(timezone.utc).date():
        return False
    filed = valid_date(row.get("filed"))
    if filed and filed < end:
        return False
    if (row.get("form") or "") not in ANNUAL_FORMS:
        return False
    start = row.get("start")
    if kind == "instant":
        return not bool(start)
    if not start:
        return False
    sd = valid_date(start)
    if not sd or sd > end:
        return False
    return 300 <= (end - sd).days <= 380


def select_field_year(rows: list[dict[str, Any]], field: str, spec: dict[str, Any]) -> dict[int, dict[str, Any]]:
    aliases = list(spec.get("aliases") or [])
    alias_rank = {a: i for i, a in enumerate(aliases)}
    candidates = []
    for r in rows:
        if not valid_row(r, spec["kind"]):
            continue
        concept = local_concept(r.get("concept"))
        if concept not in alias_rank:
            continue
        end = valid_date(r.get("end"))
        if not end:
            continue
        ns_rank = {"us-gaap": 3, "ifrs-full": 2}.get(str(r.get("namespace") or ""), 1)
        amendment = 1 if str(r.get("form") or "").endswith("/A") else 0
        candidates.append((
            end.year,
            (ns_rank, -alias_rank[concept], str(r.get("filed") or ""), amendment),
            r,
        ))
    best: dict[int, tuple[Any, dict[str, Any]]] = {}
    for year, rank, row in candidates:
        prior = best.get(year)
        if prior is None or rank > prior[0]:
            best[year] = (rank, row)
    out = {}
    for year, (_, row) in best.items():
        out[year] = {
            "namespace": row.get("namespace"),
            "concept": local_concept(row.get("concept")),
            "unit": row.get("unit"),
            "value": row.get("value"),
            "end": row.get("end"),
            "filed": row.get("filed"),
            "form": row.get("form"),
            "fy": row.get("fy"),
            "fp": row.get("fp"),
            "start": row.get("start"),
            "context_ref": row.get("contextRef"),
        }
    return out


def same_basis(prov: dict[str, Any], keys: list[str]) -> bool:
    vals = [prov.get(k) for k in keys]
    if any(v is None for v in vals):
        return False
    return len({v.get("end") for v in vals}) == 1 and len({v.get("unit") for v in vals}) == 1


def build_rows(company: dict[str, Any], submissions: dict[str, Any], rows: list[dict[str, Any]], meta: dict[str, Any]) -> list[dict[str, Any]]:
    series = {
        field: select_field_year(rows, field, spec)
        for field, spec in FIELD_SPECS.items()
    }
    years = sorted(set().union(*(set(s.keys()) for s in series.values())))
    out = []
    for year in years:
        canonical = {}
        provenance = {}
        for field, s in series.items():
            hit = s.get(year)
            if not hit:
                continue
            canonical[field] = hit["value"]
            provenance[field] = {k: hit.get(k) for k in (
                "namespace", "concept", "unit", "end", "filed", "form", "fy", "fp", "start", "context_ref"
            )}

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

        sga_basis = None
        if canonical.get("sga") is not None:
            sga_basis = "reported_total"
        elif same_basis(provenance, ["general_and_administrative_expense", "selling_expense"]):
            canonical["sga"] = float(canonical["general_and_administrative_expense"]) + float(canonical["selling_expense"])
            provenance["sga"] = {
                "method": "g_and_a_plus_selling",
                "components": ["general_and_administrative_expense", "selling_expense"],
                "end": provenance["general_and_administrative_expense"]["end"],
                "unit": provenance["general_and_administrative_expense"]["unit"],
            }
            sga_basis = "g_and_a_plus_selling"

        period_end = None
        filed = None
        form = None
        for preferred in ("revenue", "assets", "equity", "net_income"):
            p = provenance.get(preferred)
            if p:
                period_end, filed, form = p.get("end"), p.get("filed"), p.get("form")
                break

        completeness = {
            "field_count": len(canonical),
            "debt_basis": debt_basis,
            "sga_basis": sga_basis,
            "source_filing_accession": meta.get("accession"),
            "source_primary_document": meta.get("primary_document"),
        }
        out.append({
            "ticker": company.get("ticker"),
            "cik": str(company.get("cik") or ""),
            "company_name": company.get("company_name"),
            "fiscal_year": int(year),
            "period_end": period_end,
            "filed": filed,
            "form": form,
            "fiscal_period": provenance.get("revenue", {}).get("fp"),
            "source_kind": "sec_filing_inline_xbrl",
            "source_accession": meta.get("accession"),
            "source_document": meta.get("primary_document"),
            "canonical": canonical,
            "provenance": provenance,
            "completeness": completeness,
            "recovery_status": "filing_recovered",
            "recovery_notes": {
                "market_cap": company.get("market_cap"),
                "market_cap_bucket": company.get("market_cap_bucket"),
                "classification": company.get("classification"),
                "is_foreign": company.get("is_foreign"),
                "is_adr": company.get("is_adr"),
                "is_otc": company.get("is_otc"),
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue-csv", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")
    queue = []
    with open(args.queue_csv, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if row.get("market_cap_bucket") in {"over_5b", "1b_to_5b"}:
                queue.append(row)

    if len(queue) != 249:
        raise RuntimeError(f"Expected 249 >=$1B targets from the current planner, got {len(queue)}")

    session = __import__("requests").Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    resolver = SECXBRLSearchV2_3_8(user_agent=SEC_USER_AGENT, session=session)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    summaries = []
    failures = []
    for i, company in enumerate(queue, 1):
        try:
            cik = str(company["cik"]).zfill(10)
            submissions = resolver.submissions(cik)
            accession, primary_document, filed = resolver.latest_annual_filing(submissions)
            if not accession:
                raise RuntimeError("No annual SEC filing in submissions")
            rows, meta = resolver._inline_filing_rows(cik, submissions)
            filing_meta = {
                "accession": accession,
                "primary_document": primary_document,
                "filed": filed,
            }
            annual_rows = build_rows(company, submissions, rows, filing_meta)
            if not annual_rows:
                raise RuntimeError("Annual filing parsed but no canonical fields matched")
            for j in range(0, len(annual_rows), 100):
                sb.table("US_Fundamental_Annual").upsert(
                    annual_rows[j:j + 100],
                    on_conflict="ticker,fiscal_year",
                ).execute()
            summaries.append({
                "ticker": company["ticker"],
                "market_cap": float(company["market_cap"]) if company.get("market_cap") else None,
                "annual_rows": len(annual_rows),
                "fields_latest": len(max(annual_rows, key=lambda r: r["fiscal_year"])["canonical"]),
                "accession": accession,
            })
        except Exception as exc:
            failures.append({
                "ticker": company.get("ticker"),
                "cik": company.get("cik"),
                "market_cap": company.get("market_cap"),
                "error": f"{type(exc).__name__}:{exc}",
            })
        if i % 10 == 0 or i == len(queue):
            print(f"[FILING-RECOVERY] progress={i}/{len(queue)} recovered={len(summaries)} failures={len(failures)}")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result = {
        "targets": len(queue),
        "recovered": len(summaries),
        "failures": len(failures),
        "summary": summaries,
        "failures_detail": failures,
    }
    (out / "us_gt1b_filing_recovery_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

# Trigger corrected >=$1B filing-recovery run.
