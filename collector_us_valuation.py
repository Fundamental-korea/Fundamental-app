"""US fundamental collection entrypoint with market valuation snapshot.

This wraps the existing US fundamental collector without changing its scoring
logic. It only enriches the persisted snapshot with current market facts and
SEC-safe valuation metrics.
"""

from __future__ import annotations

import argparse

import yfinance as yf
from supabase import create_client

from collector_us_fundamental import (
    SEC_USER_AGENT,
    SUPABASE_KEY,
    SUPABASE_URL,
    build_result,
    get_universe,
    load_company,
)
from downturn_us import BENCHMARK, _close_series
from us_valuation import build_valuation_snapshot, normalize_market_quote, parse_common_shares_from_filing


def load_market_quote(ticker):
    """Load a scalar market quote plus one-year history for 52-week extremes."""
    symbol = yf.Ticker(ticker)
    try:
        info = dict(symbol.fast_info)
    except Exception:
        info = {}
    try:
        history = symbol.history(period="1y", auto_adjust=False, actions=False)
    except Exception:
        history = None
    return normalize_market_quote(info, history)


def find_latest_filing(submissions, fiscal_end):
    """Return the SEC filing metadata that reports the requested fiscal period."""
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    reports = recent.get("reportDate") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    best = None
    for idx, report_date in enumerate(reports):
        if report_date != fiscal_end:
            continue
        form = forms[idx] if idx < len(forms) else None
        if form not in {"10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
            continue
        accession = accessions[idx] if idx < len(accessions) else None
        document = documents[idx] if idx < len(documents) else None
        filed = ((recent.get("filingDate") or [None] * len(reports))[idx] if idx < len(recent.get("filingDate") or []) else None)
        if accession and document:
            candidate = {"form": form, "accession": accession, "document": document, "report_date": report_date, "filing_date": filed or ""}
            if best is None or candidate["filing_date"] > best["filing_date"]:
                best = candidate
    return best


def filing_text(session, cik, filing, filename=None):
    if not filing:
        return None
    cik_int = str(int(str(cik)))
    accession = filing["accession"].replace("-", "")
    name = filename or filing["document"]
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{name}"
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        return response.text
    except Exception:
        return None


def collect_one(sb, session, row, market=None):
    ticker, cik = row["ticker"], row["cik"]
    facts, submissions = load_company(session, ticker, cik)
    stock = None
    try:
        stock = _close_series(ticker)
    except Exception:
        pass

    result = build_result(
        ticker,
        cik,
        row.get("company_name") or submissions.get("name") or ticker,
        facts,
        submissions,
        universe_row=row,
        market_prices={"market": market, "stock": stock},
    )

    snapshot = result.get("snapshot")
    if snapshot and snapshot.get("fiscal_end"):
        quote = load_market_quote(ticker)
        fiscal_end = snapshot["fiscal_end"]
        filing = find_latest_filing(submissions, fiscal_end)

        # Period-end shares: use the primary 10-Q/10-K because this is where
        # balance-sheet equity and the fiscal-end outstanding share count align.
        primary_text = filing_text(session, cik, filing)
        period_filing_shares = parse_common_shares_from_filing(
            primary_text,
            fiscal_end,
            for_current=False,
        ) if primary_text else None

        # Current shares: SEC filing-level cover-page XBRL (typically R1.htm)
        # explicitly reports each common class outstanding on the cover date.
        cover_text = filing_text(session, cik, filing, filename="R1.htm")
        current_filing_shares = parse_common_shares_from_filing(
            cover_text,
            fiscal_end,
            for_current=True,
        ) if cover_text else None

        # Some filings may not expose R1.htm under that name; the primary filing
        # itself can still carry the cover-page disclosure, so try it as a fallback.
        if current_filing_shares is None and primary_text:
            current_filing_shares = parse_common_shares_from_filing(
                primary_text,
                fiscal_end,
                for_current=True,
            )

        valuation = build_valuation_snapshot(
            facts,
            fiscal_end,
            quote,
            filing_shares=period_filing_shares,
            current_filing_shares=current_filing_shares,
        )
        if filing:
            valuation["filing_form"] = filing["form"]
            valuation["filing_accession"] = filing["accession"]
            valuation["filing_document"] = filing["document"]
            valuation["filing_date"] = filing.get("filing_date")
        valuation["period_filing_shares_found"] = period_filing_shares is not None
        valuation["current_filing_shares_found"] = current_filing_shares is not None
        snapshot["market"] = quote
        snapshot["valuation"] = valuation
        result["snapshot"] = snapshot

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker")
    parser.add_argument("--tickers")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--all", action="store_true", dest="all_rows")
    args = parser.parse_args()
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    tickers = [args.ticker.upper().strip()] if args.ticker else ([x.upper().strip() for x in args.tickers.split(",") if x.strip()] if args.tickers else None)
    rows = get_universe(sb, tickers=tickers, limit=args.limit, all_rows=(args.all_rows or bool(tickers)))

    import requests
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    market = None
    try:
        market = _close_series(BENCHMARK)
    except Exception as exc:
        print(f"[US] downturn market data unavailable: {exc}")

    for i, row in enumerate(rows, 1):
        ticker = row["ticker"]
        try:
            result = collect_one(sb, session, row, market=market)
            sb.table("US_Fundamental").upsert(result, on_conflict="ticker").execute()
            valuation = (result.get("snapshot") or {}).get("valuation") or {}
            print(
                f"[{i}/{len(rows)}] {ticker}: score={result['total_score']} "
                f"grade={result['grade']} snapshot={result.get('snapshot_fiscal_end')} "
                f"EPS={valuation.get('eps')} BPS={valuation.get('bps')} "
                f"PER={valuation.get('per')} PBR={valuation.get('pbr')} "
                f"periodShares={valuation.get('period_end_shares_outstanding')} "
                f"currentShares={valuation.get('current_shares_outstanding')}"
            )
        except Exception as exc:
            print(f"[{i}/{len(rows)}] {ticker}: FAILED: {exc}")

    print("Completed.")


if __name__ == "__main__":
    main()
