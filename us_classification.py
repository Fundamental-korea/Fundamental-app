"""
US company classification engine v1

Purpose
-------
SEC SIC + company metadata
    -> sector_common
    -> sector_common_ko
    -> company_type
    -> scoring_profile

This version is intentionally separated from the financial collector.
No Supabase writes are performed.

Usage
-----
python us_classification.py --test30
python us_classification.py --ticker AAPL
"""

from __future__ import annotations

import argparse
import time
from typing import Any

import requests


# ============================================================
# 1. COMMON SECTOR
# ============================================================

COMMON_SECTORS = {
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
# 2. COMPANY TYPE
# ============================================================

COMPANY_TYPES = {
    "standard",
    "bank",
    "insurance",
    "asset_manager",
    "broker_dealer",
    "bdc",
    "reit",
    "real_estate_company",
    "oil_gas",
    "midstream",
    "mlp",
    "utility",
    "telecom",
    "holding",
    "spac",
    "closed_end_fund",
}


# ============================================================
# 3. SCORING PROFILE
# ============================================================

SCORING_PROFILES = {
    "standard",
    "financial",
    "reit",
    "bdc",
    "utility",
}


COMPANY_TYPE_TO_PROFILE = {
    "standard": "standard",
    "bank": "financial",
    "insurance": "financial",
    "asset_manager": "financial",
    "broker_dealer": "financial",
    "bdc": "bdc",
    "reit": "reit",
    "real_estate_company": "standard",
    "oil_gas": "standard",
    "midstream": "standard",
    "mlp": "standard",
    "utility": "utility",
    "telecom": "standard",
    "holding": "standard",
    "spac": "standard",
    "closed_end_fund": "standard",
}


# ============================================================
# 4. REPRESENTATIVE 30
# ============================================================

TEST_COMPANIES = {
    "AAPL": "애플",
    "MSFT": "마이크로소프트",
    "NVDA": "엔비디아",
    "GOOGL": "알파벳",
    "AMZN": "아마존",
    "META": "메타",
    "AVGO": "브로드컴",
    "ORCL": "오라클",

    "JPM": "JP모건",
    "BAC": "뱅크오브아메리카",
    "GS": "골드만삭스",
    "MS": "모건스탠리",
    "BLK": "블랙록",

    "JNJ": "존슨앤드존슨",
    "PFE": "화이자",
    "MRK": "머크",
    "LLY": "일라이릴리",

    "O": "리얼티 인컴",
    "AMT": "아메리칸 타워",
    "PLD": "프로로지스",

    "NEE": "넥스트에라 에너지",
    "DUK": "듀크 에너지",

    "XOM": "엑슨모빌",
    "CVX": "셰브론",

    "RTX": "RTX",
    "LMT": "록히드마틴",
    "BA": "보잉",

    "GSBD": "골드만삭스 BDC",
    "ARCC": "아레스 캐피털",
}


# ============================================================
# 5. TEST OVERRIDES
# ============================================================
#
# 이것은 "최종 override table"이 아니라 30개 검증용이다.
# 실제 전체 수집에서는 Supabase
# US_Company_Classification_Overrides
# 로 분리한다.
#

TEST_OVERRIDES = {
    "GSBD": {
        "sector_common": "financials",
        "company_type": "bdc",
        "scoring_profile": "bdc",
        "reason": "Known BDC",
    },
    "ARCC": {
        "sector_common": "financials",
        "company_type": "bdc",
        "scoring_profile": "bdc",
        "reason": "Known BDC",
    },
}


# ============================================================
# 6. SIC RANGE HELPERS
# ============================================================

def sic_between(sic: int | None, low: int, high: int) -> bool:
    return sic is not None and low <= sic <= high


def sic_in(sic: int | None, values: set[int]) -> bool:
    return sic in values if sic is not None else False


# ============================================================
# 7. COMMON SECTOR FROM SIC
# ============================================================

def common_sector_from_sic(
    sic: int | None,
    sic_desc: str = "",
) -> str:

    desc = (sic_desc or "").lower()

    if sic is None:
        return "other"

    # --------------------------------------------------------
    # Real Estate
    # --------------------------------------------------------

    if sic == 6798:
        return "real_estate"

    if 6500 <= sic <= 6799:
        return "real_estate"

    # --------------------------------------------------------
    # Financials
    # --------------------------------------------------------

    if 6000 <= sic <= 6499:
        return "financials"

    # --------------------------------------------------------
    # Utilities
    # --------------------------------------------------------

    if 4900 <= sic <= 4999:
        return "utilities"

    # --------------------------------------------------------
    # Communication
    # --------------------------------------------------------

    if 4800 <= sic <= 4899:
        return "communication"

    # --------------------------------------------------------
    # Healthcare
    # --------------------------------------------------------

    if 2833 <= sic <= 2836:
        return "healthcare"

    if 3841 <= sic <= 3851:
        return "healthcare"

    if 8000 <= sic <= 8099:
        return "healthcare"

    # --------------------------------------------------------
    # Technology
    # --------------------------------------------------------

    if 3570 <= sic <= 3579:
        return "technology"

    if 3670 <= sic <= 3679:
        return "technology"

    if 7370 <= sic <= 7379:
        return "technology"

    # Semiconductor description fallback
    if "semiconductor" in desc:
        return "technology"

    # Software description fallback
    if any(
        word in desc
        for word in (
            "software",
            "prepackaged software",
            "computer programming",
            "computer services",
        )
    ):
        return "technology"

    # --------------------------------------------------------
    # Energy
    # --------------------------------------------------------

    energy_sics = {
        1221, 1222,
        1311,
        1381, 1382, 1389,
        2911,
        2999,
    }

    if sic_in(sic, energy_sics):
        return "energy"

    if "crude petroleum" in desc:
        return "energy"

    if "natural gas" in desc:
        return "energy"

    if "petroleum refining" in desc:
        return "energy"

    # --------------------------------------------------------
    # Materials
    # --------------------------------------------------------

    materials_ranges = (
        (1000, 1499),
        (2800, 2829),
        (2850, 2899),
        (3200, 3299),
        (3300, 3399),
        (3400, 3499),
    )

    if any(low <= sic <= high for low, high in materials_ranges):
        return "materials"

    # --------------------------------------------------------
    # Industrials
    # --------------------------------------------------------

    industrial_ranges = (
        (1500, 1799),
        (3300, 3999),
        (4000, 4799),
    )

    if any(low <= sic <= high for low, high in industrial_ranges):
        return "industrials"

    # Aircraft / aerospace
    if sic == 3721:
        return "industrials"

    # --------------------------------------------------------
    # Consumer
    # --------------------------------------------------------

    if 5000 <= sic <= 5999:
        return "consumer"

    if 7000 <= sic <= 7999:
        return "consumer"

    # --------------------------------------------------------
    # Fallback
    # --------------------------------------------------------

    return "other"


# ============================================================
# 8. COMPANY TYPE
# ============================================================

def classify_company_type(
    ticker: str,
    company_name: str,
    sic: int | None,
    sic_desc: str,
) -> str:

    ticker = ticker.upper().strip()
    name = (company_name or "").lower()
    desc = (sic_desc or "").lower()

    # --------------------------------------------------------
    # Manual override first
    # --------------------------------------------------------

    if ticker in TEST_OVERRIDES:
        return TEST_OVERRIDES[ticker]["company_type"]

    # --------------------------------------------------------
    # REIT
    # --------------------------------------------------------

    if sic == 6798:
        return "reit"

    if "real estate investment trust" in desc:
        return "reit"

    if " reit" in f" {name} " or name.endswith("reit"):
        return "reit"

    # --------------------------------------------------------
    # BDC
    # --------------------------------------------------------

    if "business development company" in desc:
        return "bdc"

    if " bdc" in f" {name} ":
        return "bdc"

    # --------------------------------------------------------
    # Bank
    # --------------------------------------------------------

    bank_sics = {
        6021, 6022, 6029,
        6035, 6036,
    }

    if sic_in(sic, bank_sics):
        return "bank"

    if "national commercial bank" in desc:
        return "bank"

    if "commercial bank" in desc:
        return "bank"

    if "savings institution" in desc:
        return "bank"

    # --------------------------------------------------------
    # Broker / Dealer
    # --------------------------------------------------------

    broker_sics = {
        6200,
        6211,
        6221,
    }

    if sic_in(sic, broker_sics):
        return "broker_dealer"

    if "security broker" in desc:
        return "broker_dealer"

    if "commodity broker" in desc:
        return "broker_dealer"

    # --------------------------------------------------------
    # Asset Manager
    # --------------------------------------------------------

    if sic == 6282:
        return "asset_manager"

    if "investment advice" in desc:
        return "asset_manager"

    if "asset management" in name:
        return "asset_manager"

    if "investment management" in name:
        return "asset_manager"

    # --------------------------------------------------------
    # Insurance
    # --------------------------------------------------------

    insurance_sics = {
        6311, 6321, 6324,
        6331, 6351, 6361,
        6399, 6411,
    }

    if sic_in(sic, insurance_sics):
        return "insurance"

    if "insurance" in desc:
        return "insurance"

    # --------------------------------------------------------
    # Utility
    # --------------------------------------------------------

    if 4900 <= (sic or -1) <= 4999:
        return "utility"

    # --------------------------------------------------------
    # Telecom
    # --------------------------------------------------------

    if 4800 <= (sic or -1) <= 4899:
        return "telecom"

    if "telephone" in desc:
        return "telecom"

    if "telecommunications" in desc:
        return "telecom"

    # --------------------------------------------------------
    # Oil / Gas
    # --------------------------------------------------------

    oil_gas_sics = {
        1311,
        1381, 1382, 1389,
        2911,
        2999,
    }

    if sic_in(sic, oil_gas_sics):
        return "oil_gas"

    if "crude petroleum" in desc:
        return "oil_gas"

    if "petroleum refining" in desc:
        return "oil_gas"

    # --------------------------------------------------------
    # Midstream
    # --------------------------------------------------------

    midstream_words = (
        "pipeline",
        "natural gas transmission",
        "natural gas distribution",
    )

    if any(word in name for word in midstream_words):
        return "midstream"

    if any(word in desc for word in midstream_words):
        return "midstream"

    # --------------------------------------------------------
    # MLP
    # --------------------------------------------------------

    if "limited partnership" in name:
        return "mlp"

    # --------------------------------------------------------
    # Holding company
    # --------------------------------------------------------

    if "holding company" in desc:
        return "holding"

    # --------------------------------------------------------
    # SPAC
    # --------------------------------------------------------

    spac_words = (
        "acquisition corp",
        "acquisition company",
        "blank check",
    )

    if any(word in name for word in spac_words):
        return "spac"

    if "blank check" in desc:
        return "spac"

    # --------------------------------------------------------
    # Closed-end fund
    # --------------------------------------------------------

    if "closed-end" in desc:
        return "closed_end_fund"

    if "closed end" in desc:
        return "closed_end_fund"

    return "standard"


# ============================================================
# 9. FULL CLASSIFICATION
# ============================================================

def classify_company(
    ticker: str,
    company_name: str,
    sic: int | None,
    sic_desc: str,
) -> dict[str, Any]:

    ticker = ticker.upper().strip()

    # --------------------------------------------------------
    # 1. Override
    # --------------------------------------------------------

    if ticker in TEST_OVERRIDES:
        override = TEST_OVERRIDES[ticker]

        sector_common = override["sector_common"]
        company_type = override["company_type"]
        scoring_profile = override["scoring_profile"]

        return {
            "ticker": ticker,
            "company_name": company_name,
            "sic_code": sic,
            "sector_source": "SEC SIC",
            "sector_raw": sic_desc,
            "sector_common": sector_common,
            "sector_common_ko": COMMON_SECTORS[sector_common],
            "company_type": company_type,
            "scoring_profile": scoring_profile,
            "classification_reason": override["reason"],
            "classification_source": "manual_test_override",
        }

    # --------------------------------------------------------
    # 2. Company type
    # --------------------------------------------------------

    company_type = classify_company_type(
        ticker,
        company_name,
        sic,
        sic_desc,
    )

    # --------------------------------------------------------
    # 3. Common sector
    # --------------------------------------------------------

    sector_common = common_sector_from_sic(
        sic,
        sic_desc,
    )

    # --------------------------------------------------------
    # 4. Profile
    # --------------------------------------------------------

    scoring_profile = COMPANY_TYPE_TO_PROFILE.get(
        company_type,
        "standard",
    )

    return {
        "ticker": ticker,
        "company_name": company_name,
        "sic_code": sic,
        "sector_source": "SEC SIC",
        "sector_raw": sic_desc,
        "sector_common": sector_common,
        "sector_common_ko": COMMON_SECTORS[sector_common],
        "company_type": company_type,
        "scoring_profile": scoring_profile,
        "classification_reason": "automatic SIC/name classification",
        "classification_source": "automatic",
    }


# ============================================================
# 10. SEC CLIENT
# ============================================================

SEC_HEADERS = {
    "User-Agent": "Fundamental-app contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}


def fetch_json(
    session: requests.Session,
    url: str,
    retries: int = 3,
) -> dict:

    for attempt in range(retries):
        response = session.get(
            url,
            headers=SEC_HEADERS,
            timeout=30,
        )

        if response.status_code == 200:
            return response.json()

        if response.status_code in {
            429, 500, 502, 503, 504
        }:
            time.sleep(1.5 * (attempt + 1))
            continue

        response.raise_for_status()

    raise RuntimeError(
        f"SEC request failed: {url}"
    )


def get_sec_company(
    session: requests.Session,
    cik: str,
) -> dict:

    cik10 = str(cik).zfill(10)

    return fetch_json(
        session,
        f"https://data.sec.gov/submissions/CIK{cik10}.json",
    )


# ============================================================
# 11. COMPANY TICKER MASTER
# ============================================================

def load_sec_ticker_master(
    session: requests.Session,
) -> dict[str, dict]:

    data = fetch_json(
        session,
        "https://www.sec.gov/files/company_tickers.json",
    )

    result = {}

    for item in data.values():
        ticker = str(
            item.get("ticker", "")
        ).upper().strip()

        if not ticker:
            continue

        result[ticker] = {
            "ticker": ticker,
            "cik": str(item.get("cik_str", "")).zfill(10),
            "company_name": item.get(
                "title",
                "",
            ),
        }

    return result


# ============================================================
# 12. TEST 30
# ============================================================

def run_test30():

    session = requests.Session()

    print("=" * 110)
    print("US CLASSIFICATION ENGINE v1 — 30 COMPANY DRY RUN")
    print("=" * 110)

    master = load_sec_ticker_master(session)

    results = []

    for ticker, korean_name in TEST_COMPANIES.items():

        row = master.get(ticker)

        if not row:
            print(
                f"[MISSING SEC MASTER] {ticker}"
            )
            continue

        submissions = get_sec_company(
            session,
            row["cik"],
        )

        sic_raw = submissions.get("sic")

        try:
            sic = int(sic_raw)
        except (
            TypeError,
            ValueError,
        ):
            sic = None

        sic_desc = (
            submissions.get(
                "sicDescription"
            )
            or ""
        )

        result = classify_company(
            ticker=ticker,
            company_name=row["company_name"],
            sic=sic,
            sic_desc=sic_desc,
        )

        result["company_name_ko"] = korean_name
        result["cik"] = row["cik"]

        results.append(result)

        print(
            f"{ticker:<6} | "
            f"{korean_name:<16} | "
            f"SIC {str(sic):<5} | "
            f"{sic_desc[:32]:<32} | "
            f"{result['sector_common_ko']:<8} | "
            f"{result['company_type']:<18} | "
            f"{result['scoring_profile']}"
        )

        time.sleep(0.15)

    print("=" * 110)
    print(
        f"Completed: {len(results)}/{len(TEST_COMPANIES)}"
    )
    print("=" * 110)

    return results


# ============================================================
# 13. SINGLE TEST
# ============================================================

def run_single(ticker: str):

    session = requests.Session()

    master = load_sec_ticker_master(session)

    ticker = ticker.upper().strip()

    row = master.get(ticker)

    if not row:
        raise RuntimeError(
            f"{ticker} not found in SEC company master"
        )

    submissions = get_sec_company(
        session,
        row["cik"],
    )

    sic_raw = submissions.get("sic")

    try:
        sic = int(sic_raw)
    except (
        TypeError,
        ValueError,
    ):
        sic = None

    sic_desc = (
        submissions.get(
            "sicDescription"
        )
        or ""
    )

    result = classify_company(
        ticker,
        row["company_name"],
        sic,
        sic_desc,
    )

    for key, value in result.items():
        print(f"{key}: {value}")

    return result


# ============================================================
# 14. CLI
# ============================================================

def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--test30",
        action="store_true",
        help="Run representative 30-company classification test",
    )

    parser.add_argument(
        "--ticker",
        help="Classify one ticker",
    )

    args = parser.parse_args()

    if args.test30:
        run_test30()
        return

    if args.ticker:
        run_single(args.ticker)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
