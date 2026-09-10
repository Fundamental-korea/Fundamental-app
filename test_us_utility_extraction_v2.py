"""US Utility extraction diagnostic v2 — DRY RUN ONLY.

Purpose:
- Validate SEC Company Facts extraction before changing Utility v3 scoring.
- Focus on Revenue, Operating Income, Assets, Debt, Equity, OCF, CapEx,
  Dividends and Interest Expense.
- Print the selected SEC namespace/tag/year so tag-definition mistakes are visible.
- Flag suspicious ratios such as OPM > 100% or negative/implausible values.

NO Supabase writes. NO production scorer changes.

Run examples:
    python test_us_utility_extraction_v2.py
    python test_us_utility_extraction_v2.py --tickers PEG,AWR
    python test_us_utility_extraction_v2.py --tickers DUK,NEE,VST,CEG,SO,D,AEP,EXC,ETR,PEG,AWR
"""
from __future__ import annotations

import argparse
import math
import os
from typing import Any

import requests

SEC_HEADERS = {
    "User-Agent": os.getenv(
        "SEC_USER_AGENT",
        "Fundamental-app diagnostic contact@example.com",
    ),
}

TICKERS = ["DUK", "NEE", "VST", "CEG", "SO", "D", "AEP", "EXC", "ETR", "PEG", "AWR"]

