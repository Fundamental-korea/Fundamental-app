"""Refined US classification wrapper.

Keeps the original SEC/SIC classifier intact while adding a service-level
layer for business-model exceptions that SIC alone cannot represent well.
"""
from __future__ import annotations

from us_classification import (
    COMMON_SECTORS,
    classify_company as _base_classify_company,
)

# Business-model exceptions. This list is intentionally conservative: only
# companies whose primary investment identity is clearly different from their
# SIC bucket are overridden here. It can later move to a DB override table.
DEFENSE_TICKERS = {
    "AVAV", "BA", "GD", "HII", "LHX", "LMT", "NOC", "RTX", "TDG",
    "KTOS", "LDOS", "TXT", "HWM", "CW", "HEI", "SPR", "DRS", "PLTR",
}

MATERIALS_TICKERS = {
    "RGLD",  # Royal Gold: mining/royalty, not real estate
    "WPM", "FNV", "NEM", "AEM", "GOLD", "KGC", "AGI",
}

# Known foreign/holding-name collision. The collector should still validate
# the SEC CIK mapping; this prevents a misleading company-level sector result.
KNOWN_ENERGY_TICKERS = {"XOM", "CVX", "COP", "EOG", "OXY", "MPC", "PSX", "VLO"}


def classify_company(ticker: str, company_name: str, sic, sic_desc):
    ticker = (ticker or "").upper().strip()
    result = _base_classify_company(ticker, company_name, sic, sic_desc)

    # Defense is a distinct top-level sector for investment analysis.
    if ticker in DEFENSE_TICKERS:
        result["sector_common"] = "defense"
        result["sector_common_ko"] = "방산"
        result["company_type"] = "defense"
        result["scoring_profile"] = "defense"
        result["sector_source"] = "SEC_SIC_PLUS_OVERRIDE"
        return result

    # Mining/royalty companies belong with materials rather than real estate.
    if ticker in MATERIALS_TICKERS:
        result["sector_common"] = "materials"
        result["sector_common_ko"] = COMMON_SECTORS["materials"]
        result["company_type"] = "mining_royalty"
        result["scoring_profile"] = "standard"
        result["sector_source"] = "SEC_SIC_PLUS_OVERRIDE"
        return result

    if ticker in KNOWN_ENERGY_TICKERS and result.get("sector_common") == "energy":
        result["company_type"] = "oil_gas"
        result["scoring_profile"] = "energy"
        return result

    return result
