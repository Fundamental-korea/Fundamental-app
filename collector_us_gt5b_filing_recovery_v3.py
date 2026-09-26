"""Phase-1 SEC filing recovery for the highest-value Company-Facts-404 issuers.

This is intentionally conservative:
- phase 1 processes only the >$5B slice (166 current targets);
- every source is tied to the queue CIK + SEC accession;
- filing entity CIK is checked when DEI is present;
- primary Inline XBRL is tried first, then multiple filing XBRL/XML instances;
- data is merged field-by-field into the annual raw layer rather than blindly
  replacing a richer existing record;
- no value is coerced to zero and no score fields are written.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from lxml import html
from supabase import create_client

from collector_us_canonical_field_inventory import FIELD_SPECS
from collector_us_gt1b_filing_recovery import (
    SEC_SUBMISSIONS_URL,
    ANNUAL_FORMS,
    annual_filing_candidates,
    fetch_filing_rows,
    valid_date,
    build_rows,
)
from sec_xbrl_search import _local as xbrl_local
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"


def local_name(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def compact_cik(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return str(int(digits)) if digits else ""


def extract_entity_metadata(document_text: str | bytes) -> dict[str, Any]:
    """Read DEI entity facts without storing the filing body."""
    payload = document_text.encode("utf-8") if isinstance(document_text, str) else document_text
    out: dict[str, Any] = {}
    try:
        root = html.fromstring(payload)
        for e in root.iter():
            name = local_name(e.tag)
            if name in {"EntityCentralIndexKey", "EntityRegistrantName"}:
                text = " ".join("".join(e.itertext()).split()).strip()
                if text:
                    out[name] = text
    except Exception:
        return out
    return out


def extract_entity_metadata_xml(xml_text: str | bytes) -> dict[str, Any]:
    payload = xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text
    out: dict[str, Any] = {}
    try:
        root = ET.fromstring(payload)
        for e in root.iter():
            name = local_name(e.tag)
            if name in {"EntityCentralIndexKey", "EntityRegistrantName"}:
                text = " ".join((e.text or "").split()).strip()
                if text:
                    out[name] = text
    except Exception:
        return out
    return out


def candidate_instance_files(index: dict[str, Any], primary_document: str | None) -> list[str]:
    names = [
        x.get("name")
        for x in index.get("directory", {}).get("item", [])
        if isinstance(x, dict) and x.get("name")
    ]
    primary_stem = re.sub(r"\.[^.]+$", "", primary_document or "").lower()
    out: list[str] = []
    for name in names:
        low = name.lower()
        if not low.endswith(".xml"):
            continue
        if any(token in low for token in (
            "_cal.xml", "_def.xml", "_lab.xml", "_pre.xml", "_ref.xml",
            "filingsummary", "schema", "presentation", "definition",
            "calculation", "label", "reference",
        )):
            continue
        if low.endswith(".xsd"):
            continue
        out.append(name)

    def rank(name: str) -> tuple[int, int, int]:
        low = name.lower()
        stem = 0 if primary_stem and low.startswith(primary_stem) else 1
        hint = 0 if any(k in low for k in ("instance", "xbrl", "20f", "10k", "40f")) else 1
        return (stem, hint, -len(name))

    return sorted(dict.fromkeys(out), key=rank)[:5]


def parse_instance_rows(resolver: SECXBRLSearchV2_3_8, url: str, labels: dict[str, str],
                        filed: str | None, form: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    response = resolver._get(url)
    text = response.text
    rows = resolver._parse_instance(text, labels)
    for row in rows:
        row["filed"] = filed
        row["form"] = form
        end = valid_date(row.get("end"))
        row["fy"] = end.year if end else None
    return rows, extract_entity_metadata_xml(text)


def parse_labels_safe(resolver: SECXBRLSearchV2_3_8, index: dict[str, Any],
                      compact_accession: str, cik: str) -> tuple[dict[str, str], str | None]:
    label_file = resolver._choose_label_file(index)
    if not label_file:
        return {}, None
    url = f"{SEC_ARCHIVE}/{int(cik)}/{compact_accession}/{label_file}"
    try:
        return resolver._parse_labels(resolver._get(url).text), label_file
    except Exception:
        return {}, label_file


def choose_candidate_rows(
    resolver: SECXBRLSearchV2_3_8,
    cik: str,
    candidate: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    accession = candidate.get("accession")
    primary_document = candidate.get("primary_document")
    filed = candidate.get("filed")
    form = candidate.get("form") or ""
    if not accession or not primary_document or form not in ANNUAL_FORMS:
        return [], {"used": False, "reason": "missing_or_invalid_annual_filing_metadata"}

    index, compact = resolver.filing_index(cik, accession)
    labels, label_file = parse_labels_safe(resolver, index, compact, cik)

    # First: primary filing HTML / Inline XBRL.
    primary_url = f"{SEC_ARCHIVE}/{int(cik)}/{compact}/{primary_document}"
    try:
        primary_response = resolver._get(primary_url)
        entity_meta = extract_entity_metadata(primary_response.text)
        rows = []
        from sec_xbrl_inline import parse_inline_xbrl
        rows = parse_inline_xbrl(
            primary_response.text,
            labels=labels,
            filed=filed,
            form=form,
        )
        for row in rows:
            row["form"] = form
            row["filed"] = filed
            end = valid_date(row.get("end"))
            row["fy"] = end.year if end else None
        if rows:
            return rows, {
                "used": True,
                "reason": "inline_xbrl_primary_document",
                "accession": accession,
                "primary_document": primary_document,
                "filed": filed,
                "form": form,
                "label_file": label_file,
                "instance": None,
                "entity_meta": entity_meta,
                "concept_count": len({r.get("concept") for r in rows}),
            }
    except Exception:
        pass

    # Second: try several likely XBRL instance XMLs instead of guessing one.
    attempts: list[dict[str, Any]] = []
    entity_meta: dict[str, Any] = {}
    best_rows: list[dict[str, Any]] = []
    best_name: str | None = None
    for instance in candidate_instance_files(index, primary_document):
        url = f"{SEC_ARCHIVE}/{int(cik)}/{compact}/{instance}"
        try:
            rows, em = parse_instance_rows(resolver, url, labels, filed, form)
            if em:
                entity_meta = em
            if len(rows) > len(best_rows):
                best_rows = rows
                best_name = instance
            attempts.append({"instance": instance, "rows": len(rows)})
            # A numeric instance with a useful fact count is enough to proceed.
            if len(rows) >= 100:
                break
        except Exception as exc:
            attempts.append({
                "instance": instance,
                "rows": 0,
                "error": f"{type(exc).__name__}:{exc}",
            })

    return best_rows, {
        "used": bool(best_rows),
        "reason": "xbrl_instance" if best_rows else "no_numeric_xbrl_instance",
        "accession": accession,
        "primary_document": primary_document,
        "filed": filed,
        "form": form,
        "label_file": label_file,
        "instance": best_name,
        "entity_meta": entity_meta,
        "instance_attempts": attempts,
        "concept_count": len({r.get("concept") for r in best_rows}),
    }


def merge_row(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Merge only missing fields. Existing field values/provenance win."""
    if not existing:
        return incoming, len(incoming.get("canonical") or {})

    out = dict(existing)
    old_c = dict(existing.get("canonical") or {})
    old_p = dict(existing.get("provenance") or {})
    new_c = dict(incoming.get("canonical") or {})
    new_p = dict(incoming.get("provenance") or {})
    added = 0

    for field, value in new_c.items():
        if field in old_c and old_c.get(field) is not None:
            continue
        if value is None:
            continue
        old_c[field] = value
        if field in new_p:
            old_p[field] = new_p[field]
        added += 1

    out["canonical"] = old_c
    out["provenance"] = old_p
    out["completeness"] = dict(incoming.get("completeness") or {}, field_count=len(old_c))
    out["source_kind"] = existing.get("source_kind") or incoming.get("source_kind")
    out["source_accession"] = existing.get("source_accession") or incoming.get("source_accession")
    out["source_document"] = existing.get("source_document") or incoming.get("source_document")
    out["period_end"] = existing.get("period_end") or incoming.get("period_end")
    out["filed"] = existing.get("filed") or incoming.get("filed")
    out["form"] = existing.get("form") or incoming.get("form")
    out["fiscal_period"] = existing.get("fiscal_period") or incoming.get("fiscal_period")
    out["recovery_status"] = existing.get("recovery_status") or "filing_recovered"
    notes = dict(existing.get("recovery_notes") or {})
    notes.update({k: v for k, v in (incoming.get("recovery_notes") or {}).items() if v not in (None, "")})
    out["recovery_notes"] = notes
    out["updated_at"] = datetime.now(timezone.utc).isoformat()
    return out, added


