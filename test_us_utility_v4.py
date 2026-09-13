"""Read-only regression test for utility extraction v4.

No Supabase writes and no raw SEC payloads are saved.
"""
from __future__ import annotations

import requests

from collector_us_fundamental import SEC_USER_AGENT, SEC_FACTS_URL, SEC_SUBMISSIONS_URL, fetch_json
from collector_us_utility_v4 import SEC_TICKERS
from downturn_us import BENCHMARK, _close_series, calculate_downturn_defense
from us_utility_extraction_v4 import (
    REVENUE_TAGS, OPERATING_INCOME_TAGS, NET_INCOME_TAGS,
    ASSETS_TAGS, EQUITY_TAGS, pick_flow, pick_instant, pick_eps,
    pick_interest, pick_debt, pick_ocf, pick_capex, pick_dividend,
    core_years,
)
from us_utility_filing_fallback import augment_with_latest_filing

TICKERS = ["XEL", "TVE", "PPL", "SWX", "TAC"]


def ticker_cik(session, ticker):
    data = fetch_json(session, SEC_TICKERS)
    for item in data.values():
        if str(item.get("ticker", "")).upper() == ticker.upper():
            return str(item["cik_str"]).zfill(10)
    raise RuntimeError(f"CIK not found: {ticker}")


def value(facts, picker, year, tags=None):
    row = picker(facts, tags, year) if tags is not None else picker(facts, year)
    return row["val"] if row else None


def show(ticker, facts, submissions, session):
    years = sorted(y for y in core_years(facts) if 2018 <= y <= 2026)
    latest = max(years) if years else None
    print(f"\n=== {ticker} | latest={latest} ===")
    if latest is None:
        return
    print("Company Facts:")
    for label, picker, tags, instant in [
        ("revenue", pick_flow, REVENUE_TAGS, False),
        ("op_income", pick_flow, OPERATING_INCOME_TAGS, False),
        ("net_income", pick_flow, NET_INCOME_TAGS, False),
        ("assets", pick_instant, ASSETS_TAGS, True),
        ("equity", pick_instant, EQUITY_TAGS, True),
    ]:
        row = picker(facts, tags, latest)
        print(f"  {label:12} -> {row}")
    print(f"  {'eps':12} -> {pick_eps(facts, latest)}")
    print(f"  {'debt':12} -> {pick_debt(facts, latest)}")
    print(f"  {'ocf':12} -> {pick_ocf(facts, latest)}")
    print(f"  {'capex':12} -> {pick_capex(facts, latest)}")
    print(f"  {'interest':12} -> {pick_interest(facts, latest)}")
    print(f"  {'dividend':12} -> {pick_dividend(facts, latest)}")

    debt = pick_debt(facts, latest)
    equity = value(facts, pick_instant, latest, EQUITY_TAGS)
    ocf = value(facts, pick_ocf, latest)
    capex = value(facts, pick_capex, latest)
    if debt:
        print(f"  debt_value={debt['val']}")
    if debt and debt.get("val") not in (None, 0) and ocf is not None:
        print(f"  ocf_debt={ocf / debt['val'] * 100:.2f}%")
    if debt and debt.get("val") not in (None, 0) and ocf is not None and capex is not None:
        print(f"  fcf_debt={(ocf - abs(capex)) / debt['val'] * 100:.2f}%")
    print(f"  equity_value={equity}")

    augmented, meta = augment_with_latest_filing(session, ticker_cik(session, ticker), submissions, facts)
    print(f"Filing fallback: {meta}")
    if meta.get("used"):
        print("After fallback:")
        print(f"  equity={value(augmented, pick_instant, latest, EQUITY_TAGS)}")
        print(f"  eps={pick_eps(augmented, latest)}")
        print(f"  debt={pick_debt(augmented, latest)}")
        print(f"  dividend={pick_dividend(augmented, latest)}")


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})
    for ticker in TICKERS:
        cik = ticker_cik(session, ticker)
        facts = fetch_json(session, SEC_FACTS_URL.format(cik=cik))
        submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik))
        show(ticker, facts, submissions, session)
    print("\nRead-only regression test completed.")


if __name__ == "__main__":
    main()
