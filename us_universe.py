"""
Build the US fundamental-analysis universe from SEC company master data.

Step 2:
- Keep ordinary operating companies for fundamental analysis.
- Exclude obvious funds/ETFs/trusts/shell/security vehicles by name.
- Preserve the raw SEC universe in US_Companies.
- Update only eligibility-related columns on existing rows.

IMPORTANT:
Do NOT use upsert here. US_Companies has NOT NULL columns such as cik
and company_name, so a partial upsert can accidentally attempt an INSERT.
"""

import os
import re
from datetime import datetime, timezone

from supabase import create_client


SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://cnweggechipghcivruie.supabase.co",
)

SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


# ---------------------------------------------------------
# Conservative exclusion rules
# ---------------------------------------------------------
# ADRs are intentionally NOT excluded.
# REIT operating companies are also intentionally NOT excluded
# unless the SEC company name clearly looks like a REIT/security
# vehicle. This keeps the first universe reasonably broad.
# ---------------------------------------------------------

EXCLUSION_PATTERNS = [
    (
        "ETF/FUND",
        re.compile(
            r"\b(ETF|FUND|FUNDS|MUTUAL FUND|INDEX FUND)\b",
            re.I,
        ),
    ),
    (
        "SPAC/SHELL",
        re.compile(
            r"\b(ACQUISITION CORP|ACQUISITION COMPANY|BLANK CHECK|SPECIAL PURPOSE ACQUISITION)\b",
            re.I,
        ),
    ),
    (
        "REIT VEHICLE",
        re.compile(
            r"\b(REIT|REAL ESTATE INVESTMENT TRUST)\b",
            re.I,
        ),
    ),
    (
        "PREFERRED/DEPOSITARY SECURITY",
        re.compile(
            r"\b(PREFERRED|DEPOSITARY|DEPOSITARY SHARES)\b",
            re.I,
        ),
    ),
    (
        "TRUST",
        re.compile(
            r"\b(TRUST|TRUSTS)\b",
            re.I,
        ),
    ),
]


def get_supabase_client():
    """Create Supabase client."""
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_KEY is not set.")

    return create_client(SUPABASE_URL, SUPABASE_KEY)


def classify_company(company_name):
    """
    Classify a company based on conservative name rules.

    Returns:
        (True, None) for eligible companies
        (False, reason) for excluded companies
    """
    name = str(company_name or "").strip()

    for reason, pattern in EXCLUSION_PATTERNS:
        if pattern.search(name):
            return False, reason

    return True, None


def load_us_companies(supabase, page_size=1000):
    """Load all US_Companies rows using pagination."""
    rows = []
    offset = 0

    while True:
        response = (
            supabase
            .table("US_Companies")
            .select(
                "ticker,cik,company_name,"
                "entity_type,exchange,is_active,"
                "is_fundamental_eligible,exclusion_reason"
            )
            .range(offset, offset + page_size - 1)
            .execute()
        )

        page = response.data or []
        rows.extend(page)

        print(f"[US Universe] Loaded {len(rows):,} rows...")

        if len(page) < page_size:
            break

        offset += page_size

    return rows


def build_us_universe():
    """Build and save the US fundamental universe."""
    supabase = get_supabase_client()

    print("\n" + "=" * 60)
    print("🇺🇸 US Fundamental Universe Builder")
    print("=" * 60)

    # -----------------------------------------------------
    # Load source universe
    # -----------------------------------------------------
    rows = load_us_companies(supabase)

    print(f"\n[US Universe] Source companies: {len(rows):,}")

    if not rows:
        raise RuntimeError("US_Companies is empty.")

    # -----------------------------------------------------
    # Classification
    # -----------------------------------------------------
    now = datetime.now(timezone.utc).isoformat()

    updates = []
    unchanged = 0
    reason_counts = {}
    eligible_count = 0
    excluded_count = 0

    for row in rows:
        ticker = row.get("ticker")
        company_name = row.get("company_name")

        eligible, reason = classify_company(company_name)

        if eligible:
            eligible_count += 1
        else:
            excluded_count += 1
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        old_eligible = row.get("is_fundamental_eligible")
        old_reason = row.get("exclusion_reason")

        if old_eligible == eligible and old_reason == reason:
            unchanged += 1
            continue

        updates.append(
            {
                "ticker": ticker,
                "is_fundamental_eligible": eligible,
                "exclusion_reason": reason,
                "filtered_at": now,
                "updated_at": now,
            }
        )

    # -----------------------------------------------------
    # Print classification result
    # -----------------------------------------------------
    print("\n" + "-" * 60)
    print("📊 Classification Result")
    print("-" * 60)
    print(f"Total companies       : {len(rows):,}")
    print(f"Fundamental eligible  : {eligible_count:,}")
    print(f"Excluded              : {excluded_count:,}")
    print(f"Already unchanged     : {unchanged:,}")
    print(f"Rows requiring update : {len(updates):,}")

    if reason_counts:
        print("\n" + "-" * 60)
        print("🚫 Exclusion Breakdown")
        print("-" * 60)

        for reason, count in sorted(
            reason_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            print(f"{reason:<32} {count:,}")

    # -----------------------------------------------------
    # UPDATE changed rows only
    # -----------------------------------------------------
    updated_count = 0

    if not updates:
        print("\n[US Universe] No database updates required.")
    else:
        print("\n" + "-" * 60)
        print(f"💾 Updating {len(updates):,} changed rows...")
        print("-" * 60)

        for item in updates:
            ticker = item["ticker"]

            payload = {
                "is_fundamental_eligible": item["is_fundamental_eligible"],
                "exclusion_reason": item["exclusion_reason"],
                "filtered_at": item["filtered_at"],
                "updated_at": item["updated_at"],
            }

            try:
                response = (
                    supabase
                    .table("US_Companies")
                    .update(payload)
                    .eq("ticker", ticker)
                    .execute()
                )

                if not response.data:
                    print(f"[US Universe] ⚠️ No row updated: {ticker}")
                    continue

                updated_count += 1

                if updated_count % 100 == 0 or updated_count == len(updates):
                    print(
                        f"[US Universe] Update progress: "
                        f"{updated_count:,} / {len(updates):,}"
                    )

            except Exception as exc:
                print(f"\n❌ Update failed: {ticker}")
                print(type(exc).__name__, ":", exc)
                raise

    # -----------------------------------------------------
    # Final result
    # -----------------------------------------------------
    print("\n" + "=" * 60)
    print("🇺🇸 US Fundamental Universe finished")
    print("=" * 60)
    print(f"Total                 : {len(rows):,}")
    print(f"Fundamental eligible  : {eligible_count:,}")
    print(f"Excluded              : {excluded_count:,}")
    print(f"DB rows updated       : {updated_count:,}")
    print(f"DB rows unchanged     : {unchanged:,}")
    print("=" * 60)

    return {
        "total": len(rows),
        "eligible": eligible_count,
        "excluded": excluded_count,
        "updated": updated_count,
        "unchanged": unchanged,
        "exclusion_breakdown": reason_counts,
    }


if __name__ == "__main__":
    try:
        result = build_us_universe()

        print("\n🎉 US Universe collection completed!")
        print(f"Fundamental eligible: {result['eligible']:,}")

    except Exception as exc:
        print("\n❌ US Universe collection failed.")
        print(type(exc).__name__, ":", exc)
