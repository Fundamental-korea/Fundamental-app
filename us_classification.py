"""
US company classification

Purpose
-------
SEC SIC 기반으로 미국 기업을 서비스용 공통 분류체계로 변환한다.

Output
------
{
    "sector_source": ...,
    "sector_raw": ...,
    "sector_common": ...,
    "sector_common_ko": ...,
    "company_type": ...,
    "scoring_profile": ...
}

주의
----
- sector_raw는 SEC SIC description을 그대로 보존
- sector_common은 서비스/UI용 공통 대분류
- company_type은 금융/REIT/BDC/Utility 등의 세부 유형
- scoring_profile은 실제 scoring.py 적용용
"""

from __future__ import annotations

import argparse
import os
import re
from typing import Optional


# ============================================================
# 1. COMMON SECTORS
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
# 2. COMPANY TYPES
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
# 3. SCORING PROFILES
# ============================================================

SCORING_PROFILES = {
    "standard",
    "financial",
    "reit",
    "bdc",
    "utility",
}


PROFILE_BY_COMPANY_TYPE = {
    "bank": "financial",
    "insurance": "financial",
    "asset_manager": "financial",
    "broker_dealer": "financial",
    "bdc": "bdc",
    "reit": "reit",
    "utility": "utility",
}


# ============================================================
# 4. MANUAL OVERRIDES
# ============================================================
#
# 중요한 예외기업.
#
# 실제 전체 universe에서는 이후
# US_Company_Classification_Overrides 테이블과 연결할 예정.
#
# 여기서는 classifier 자체가 독립적으로 테스트될 수 있도록
# 핵심 예외만 유지한다.
#

TEST_OVERRIDES = {
    "BLK": {
        "sector_common": "financials",
        "company_type": "asset_manager",
        "scoring_profile": "financial",
        "reason": "BlackRock is an asset manager; SEC SIC 6211 alone is insufficient.",
    },
    "GSBD": {
        "sector_common": "financials",
        "company_type": "bdc",
        "scoring_profile": "bdc",
        "reason": "Goldman Sachs BDC.",
    },
    "ARCC": {
        "sector_common": "financials",
        "company_type": "bdc",
        "scoring_profile": "bdc",
        "reason": "Ares Capital Corporation BDC.",
    },
}


# ============================================================
# 5. REPRESENTATIVE TEST COMPANIES
# ============================================================

TEST_COMPANIES = [
    # Technology
    "AAPL",
    "MSFT",
    "NVDA",
    "GOOGL",
    "AMZN",
    "META",
    "AVGO",
    "ORCL",

    # Financials
    "JPM",
    "BAC",
    "GS",
    "MS",
    "BLK",

    # Healthcare
    "JNJ",
    "PFE",
    "MRK",
    "LLY",

    # Real estate
    "O",
    "AMT",
    "PLD",

    # Utilities
    "NEE",
    "DUK",

    # Energy
    "XOM",
    "CVX",

    # Industrials / defense
    "RTX",
    "LMT",
    "BA",

    # BDC
    "GSBD",
    "ARCC",
]


# ============================================================
# 6. HELPERS
# ============================================================

def _normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""

    return re.sub(
        r"\s+",
        " ",
        str(value).strip().lower(),
    )


def _sic_int(sic: Optional[str | int]) -> Optional[int]:
    if sic is None:
        return None

    try:
        return int(str(sic).strip())
    except (TypeError, ValueError):
        return None


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


# ============================================================
# 7. COMMON SECTOR — SIC
# ============================================================

