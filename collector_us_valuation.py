"""US fundamental collection entrypoint with market valuation snapshot.

This wraps the existing US fundamental collector without changing its scoring
logic. It only enriches the persisted snapshot with current market facts and
SEC-safe valuation metrics.
"""

from __future__ import annotations

import argparse
import re
from html import unescape

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
    filed_dates = recent.get("filingDate") or []
    best = None
    for idx, report_date in enumerate(reports):
        if report_date != fiscal_end:
            continue
        form = forms[idx] if idx < len(forms) else None
        if form not in {"10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
            continue
        accession = accessions[idx] if idx < len(accessions) else None
        document = documents[idx] if idx < len(documents) else None
        filed = filed_dates[idx] if idx < len(filed_dates) else ""
        if accession and document:
            candidate = {"form": form, "accession": accession, "document": document, "report_date": report_date, "filing_date": filed}
            if best is None or candidate["filing_date"] > best["filing_date"]:
                best = candidate
    return best


def filing_text(session, cik, filing, filename=None):
    """Fetch a filing document from the SEC archive."""
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


def _normalized_filing_text(text):
    """Normalize SEC HTML/XML into text while preserving table wording."""
    clean = unescape(text or "")
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = clean.replace("\xa0", " ")
    clean = re.sub(r"[\u2012\u2013\u2014\u2212]", "-", clean)
    return re.sub(r"\s+", " ", clean).strip()


def parse_fiscal_end_common_shares(text, fiscal_end):
    """Extract period-end common shares from the filing balance-sheet text.

    A company may mention Class A/B multiple times in one filing (cover page,
    EPS note, balance sheet). We inspect every class block and only accept a
    block containing the requested fiscal-end date, preventing the cover-page
    class mention from masking the later balance-sheet class mention.
    """
    clean = _normalized_filing_text(text)
    if not clean or not fiscal_end:
        return None
    try:
        dt = __import__("datetime").date.fromisoformat(fiscal_end)
    except ValueError:
        return None

    months = {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December",
    }
    date_phrase = rf"{months[dt.month]}\s+{dt.day}(?:st|nd|rd|th)?,\s+{dt.year}"

    block_pattern = re.compile(
        r"(?:Class\s+[A-C]\b|Common Class [A-C] \[Member\])"
        r"(?P<body>.*?)(?=\bClass\s+[A-C]\b|\bCommon Class [A-C] \[Member\]\b|"
        r"\bAdditional paid-in capital\b|\bRetained earnings\b|"
        r"\bAccumulated other comprehensive\b|\bTreasury stock\b|"
        r"\bTotal stockholders[’'] equity\b|$)",
        re.I,
    )

    values = []
    for match in block_pattern.finditer(clean):
        body = match.group("body")
        patterns = [
            rf"Outstanding\s*-\s*(\d[\d,]*)\s+(?:and\s+\d[\d,]*\s+)?shares\s+as\s+of\s+{date_phrase}",
            rf"Issued\s+and\s+Outstanding\s*-\s*(\d[\d,]*)\s+shares\s+as\s+of\s+{date_phrase}",
        ]
        for pattern in patterns:
            found = re.search(pattern, body, re.I)
            if found:
                number = int(found.group(1).replace(",", ""))
                if number > 0:
                    values.append(number)
                    break

    if not values:
        return None

    unique_values = list(dict.fromkeys(values))
    return {
        "value": sum(unique_values),
        "tag": "filing-fiscal-end-common-shares",
        "namespace": "filing",
        "basis": "filing-fiscal-end-sum-of-common-classes",
        "class_count": len(unique_values),
        "fiscal_end": fiscal_end,
        "date_basis": "fiscal-end",
    }


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

        # Period-end shares come from the balance-sheet portion of the
        # primary filing. This is deliberately separate from cover-page shares.
        primary_text = filing_text(session, cik, filing)
        period_filing_shares = parse_fiscal_end_common_shares(primary_text, fiscal_end) if primary_text else None
        if period_filing_shares is None and primary_text:
            period_filing_shares = parse_common_shares_from_filing(primary_text, fiscal_end, for_current=False)

        # Current shares come from filing-level cover-page XBRL (usually R1.htm).
        cover_text = filing_text(session, cik, filing, filename="R1.htm")
        current_filing_shares = parse_common_shares_from_filing(
            cover_text,
            fiscal_end,
            for_current=True,
        ) if cover_text else None

        # Some filings do not expose the cover report as R1.htm; fall back to
        # the primary filing's cover-page wording.
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

        # When market data has no current share count, always prefer the SEC
        # filing cover-page total over Company Facts DEI. Company Facts can
        # expose only one class for multi-class issuers and understate market cap.
        if quote.get("current_shares") is None and current_filing_shares is not None:
            cover_value = current_filing_shares.get("value")
            if cover_value is not None and cover_value > 0:
                valuation["current_shares_outstanding"] = cover_value
                valuation["current_shares_source"] = "filing-cover-fallback"
                price = valuation.get("price")
                if price is not None and price > 0:
                    valuation["market_cap"] = price * cover_value
                    valuation["market_cap_basis"] = "current-price-times-sec-filing-cover-shares"

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
