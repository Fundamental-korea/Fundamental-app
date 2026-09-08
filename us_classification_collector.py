"""
us_classification_collector.py

SEC company metadata를 기반으로 US_Companies 전체 기업을 분류하고
Supabase에 classification 결과만 저장한다.

저장:
- sic_code
- sector_source
- sector_raw
- sector_common
- sector_common_ko
- company_type
- scoring_profile

저장하지 않음:
- SEC 원본 JSON
- 재무제표 원본
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Any

import requests
from supabase import create_client

from us_classification import classify_company


# ============================================================
# CONFIG
# ============================================================

SEC_BASE = "https://data.sec.gov"

REQUEST_DELAY = 0.15

SECTOR_COMMON_KO = {
    "technology": "기술",
    "healthcare": "헬스케어",
    "financials": "금융",
    "consumer": "소비재",
    "industrials": "산업재",
    "energy": "에너지",
    "utilities": "유틸리티",
    "real_estate": "부동산",
    "materials": "원자재",
    "communication": "커뮤니케이션",
    "other": "기타",
}


# ============================================================
# ENV
# ============================================================

SUPABASE_URL = os.getenv("https://cnweggechipghcivruie.supabase.co")
SUPABASE_KEY = os.getenv("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNud2VnZ2VjaGlwZ2hjaXZydWllIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NzA4NTk4OSwiZXhwIjoyMTAyNjYxOTg5fQ.3JZlKnrWH9RcaExixzADDYN97gduWGoNs8HB1K1IyFc")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_URL 또는 SUPABASE_SERVICE_ROLE_KEY 환경변수가 없습니다."
    )

supabase = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
)


# ============================================================
# SEC SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": "Fundamental-korea Fundamental-app contact@example.com",
        "Accept-Encoding": "gzip, deflate",
        "Host": "data.sec.gov",
    }
)


# ============================================================
# SEC SUBMISSIONS
# ============================================================

def fetch_sec_submissions(cik: str) -> dict[str, Any]:
    """
    SEC submissions metadata만 가져온다.
    """

    cik_clean = str(cik).strip().zfill(10)

    url = f"{SEC_BASE}/submissions/CIK{cik_clean}.json"

    response = session.get(
        url,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# CLASSIFICATION
# ============================================================

def classify_from_sec(
    ticker: str,
    company_name: str,
    cik: str,
) -> dict[str, Any]:

    submissions = fetch_sec_submissions(cik)

    sic_raw = submissions.get("sic")
    sic_desc = submissions.get("sicDescription") or ""

    try:
        sic = int(sic_raw) if sic_raw is not None else None
    except (TypeError, ValueError):
        sic = None

    result = classify_company(
        ticker=ticker,
        company_name=company_name,
        sic=sic,
        sic_desc=sic_desc,
    )

    # classify_company() 결과가 dict인 현재 구조를 기준으로 처리
    if not isinstance(result, dict):
        raise RuntimeError(
            f"{ticker}: classify_company()가 dict를 반환하지 않았습니다: "
            f"{type(result)}"
        )

    sector_common = result.get("sector_common")
    company_type = result.get("company_type")
    scoring_profile = result.get("scoring_profile")

    sector_common_ko = SECTOR_COMMON_KO.get(
        sector_common,
        "기타",
    )

    return {
        "ticker": ticker,
        "sic_code": str(sic) if sic is not None else None,
        "sector_source": "SEC_SIC",
        "sector_raw": sic_desc or None,
        "sector_common": sector_common,
        "sector_common_ko": sector_common_ko,
        "company_type": company_type or "standard",
        "scoring_profile": scoring_profile or "standard",
    }


# ============================================================
# SUPABASE
# ============================================================

def get_eligible_companies() -> list[dict[str, Any]]:
    """
    Fundamental eligible 기업만 가져온다.
    """

    rows: list[dict[str, Any]] = []

    start = 0
    page_size = 1000

    while True:

        result = (
            supabase
            .table("US_Companies")
            .select(
                "ticker,cik,company_name"
            )
            .eq(
                "is_fundamental_eligible",
                True,
            )
            .range(
                start,
                start + page_size - 1,
            )
            .execute()
        )

        batch = result.data or []

        rows.extend(batch)

        if len(batch) < page_size:
            break

        start += page_size

    return rows


def save_classification(
    result: dict[str, Any],
) -> None:

    ticker = result["ticker"]

    payload = {
        "sic_code": result["sic_code"],
        "sector_source": result["sector_source"],
        "sector_raw": result["sector_raw"],
        "sector_common": result["sector_common"],
        "sector_common_ko": result["sector_common_ko"],
        "company_type": result["company_type"],
        "scoring_profile": result["scoring_profile"],
    }

    (
        supabase
        .table("US_Companies")
        .update(payload)
        .eq(
            "ticker",
            ticker,
        )
        .execute()
    )


# ============================================================
# RUN
# ============================================================

def run(
    limit: int | None = None,
) -> None:

    companies = get_eligible_companies()

    total = len(companies)

    if limit is not None:
        companies = companies[:limit]

    print("=" * 70)
    print("US CLASSIFICATION COLLECTOR")
    print("=" * 70)

    print(f"Eligible companies : {total:,}")
    print(f"Companies to run   : {len(companies):,}")
    print()

    success = 0
    failed = 0

    for index, company in enumerate(
        companies,
        start=1,
    ):

        ticker = (
            company.get("ticker")
            or ""
        ).strip().upper()

        cik = company.get("cik")

        company_name = (
            company.get("company_name")
            or ""
        ).strip()

        if not ticker or not cik:

            print(
                f"[{index}/{len(companies)}] "
                f"SKIP {ticker or '?'} "
                f"(missing ticker/cik)"
            )

            failed += 1
            continue

        try:

            result = classify_from_sec(
                ticker=ticker,
                company_name=company_name,
                cik=cik,
            )

            save_classification(result)

            success += 1

            print(
                f"[{index}/{len(companies)}] "
                f"{ticker:<6} | "
                f"{result['sector_common']:<14} | "
                f"{result['company_type']:<18} | "
                f"{result['scoring_profile']}"
            )

        except Exception as exc:

            failed += 1

            print(
                f"[{index}/{len(companies)}] "
                f"ERROR {ticker}: {exc}"
            )

        time.sleep(REQUEST_DELAY)

    print()
    print("=" * 70)
    print("COMPLETED")
    print("=" * 70)

    print(f"Success : {success:,}")
    print(f"Failed  : {failed:,}")
    print()


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="테스트용. 지정하면 앞에서부터 N개만 실행.",
    )

    args = parser.parse_args()

    run(
        limit=args.limit,
    )