def common_sector_from_sic(
    sic: Optional[str | int],
    sic_desc: Optional[str] = None,
) -> str:
    """
    SEC SIC → common sector.

    중요한 원칙:
    1. 금융/부동산/유틸리티/커뮤니케이션은 우선 처리
    2. 의료/기술의 대표 SIC를 명시적으로 처리
    3. 소비재와 산업재의 경계는 description 보정
    4. 애매하면 other
    """

    sic_num = _sic_int(sic)
    desc = _normalize_text(sic_desc)

    if sic_num is None:
        return _sector_from_description(desc)

    # --------------------------------------------------------
    # REAL ESTATE
    # --------------------------------------------------------

    if 6500 <= sic_num <= 6799:
        return "real_estate"

    # --------------------------------------------------------
    # FINANCIALS
    # --------------------------------------------------------

    if 6000 <= sic_num <= 6499:
        return "financials"

    # --------------------------------------------------------
    # UTILITIES
    # --------------------------------------------------------

    if 4900 <= sic_num <= 4999:
        return "utilities"

    # --------------------------------------------------------
    # COMMUNICATION
    # --------------------------------------------------------

    if 4800 <= sic_num <= 4899:
        return "communication"

    # --------------------------------------------------------
    # HEALTHCARE
    # --------------------------------------------------------

    healthcare_sic_ranges = [
        (2833, 2836),   # Medicinal / Pharmaceutical
        (3841, 3851),   # Medical instruments / equipment
        (8000, 8099),   # Health services
    ]

    for lo, hi in healthcare_sic_ranges:
        if lo <= sic_num <= hi:
            return "healthcare"

    # --------------------------------------------------------
    # TECHNOLOGY
    # --------------------------------------------------------

    technology_sic_ranges = [
        (3570, 3579),   # Computer / office equipment
        (3660, 3669),   # Communication equipment
        (3670, 3679),   # Electronic components / semiconductors
        (3812, 3812),   # Search / navigation / guidance
        (3823, 3829),   # Industrial measurement / controls
        (7370, 7379),   # Computer programming / data processing
    ]

    for lo, hi in technology_sic_ranges:
        if lo <= sic_num <= hi:
            return "technology"

    # --------------------------------------------------------
    # ENERGY
    # --------------------------------------------------------
    #
    # 1200~1399에는 oil/gas extraction 및 related services가
    # 포함된다.
    #
    # 단, mining 전체를 energy로 보면 안 되기 때문에
    # 1000~1199는 materials로 남긴다.
    #

    energy_sic_ranges = [
        (1200, 1399),
        (2911, 2911),
        (2999, 2999),
    ]

    for lo, hi in energy_sic_ranges:
        if lo <= sic_num <= hi:
            return "energy"

    # --------------------------------------------------------
    # MATERIALS
    # --------------------------------------------------------

    materials_sic_ranges = [
        (1000, 1199),   # Metal / coal mining
        (1400, 1499),   # Nonmetallic minerals
        (2800, 2829),   # Chemicals
        (2850, 2899),   # Chemical products
        (3200, 3299),   # Stone / clay / glass
        (3300, 3399),   # Primary metal industries
    ]

    for lo, hi in materials_sic_ranges:
        if lo <= sic_num <= hi:
            return "materials"

    # --------------------------------------------------------
    # CONSUMER
    # --------------------------------------------------------
    #
    # 핵심:
    #
    # PG = 2840 → consumer
    # PM = 2111 → consumer
    #
    # 기존 버전에서 5000~5999만 소비재로 잡았기 때문에
    # PG / PM 같은 제조업 소비재가 other로 빠졌음.
    #

    consumer_sic_ranges = [
        (2000, 2399),   # Food / tobacco / textile / apparel
        (2500, 2599),   # Furniture
        (2600, 2699),   # Paper products
        (2700, 2799),   # Printing / publishing
        (2840, 2849),   # Soap / cosmetics / personal products
        (3100, 3199),   # Leather / footwear
        (3900, 3999),   # Misc manufacturing
        (5000, 5999),   # Wholesale / retail
        (7000, 7999),   # Services / leisure / consumer services
    ]

    for lo, hi in consumer_sic_ranges:
        if lo <= sic_num <= hi:
            return "consumer"

    # --------------------------------------------------------
    # INDUSTRIALS
    # --------------------------------------------------------
    #
    # Materials와 겹치는 SIC는 위에서 먼저 처리한다.
    #

    industrial_sic_ranges = [
        (1500, 1799),   # Construction
        (3000, 3099),   # Rubber / plastics
        (3400, 3499),   # Fabricated metal
        (3500, 3599),   # Machinery
        (3600, 3669),   # Electrical equipment
        (3700, 3799),   # Transportation equipment
        (3800, 3839),   # Instruments / measurement
    ]

    for lo, hi in industrial_sic_ranges:
        if lo <= sic_num <= hi:
            return "industrials"

    # --------------------------------------------------------
    # DESCRIPTION FALLBACK
    # --------------------------------------------------------

    return _sector_from_description(desc)


# ============================================================
# 8. DESCRIPTION FALLBACK
# ============================================================

