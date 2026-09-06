"""SEC EDGAR XBRL collector for US companies.

Step 3:
- Preserve the full SEC Company Facts payload so the app can expose detailed
  financial statements later instead of collecting only a small metric subset.
- Preserve the SEC submissions payload and filing metadata for 10-K/10-Q/8-K
  navigation later.

The raw payload is intentionally stored as JSONB. A later normalization layer
will extract standardized metrics into US_Fundamental without throwing away
source concepts, units, dimensions, or filing provenance.
"""

import os
import time
from datetime import datetime, timezone

import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "Fundamental-app/1.0 qkrrjsdnd123789@gmail.com",
)
SEC_HEADERS = {
    "User-Agent": SEC_USER_AGENT,
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov",
}


def get_supabase_client():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_KEY is not set.")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def sec_get(url, timeout=60):
    response = requests.get(url, headers=SEC_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_company_facts(cik):
    cik10 = str(cik).zfill(10)
    return sec_get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json")


def fetch_submissions(cik):
    cik10 = str(cik).zfill(10)
    return sec_get(f"https://data.sec.gov/submissions/CIK{cik10}.json")


def clean_date(value):
    """Convert SEC empty date strings to None for PostgreSQL date columns."""
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


def clean_datetime(value):
    """Convert SEC empty datetime strings to None for PostgreSQL timestamp columns."""
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


def filing_rows(cik, submissions):
    recent = (submissions or {}).get("filings", {}).get("recent", {})
    accession = recent.get("accessionNumber", [])
    rows = []
    now = datetime.now(timezone.utc).isoformat()

    fields = [
        "form", "filingDate", "reportDate", "acceptanceDateTime",
        "primaryDocument", "primaryDocDescription", "isXBRL",
    ]

    for i, acc in enumerate(accession):
        if not acc:
            continue

        filing_date = clean_date(
            recent.get("filingDate", [None] * len(accession))[i]
        )
        report_date = clean_date(
            recent.get("reportDate", [None] * len(accession))[i]
        )
        acceptance_datetime = clean_datetime(
            recent.get("acceptanceDateTime", [None] * len(accession))[i]
        )
        primary_document = recent.get(
            "primaryDocument", [None] * len(accession)
        )[i]

        row = {
            "accession_number": acc,
            "cik": str(cik).zfill(10),
            "form": recent.get("form", [None] * len(accession))[i],
            "filing_date": filing_date,
            "report_date": report_date,
            "acceptance_datetime": acceptance_datetime,
            "primary_document": primary_document,
            "primary_doc_description": recent.get(
                "primaryDocDescription", [None] * len(accession)
            )[i],
            "is_xbrl": bool(
                recent.get("isXBRL", [0] * len(accession))[i]
            ),
            "filing_url": (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{acc.replace('-', '')}/"
                f"{primary_document or ''}"
            ),
            "raw_filing": {
                field: recent.get(
                    field, [None] * len(accession)
                )[i]
                for field in fields
            },
            "fetched_at": now,
            "updated_at": now,
        }
        rows.append(row)

    return rows


def save_company(cik, ticker, company_facts, submissions, batch_size=500):
    supabase = get_supabase_client()
    now = datetime.now(timezone.utc).isoformat()
    entity_name = (
        submissions.get("name")
        if submissions
        else company_facts.get("entityName")
    )
    sic = submissions.get("sic") if submissions else None
    sic_description = (
        submissions.get("sicDescription") if submissions else None
    )

    supabase.table("US_XBRL_Raw").upsert(
        {
            "cik": str(cik).zfill(10),
            "ticker": ticker,
            "company_name": entity_name,
            "entity_name": company_facts.get("entityName"),
            "sic": str(sic) if sic is not None else None,
            "sic_description": sic_description,
            "fiscal_year": submissions.get("fiscalYear") if submissions else None,
            "last_fiscal_period": submissions.get("fiscalYear") if submissions else None,
            "companyfacts": company_facts,
            "submissions": submissions,
            "fetched_at": now,
            "updated_at": now,
        },
        on_conflict="cik",
    ).execute()

    rows = filing_rows(cik, submissions)
    for start in range(0, len(rows), batch_size):
        supabase.table("US_Filings").upsert(
            rows[start:start + batch_size],
            on_conflict="accession_number",
        ).execute()


def collect_one(cik, ticker=None, pause=0.2):
    company_facts = fetch_company_facts(cik)
    time.sleep(pause)
    submissions = fetch_submissions(cik)
    save_company(cik, ticker, company_facts, submissions)
    return {
        "cik": str(cik).zfill(10),
        "ticker": ticker,
        "concept_count": sum(
            len(namespace.get("facts", {}))
            for namespace in company_facts.get("facts", {}).values()
            if isinstance(namespace, dict)
        ),
    }


def collect_from_universe(limit=None):
    """Collect raw SEC facts for eligible companies in US_Companies."""
    supabase = get_supabase_client()
    query = (
        supabase.table("US_Companies")
        .select("ticker,cik,company_name")
        .eq("is_fundamental_eligible", True)
        .eq("is_active", True)
        .order("ticker")
    )
    if limit:
        query = query.limit(limit)
    rows = query.execute().data or []

    success = 0
    failed = 0
    for row in rows:
        try:
            result = collect_one(row["cik"], row["ticker"])
            success += 1
            print(
                f"[SEC XBRL] {row['ticker']}: "
                f"{result['concept_count']:,} concepts"
            )
        except Exception as exc:
            failed += 1
            print(
                f"[SEC XBRL] FAILED {row['ticker']} "
                f"({row['cik']}): {exc}"
            )

    print(
        f"[SEC XBRL] Completed. "
        f"success={success:,}, failed={failed:,}"
    )
    return success, failed


if __name__ == "__main__":
    collect_from_universe()
