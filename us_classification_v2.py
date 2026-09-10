"""Refined US classification wrapper.

Keeps the original SEC/SIC classifier intact while adding a service-level
layer for business-model exceptions that SIC alone cannot represent well.
"""
from __future__ import annotations

from us_classification import COMMON_SECTORS, classify_company as _base_classify_company

COMMON_SECTORS.setdefault("defense", "방산")

DEFENSE_TICKERS = {
    "AVAV", "BA", "GD", "HII", "LHX", "LMT", "NOC", "RTX", "TDG",
    "KTOS", "LDOS", "TXT", "HWM", "CW", "HEI", "SPR", "DRS",
}
MATERIALS_TICKERS = {"RGLD", "WPM", "FNV", "NEM", "AEM", "GOLD", "KGC", "AGI"}
KNOWN_ENERGY_TICKERS = {"XOM", "CVX", "COP", "EOG", "OXY", "MPC", "PSX", "VLO"}


def classify_company(ticker: str, company_name: str, sic, sic_desc):
    ticker = (ticker or "").upper().strip()
    result = _base_classify_company(ticker, company_name, sic, sic_desc)

    if ticker in DEFENSE_TICKERS:
        result.update({"sector_common": "defense", "sector_common_ko": "방산", "company_type": "defense", "scoring_profile": "defense", "sector_source": "SEC_SIC_PLUS_OVERRIDE"})
        return result

    if ticker in MATERIALS_TICKERS:
        result.update({"sector_common": "materials", "sector_common_ko": COMMON_SECTORS["materials"], "company_type": "mining_royalty", "scoring_profile": "standard", "sector_source": "SEC_SIC_PLUS_OVERRIDE"})
        return result

    if result.get("sector_common") == "industrials":
        result["sector_common_ko"] = "산업"

    if ticker in KNOWN_ENERGY_TICKERS and result.get("sector_common") == "energy":
        result["company_type"] = "oil_gas"
        result["scoring_profile"] = "standard"

    return result
