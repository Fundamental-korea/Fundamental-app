"""
Build the US fundamental-analysis universe from SEC company master data.

Step 2:
- Keep ordinary operating companies for fundamental analysis.
- Exclude obvious funds/ETFs/trusts/shell entities using SEC company-name rules.
- Preserve the raw SEC universe in US_Companies.
- Only update eligibility fields when the classification actually changes.
"""

import os
import re
from datetime import datetime, timezone

from supabase import create_client


SUPABASE_URL = os.environ.get(
    "SUPABASE_URL"
) or "https://cnweggechipghcivruie.supabase.co"

SUPABASE_KEY = os.environ.get(
    "SUPABASE_KEY",
    ""
)


# ---------------------------------------------------------
# Conservative exclusion rules
# ---------------------------------------------------------
#
# We deliberately do NOT exclude ADRs.
#
# Many major US-traded foreign operating companies
# are SEC-reporting issuers and can have XBRL fundamentals.
#
# REITs are NOT automatically excluded merely because
# they are real-estate businesses. We only target obvious
# REIT/security vehicles here.
# ---------------------------------------------------------

EXCLUSION_PATTERNS = [

    (
        "ETF/FUND",
        re.compile(
            r"\b("
            r"ETF|"
            r"FUND|"
            r"FUNDS|"
            r"MUTUAL FUND|"
            r"INDEX FUND"
            r")\b",
            re.I,
        ),
    ),

    (
        "SPAC/SHELL",
        re.compile(
            r"\b("
            r"ACQUISITION CORP|"
            r"ACQUISITION COMPANY|"
            r"BLANK CHECK|"
            r"SPECIAL PURPOSE ACQUISITION"
            r")\b",
            re.I,
        ),
    ),

    (
        "REIT VEHICLE",
        re.compile(
            r"\b("
            r"REIT|"
            r"REAL ESTATE INVESTMENT TRUST"
            r")\b",
            re.I,
        ),
    ),

    (
        "PREFERRED/DEPOSITARY SECURITY",
        re.compile(
            r"\b("
            r"PREFERRED|"
            r"DEPOSITARY|"
            r"DEPOSITARY SHARES"
            r")\b",
            re.I,
        ),
    ),

    (
        "TRUST",
        re.compile(
            r"\b("
            r"TRUST|"
            r"TRUSTS"
            r")\b",
            re.I,
        ),
    ),
]


def get_supabase_client():
    """Create Supabase client."""

    if not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_KEY is not set."
        )

    return create_client(
        SUPABASE_URL,
        SUPABASE_KEY,
    )


def classify_company(company_name):
    """
    Classify a company based on conservative name rules.

    Returns:
        (True, None)
        or
        (False, exclusion_reason)
    """

    name = str(
        company_name or ""
    ).strip()

    for reason, pattern in EXCLUSION_PATTERNS:

        if pattern.search(name):
            return False, reason

    return True, None


def load_us_companies(
    supabase,
    page_size=1000,
):
    """
    Load all US_Companies rows using pagination.
    """

    rows = []
    offset = 0

    while True:

        response = (
            supabase
            .table("US_Companies")
            .select(
                "ticker,cik,company_name,"
                "entity_type,exchange,is_active,"
                "is_fundamental_eligible,"
                "exclusion_reason"
            )
            .range(
                offset,
                offset + page_size - 1,
            )
            .execute()
        )

        page = response.data or []

        rows.extend(page)

        print(
            f"[US Universe] Loaded "
            f"{len(rows):,} rows..."
        )

        if len(page) < page_size:
            break

        offset += page_size

    return rows


