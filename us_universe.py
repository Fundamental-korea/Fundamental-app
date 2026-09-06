"""Build the US fundamental-analysis universe from SEC company master data.

Step 2:
- Keep ordinary operating companies for fundamental analysis.
- Exclude obvious funds/ETFs/trusts/shell entities using SEC company-name rules.
- Preserve the raw SEC universe in US_Companies; only mark eligibility here.
"""

import os
import re
from datetime import datetime, timezone

from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# Conservative exclusions. We deliberately do not exclude ADRs here because
# many major US-traded operating companies are foreign issuers and can still
# have SEC XBRL fundamentals. They can be handled separately later.
EXCLUSION_PATTERNS = [
    ("ETF/FUND", re.compile(r"\b(ETF|FUND|FUNDS|MUTUAL FUND|INDEX FUND)\b", re.I)),
    ("TRUST", re.compile(r"\b(TRUST|TRUSTS)\b", re.I)),
    ("SPAC/SHELL", re.compile(r"\b(ACQUISITION CORP|ACQUISITION COMPANY|BLANK CHECK)\b", re.I)),
    ("REIT VEHICLE", re.compile(r"\b(REIT|REAL ESTATE INVESTMENT TRUST)\b", re.I)),
    ("PREFERRED/DEPOSITARY SECURITY", re.compile(r"\b(PREFERRED|DEPOSITARY|DEPOSITARY SHARES)\b", re.I)),
]


def get_supabase_client():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_KEY is not set.")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def classify_company(company_name):
    name = str(company_name or "").strip()
    for reason, pattern in EXCLUSION_PATTERNS:
        if pattern.search(name):
            return False, reason
    return True, None


def build_us_universe(batch_size=500):
    supabase = get_supabase_client()
    rows = []
    start = 0

    while True:
        result = (
            supabase.table("US_Companies")
            .select("ticker,cik,company_name,entity_type,exchange,is_active")
            .range(start, start + batch_size - 1)
            .execute()
        )
        batch = result.data or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < batch_size:
            break
        start += batch_size

    now = datetime.now(timezone.utc).isoformat()
    updates = []
    for row in rows:
        eligible, reason = classify_company(row.get("company_name"))
        updates.append(
            {
                "ticker": row["ticker"],
                "is_fundamental_eligible": eligible,
                "exclusion_reason": reason,
                "filtered_at": now,
                "updated_at": now,
            }
        )

    for start in range(0, len(updates), batch_size):
        supabase.table("US_Companies").upsert(
            updates[start : start + batch_size], on_conflict="ticker"
        ).execute()

    eligible_count = sum(1 for row in updates if row["is_fundamental_eligible"])
    excluded_count = len(updates) - eligible_count
    print(f"[US Universe] Total: {len(updates):,}")
    print(f"[US Universe] Fundamental eligible: {eligible_count:,}")
    print(f"[US Universe] Excluded: {excluded_count:,}")
    return eligible_count, excluded_count


if __name__ == "__main__":
    build_us_universe()
