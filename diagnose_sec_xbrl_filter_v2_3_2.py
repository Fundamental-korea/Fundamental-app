"""Read-only diagnostic for SEC XBRL V2.3.2 candidate filtering.

Purpose: identify exactly which filing-fallback filter removes candidates.
No Supabase writes. Raw SEC payloads remain in memory only.
"""
from __future__ import annotations

import requests

from sec_xbrl_search_v2_3_2 import SECXBRLSearchV2_3_2
from sec_xbrl_search_v2_3 import INSTANT_METRICS, DURATION_METRICS, _annual_duration, _date, _hard_excluded

SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
CASES = {
    "HON": ["liabilities"],
    "VZ": ["liabilities", "receivables"],
    "T": ["liabilities", "inventory"],
    "NEM": ["interest_expense", "inventory", "sga", "operating_income"],
    "DE": ["operating_income", "current_assets", "current_liabilities"],
    "META": ["sga"],
}

session = requests.Session()
session.headers.update({
    "User-Agent": "Fundamental-app filter diagnostic contact@example.com",
    "Accept-Encoding": "gzip, deflate",
})


def ticker_map():
    r = session.get(SEC_TICKER_URL, timeout=30)
    r.raise_for_status()
    return {
        str(v.get("ticker", "")).upper().strip(): str(int(v["cik_str"]))
        for v in r.json().values()
        if v.get("ticker") and v.get("cik_str")
    }


def compact(r):
    return (f"{r.get('namespace')}:{r.get('concept')} | "
            f"label='{r.get('label','')}' | value={r.get('value')} | "
            f"start={r.get('start')} end={r.get('end')} | fy={r.get('fy')} | "
            f"instant={r.get('instant')} dim={r.get('dimensioned')}")


def stage(rows, metric, year, engine):
    total = len(rows)
    s_year = [r for r in rows if year is None or ((_date(r.get("end")) is not None) and _date(r.get("end")).year == int(year))]
    s_dim = [r for r in s_year if r.get("dimensioned") or True]
    # We report the normal production path: dimensioned rows are removed.
    s_dim = [r for r in s_year if not r.get("dimensioned")]
    s_hard = [r for r in s_dim if not _hard_excluded(metric, r.get("concept", ""), r.get("label", ""))]
    s_allowed = [r for r in s_hard if engine._candidate_allowed(metric, r.get("concept", ""), r.get("label", ""), "filing-xbrl")]

    s_period = []
    for r in s_allowed:
        instant = bool(r.get("instant"))
        annual = bool(r.get("start")) and _annual_duration(r.get("start"), r.get("end"))
        if metric in INSTANT_METRICS and not instant:
            continue
        if metric in DURATION_METRICS and metric != "eps" and not annual:
            continue
        s_period.append(r)

    return {
        "rows": rows,
        "total": total,
        "year": s_year,
        "dimension": s_dim,
        "hard": s_hard,
        "allowed": s_allowed,
        "period": s_period,
    }


def show_stage(name, arr, limit=8):
    print(f"{name:<22}: {len(arr):>4}")
    for r in arr[:limit]:
        print("    " + compact(r))


def main():
    print("=" * 120)
    print("SEC XBRL V2.3.2 FILTER DIAGNOSTIC")
    print("=" * 120)
    tm = ticker_map()
    engine = SECXBRLSearchV2_3_2(user_agent="Fundamental-app filter diagnostic contact@example.com")

    for ticker, metrics in CASES.items():
        cik = tm.get(ticker)
        print("\n" + "=" * 120)
        print(ticker, "CIK=", cik)
        print("=" * 120)
        if not cik:
            print("CIK NOT FOUND")
            continue
        subs = engine.submissions(cik)
        target_fy = engine._latest_annual_fy(subs)
        rows, meta = engine._inline_filing_rows(cik, subs)
        print(f"Target FY: {target_fy} | total parsed rows: {len(rows)} | meta: {meta}")

        for metric in metrics:
            print("\n" + "-" * 120)
            print("METRIC:", metric)
            print("-" * 120)
            st = stage(rows, metric, target_fy, engine)
            show_stage("rows_total", st["rows"])
            show_stage("year_pass", st["year"])
            show_stage("dimension_pass", st["dimension"])
            show_stage("hard_exclude_pass", st["hard"])
            show_stage("candidate_allowed_pass", st["allowed"])
            show_stage("period_pass", st["period"])

            # Show the rows that were removed at each transition.
            removed = {
                "year_removed": [r for r in st["rows"] if r not in st["year"]],
                "dimension_removed": [r for r in st["year"] if r not in st["dimension"]],
                "hard_removed": [r for r in st["dimension"] if r not in st["hard"]],
                "allowed_removed": [r for r in st["hard"] if r not in st["allowed"]],
                "period_removed": [r for r in st["allowed"] if r not in st["period"]],
            }
            print("\nREMOVED SAMPLE")
            for name, arr in removed.items():
                print(f"\n{name}: {len(arr)}")
                for r in arr[:5]:
                    print("    " + compact(r))

            # Special balance-sheet identity diagnostics for liabilities.
            if metric == "liabilities":
                print("\nBALANCE-SHEET IDENTITY INPUTS")
                assets = [r for r in rows if not r.get("dimensioned") and r.get("instant") and r.get("namespace") == "us-gaap" and r.get("concept") == "Assets" and _date(r.get("end")) and _date(r.get("end")).year == int(target_fy)]
                equity_names = {
                    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                    "StockholdersEquity",
                    "EquityAttributableToOwnersOfParent",
                    "Equity",
                    "ProprietaryCapital",
                    "TotalProprietaryCapital",
                }
                equity = [r for r in rows if not r.get("dimensioned") and r.get("instant") and r.get("namespace") == "us-gaap" and r.get("concept") in equity_names and _date(r.get("end")) and _date(r.get("end")).year == int(target_fy)]
                show_stage("assets_same_fy", assets)
                show_stage("equity_same_fy", equity)
                if assets and equity:
                    print("Derived possible:", float(assets[0]["value"]) - float(equity[0]["value"]))

    print("\nDONE")


if __name__ == "__main__":
    main()
