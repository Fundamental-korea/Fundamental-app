"""Diagnose the exact Standard-type snapshot gaps.

Scope is intentionally limited to:
  US_Companies.is_fundamental_eligible = true
  sector_common in the 7 Standard sectors
  US_Companies.company_type = 'standard'
  US_Fundamental.snapshot_filed IS NULL

No database writes. The script only checks SEC submissions and classifies each
row as domestic, foreign, no relevant filing, or error.
"""

import os
import time

import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY") or ""
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

STANDARD_SECTORS = {
    "technology",
    "healthcare",
    "consumer",
    "industrials",
    "energy",
    "materials",
    "communication",
}

RELEVANT_FORMS = {
    "10-Q", "10-Q/A", "10-K", "10-K/A",
    "20-F", "20-F/A", "40-F", "40-F/A", "6-K",
}
FOREIGN_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}
PAGE_SIZE = 1000


def fetch_json(session, url):
    for attempt in range(4):
        response = session.get(url, timeout=30)
        if response.status_code == 200:
            return response.json()
        if response.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue
        response.raise_for_status()
    raise RuntimeError(f"SEC request failed: {url}")


def paginated_query(query_builder):
    rows = []
    offset = 0
    while True:
        page = query_builder(offset, offset + PAGE_SIZE - 1).execute().data or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def load_exact_standard_missing(sb):
    def build(offset, end):
        return (
            sb.table("US_Fundamental")
            .select("ticker,company_name,cik,snapshot_filed,data_unavailable,data_reliability")
            .is_("snapshot_filed", "null")
            .order("ticker")
            .range(offset, end)
        )

    missing = paginated_query(build)

    def build_companies(offset, end):
        return (
            sb.table("US_Companies")
            .select("ticker,company_type,sector_common,is_fundamental_eligible")
            .eq("is_fundamental_eligible", True)
            .eq("company_type", "standard")
            .in_("sector_common", list(STANDARD_SECTORS))
            .order("ticker")
            .range(offset, end)
        )

    standard = paginated_query(build_companies)
    standard_tickers = {r["ticker"] for r in standard if r.get("ticker")}

    rows = [r for r in missing if r.get("ticker") in standard_tickers]
    rows.sort(key=lambda r: r.get("ticker") or "")
    return rows, len(standard_tickers)


def latest_relevant_filing(submissions):
    recent = submissions.get("filings", {}).get("recent", {}) or {}
    rows = []
    for form, filed, accession, primary in zip(
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("accessionNumber", []),
        recent.get("primaryDocument", []),
    ):
        if form not in RELEVANT_FORMS:
            continue
        rows.append({
            "form": form,
            "filed": filed,
            "accession": accession,
            "primary": primary,
        })
    rows.sort(key=lambda r: (r.get("filed") or "", r.get("accession") or ""), reverse=True)
    return rows[0] if rows else None


def main():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows, standard_type_total = load_exact_standard_missing(sb)

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    print(f"[INPUT] standard_type_universe={standard_type_total}")
    print(f"[INPUT] exact_standard_type_snapshot_missing={len(rows)}")
    print("ticker|company|cik|latest_form|filed|kind|accession|primary")

    counts = {"domestic": 0, "foreign": 0, "no_relevant_filing": 0, "error": 0}

    for row in rows:
        ticker = row.get("ticker") or ""
        company = row.get("company_name") or ""
        cik = str(row.get("cik") or "").strip()
        try:
            if not cik:
                counts["error"] += 1
                print(f"{ticker}|{company}||ERROR|missing CIK")
                continue

            submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik.zfill(10)))
            filing = latest_relevant_filing(submissions)
            if not filing:
                counts["no_relevant_filing"] += 1
                print(f"{ticker}|{company}|{cik}|NONE|||no relevant filing")
                continue

            kind = "foreign" if filing["form"] in FOREIGN_FORMS else "domestic"
            counts[kind] += 1
            print("|".join([
                ticker,
                company,
                cik,
                filing["form"],
                filing["filed"],
                kind,
                filing["accession"],
                filing["primary"],
            ]))
        except Exception as exc:
            counts["error"] += 1
            print(f"{ticker}|{company}|{cik}|ERROR|{type(exc).__name__}:{exc}")
        time.sleep(0.12)

    print(
        f"[SUMMARY] domestic={counts['domestic']} "
        f"foreign={counts['foreign']} "
        f"no_relevant_filing={counts['no_relevant_filing']} "
        f"error={counts['error']}"
    )


if __name__ == "__main__":
    main()
