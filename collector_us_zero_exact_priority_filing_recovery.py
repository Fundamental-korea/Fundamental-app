"""Recover the zero-exact Company Facts priority set from SEC annual filings.

The priority set is limited to zero-exact companies whose Company Facts payload
contains metric-like US-GAAP concepts. This is a raw-source recovery only; no
scoring fields are mutated directly.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from supabase import create_client

from collector_us_gt1b_filing_recovery import annual_filing_candidates, fetch_filing_rows, build_rows

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--diagnostic-json", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    diag = json.loads(Path(args.diagnostic_json).read_text(encoding="utf-8"))
    companies = [
        c for c in diag.get("companies", [])
        if c.get("metric_like_fields")
    ]
    if len(companies) != 22:
        raise RuntimeError(f"Expected 22 metric-like zero-exact targets, got {len(companies)}")

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8
    resolver = SECXBRLSearchV2_3_8(user_agent=SEC_USER_AGENT, session=session)
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    recovered = []
    failures = []
    for i, company in enumerate(companies, 1):
        ticker = company.get("ticker")
        cik = str(company.get("cik") or "").zfill(10)
        try:
            submissions = resolver.submissions(cik)
            candidates = annual_filing_candidates(resolver, submissions, max_candidates=8)
            if not candidates:
                raise RuntimeError("No annual SEC filing in recent submissions or submission archives")
            annual_rows = []
            selected = None
            attempts = []
            for candidate in candidates:
                try:
                    rows, meta = fetch_filing_rows(resolver, cik, candidate)
                    candidate_rows = build_rows(company, submissions, rows, candidate) if rows else []
                except Exception as exc:
                    attempts.append({
                        "accession": candidate.get("accession"),
                        "form": candidate.get("form"),
                        "filed": candidate.get("filed"),
                        "error": f"{type(exc).__name__}:{exc}",
                    })
                    continue
                if candidate_rows:
                    annual_rows = candidate_rows
                    selected = meta
                    break
                attempts.append({
                    "accession": candidate.get("accession"),
                    "form": candidate.get("form"),
                    "filed": candidate.get("filed"),
                    "error": meta.get("reason"),
                })
            if not annual_rows:
                raise RuntimeError(f"Annual candidates exhausted: {json.dumps(attempts, ensure_ascii=False)}")

            # Do not overwrite richer filing recovery data with a thinner row set
            # from a later retry unless it is the same source accession/year.
            for j in range(0, len(annual_rows), 25):
                sb.table("US_Fundamental_Annual").upsert(
                    annual_rows[j:j + 25],
                    on_conflict="ticker,fiscal_year",
                ).execute()

            latest = max(annual_rows, key=lambda r: r["fiscal_year"])
            recovered.append({
                "ticker": ticker,
                "annual_rows": len(annual_rows),
                "latest_fiscal_year": latest["fiscal_year"],
                "latest_field_count": len(latest["canonical"]),
                "accession": selected.get("accession") if selected else None,
                "form": selected.get("form") if selected else None,
                "parser": selected.get("reason") if selected else None,
            })
        except Exception as exc:
            failures.append({
                "ticker": ticker,
                "cik": company.get("cik"),
                "error": f"{type(exc).__name__}:{exc}",
            })
        if i % 5 == 0 or i == len(companies):
            print(f"[ZERO-EXACT-RECOVERY] progress={i}/{len(companies)} recovered={len(recovered)} failures={len(failures)}")

    result = {
        "targets": len(companies),
        "recovered": len(recovered),
        "failures": len(failures),
        "recovered_detail": recovered,
        "failures_detail": failures,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "zero_exact_priority_recovery.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
