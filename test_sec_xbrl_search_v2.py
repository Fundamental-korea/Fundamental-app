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
                best = result.get("best")
                if best:
                    print(f"BEST: {best.get('namespace')}:{best.get('concept')}")
                    print(f"VALUE: {best.get('value')}")
                    print(f"UNIT: {best.get('unit')}")
                    print(f"END: {best.get('end')}")
                    print(f"FY: {best.get('fy')}")
                    print(f"SOURCE: {best.get('source')}")
                    print(f"SCORE: {best.get('score')}")
                    print(f"REASON: {best.get('reason')}")
                else:
                    print("BEST: NONE")
            except Exception as e:
                print("ERROR:", type(e).__name__, str(e))


if __name__ == "__main__":
    main()