def _sector_from_description(desc: str) -> str:

    if not desc:
        return "other"

    # --------------------------------------------------------
    # Healthcare
    # --------------------------------------------------------

    if _contains_any(
        desc,
        [
            "pharmaceutical",
            "medicinal",
            "medical",
            "surgical",
            "health care",
            "healthcare",
            "hospital",
            "diagnostic",
            "laboratory",
            "biological products",
            "biotechnology",
        ],
    ):
        return "healthcare"

    # --------------------------------------------------------
    # Technology
    # --------------------------------------------------------

    if _contains_any(
        desc,
        [
            "semiconductor",
            "software",
            "computer",
            "electronic",
            "data processing",
            "information retrieval",
            "prepackaged software",
            "computer programming",
            "communication equipment",
            "electronic component",
        ],
    ):
        return "technology"

    # --------------------------------------------------------
    # Energy
    # --------------------------------------------------------

    if _contains_any(
        desc,
        [
            "crude petroleum",
            "natural gas",
            "petroleum",
            "oil and gas",
            "oil & gas",
            "petroleum refining",
            "oilfield",
            "drilling",
            "exploration",
        ],
    ):
        return "energy"

    # --------------------------------------------------------
    # Utilities
    # --------------------------------------------------------

    if _contains_any(
        desc,
        [
            "electric services",
            "natural gas transmission",
            "natural gas distribution",
            "water supply",
            "sanitary services",
            "gas services",
            "utility",
        ],
    ):
        return "utilities"

    # --------------------------------------------------------
    # Communication
    # --------------------------------------------------------

    if _contains_any(
        desc,
        [
            "telephone",
            "telegraph",
            "radio",
            "television",
            "broadcasting",
            "cable",
            "communications",
            "telecommunications",
        ],
    ):
        return "communication"

    # --------------------------------------------------------
    # Financials
    # --------------------------------------------------------

    if _contains_any(
        desc,
        [
            "bank",
            "commercial bank",
            "savings institution",
            "security broker",
            "dealer",
            "investment advice",
            "investment company",
            "asset management",
            "insurance",
            "credit",
            "financial services",
        ],
    ):
        return "financials"

    # --------------------------------------------------------
    # Real Estate
    # --------------------------------------------------------

    if _contains_any(
        desc,
        [
            "real estate",
            "reit",
            "realty",
            "real estate investment",
        ],
    ):
        return "real_estate"

    # --------------------------------------------------------
    # Materials
    # --------------------------------------------------------

    if _contains_any(
        desc,
        [
            "steel",
            "aluminum",
            "metal mining",
            "gold",
            "silver",
            "copper",
            "cement",
            "building materials",
            "chemicals",
            "industrial chemicals",
            "mineral",
            "paper mill",
            "paper products",
        ],
    ):
        return "materials"

    # --------------------------------------------------------
    # Consumer
    # --------------------------------------------------------

    if _contains_any(
        desc,
        [
            "cigarettes",
            "tobacco",
            "food",
            "beverages",
            "apparel",
            "clothing",
            "cosmetics",
            "perfumes",
            "soap",
            "detergents",
            "retail",
            "restaurants",
            "hotels",
            "amusement",
            "motion picture",
            "consumer",
        ],
    ):
        return "consumer"

    # --------------------------------------------------------
    # Industrials
    # --------------------------------------------------------

    if _contains_any(
        desc,
        [
            "construction",
            "machinery",
            "industrial machinery",
            "aerospace",
            "aircraft",
            "defense",
            "transportation equipment",
            "fabricated metal",
            "engineering",
        ],
    ):
        return "industrials"

    return "other"


# ============================================================
# 9. COMPANY TYPE
# ============================================================

