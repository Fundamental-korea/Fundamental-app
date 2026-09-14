"""Read-only SEC XBRL V2.3.2 regression test.

Exercises the generic resolver against problematic Standard-sector issuers.
No Supabase writes. Raw SEC responses remain in memory only.
"""
from __future__ import annotations

import time
import requests

from sec_xbrl_search_v2_3_2 import SECXBRLSearchV2_3_2

SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
TEST_CASES = {
    "HON": ["liabilities", "interest_expense", "sga", "operating_income"],
    "VZ": ["liabilities", "receivables", "interest_expense"],
    "T": ["liabilities", "inventory", "interest_expense"],
    "NEM": ["liabilities", "interest_expense", "inventory", "sga", "operating_income"],
    "DE": ["liabilities", "operating_income", "current_assets", "current_liabilities", "interest_expense"],
    "APD": ["liabilities", "interest_expense", "operating_cash_flow"],
    "LIN": ["liabilities", "interest_expense", "operating_income", "sga"],
    "META": ["liabilities", "interest_expense", "operating_income", "sga"],
}

session = requests.Session()
session.headers.update({
    "User-Agent": "Fundamental-app regression-test contact@example.com",
    "Accept-Encoding": "gzip, deflate",
})


def load_ticker_map():
    r = session.get(SEC_TICKER_URL, timeout=30)
    r.raise_for_status()
    return {
        str(v.get("ticker", "")).upper().strip(): str(int(v["cik_str"]))
        for v in r.json().values()
        if v.get("ticker") and v.get("cik_str")
    }


def fmt(v):
    if v is None:
        return "NONE"
    if abs(v) >= 1e9:
        return f"{v/1e9:.3f}B"
    if abs(v) >= 1e6:
        return f"{v/1e6:.3f}M"
    return f"{v:.6f}"


def show(c, prefix="  "):
    if not c:
        print(prefix + "NONE")
        return
    print(
        f"{prefix}{c.get('namespace')}:{c.get('concept')} | "
        f"label='{c.get('label','')}' | value={fmt(c.get('value'))} | "
        f"end={c.get('end')} | start={c.get('start')} | fy={c.get('fy')} | "
        f"form={c.get('form')} | source={c.get('source')} | score={c.get('score')} | "
        f"reason={c.get('reason')}"
    )


def main():
    print("=" * 100)
    print("SEC XBRL SEARCH V2.3.2 REGRESSION TEST")
    print("=" * 100)
    ticker_map = load_ticker_map()
    print(f"\nSEC ticker map: {len(ticker_map):,} tickers")
    engine = SECXBRLSearchV2_3_2(user_agent="Fundamental-app regression-test contact@example.com")

    total = passed = review = 0
    for ticker, metrics in TEST_CASES.items():
        print("\n" + "=" * 100)
        print(ticker)
        print("=" * 100)
        cik = ticker_map.get(ticker)
        if not cik:
            print("CIK: NOT FOUND")
            continue
        submissions = engine.submissions(cik)
        annual_fy = engine._latest_annual_fy(submissions)
        accession, doc, filed = engine.latest_annual_filing(submissions)
        print(f"CIK: {cik}")
        print(f"Latest annual FY : {annual_fy}")
        print(f"Annual accession : {accession}")
        print(f"Primary document : {doc}")
        print(f"Filed            : {filed}")

        for metric in metrics:
            total += 1
            print("\n" + "-" * 100)
            print(f"METRIC: {metric}")
            print("-" * 100)
            try:
                result = engine.resolve(cik=cik, metric=metric, year=None, suspicious=False, limit=10)
            except Exception as e:
                review += 1
                print(f"ERROR: {e}")
                continue

            print(f"Target FY: {result.get('year')}")
            cf = result.get("company_facts", [])
            fx = result.get("filing_xbrl", [])
            print(f"\nCompany Facts candidates: {len(cf)}")
            for i, c in enumerate(cf[:5], 1): show(c, f"  CF #{i}: ")
            print(f"\nFiling XBRL candidates: {len(fx)}")
            for i, c in enumerate(fx[:5], 1): show(c, f"  FX #{i}: ")
            print("\nBEST:")
            best = result.get("best")
            show(best)

            reasons = []
            if best is None:
                reasons.append("NO_RESULT")
            else:
                if annual_fy is not None and result.get("year") != annual_fy:
                    reasons.append(f"WRONG_TARGET_FY expected={annual_fy}")
                end = best.get("end")
                if end and result.get("year") is not None and str(end)[:4] != str(result["year"]):
                    reasons.append(f"WRONG_END_YEAR end={end}")
                if metric == "liabilities":
                    bad = ("liabilitiesandstockholdersequity", "liabilitiescurrent", "longtermdebt", "debtcurrent", "accruedliabilities", "operatingleaseliabilities")
                    compact = (str(best.get("concept", "")) + str(best.get("label", ""))).lower().replace(" ", "")
                    for x in bad:
                        if x in compact: reasons.append(f"COMPONENT_LIABILITY:{x}")
                if metric == "interest_expense":
                    bad = ("interestpaid", "interestcostscapitalized", "interestincome", "financeleaseinterestexpense", "unrecognizedtaxbenefits", "taxpenalties")
                    compact = (str(best.get("concept", "")) + str(best.get("label", ""))).lower().replace(" ", "")
                    for x in bad:
                        if x in compact: reasons.append(f"BAD_INTEREST:{x}")
                if metric == "inventory":
                    bad = ("orestockpiles", "leachpads", "finishedgoods", "workinprocess", "rawmaterials", "suppliesinventory")
                    compact = (str(best.get("concept", "")) + str(best.get("label", ""))).lower().replace(" ", "")
                    for x in bad:
                        if x in compact: reasons.append(f"COMPONENT_INVENTORY:{x}")
                if metric == "operating_income":
                    compact = (str(best.get("concept", "")) + str(best.get("label", ""))).lower().replace(" ", "")
                    if "nonoperating" in compact: reasons.append("NONOPERATING_INCOME")
                if metric == "current_assets" and "noncurrent" in str(best.get("concept", "")).lower():
                    reasons.append("NONCURRENT_ASSET")
                if metric == "current_liabilities" and "noncurrent" in str(best.get("concept", "")).lower():
                    reasons.append("NONCURRENT_LIABILITY")

            if reasons:
                review += 1
                print("\nRESULT: REVIEW")
                for r in reasons: print(f"  - {r}")
            else:
                passed += 1
                print("\nRESULT: PASS")
            time.sleep(0.15)

    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print(f"TOTAL  : {total}")
    print(f"PASS   : {passed}")
    print(f"REVIEW : {review}")
    print(f"PASS % : {passed / total * 100:.1f}%" if total else "PASS % : 0.0%")
    print("=" * 100)


if __name__ == "__main__":
    main()
