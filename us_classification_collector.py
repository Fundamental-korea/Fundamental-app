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
# MANUAL SIC OVERRIDES
# ============================================================

# SEC submissions와 US_Companies 양쪽에서 SIC가 없는
# 일부 기업을 위한 수동 override.
#
# CYATY:
# Contemporary Amperex Technology Co., Limited/ADR
#
# SEC submissions에는 SIC가 제공되지 않기 때문에
# SIC 2834를 사용하여 healthcare로 분류한다.
MANUAL_SIC_OVERRIDES = {
    "CYATY": 2834,
}


# ============================================================
# ENV
# ============================================================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SECRET_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "SUPABASE_URL 또는 SUPABASE_SECRET_KEY 환경변수가 없습니다."
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
    existing_sic: Any = None,
) -> dict[str, Any]:
    """
    SEC metadata를 기반으로 기업 classification을 수행한다.

    SIC 우선순위:

    1. SEC submissions SIC
    2. US_Companies 기존 sic_code
    3. MANUAL_SIC_OVERRIDES
    """

    submissions = fetch_sec_submissions(cik)

    # --------------------------------------------------------
    # 1. SEC submissions SIC
    # --------------------------------------------------------

    sic_raw = submissions.get("sic")

    sector_source = "SEC_SIC"

    # --------------------------------------------------------
    # 2. 기존 US_Companies SIC fallback
    # --------------------------------------------------------

    if not sic_raw and existing_sic is not None:
        sic_raw = existing_sic
        sector_source = "US_COMPANIES_SIC"

    # --------------------------------------------------------
    # 3. Manual SIC override
    # --------------------------------------------------------

    if not sic_raw and ticker in MANUAL_SIC_OVERRIDES:
        sic_raw = MANUAL_SIC_OVERRIDES[ticker]
        sector_source = "MANUAL_OVERRIDE"

    # --------------------------------------------------------
    # SIC 숫자 변환
    # --------------------------------------------------------

    try:
        sic = int(sic_raw) if sic_raw is not None else None
    except (TypeError, ValueError):
        sic = None

    # 변환 실패 후에도 manual override 적용
    if sic is None and ticker in MANUAL_SIC_OVERRIDES:
        sic = MANUAL_SIC_OVERRIDES[ticker]
        sector_source = "MANUAL_OVERRIDE"

    # --------------------------------------------------------
    # SEC SIC description
    # --------------------------------------------------------

    sic_desc = submissions.get("sicDescription")

    # --------------------------------------------------------
    # Classification
    # --------------------------------------------------------

    result = classify_company(
        ticker=ticker,
        company_name=company_name,
        sic=sic,
        sic_desc=sic_desc,
    )

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

    # SIC를 최종적으로 얻지 못한 경우
    if sic is None:
        sector_source = "UNAVAILABLE"

    return {
        "ticker": ticker,
        "sic_code": str(sic) if sic is not None else None,
        "sector_source": sector_source,
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
                "ticker,cik,company_name,sic_code"
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
    ticker_filter: str | None = None,
) -> None:

    companies = get_eligible_companies()

    total = len(companies)

    # --------------------------------------------------------
    # 특정 ticker만 실행
    # --------------------------------------------------------

    if ticker_filter:

        ticker_filter = ticker_filter.strip().upper()

        companies = [
            company
            for company in companies
            if (
                company.get("ticker")
                or ""
            ).strip().upper()
            == ticker_filter
        ]

    # --------------------------------------------------------
    # limit 실행
    # --------------------------------------------------------

    elif limit is not None:

        companies = companies[:limit]

    print("=" * 70)
    print("US CLASSIFICATION COLLECTOR")
    print("=" * 70)

    print(f"Eligible companies : {total:,}")
    print(f"Companies to run   : {len(companies):,}")
    print()

    if ticker_filter and not companies:
        print(
            f"Ticker not found in eligible companies: "
            f"{ticker_filter}"
        )
        return

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
                existing_sic=company.get("sic_code"),
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

    parser.add_argument(
        "--ticker",
        type=str,
        default=None,
        help="특정 ticker 하나만 테스트.",
    )

    args = parser.parse_args()

    run(
        limit=args.limit,
        ticker_filter=args.ticker,
    )
