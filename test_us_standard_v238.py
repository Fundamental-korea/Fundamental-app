"""Read-only regression for Standard-sector V2.3.8 collector integration."""
from __future__ import annotations

import requests

from collector_us_fundamental import SEC_USER_AGENT, load_company
from collector_us_standard_fallback import build_result_with_v238

TICKERS = {
    "technology": ["AAPL", "MSFT", "NVDA"],
    "consumer": ["COST", "WMT", "AMZN"],
    "industrials": ["CAT", "HON", "DE"],
    "materials": ["LIN", "APD", "NEM"],
    "communication": ["META", "VZ", "T"],
}

CIKS = {
    "AAPL": "320193", "MSFT": "789019", "NVDA": "1045810",
    "COST": "909832", "WMT": "104169", "AMZN": "1018724",
    "CAT": "18230", "HON": "773840", "DE": "315189",
    "LIN": "1707925", "APD": "2969", "NEM": "1164727",
    "META": "1326801", "VZ": "732712", "T": "732717",
}


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8
    resolver = SECXBRLSearchV2_3_8(session=session)

    passed = 0
    reviewed = 0
    print("=" * 100)
    print("STANDARD V2.3.8 COLLECTOR INTEGRATION REGRESSION")
    print("=" * 100)

    for sector, tickers in TICKERS.items():
        for ticker in tickers:
            cik = CIKS[ticker]
            try:
                facts, submissions = load_company(session, ticker, cik)
                result = build_result_with_v238(
                    ticker, cik, submissions.get("name") or ticker,
                    facts, submissions,
                    universe_row={"sector_common": sector, "scoring_profile": "standard"},
                    market_prices={"market": None, "stock": None},
                    resolver=resolver,
                )
                missing = result["missing_metric_count"]
                status = "PASS" if result["period_scores"] and missing <= 2 else "REVIEW"
                if status == "PASS":
                    passed += 1
                else:
                    reviewed += 1
                latest = result["period_scores"].get("1", {})
                metrics = latest.get("metrics", {})
                print(f"{sector:13} {ticker:5} score={result['total_score']} grade={result['grade']} missing={missing} {status}")
                print("  " + " | ".join(f"{k}={metrics.get(k)}" for k in (
                    "revenue_growth", "eps_growth", "opm", "roic", "debt_rate",
                    "quick_ratio", "interest_coverage", "ocf_ratio", "sga_ratio",
                )))
            except Exception as exc:
                reviewed += 1
                print(f"{sector:13} {ticker:5} FAILED: {exc}")

    print("=" * 100)
    print(f"PASS={passed} REVIEW={reviewed}")


if __name__ == "__main__":
    main()