def classify_company_type(
    ticker: str,
    company_name: str,
    sic: Optional[str | int],
    sic_desc: Optional[str],
) -> str:

    ticker = (ticker or "").upper().strip()
    name = _normalize_text(company_name)
    desc = _normalize_text(sic_desc)

    sic_num = _sic_int(sic)

    # --------------------------------------------------------
    # Manual override
    # --------------------------------------------------------

    if ticker in TEST_OVERRIDES:
        return TEST_OVERRIDES[ticker]["company_type"]

    # --------------------------------------------------------
    # REIT
    # --------------------------------------------------------

    if sic_num == 6798:
        return "reit"

    if _contains_any(
        desc,
        [
            "real estate investment trust",
            "reit",
        ],
    ):
        return "reit"

    if re.search(r"\breit\b", name):
        return "reit"

    # --------------------------------------------------------
    # BDC
    # --------------------------------------------------------

    if _contains_any(
        name,
        [
            "business development company",
            "bdc",
        ],
    ):
        return "bdc"

    # Common BDC wording
    if _contains_any(
        desc,
        [
            "business development",
            "closed-end investment company",
        ],
    ):
        if "capital" in name or "income" in name or "bdc" in name:
            return "bdc"

    # --------------------------------------------------------
    # BANK
    # --------------------------------------------------------

    bank_sic = {
        6021,
        6022,
        6029,
        6035,
        6036,
    }

    if sic_num in bank_sic:
        return "bank"

    if _contains_any(
        desc,
        [
            "national commercial bank",
            "commercial banks",
            "state commercial banks",
            "savings institutions",
            "federal savings",
        ],
    ):
        return "bank"

    # --------------------------------------------------------
    # BROKER / DEALER
    # --------------------------------------------------------

    broker_sic = {
        6200,
        6211,
        6221,
    }

    if sic_num in broker_sic:
        return "broker_dealer"

    if _contains_any(
        desc,
        [
            "security brokers",
            "security dealers",
            "investment bankers",
            "broker dealers",
            "commodity contracts",
        ],
    ):
        return "broker_dealer"

    # --------------------------------------------------------
    # ASSET MANAGER
    # --------------------------------------------------------

    if sic_num == 6282:
        return "asset_manager"

    if _contains_any(
        desc,
        [
            "investment advice",
            "asset management",
            "investment management",
            "portfolio management",
        ],
    ):
        return "asset_manager"

    if _contains_any(
        name,
        [
            "asset management",
            "capital management",
            "investment management",
        ],
    ):
        return "asset_manager"

    # --------------------------------------------------------
    # INSURANCE
    # --------------------------------------------------------

    insurance_sic = {
        6311,
        6321,
        6324,
        6331,
        6351,
        6361,
        6399,
        6411,
    }

    if sic_num in insurance_sic:
        return "insurance"

    if "insurance" in desc:
        return "insurance"

    # --------------------------------------------------------
    # UTILITY
    # --------------------------------------------------------

    if sic_num is not None and 4900 <= sic_num <= 4999:
        return "utility"

    if _contains_any(
        desc,
        [
            "electric services",
            "natural gas transmission",
            "natural gas distribution",
            "water supply",
            "utility",
        ],
    ):
        return "utility"

    # --------------------------------------------------------
    # TELECOM
    # --------------------------------------------------------

    if sic_num is not None and 4800 <= sic_num <= 4899:
        return "telecom"

    if _contains_any(
        desc,
        [
            "telephone communications",
            "telecommunications",
            "telephone",
            "cable television",
            "broadcasting",
        ],
    ):
        return "telecom"

    # --------------------------------------------------------
    # OIL / GAS
    # --------------------------------------------------------

    oil_gas_sic = {
        1311,
        1381,
        1382,
        1389,
        2911,
        2999,
    }

    if sic_num in oil_gas_sic:
        return "oil_gas"

    if _contains_any(
        desc,
        [
            "crude petroleum",
            "natural gas",
            "petroleum refining",
            "oil and gas",
            "oil & gas",
            "oilfield",
            "drilling",
        ],
    ):
        return "oil_gas"

    # --------------------------------------------------------
    # MIDSTREAM
    # --------------------------------------------------------

    if _contains_any(
        name,
        [
            "midstream",
            "pipeline",
            "partners",
        ],
    ):
        if _contains_any(
            desc,
            [
                "natural gas",
                "petroleum",
                "pipeline",
                "crude petroleum",
            ],
        ):
            return "midstream"

    if _contains_any(
        desc,
        [
            "pipeline transportation",
            "natural gas transmission",
        ],
    ):
        return "midstream"

    # --------------------------------------------------------
    # MLP
    # --------------------------------------------------------

    if _contains_any(
        name,
        [
            "limited partnership",
            "lp",
        ],
    ):
        if "energy" in name or "midstream" in name or "pipeline" in name:
            return "mlp"

    # --------------------------------------------------------
    # HOLDING COMPANY
    # --------------------------------------------------------

    if "holding company" in desc:
        return "holding"

    if re.search(r"\bholdings?\b", name):
        return "holding"

    # --------------------------------------------------------
    # SPAC
    # --------------------------------------------------------

    if _contains_any(
        name,
        [
            "acquisition corp",
            "acquisition corporation",
            "blank check",
        ],
    ):
        return "spac"

    if _contains_any(
        desc,
        [
            "blank check",
            "shell companies",
        ],
    ):
        return "spac"

    # --------------------------------------------------------
    # CLOSED-END FUND
    # --------------------------------------------------------

    if _contains_any(
        desc,
        [
            "closed-end management investment company",
            "closed-end investment company",
        ],
    ):
        return "closed_end_fund"

    return "standard"


