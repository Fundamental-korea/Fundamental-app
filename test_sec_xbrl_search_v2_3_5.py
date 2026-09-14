"""Read-only regression for SEC XBRL V2.3.5."""
from __future__ import annotations

import os
import requests
from sec_xbrl_search_v2_3_5 import SECXBRLSearchV2_3_5

TICKERS = {"HON":"773840","VZ":"732712","T":"732717","NEM":"1164727","DE":"315189","META":"1326801","APD":"2969","LIN":"1707925"}
METRICS = {
    "HON":["liabilities"], "VZ":["liabilities","receivables"], "T":["liabilities","inventory"],
    "NEM":["interest_expense","inventory","sga","operating_income"],
    "DE":["operating_income","current_assets","current_liabilities"],
    "META":["sga"], "APD":["interest_expense","operating_cash_flow"], "LIN":["interest_expense"]
}

def main():
    session=requests.Session(); session.headers.update({"User-Agent":os.environ.get("SEC_USER_AGENT","Fundamental-app contact@example.com")})
    resolver=SECXBRLSearchV2_3_5(session=session)
    for ticker,cik in TICKERS.items():
        submissions=resolver.submissions(cik); target=resolver._latest_annual_fy(submissions)
        print("="*80); print(ticker,"target_fy=",target)
        for metric in METRICS[ticker]:
            candidates,meta=resolver.search_filing(cik,metric,year=target,submissions=submissions,limit=5)
            print("-",metric,"candidates=",len(candidates))
            for c in candidates[:5]: print("  ",c.concept,"|",c.value,"| score=",c.score,"|",c.reason)
            for k in ("derived_balance_sheet", "derived_operating_income", "derived_sga"):
                if k in meta: print("   ",k,"=",meta[k])

if __name__ == "__main__": main()
