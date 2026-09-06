"""
US SEC EDGAR collector

Step 1:
- Fetch the official SEC ticker -> CIK/company-name master list.
- Store the master list in Supabase.

This file intentionally does NOT collect financial statements yet.
That will be Step 2/3 after the company universe is verified.
"""

import os
from datetime import datetime, timezone

import requests
from supabase import create_client


SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://cnweggechipghcivruie.supabase.co",
)

SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT",
    "Fundamental-app/1.0 qkrrjsdnd123789@gmail.com",
)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


def get_supabase_client():
    """Create Supabase client."""
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_KEY is not set.")

    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_sec_company_tickers(timeout=30):
    """
    Fetch the official SEC ticker / CIK / company-name master list.
    """

    headers = {
        "User-Agent": SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov",
    }

    print("[SEC] Downloading official SEC company master...")

    response = requests.get(
        SEC_TICKERS_URL,
        headers=headers,
        timeout=timeout,
    )

    response.raise_for_status()

    payload = response.json()

    companies = []
    seen_ciks = set()

    for item in payload.values():

        ticker = str(
            item.get("ticker", "")
        ).strip().upper()

        cik_int = item.get("cik_str")

        title = str(
            item.get("title", "")
        ).strip()

        if not ticker or cik_int is None or not title:
            continue

        cik = f"{int(cik_int):010d}"

        # US_Companies has UNIQUE CIK.
        # Keep one canonical ticker per issuer.
        if cik in seen_ciks:
            continue

        seen_ciks.add(cik)

        companies.append(
            {
                "ticker": ticker,
                "cik": cik,
                "company_name": title,
                "entity_type": None,
                "exchange": None,
                "is_active": True,
                "source": "SEC_EDGAR",
                "fetched_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "updated_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }
        )

    print(
        f"[SEC] Downloaded {len(companies):,} "
        "unique CIK records."
    )

    return companies


def _load_existing_companies(
    supabase,
    page_size=1000,
):
    """
    Load existing US_Companies rows.

    Only the fields required for reconciliation are loaded.
    Pagination is used because Supabase/PostgREST may limit
    the number of rows returned in one request.
    """

    rows = []
    offset = 0

    while True:

        response = (
            supabase
            .table("US_Companies")
            .select("ticker,cik,company_name")
            .range(
                offset,
                offset + page_size - 1,
            )
            .execute()
        )

        page = response.data or []

        rows.extend(page)

        if len(page) < page_size:
            break

        offset += page_size

    return rows


