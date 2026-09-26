"""Read-only internal consistency QA for the US company universe.

This does not modify Supabase. It validates the relationship between
US_Companies, US_Fundamental, and the manual classification override table
before any eligibility mutation is considered.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
PAGE_SIZE = 1000
OUT = Path("artifacts")
OUT.mkdir(exist_ok=True)


def fetch_all(sb, table: str, columns: str):
    rows = []
    offset = 0
    while True:
        page = (
            sb.table(table)
            .select(columns)
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def main():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    companies = fetch_all(
        sb,
        "US_Companies",
        "ticker,cik,company_name,entity_type,exchange,security_type,is_active,"
        "is_fundamental_eligible,exclusion_reason,company_type,scoring_profile,"
        "sector_common,sector_raw",
    )
    fundamentals = fetch_all(
        sb,
        "US_Fundamental",
        "ticker,cik,company_name,base_year,data_unavailable",
    )
    overrides = fetch_all(
        sb,
        "US_Company_Classification_Overrides",
        "ticker,company_type,scoring_profile,is_fundamental_eligible,reason,"
        "source_url,reviewed_at,active",
    )

    company_by_ticker = {r["ticker"]: r for r in companies}
    fundamental_tickers = {r["ticker"] for r in fundamentals}

    eligible = [r for r in companies if r.get("is_fundamental_eligible")]
    ineligible = [r for r in companies if not r.get("is_fundamental_eligible")]

    eligible_missing_fundamental = sorted(
        r["ticker"] for r in eligible if r["ticker"] not in fundamental_tickers
    )
    fundamental_without_company = sorted(
        r["ticker"] for r in fundamentals if r["ticker"] not in company_by_ticker
    )
    fundamental_on_ineligible = sorted(
        r["ticker"]
        for r in fundamentals
        if r["ticker"] in company_by_ticker
        and not company_by_ticker[r["ticker"]].get("is_fundamental_eligible")
    )

    override_active = [r for r in overrides if r.get("active", True)]
    override_conflicts = []
    override_missing_reason = []
    for r in override_active:
        local = company_by_ticker.get(r["ticker"])
        if not local:
            continue
        forced = r.get("is_fundamental_eligible")
        current = local.get("is_fundamental_eligible")
        if forced is not None and forced != current:
            override_conflicts.append(
                {
                    "ticker": r["ticker"],
                    "override_eligible": forced,
                    "current_eligible": current,
                    "reason": r.get("reason"),
                }
            )
        if not (r.get("reason") or "").strip():
            override_missing_reason.append(r["ticker"])

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "company_total": len(companies),
        "eligible": len(eligible),
        "ineligible": len(ineligible),
        "active_false": sum(1 for r in companies if r.get("is_active") is False),
        "eligible_missing_company_type": sum(
            1 for r in eligible if not r.get("company_type")
        ),
        "eligible_missing_scoring_profile": sum(
            1 for r in eligible if not r.get("scoring_profile")
        ),
        "eligible_missing_sector_common": sum(
            1 for r in eligible if not r.get("sector_common")
        ),
        "ineligible_missing_exclusion_reason": sum(
            1 for r in ineligible if not r.get("exclusion_reason")
        ),
        "fundamental_total": len(fundamentals),
        "eligible_missing_fundamental": len(eligible_missing_fundamental),
        "fundamental_without_company": len(fundamental_without_company),
        "fundamental_on_ineligible": len(fundamental_on_ineligible),
        "fundamental_unavailable": sum(
            1 for r in fundamentals if r.get("data_unavailable")
        ),
        "active_overrides": len(override_active),
        "override_forced_eligible": sum(
            1 for r in override_active if r.get("is_fundamental_eligible") is True
        ),
        "override_forced_ineligible": sum(
            1 for r in override_active if r.get("is_fundamental_eligible") is False
        ),
        "override_conflicts": len(override_conflicts),
        "override_missing_reason": len(override_missing_reason),
    }

    report = {
        "summary": summary,
        "company_type_distribution": dict(Counter(
            (r.get("company_type") or "<NULL>") for r in companies
        )),
        "profile_distribution": dict(Counter(
            (r.get("scoring_profile") or "<NULL>") for r in companies
        )),
        "ineligible_reason_distribution": dict(Counter(
            (r.get("exclusion_reason") or "<NULL>") for r in ineligible
        )),
        "eligible_missing_fundamental_tickers": eligible_missing_fundamental,
        "fundamental_without_company_tickers": fundamental_without_company,
        "fundamental_on_ineligible_tickers": fundamental_on_ineligible,
        "override_conflicts": override_conflicts,
        "override_missing_reason_tickers": sorted(override_missing_reason),
    }

    out = OUT / "us_company_internal_universe_qa_v1.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=== US COMPANY INTERNAL UNIVERSE QA v1 ===")
    for key, value in summary.items():
        print(f"{key}={value}")
    print("\n[INELIGIBLE REASONS]")
    for key, value in Counter(
        (r.get("exclusion_reason") or "<NULL>") for r in ineligible
    ).most_common():
        print(f"{key}={value}")
    print(f"\n[REPORT] {out}")


if __name__ == "__main__":
    main()