FACT_ALIASES = {
    "revenue": ["Revenue", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "operating_income": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent"],
    "assets": ["Assets"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "Equity", "EquityAttributableToOwnersOfParent"],
    "liabilities": ["Liabilities"],
    "current_liabilities": ["LiabilitiesCurrent", "CurrentLiabilities"],
    "current_assets": ["AssetsCurrent", "CurrentAssets"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents"],
    "debt_current": ["LongTermDebtCurrent", "ShortTermBorrowings"],
    "debt_noncurrent": ["LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent", "LongTermDebtAndFinanceLeaseObligations"],
    "interest_expense": ["InterestExpenseNonOperating", "InterestExpense", "FinanceCosts", "InterestExpenseDebt"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets", "PaymentsToAcquirePropertyPlantAndEquipmentAndOtherProductiveAssets"],
    "dividends": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock", "DividendsCommonStockCash"],
    "eps_diluted": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}


def get_json(url: str) -> dict[str, Any]:
    r = requests.get(url, headers=SEC_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def clean(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(x):
        return None
    return x


def annual_rows(facts: dict[str, Any], tags: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for namespace in ("us-gaap", "ifrs-full"):
        ns = facts.get(namespace, {})
        for tag in tags:
            obj = ns.get(tag)
            if not obj:
                continue
            for unit, entries in obj.get("units", {}).items():
                for row in entries:
                    form = row.get("form")
                    if form not in {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
                        continue
                    end = row.get("end")
                    if not end or len(end) < 10:
                        continue
                    # Annual flow facts: require FY or an annual duration around one year.
                    start = row.get("start")
                    if start:
                        try:
                            import datetime as dt
                            days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
                            if days < 300 or days > 430:
                                continue
                        except ValueError:
                            continue
                    rows.append({
                        "year": int(end[:4]),
                        "end": end,
                        "start": start,
                        "val": clean(row.get("val")),
                        "form": form,
                        "fy": row.get("fy"),
                        "fp": row.get("fp"),
                        "namespace": namespace,
                        "tag": tag,
                        "unit": unit,
                        "filed": row.get("filed"),
                    })
    # Deduplicate same year/tag/end; latest filing first, then prefer us-gaap.
    rows.sort(key=lambda r: (r["year"], r.get("filed") or "", r["namespace"] == "us-gaap"), reverse=True)
    return rows


def annual_instant_rows(facts: dict[str, Any], tags: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for namespace in ("us-gaap", "ifrs-full"):
        ns = facts.get(namespace, {})
        for tag in tags:
            obj = ns.get(tag)
            if not obj:
                continue
            for unit, entries in obj.get("units", {}).items():
                for row in entries:
                    if row.get("form") not in {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
                        continue
                    end = row.get("end")
                    if not end or len(end) < 10:
                        continue
                    rows.append({
                        "year": int(end[:4]),
                        "end": end,
                        "start": row.get("start"),
                        "val": clean(row.get("val")),
                        "form": row.get("form"),
                        "fy": row.get("fy"),
                        "fp": row.get("fp"),
                        "namespace": namespace,
                        "tag": tag,
                        "unit": unit,
                        "filed": row.get("filed"),
                    })
    rows.sort(key=lambda r: (r["year"], r.get("filed") or "", r["namespace"] == "us-gaap"), reverse=True)
    return rows


def select_latest_by_year(rows: list[dict[str, Any]], year: int) -> dict[str, Any] | None:
    candidates = [r for r in rows if r["year"] == year and r["val"] is not None]
    if not candidates:
        return None
    # Prefer us-gaap, then latest filed, then deterministic tag order already retained.
    candidates.sort(key=lambda r: (r["namespace"] == "us-gaap", r.get("filed") or ""), reverse=True)
    return candidates[0]


def tag_inventory(facts: dict[str, Any], pattern: str) -> list[str]:
    found: list[str] = []
    for namespace in ("us-gaap", "ifrs-full"):
        for tag in facts.get(namespace, {}):
            if pattern.lower() in tag.lower():
                found.append(f"{namespace}:{tag}")
    return found


def fmt(v: float | None, digits: int = 3) -> str:
    if v is None:
        return "MISSING"
    return f"{v:,.{digits}f}"


def analyze(ticker: str) -> None:
    ticker_map = get_json("https://www.sec.gov/files/company_tickers.json")
    match = next((x for x in ticker_map.values() if x.get("ticker", "").upper() == ticker.upper()), None)
    if not match:
        print(f"\n{ticker}: ticker not found")
        return
    cik = int(match["cik_str"])
    facts = get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    name = facts.get("entityName", match.get("title", ticker))

    print("\n" + "=" * 100)
    print(f"{ticker} | {name} | CIK {cik:010d}")
    print("SEC extraction diagnostic v2 — NO DB WRITE")
    print("=" * 100)

    years = list(range(2021, 2026))
    records: dict[str, dict[int, dict[str, Any] | None]] = {}
    for metric, aliases in FACT_ALIASES.items():
        rows = annual_instant_rows(facts, aliases) if metric not in {"revenue", "operating_income", "net_income", "interest_expense", "operating_cash_flow", "capex", "dividends", "eps_diluted"} else annual_rows(facts, aliases)
        records[metric] = {year: select_latest_by_year(rows, year) for year in years}

    # Show selected facts with exact tag/source.
    for metric in FACT_ALIASES:
        print(f"\n[{metric}]")
        for year in years:
            r = records[metric][year]
            if not r:
                print(f"  {year}: MISSING")
            else:
                print(f"  {year}: {fmt(r['val'])} | {r['namespace']}:{r['tag']} | {r['unit']} | end={r['end']} | filed={r.get('filed')} | fy={r.get('fy')} fp={r.get('fp')}")

    # Derived 2025 ratios, with explicit inputs.
    def val(metric: str, year: int = 2025) -> float | None:
        r = records[metric][year]
        return r["val"] if r else None

    rev = val("revenue")
    op = val("operating_income")
    ni = val("net_income")
    assets25 = val("assets", 2025)
    assets24 = val("assets", 2024)
    equity = val("equity")
    debt_cur = val("debt_current")
    debt_lt = val("debt_noncurrent")
    ocf = val("operating_cash_flow")
    capex = val("capex")
    div = val("dividends")
    interest = val("interest_expense")
    rev24 = val("revenue", 2024)

    debt = None if debt_cur is None and debt_lt is None else (debt_cur or 0) + (debt_lt or 0)
    debt_capital = None if debt is None or equity is None or debt + equity <= 0 else debt / (debt + equity) * 100
    opm = None if rev in (None, 0) or op is None else op / rev * 100
    roa = None if ni is None or assets25 is None or assets24 is None or (assets25 + assets24) == 0 else ni / ((assets25 + assets24) / 2) * 100
    rev_growth = None if rev is None or rev24 in (None, 0) else (rev / rev24 - 1) * 100
    fcf = None if ocf is None or capex is None else ocf - capex
    ocf_debt = None if ocf is None or debt in (None, 0) else ocf / debt * 100
    fcf_debt = None if fcf is None or debt in (None, 0) else fcf / debt * 100
    dividend_coverage = None if ocf is None or div in (None, 0) else ocf / abs(div)
    interest_coverage = None if op is None or interest in (None, 0) else op / abs(interest)

    print("\n[DERIVED 2025]")
    for label, value in [
        ("Revenue Growth", rev_growth),
        ("OPM", opm),
        ("ROA", roa),
        ("Debt/Capital %", debt_capital),
        ("OCF/Debt %", ocf_debt),
        ("FCF/Debt %", fcf_debt),
        ("Dividend Coverage x", dividend_coverage),
        ("Interest Coverage x", interest_coverage),
    ]:
        print(f"  {label:24s}: {fmt(value)}")

    print("\n[FLAGS]")
    flags: list[str] = []
    if opm is not None and (opm > 100 or opm < -100):
        flags.append(f"SUSPICIOUS OPM={opm:.2f}% — likely Revenue/Operating Income definition mismatch")
    if rev is not None and rev > 0 and op is not None and op > rev:
        flags.append("Operating income exceeds revenue")
    if capex is not None and capex < 0:
        flags.append("CapEx is negative before sign normalization")
    if div is not None and div < 0:
        flags.append("Dividend cash-flow tag has negative sign; coverage uses abs(dividend)")
    if debt is not None and equity is not None and debt > 3 * max(equity, 1):
        flags.append("Very high debt/equity — verify debt components")
    if interest is None:
        flags.append("Interest expense missing — inspect tag inventory below")
    if capex is None:
        flags.append("CapEx missing — inspect tag inventory below")
    if div is None:
        flags.append("Dividends missing — inspect tag inventory below")
    if not flags:
        flags.append("No automatic red flags")
    for f in flags:
        print("  -", f)

    print("\n[TAG INVENTORY — useful for fixing aliases]")
    for pattern in ["Interest", "FinanceCost", "AcquireProperty", "AcquireProductive", "Dividend", "Revenue", "OperatingIncome", "Debt"]:
        found = tag_inventory(facts, pattern)
        print(f"  {pattern:20s}: {', '.join(found[:40]) if found else 'NONE'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=",".join(TICKERS))
    args = parser.parse_args()
    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    for ticker in tickers:
        try:
            analyze(ticker)
        except Exception as exc:
            print(f"\n{ticker}: ERROR {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