def safe_existing_rows(sb, ticker: str) -> dict[int, dict[str, Any]]:
    response = sb.table("US_Fundamental_Annual").select(
        "*"
    ).eq("ticker", ticker).execute()
    return {
        int(row["fiscal_year"]): row
        for row in (response.data or [])
        if row.get("fiscal_year") is not None
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue-csv", required=True)
    ap.add_argument("--bucket", default="over_5b")
    ap.add_argument("--expected-targets", type=int, default=166)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    queue: list[dict[str, Any]] = []
    with open(args.queue_csv, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            if row.get("market_cap_bucket") == args.bucket:
                queue.append(row)

    if len(queue) != args.expected_targets:
        raise RuntimeError(
            f"Expected {args.expected_targets} {args.bucket} targets, got {len(queue)}"
        )

    session = requests.Session()
    session.headers.update({
        "User-Agent": SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    })
    resolver = SECXBRLSearchV2_3_8(user_agent=SEC_USER_AGENT, session=session)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    recovered: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for i, company in enumerate(queue, 1):
        ticker = company.get("ticker")
        cik = str(company.get("cik") or "").zfill(10)
        try:
            submissions = resolver.submissions(cik)
            candidates = annual_filing_candidates(resolver, submissions, max_candidates=10)
            if not candidates:
                raise RuntimeError("No annual SEC filing found")

            annual_rows: list[dict[str, Any]] = []
            selected_meta: dict[str, Any] | None = None
            attempted: list[dict[str, Any]] = []

            for candidate in candidates:
                try:
                    rows, meta = choose_candidate_rows(resolver, cik, candidate)
                except Exception as exc:
                    attempted.append({
                        "accession": candidate.get("accession"),
                        "form": candidate.get("form"),
                        "filed": candidate.get("filed"),
                        "error": f"{type(exc).__name__}:{exc}",
                    })
                    continue

                if not rows:
                    attempted.append({
                        "accession": candidate.get("accession"),
                        "form": candidate.get("form"),
                        "filed": candidate.get("filed"),
                        "error": meta.get("reason"),
                    })
                    continue

                entity_cik = compact_cik(meta.get("entity_meta", {}).get("EntityCentralIndexKey"))
                if entity_cik and entity_cik != str(int(cik)):
                    attempted.append({
                        "accession": candidate.get("accession"),
                        "form": candidate.get("form"),
                        "filed": candidate.get("filed"),
                        "error": f"entity_cik_mismatch:{entity_cik}!={int(cik)}",
                    })
                    continue

                filing_meta = {
                    "accession": meta.get("accession"),
                    "primary_document": meta.get("primary_document"),
                    "filed": meta.get("filed"),
                }
                candidate_rows = build_rows(company, submissions, rows, filing_meta)
                if candidate_rows:
                    annual_rows = candidate_rows
                    selected_meta = meta
                    break

                attempted.append({
                    "accession": candidate.get("accession"),
                    "form": candidate.get("form"),
                    "filed": candidate.get("filed"),
                    "error": "parsed but no canonical fields matched",
                })

            if not annual_rows:
                raise RuntimeError(
                    "Annual candidates exhausted: " + json.dumps(attempted, ensure_ascii=False)
                )

            existing = safe_existing_rows(sb, ticker)
            merged_rows: list[dict[str, Any]] = []
            fields_added = 0
            for incoming in annual_rows:
                fy = int(incoming["fiscal_year"])
                merged, added = merge_row(existing.get(fy), incoming)
                merged["recovery_notes"] = dict(
                    merged.get("recovery_notes") or {},
                    phase="B3_gt5b",
                    entity_verified=True,
                    filing_entity_name=(selected_meta or {}).get("entity_meta", {}).get("EntityRegistrantName"),
                )
                merged_rows.append(merged)
                fields_added += added

            # Small chunks avoid another Supabase statement-timeout incident.
            for start in range(0, len(merged_rows), 20):
                sb.table("US_Fundamental_Annual").upsert(
                    merged_rows[start:start + 20],
                    on_conflict="ticker,fiscal_year",
                ).execute()

            latest = max(merged_rows, key=lambda r: r["fiscal_year"])
            recovered.append({
                "ticker": ticker,
                "cik": cik,
                "market_cap": float(company["market_cap"]) if company.get("market_cap") else None,
                "annual_rows": len(merged_rows),
                "latest_fiscal_year": latest["fiscal_year"],
                "latest_field_count": len(latest.get("canonical") or {}),
                "fields_added": fields_added,
                "accession": (selected_meta or {}).get("accession"),
                "form": (selected_meta or {}).get("form"),
                "parser": (selected_meta or {}).get("reason"),
                "entity_name": (selected_meta or {}).get("entity_meta", {}).get("EntityRegistrantName"),
            })
        except Exception as exc:
            failures.append({
                "ticker": ticker,
                "cik": cik,
                "market_cap": company.get("market_cap"),
                "error": f"{type(exc).__name__}:{exc}",
            })

        if i % 10 == 0 or i == len(queue):
            print(
                f"[B3-GT5B] progress={i}/{len(queue)} "
                f"recovered={len(recovered)} failures={len(failures)}"
            )

    result = {
        "phase": "B3_gt5b",
        "bucket": args.bucket,
        "targets": len(queue),
        "recovered": len(recovered),
        "failures": len(failures),
        "recovered_detail": recovered,
        "failures_detail": failures,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "us_gt5b_filing_recovery_v3.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({
        "phase": result["phase"],
        "targets": result["targets"],
        "recovered": result["recovered"],
        "failures": result["failures"],
        "total_fields_added": sum(x.get("fields_added", 0) for x in recovered),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
