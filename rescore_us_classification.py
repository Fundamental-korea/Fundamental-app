"""Conservative refinement of US classification edge cases.

This script intentionally changes only two narrowly defined cases:
1) non-REIT real-estate SIC 65xx/67xx rows that are still typed as standard;
2) SIC 6199 Standard rows with explicit digital-asset keywords that are
   currently placed in financials.

Active manual overrides are authoritative and are never modified.
"""
from __future__ import annotations

import argparse
import os

from supabase import create_client

from us_classification import classify_company

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
PAGE_SIZE = 500
UPDATE_BATCH = 50


def fetch_all(sb, table, columns):
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    universe = fetch_all(
        sb,
        "US_Companies",
        "ticker,company_name,sic_code,sector_raw,sector_common,company_type,scoring_profile,is_fundamental_eligible",
    )
    overrides = {
        row["ticker"]
        for row in fetch_all(
            sb,
            "US_Company_Classification_Overrides",
            "ticker",
        )
        if row.get("ticker")
    }

    changes = []
    for row in universe:
        ticker = row.get("ticker")
        if not row.get("is_fundamental_eligible") or not ticker or ticker in overrides:
            continue

        classified = classify_company(
            ticker=ticker,
            company_name=row.get("company_name") or ticker,
            sic=row.get("sic_code"),
            sic_desc=row.get("sector_raw"),
        )
        current = {
            "sector_common": row.get("sector_common"),
            "company_type": row.get("company_type"),
            "scoring_profile": row.get("scoring_profile"),
        }
        proposed = {
            "sector_common": classified["sector_common"],
            "company_type": classified["company_type"],
            "scoring_profile": classified["scoring_profile"],
        }

        # Safe rule A: explicit non-REIT real-estate business type only.
        sic = str(row.get("sic_code") or "").strip()
        safe_real_estate = (
            6500 <= int(sic) <= 6799
            if sic.isdigit()
            else False
        ) and sic != "6798"

        if (
            safe_real_estate
            and current["sector_common"] == "real_estate"
            and current["company_type"] == "standard"
            and proposed["company_type"] == "real_estate_company"
            and proposed["scoring_profile"] == "standard"
        ):
            changes.append(
                {
                    "ticker": ticker,
                    "updates": {"company_type": "real_estate_company"},
                    "reason": "Non-REIT real-estate SIC typed explicitly.",
                }
            )
            continue

        # Safe rule B: only the classifier's explicit SIC 6199 digital-asset
        # refinement; no scoring profile change is permitted here.
        if (
            sic == "6199"
            and current["sector_common"] == "financials"
            and current["scoring_profile"] == "standard"
            and proposed["sector_common"] == "other"
            and proposed["scoring_profile"] == "standard"
        ):
            changes.append(
                {
                    "ticker": ticker,
                    "updates": {"sector_common": "other", "sector_common_ko": "기타"},
                    "reason": "SIC 6199 + explicit digital-asset keyword.",
                }
            )

    from collections import Counter

    reason_counts = Counter(item["reason"] for item in changes)
    print(f"[CLASSIFICATION] safe changes proposed={len(changes)}")
    for reason, count in reason_counts.items():
        print(f"  [reason-count] {reason}: {count}")
    for item in changes[:30]:
        print(f"  {item['ticker']}: {item['reason']} -> {item['updates']}")
    if len(changes) > 30:
        print(f"  ... {len(changes) - 30} more")

    if not args.apply:
        print("[CLASSIFICATION] dry-run only; no database changes applied.")
        return

    for i in range(0, len(changes), UPDATE_BATCH):
        batch = changes[i:i + UPDATE_BATCH]
        for item in batch:
            sb.table("US_Companies").update(item["updates"]).eq(
                "ticker", item["ticker"]
            ).execute()

            # US_Fundamental carries a duplicated sector field for fast app reads.
            # Keep it synchronized whenever the classification sector changes.
            if "sector_common" in item["updates"]:
                sb.table("US_Fundamental").update(
                    {"sector": item["updates"]["sector_common"]}
                ).eq("ticker", item["ticker"]).execute()

        print(f"[CLASSIFICATION] applied {min(i + UPDATE_BATCH, len(changes))}/{len(changes)}")

    print("[CLASSIFICATION] refinement completed.")


if __name__ == "__main__":
    main()
