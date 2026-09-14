import requests
from sec_xbrl_search_v2 import SECXBRLSearchV2

TEST_CASES = {
    "HON": ["interest_expense", "liabilities", "sga", "operating_income", "operating_cash_flow", "current_assets", "current_liabilities", "receivables", "inventory"],
    "DE": ["interest_expense", "liabilities", "sga", "operating_income", "operating_cash_flow", "current_assets", "current_liabilities", "receivables", "inventory"],
    "APD": ["interest_expense", "operating_cash_flow"],
    "NEM": ["interest_expense", "liabilities", "sga", "operating_income", "operating_cash_flow", "inventory"],
    "VZ": ["interest_expense", "liabilities", "receivables"],
    "T": ["interest_expense", "liabilities", "inventory"],
}

HEADERS = {"User-Agent": "Fundamental-app contact@example.com"}


def load_ticker_map():
    r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return {item["ticker"].upper(): str(item["cik_str"]) for item in r.json().values()}


def print_candidate(title, item):
    if not item:
        print(f"{title}: NONE")
        return
    print(f"{title}: {item.get('namespace')}:{item.get('concept')}")
    print(f"  value={item.get('value')} unit={item.get('unit')} end={item.get('end')} fy={item.get('fy')}")
    print(f"  source={item.get('source')} score={item.get('score')}")
    print(f"  reason={item.get('reason')}")


def main():
    ticker_map = load_ticker_map()
    engine = SECXBRLSearchV2(user_agent="Fundamental-app contact@example.com")

    for ticker, metrics in TEST_CASES.items():
        cik = ticker_map.get(ticker)
        print("\n" + "=" * 90)
        print(f"{ticker}  CIK={cik}")
        print("=" * 90)

        if not cik:
            print("CIK NOT FOUND")
            continue

        for metric in metrics:
            print(f"\n[{metric}]")
            try:
                result = engine.resolve(cik, metric)
                print_candidate("BEST", result.get("best"))
                print(f"  filing_fallback={result.get('filing_meta')}")

                cf = result.get("company_facts", [])
                fx = result.get("filing_xbrl", [])

                if cf:
                    print("  company_facts candidates:")
                    for item in cf[:3]:
                        print_candidate("    -", item)

                if fx:
                    print("  filing_xbrl candidates:")
                    for item in fx[:3]:
                        print_candidate("    -", item)

            except Exception as e:
                print("ERROR:", type(e).__name__, str(e))


if __name__ == "__main__":
    main()