def build_us_universe(
    batch_size=500,
):
    """
    Build and save the US fundamental universe.

    Existing classification is only updated when it changes.
    """

    supabase = get_supabase_client()

    print("\n" + "=" * 60)
    print("🇺🇸 US Fundamental Universe Builder")
    print("=" * 60)

    # -----------------------------------------------------
    # Load source universe
    # -----------------------------------------------------

    rows = load_us_companies(
        supabase
    )

    print(
        f"\n[US Universe] "
        f"Source companies: {len(rows):,}"
    )

    if not rows:
        raise RuntimeError(
            "US_Companies is empty."
        )

    # -----------------------------------------------------
    # Classification
    # -----------------------------------------------------

    now = datetime.now(
        timezone.utc
    ).isoformat()

    updates = []

    unchanged = 0

    reason_counts = {}

    eligible_count = 0
    excluded_count = 0

    for row in rows:

        ticker = row.get(
            "ticker"
        )

        company_name = row.get(
            "company_name"
        )

        old_eligible = row.get(
            "is_fundamental_eligible"
        )

        old_reason = row.get(
            "exclusion_reason"
        )

        eligible, reason = classify_company(
            company_name
        )

        if eligible:
            eligible_count += 1
        else:
            excluded_count += 1

            reason_counts[reason] = (
                reason_counts.get(
                    reason,
                    0,
                ) + 1
            )

        # -------------------------------------------------
        # Only write rows whose classification changed
        # -------------------------------------------------

        if (
            old_eligible == eligible
            and old_reason == reason
        ):
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

    print(
        f"Total companies       : {len(rows):,}"
    )

    print(
        f"Fundamental eligible  : {eligible_count:,}"
    )

    print(
        f"Excluded              : {excluded_count:,}"
    )

    print(
        f"Already unchanged     : {unchanged:,}"
    )

    print(
        f"Rows requiring update : {len(updates):,}"
    )

    # -----------------------------------------------------
    # Exclusion breakdown
    # -----------------------------------------------------

    if reason_counts:

        print("\n" + "-" * 60)
        print("🚫 Exclusion Breakdown")
        print("-" * 60)

        for reason, count in sorted(
            reason_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        ):

            print(
                f"{reason:<32} {count:,}"
            )

    # -----------------------------------------------------
    # Write changed rows only
    # -----------------------------------------------------

    updated_count = 0

    if updates:

        print("\n" + "-" * 60)
        print(
            f"💾 Updating {len(updates):,} "
            "changed rows..."
        )
        print("-" * 60)

            # -----------------------------------------------------
    # UPDATE changed rows only
    # -----------------------------------------------------
    #
    # IMPORTANT:
    # Do NOT use upsert here.
    #
    # US_Companies has NOT NULL columns such as CIK.
    # Universe filtering only changes eligibility fields
    # on existing rows, so UPDATE is safer than UPSERT.
    # -----------------------------------------------------

    updated_count = 0

    if updates:

        print("\n" + "-" * 60)
        print(
            f"💾 Updating {len(updates):,} "
            "changed rows..."
        )
        print("-" * 60)

        for item in updates:

            ticker = item["ticker"]

            payload = {
                "is_fundamental_eligible":
                    item["is_fundamental_eligible"],

                "exclusion_reason":
                    item["exclusion_reason"],

                "filtered_at":
                    item["filtered_at"],

                "updated_at":
                    item["updated_at"],
            }

            try:

                response = (
                    supabase
                    .table("US_Companies")
                    .update(payload)
                    .eq("ticker", ticker)
                    .execute()
                )

                # Make sure the target row actually existed.
                if not response.data:
                    print(
                        f"[US Universe] "
                        f"⚠️ No row updated: {ticker}"
                    )
                    continue

                updated_count += 1

                # Don't print every row.
                # Print progress every 100 rows.
                if (
                    updated_count % 100 == 0
                    or updated_count == len(updates)
                ):
                    print(
                        f"[US Universe] "
                        f"Update progress: "
                        f"{updated_count:,} / "
                        f"{len(updates):,}"
                    )

            except Exception as exc:

                print(
                    f"\n❌ Update failed: {ticker}"
                )

                print(
                    type(exc).__name__,
                    ":",
                    exc,
                )

                raise

    else:

        print(
            "\n[US Universe] "
            "No database updates required."
        )
            except Exception as exc:

                print(
                    "\n❌ Batch update failed:"
                )

                print(
                    type(exc).__name__,
                    ":",
                    exc,
                )

                raise

    else:

        print(
            "\n[US Universe] "
            "No database updates required."
        )

    # -----------------------------------------------------
    # Final result
    # -----------------------------------------------------

    print("\n" + "=" * 60)
    print("🇺🇸 US Fundamental Universe finished")
    print("=" * 60)

    print(
        f"Total                 : {len(rows):,}"
    )

    print(
        f"Fundamental eligible  : {eligible_count:,}"
    )

    print(
        f"Excluded              : {excluded_count:,}"
    )

    print(
        f"DB rows updated       : {updated_count:,}"
    )

    print(
        f"DB rows unchanged     : {unchanged:,}"
    )

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

        print(
            "\n🎉 US Universe collection completed!"
        )

        print(
            f"Fundamental eligible: "
            f"{result['eligible']:,}"
        )

    except Exception as exc:

        print(
            "\n❌ US Universe collection failed."
        )

        print(
            type(exc).__name__,
            ":",
            exc,
        )