# ============================================================
# 10. COMPANY CLASSIFICATION
# ============================================================

def classify_company(
    ticker: str,
    company_name: str,
    sic: Optional[str | int],
    sic_desc: Optional[str],
) -> dict:

    ticker = (ticker or "").upper().strip()

    # --------------------------------------------------------
    # 1. Manual override
    # --------------------------------------------------------

    if ticker in TEST_OVERRIDES:

        override = TEST_OVERRIDES[ticker]

        sector_common = override["sector_common"]
        company_type = override["company_type"]
        scoring_profile = override["scoring_profile"]

        return {
            "sector_source": "SEC_SIC",
            "sector_raw": sic_desc,
            "sector_common": sector_common,
            "sector_common_ko": COMMON_SECTORS[sector_common],
            "company_type": company_type,
            "scoring_profile": scoring_profile,
        }

    # --------------------------------------------------------
    # 2. Company type
    # --------------------------------------------------------

    company_type = classify_company_type(
        ticker=ticker,
        company_name=company_name,
        sic=sic,
        sic_desc=sic_desc,
    )

    # --------------------------------------------------------
    # 3. Common sector
    # --------------------------------------------------------

    sector_common = common_sector_from_sic(
        sic=sic,
        sic_desc=sic_desc,
    )

    # --------------------------------------------------------
    # 4. Financial special handling
    # --------------------------------------------------------

    if company_type in {
        "bank",
        "insurance",
        "asset_manager",
        "broker_dealer",
        "bdc",
    }:
        sector_common = "financials"

    elif company_type in {
        "reit",
        "real_estate_company",
    }:
        sector_common = "real_estate"

    elif company_type == "utility":
        sector_common = "utilities"

    elif company_type == "telecom":
        sector_common = "communication"

    elif company_type in {
        "oil_gas",
        "midstream",
        "mlp",
    }:
        sector_common = "energy"

    # --------------------------------------------------------
    # 5. Scoring profile
    # --------------------------------------------------------

    scoring_profile = PROFILE_BY_COMPANY_TYPE.get(
        company_type,
        "standard",
    )

    return {
        "sector_source": "SEC_SIC",
        "sector_raw": sic_desc,
        "sector_common": sector_common,
        "sector_common_ko": COMMON_SECTORS.get(
            sector_common,
            COMMON_SECTORS["other"],
        ),
        "company_type": company_type,
        "scoring_profile": scoring_profile,
    }


# ============================================================
# 11. SEC HELPERS
# ============================================================

SEC_HEADERS = {
    "User-Agent": "Fundamental Korea research contact@example.com"
}


