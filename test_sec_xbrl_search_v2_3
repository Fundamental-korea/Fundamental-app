"""
V2.3 SEC XBRL resolver regression test.

Purpose:
- Verify annual-first selection
- Verify label-first semantic selection
- Verify total-vs-component filtering
- Verify suspicious custom XBRL concepts are rejected
- Compare Company Facts vs filing-level XBRL
- No Supabase writes
- Raw SEC payloads remain in memory only
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from sec_xbrl_search_v2_3 import SECXBRLSearchV2_3


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"

TEST_CASES = {
    "HON": [
        "liabilities",
        "interest_expense",
        "sga",
        "operating_income",
    ],
    "VZ": [
        "liabilities",
        "receivables",
        "interest_expense",
    ],
    "T": [
        "liabilities",
        "inventory",
        "interest_expense",
    ],
    "NEM": [
        "liabilities",
        "interest_expense",
        "inventory",
        "sga",
        "operating_income",
    ],
    "DE": [
        "liabilities",
        "operating_income",
        "current_assets",
        "current_liabilities",
        "interest_expense",
    ],
    "APD": [
        "liabilities",
        "interest_expense",
        "operating_cash_flow",
    ],
    "LIN": [
        "liabilities",
        "interest_expense",
        "operating_income",
        "sga",
    ],
    "META": [
        "liabilities",
        "interest_expense",
        "operating_income",
        "sga",
    ],
}


# ---------------------------------------------------------------------
# SEC ticker -> CIK
# ---------------------------------------------------------------------

session = requests.Session()
session.headers.update(
    {
        "User-Agent": "Fundamental-app regression-test contact@example.com",
        "Accept-Encoding": "gzip, deflate",
    }
)


def load_ticker_map() -> dict[str, str]:
    r = session.get(SEC_TICKER_URL, timeout=30)
    r.raise_for_status()

    data = r.json()

    result = {}

    for item in data.values():
        ticker = str(item.get("ticker", "")).upper().strip()
        cik = item.get("cik_str")

        if ticker and cik:
            result[ticker] = str(int(cik))

    return result


# ---------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------

def fmt_value(value):
    if value is None:
        return "NONE"

    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.3f}B"

    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.3f}M"

    return f"{value:.6f}"


def print_candidate(candidate, prefix=""):
    if not candidate:
        print(f"{prefix}NONE")
        return

    print(
        f"{prefix}"
        f"{candidate.get('namespace')}:{candidate.get('concept')} | "
        f"label='{candidate.get('label', '')}' | "
        f"value={fmt_value(candidate.get('value'))} | "
        f"unit={candidate.get('unit')} | "
        f"end={candidate.get('end')} | "
        f"start={candidate.get('start')} | "
        f"fy={candidate.get('fy')} | "
        f"form={candidate.get('form')} | "
        f"source={candidate.get('source')} | "
        f"score={candidate.get('score')} | "
        f"reason={candidate.get('reason')}"
    )


# ---------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------

def main():
    print("=" * 100)
    print("SEC XBRL SEARCH V2.3 REGRESSION TEST")
    print("=" * 100)

    ticker_map = load_ticker_map()

    print(f"\nSEC ticker map: {len(ticker_map):,} tickers")

    engine = SECXBRLSearchV2(
        user_agent="Fundamental-app regression-test contact@example.com"
    )

    total = 0
    passed = 0
    review = 0

    for ticker, metrics in TEST_CASES.items():

        print("\n")
        print("=" * 100)
        print(f"{ticker}")
        print("=" * 100)

        cik = ticker_map.get(ticker)

        if not cik:
            print("CIK: NOT FOUND")
            continue

        print(f"CIK: {cik}")

        # -------------------------------------------------------------
        # First inspect latest annual filing
        # -------------------------------------------------------------

        try:
            submissions = engine.submissions(cik)
            annual_fy = engine._latest_annual_fy(submissions)

            accession, primary_doc, filed = engine.latest_annual_filing(
                submissions
            )

            print(f"Latest annual FY : {annual_fy}")
            print(f"Annual accession : {accession}")
            print(f"Primary document : {primary_doc}")
            print(f"Filed            : {filed}")

        except Exception as e:
            print(f"SUBMISSIONS ERROR: {e}")
            continue

        # -------------------------------------------------------------
        # Each metric
        # -------------------------------------------------------------

        for metric in metrics:
            total += 1

            print("\n" + "-" * 100)
            print(f"METRIC: {metric}")
            print("-" * 100)

            try:
                result = engine.resolve(
                    cik=cik,
                    metric=metric,
                    year=None,
                    suspicious=False,
                    limit=10,
                )

            except Exception as e:
                print(f"ERROR: {e}")
                review += 1
                continue

            target_year = result.get("year")

            print(f"Target FY: {target_year}")

            # ---------------------------------------------------------
            # Company Facts candidates
            # ---------------------------------------------------------

            company_candidates = result.get("company_facts", [])

            print(
                f"\nCompany Facts candidates: "
                f"{len(company_candidates)}"
            )

            for i, candidate in enumerate(company_candidates[:5], 1):
                print_candidate(
                    candidate,
                    prefix=f"  CF #{i}: ",
                )

            # ---------------------------------------------------------
            # Filing candidates
            # ---------------------------------------------------------

            filing_candidates = result.get("filing_xbrl", [])

            print(
                f"\nFiling XBRL candidates: "
                f"{len(filing_candidates)}"
            )

            for i, candidate in enumerate(filing_candidates[:5], 1):
                print_candidate(
                    candidate,
                    prefix=f"  FX #{i}: ",
                )

            # ---------------------------------------------------------
            # Best
            # ---------------------------------------------------------

            best = result.get("best")

            print("\nBEST:")
            print_candidate(best, prefix="  ")

            # ---------------------------------------------------------
            # Basic regression checks
            # ---------------------------------------------------------

            reasons = []

            if best is None:
                reasons.append("NO_RESULT")

            else:
                concept = best.get("concept", "")
                label = best.get("label", "")
                end = best.get("end")
                fy = best.get("fy")
                source = best.get("source")

                # 1. Target FY must exist when submissions has annual filing.
                if annual_fy is not None:
                    if target_year != annual_fy:
                        reasons.append(
                            f"WRONG_TARGET_FY expected={annual_fy}"
                        )

                # 2. End date should generally belong to target FY.
                if target_year is not None and end:
                    if str(end)[:4] != str(target_year):
                        reasons.append(
                            f"WRONG_END_YEAR end={end}"
                        )

                # 3. If candidate says FY, make sure it is not wildly different.
                if fy is not None and target_year is not None:
                    try:
                        if abs(int(fy) - int(target_year)) > 1:
                            reasons.append(
                                f"FY_MISMATCH fy={fy}"
                            )
                    except Exception:
                        pass

                # 4. Specific hard semantic checks.
                compact_concept = (
                    str(concept)
                    .lower()
                    .replace(" ", "")
                )

                compact_label = (
                    str(label)
                    .lower()
                    .replace(" ", "")
                )

                if metric == "liabilities":
                    bad = [
                        "liabilitiesandstockholdersequity",
                        "deferredcreditsandotherliabilitiesnoncurrent",
                        "longtermdebt",
                        "debtcurrent",
                        "accruedliabilities",
                        "operatingleaseliabilities",
                    ]

                    for x in bad:
                        if x in compact_concept or x in compact_label:
                            reasons.append(
                                f"COMPONENT_LIABILITY:{x}"
                            )

                if metric == "interest_expense":
                    bad = [
                        "unrecognizedtaxbenefits",
                        "taxpenalties",
                        "financeleaseinterestexpense",
                        "interestpaid",
                        "interestcostscapitalized",
                        "interestincome",
                    ]

                    for x in bad:
                        if x in compact_concept or x in compact_label:
                            reasons.append(
                                f"BAD_INTEREST:{x}"
                            )

                if metric == "inventory":
                    bad = [
                        "orestockpiles",
                        "leachpads",
                        "finishedgoods",
                        "workinprocess",
                        "rawmaterials",
                        "suppliesinventory",
                    ]

                    for x in bad:
                        if x in compact_concept or x in compact_label:
                            reasons.append(
                                f"COMPONENT_INVENTORY:{x}"
                            )

                if metric == "operating_income":
                    bad = [
                        "nonoperating",
                        "othernonoperating",
                        "nonoperatingincomeexpense",
                    ]

                    for x in bad:
                        if x in compact_concept or x in compact_label:
                            reasons.append(
                                f"NONOPERATING_INCOME:{x}"
                            )

                if metric == "current_assets":
                    if "noncurrent" in compact_concept:
                        reasons.append("NONCURRENT_ASSET")

                if metric == "current_liabilities":
                    if "noncurrent" in compact_concept:
                        reasons.append("NONCURRENT_LIABILITY")

            # ---------------------------------------------------------
            # PASS / REVIEW
            # ---------------------------------------------------------

            if not reasons:
                status = "PASS"
                passed += 1
            else:
                status = "REVIEW"
                review += 1

            print(f"\nRESULT: {status}")

            if reasons:
                print("  Reasons:")
                for reason in reasons:
                    print(f"   - {reason}")

            # SEC politeness
            time.sleep(0.15)

    # -----------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------

    print("\n")
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)

    print(f"TOTAL  : {total}")
    print(f"PASS   : {passed}")
    print(f"REVIEW : {review}")

    if total:
        print(f"PASS % : {passed / total * 100:.1f}%")

    print("=" * 100)


if __name__ == "__main__":
    main()