def save_us_companies(
    companies,
    batch_size=500,
):
    """
    Reconcile SEC company master with US_Companies.

    Important behavior:

    1. New CIK -> INSERT
    2. Existing CIK + same company name -> DO NOTHING
    3. Existing CIK + changed company name -> UPDATE
    4. Incoming ticker conflicts with another CIK -> SKIP

    This avoids thousands of unnecessary UPDATE requests.
    """

    supabase = get_supabase_client()

    print(
        f"[SEC] Preparing {len(companies):,} "
        "SEC records..."
    )

    existing_rows = _load_existing_companies(
        supabase
    )

    print(
        f"[SEC] Loaded {len(existing_rows):,} "
        "existing US_Companies rows."
    )

    # ---------------------------------------------------------
    # Build lookup maps
    # ---------------------------------------------------------

    by_cik = {}
    by_ticker = {}

    for row in existing_rows:

        cik = row.get("cik")
        ticker = row.get("ticker")
        company_name = row.get("company_name")

        if not cik or not ticker:
            continue

        ticker = str(ticker).upper()
        cik = str(cik)

        by_cik[cik] = {
            "ticker": ticker,
            "company_name": company_name,
        }

        by_ticker[ticker] = cik

    # ---------------------------------------------------------
    # Reconciliation
    # ---------------------------------------------------------

    to_insert = []
    to_update = []

    unchanged = 0
    skipped = 0

    for company in companies:

        ticker = company["ticker"].upper()
        cik = company["cik"]
        company_name = company["company_name"]

        existing = by_cik.get(cik)

        # -----------------------------------------------------
        # Case 1: Existing CIK
        # -----------------------------------------------------

        if existing:

            existing_name = existing.get(
                "company_name"
            )

            existing_ticker = existing.get(
                "ticker"
            )

            # Company hasn't changed.
            # IMPORTANT: no UPDATE request.
            if existing_name == company_name:

                unchanged += 1
                continue

            # Company name changed.
            # Preserve the existing ticker because changing
            # ticker can cause a UNIQUE ticker conflict.
            update_payload = {
                "company_name": company_name,
                "entity_type": company.get(
                    "entity_type"
                ),
                "exchange": company.get(
                    "exchange"
                ),
                "is_active": company.get(
                    "is_active",
                    True,
                ),
                "source": company.get(
                    "source",
                    "SEC_EDGAR",
                ),
                "updated_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }

            to_update.append(
                (
                    existing_ticker,
                    update_payload,
                )
            )

            continue

        # -----------------------------------------------------
        # Case 2: New CIK but ticker already belongs
        # to another issuer
        # -----------------------------------------------------

        existing_cik_for_ticker = (
            by_ticker.get(ticker)
        )

        if existing_cik_for_ticker:

            skipped += 1

            continue

        # -----------------------------------------------------
        # Case 3: Completely new company
        # -----------------------------------------------------

        to_insert.append(company)

        # Update in-memory maps immediately so that
        # duplicate incoming records cannot be inserted.
        by_cik[cik] = {
            "ticker": ticker,
            "company_name": company_name,
        }

        by_ticker[ticker] = cik

    # ---------------------------------------------------------
    # Print execution plan
    # ---------------------------------------------------------

    print(
        f"[SEC] Plan: "
        f"insert {len(to_insert):,}, "
        f"update {len(to_update):,}, "
        f"unchanged {unchanged:,}, "
        f"skip {skipped:,}."
    )

    # ---------------------------------------------------------
    # INSERT
    # ---------------------------------------------------------

    inserted = 0

    if to_insert:

        print(
            f"[SEC] Starting bulk insert: "
            f"{len(to_insert):,} records."
        )

    for start in range(
        0,
        len(to_insert),
        batch_size,
    ):

        batch = to_insert[
            start:start + batch_size
        ]

        try:

            (
                supabase
                .table("US_Companies")
                .insert(batch)
                .execute()
            )

            inserted += len(batch)

            print(
                f"[SEC] Insert progress: "
                f"{inserted:,} / "
                f"{len(to_insert):,}"
            )

        except Exception as exc:

            print(
                "[SEC] Bulk insert failed. "
                "Falling back to individual inserts."
            )

            print(
                f"[SEC] Error: {type(exc).__name__}: {exc}"
            )

            for company in batch:

                try:

                    (
                        supabase
                        .table("US_Companies")
                        .insert(company)
                        .execute()
                    )

                    inserted += 1

                except Exception as individual_exc:

                    print(
                        "[SEC] Skipped insert:",
                        company.get("ticker"),
                        type(individual_exc).__name__,
                        individual_exc,
                    )

    # ---------------------------------------------------------
    # UPDATE
    # ---------------------------------------------------------

    updated = 0

    if to_update:

        print(
            f"[SEC] Starting updates: "
            f"{len(to_update):,} records."
        )

    for ticker, payload in to_update:

        try:

            (
                supabase
                .table("US_Companies")
                .update(payload)
                .eq("ticker", ticker)
                .execute()
            )

            updated += 1

            print(
                f"[SEC] Update progress: "
                f"{updated:,} / "
                f"{len(to_update):,}"
            )

        except Exception as exc:

            print(
                f"[SEC] Update failed: "
                f"{ticker} | "
                f"{type(exc).__name__}: {exc}"
            )

    # ---------------------------------------------------------
    # Final result
    # ---------------------------------------------------------

    print("\n" + "=" * 60)
    print("🇺🇸 SEC Company Master finished")
    print("=" * 60)

    print(
        f"Inserted  : {inserted:,}"
    )

    print(
        f"Updated   : {updated:,}"
    )

    print(
        f"Unchanged : {unchanged:,}"
    )

    print(
        f"Skipped   : {skipped:,}"
    )

    print("=" * 60)

    return inserted + updated
        

def collect_us_company_master():
    """
    Main entry point for Step 1.
    """

    print("\n" + "=" * 60)
    print("🇺🇸 US SEC Company Master Collector")
    print("=" * 60)

    companies = fetch_sec_company_tickers()

    result = save_us_companies(
        companies
    )

    print(
        f"\n[SEC] Processed records: {result:,}"
    )

    return result


if __name__ == "__main__":

    try:

        result = collect_us_company_master()

        print(
            "\n🎉 SEC Company Master collection completed!"
        )

        print(
            f"Processed: {result:,}"
        )

    except Exception as exc:

        print(
            "\n❌ SEC Company Master collection failed."
        )

        print(
            type(exc).__name__,
            ":",
            exc,
        )