def get_sec_submissions(ticker: str) -> dict:
    """
    SEC submissions API에서 ticker → CIK → submission data 조회.

    실제 실행은 requests가 설치된 환경에서 한다.
    """

    import requests

    ticker = ticker.upper().strip()

    headers = SEC_HEADERS

    # --------------------------------------------------------
    # SEC company tickers
    # --------------------------------------------------------

    tickers_url = "https://www.sec.gov/files/company_tickers.json"

    response = requests.get(
        tickers_url,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()

    found = None

    for item in data.values():

        if item.get("ticker", "").upper() == ticker:
            found = item
            break

    if not found:
        raise ValueError(
            f"SEC company ticker not found: {ticker}"
        )

    cik = str(found["cik_str"]).zfill(10)

    submissions_url = (
        f"https://data.sec.gov/submissions/CIK{cik}.json"
    )

    response = requests.get(
        submissions_url,
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    return response.json()


# ============================================================
# 12. SINGLE COMPANY TEST
# ============================================================

def run_single_test(ticker: str) -> None:

    submissions = get_sec_submissions(ticker)

    name = submissions.get("name") or ticker
    sic = submissions.get("sic")
    sic_desc = submissions.get("sicDescription")

    result = classify_company(
        ticker=ticker,
        company_name=name,
        sic=sic,
        sic_desc=sic_desc,
    )

    print()
    print("=" * 80)
    print(f"{ticker.upper()} | {name}")
    print("=" * 80)
    print(f"SIC              : {sic}")
    print(f"SIC description  : {sic_desc}")
    print(f"sector_common    : {result['sector_common']}")
    print(f"sector_common_ko : {result['sector_common_ko']}")
    print(f"company_type     : {result['company_type']}")
    print(f"scoring_profile  : {result['scoring_profile']}")
    print()


# ============================================================
# 13. REPRESENTATIVE TEST
# ============================================================

def run_test30() -> None:

    print("=" * 100)
    print("US CLASSIFICATION REPRESENTATIVE TEST")
    print(f"Companies: {len(TEST_COMPANIES)}")
    print("=" * 100)

    success = 0
    failed = 0

    for idx, ticker in enumerate(TEST_COMPANIES, start=1):

        try:

            submissions = get_sec_submissions(ticker)

            name = submissions.get("name") or ticker
            sic = submissions.get("sic")
            sic_desc = submissions.get("sicDescription")

            result = classify_company(
                ticker=ticker,
                company_name=name,
                sic=sic,
                sic_desc=sic_desc,
            )

            print(
                f"[{idx:02d}/{len(TEST_COMPANIES)}] "
                f"{ticker:<6} | "
                f"{result['sector_common']:<13} | "
                f"{result['company_type']:<18} | "
                f"{result['scoring_profile']}"
            )

            success += 1

        except Exception as e:

            print(
                f"[{idx:02d}/{len(TEST_COMPANIES)}] "
                f"{ticker:<6} | FAILED | {e}"
            )

            failed += 1

    print()
    print("-" * 100)
    print(f"Success : {success}")
    print(f"Failed  : {failed}")
    print("-" * 100)


# ============================================================
# 14. LOCAL SIC TEST
# ============================================================

def run_sic_tests() -> None:

    cases = [
        ("PG", "Procter & Gamble", 2840,
         "Soap, Detergents, Cleaning Preparations, Perfumes, Cosmetics"),

        ("PM", "Philip Morris", 2111,
         "Cigarettes"),

        ("CYATY", "China Yatai", 2834,
         "Pharmaceutical Preparations"),

        ("AAPL", "Apple", 3571,
         "Electronic Computers"),

        ("MSFT", "Microsoft", 7372,
         "Services-Prepackaged Software"),

        ("NVDA", "NVIDIA", 3674,
         "Semiconductors & Related Devices"),

        ("JPM", "JPMorgan Chase", 6021,
         "National Commercial Banks"),

        ("GS", "Goldman Sachs", 6211,
         "Security Brokers, Dealers & Flot"),

        ("MS", "Morgan Stanley", 6211,
         "Security Brokers, Dealers & Flot"),

        ("BLK", "BlackRock", 6211,
         "Security Brokers, Dealers & Flot"),
    ]

    print("=" * 100)
    print("LOCAL SIC MAPPING TEST")
    print("=" * 100)

    for ticker, name, sic, desc in cases:

        result = classify_company(
            ticker=ticker,
            company_name=name,
            sic=sic,
            sic_desc=desc,
        )

        print(
            f"{ticker:<6} | "
            f"SIC {sic:<5} | "
            f"{result['sector_common']:<13} | "
            f"{result['company_type']:<18} | "
            f"{result['scoring_profile']}"
        )


# ============================================================
# 15. CLI
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="US SEC/SIC company classification"
    )

    parser.add_argument(
        "--ticker",
        type=str,
        help="Test a single ticker",
    )

    parser.add_argument(
        "--test30",
        action="store_true",
        help="Run representative company test",
    )

    parser.add_argument(
        "--test-sic",
        action="store_true",
        help="Run local SIC mapping tests",
    )

    args = parser.parse_args()

    if args.ticker:
        run_single_test(args.ticker)
        return

    if args.test_sic:
        run_sic_tests()
        return

    if args.test30:
        run_test30()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
