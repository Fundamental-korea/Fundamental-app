"""US Utility extraction diagnostic v2 — DRY RUN ONLY."""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
from typing import Any

import requests

SEC_HEADERS = {"User-Agent": os.getenv("SEC_USER_AGENT", "Fundamental-app diagnostic contact@example.com")}
TICKERS = ["DUK", "NEE", "VST", "CEG", "SO", "D", "AEP", "EXC", "ETR", "PEG", "AWR"]
FLOW_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}

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
    return x if math.isfinite(x) else None


def namespaces(facts: dict[str, Any]) -> dict[str, Any]:
    # SEC Company Facts is wrapped in a top-level "facts" object.
    return facts.get("facts", facts)


def collect_rows(facts: dict[str, Any], tags: list[str], flow: bool) -> list[dict[str, Any]]:
    root = namespaces(facts)
    rows: list[dict[str, Any]] = []
    for namespace in ("us-gaap", "ifrs-full"):
        ns = root.get(namespace, {})
        for tag in tags:
            obj = ns.get(tag)
            if not obj:
                continue
            for unit, entries in obj.get("units", {}).items():
                for row in entries:
                    if row.get("form") not in FLOW_FORMS:
                        continue
                    end = row.get("end")
                    if not end or len(end) < 10:
                        continue
                    start = row.get("start")
                    if flow and start:
                        try:
                            days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days
                        except ValueError:
                            continue
                        if days < 300 or days > 430:
                            continue
                    rows.append({
                        "year": int(end[:4]), "end": end, "start": start,
                        "val": clean(row.get("val")), "form": row.get("form"),
                        "fy": row.get("fy"), "fp": row.get("fp"),
                        "namespace": namespace, "tag": tag, "unit": unit,
                        "filed": row.get("filed"),
                    })
    return rows


def select_year(rows: list[dict[str, Any]], year: int) -> dict[str, Any] | None:
    candidates = [r for r in rows if r["year"] == year and r["val"] is not None]
    if not candidates:
        return None
    candidates.sort(key=lambda r: (r["namespace"] == "us-gaap", r.get("filed") or ""), reverse=True)
    return candidates[0]


def fmt(v: float | None, digits: int = 3) -> str:
    return "MISSING" if v is None else f"{v:,.{digits}f}"


def analyze(ticker: str) -> None:
    ticker_map = get_json("https://www.sec.gov/files/company_tickers.json")
    match = next((x for x in ticker_map.values() if x.get("ticker", "").upper() == ticker), None)
    if not match:
        print(f"\n{ticker}: ticker not found")
        return
    cik = int(match["cik_str"])
    facts = get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    root = namespaces(facts)
    name = facts.get("entityName", match.get("title", ticker))

    print("\n" + "=" * 100)
    print(f"{ticker} | {name} | CIK {cik:010d}")
    print("SEC extraction diagnostic v2.1 — NO DB WRITE")
    print("=" * 100)

    years = list(range(2021, 2026))
    records: dict[str, dict[int, dict[str, Any] | None]] = {}
    for metric, aliases in FACT_ALIASES.items():
        # Balance-sheet facts are instant facts; flow facts require ~1-year duration.
        flow = metric not in {"assets", "equity", "liabilities", "current_liabilities", "current_assets", "cash", "debt_current", "debt_noncurrent"}
        rows = collect_rows(facts, aliases, flow=flow)
        records[metric] = {year: select_year(rows, year) for year in years}

    for metric in FACT_ALIASES:
        print(f"\n[{metric}]")
        for year in years:
            r = records[metric][year]
            if r is None:
                print(f"  {year}: MISSING")
            else:
                print(f"  {year}: {fmt(r['val'])} | {r['namespace']}:{r['tag']} | {r['unit']} | end={r['end']} | start={r['start']} | filed={r.get('filed')} | fy={r.get('fy')} fp={r.get('fp')}")

    def val(metric: str, year: int = 2025) -> float | None:
        r = records[metric][year]
        return r["val"] if r else None

    rev, rev24 = val("revenue"), val("revenue", 2024)
    op, ni = val("operating_income"), val("net_income")
    assets25, assets24 = val("assets"), val("assets", 2024)
    equity = val("equity")
    dc, dl = val("debt_current"), val("debt_noncurrent")
    ocf, capex, div = val("operating_cash_flow"), val("capex"), val("dividends")
    interest = val("interest_expense")

    debt = None if dc is None and dl is None else (dc or 0) + (dl or 0)
    opm = None if op is None or rev in (None, 0) else op / rev * 100
    roa = None if ni is None or assets25 is None or assets24 is None else ni / ((assets25 + assets24) / 2) * 100
    rev_growth = None if rev is None or rev24 in (None, 0) else (rev / rev24 - 1) * 100
    debt_capital = None if debt is None or equity is None or debt + equity <= 0 else debt / (debt + equity) * 100
    fcf = None if ocf is None or capex is None else ocf - abs(capex)
    ocf_debt = None if ocf is None or debt in (None, 0) else ocf / debt * 100
    fcf_debt = None if fcf is None or debt in (None, 0) else fcf / debt * 100
    dividend_coverage = None if ocf is None or div in (None, 0) else ocf / abs(div)
    interest_coverage = None if op is None or interest in (None, 0) else op / abs(interest)

    print("\n[DERIVED 2025]")
    for label, value in [
        ("Revenue Growth %", rev_growth), ("OPM %", opm), ("ROA %", roa),
        ("Debt/Capital %", debt_capital), ("OCF/Debt %", ocf_debt),
        ("FCF/Debt %", fcf_debt), ("Dividend Coverage x", dividend_coverage),
        ("Interest Coverage x", interest_coverage),
    ]:
        print(f"  {label:24s}: {fmt(value)}")

    print("\n[FLAGS]")
    flags: list[str] = []
    if opm is not None and (opm > 100 or opm < -100):
        flags.append(f"SUSPICIOUS OPM={opm:.2f}% — verify Revenue and Operating Income tags")
    if rev is not None and op is not None and rev > 0 and op > rev:
        flags.append("Operating income exceeds revenue")
    if capex is not None and capex > 0:
        flags.append("CapEx value is positive; verify whether this filing presents cash outflow as positive")
    if div is not None and div > 0:
        flags.append("Dividend value is positive; verify cash-flow sign convention")
    if interest is None:
        flags.append("Interest expense missing — inspect tag inventory")
    if capex is None:
        flags.append("CapEx missing — inspect tag inventory")
    if div is None:
        flags.append("Dividends missing — inspect tag inventory")
    if not flags:
        flags.append("No automatic red flags")
    for f in flags:
        print("  -", f)

    print("\n[TAG INVENTORY]")
    for pattern in ["Interest", "FinanceCost", "AcquireProperty", "AcquireProductive", "Dividend", "Revenue", "OperatingIncome", "Debt"]:
        found = []
        for namespace in ("us-gaap", "ifrs-full"):
            for tag in root.get(namespace, {}):
                if pattern.lower() in tag.lower():
                    found.append(f"{namespace}:{tag}")
        print(f"  {pattern:20s}: {', '.join(found[:60]) if found else 'NONE'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=",".join(TICKERS))
    args = parser.parse_args()
    for ticker in [x.strip().upper() for x in args.tickers.split(",") if x.strip()]:
        try:
            analyze(ticker)
        except Exception as exc:
            print(f"\n{ticker}: ERROR {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
