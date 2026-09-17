"""Incremental US valuation snapshot collector.

This collector preserves the existing US_Fundamental score and snapshot data.
It only enriches companies that already have a Standard snapshot and do not
already contain a valuation block, unless --refresh is requested.
"""

from __future__ import annotations

import argparse
import re
import time
from datetime import datetime, timezone
from html import unescape

import requests
from supabase import create_client

from collector_us_fundamental import SEC_USER_AGENT, SUPABASE_KEY, SUPABASE_URL, load_company
from collector_us_valuation import (
    filing_text,
    find_latest_filing,
    load_market_quote,
    parse_fiscal_end_common_shares,
)
from downturn_us import BENCHMARK, _close_series
from us_valuation import build_valuation_snapshot, parse_common_shares_from_filing

STANDARD_SECTORS = (
    "technology",
    "healthcare",
    "consumer",
    "industrials",
    "energy",
    "materials",
    "communication",
)


def _load_paged(sb, table, columns, filters=None, page_size=1000):
    rows = []
    offset = 0
    while True:
        query = sb.table(table).select(columns)
        if filters:
            for method, args in filters:
                query = getattr(query, method)(*args)
        batch = query.range(offset, offset + page_size - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def _merge_standard_rows(sb):
    """Use US_Companies.sector_common as the Standard-universe authority."""
    companies = _load_paged(
        sb,
        "US_Companies",
        "ticker,cik,company_name,sector_common",
        filters=[
            ("eq", ("is_fundamental_eligible", True)),
            ("in_", ("sector_common", list(STANDARD_SECTORS))),
        ],
    )
    company_by_ticker = {r["ticker"]: r for r in companies}

    fundamentals = _load_paged(
        sb,
        "US_Fundamental",
        "ticker,cik,company_name,sector,snapshot",
    )
    fundamental_by_ticker = {r["ticker"]: r for r in fundamentals}

    rows = []
    for ticker, company in company_by_ticker.items():
        fundamental = fundamental_by_ticker.get(ticker)
        if not fundamental:
            continue
        snapshot = fundamental.get("snapshot")
        if not snapshot or not snapshot.get("fiscal_end"):
            continue
        rows.append({
            "ticker": ticker,
            "cik": company.get("cik") or fundamental.get("cik"),
            "company_name": company.get("company_name") or fundamental.get("company_name") or ticker,
            "sector": company.get("sector_common"),
            "snapshot": snapshot,
        })
    return rows


def _load_standard_rows(sb, tickers=None, refresh=False):
    rows = _merge_standard_rows(sb)
    if tickers:
        wanted = {x.upper().strip() for x in tickers}
        rows = [r for r in rows if r["ticker"] in wanted]
    if not refresh:
        rows = [r for r in rows if not r["snapshot"].get("valuation")]
    return rows


def _normalized_filing_cells(text):
    """Normalize SEC HTML while preserving table-cell boundaries."""
    clean = unescape(text or "")
    if not clean:
        return []
    clean = re.sub(
        r"</?(?:td|th|tr|p|div|li|br)[^>]*>",
        " | ",
        clean,
        flags=re.I,
    )
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = clean.replace("\xa0", " ")
    clean = re.sub(r"[\u2012\u2013\u2014\u2212]", "-", clean)
    clean = re.sub(r"\s+", " ", clean)
    cells = [part.strip() for part in clean.split("|") if part.strip()]
    return cells


def _parse_reported_number(token):
    """Parse a single SEC-rendered numeric token, including parentheses negatives."""
    token = token.strip()
    if not token or token in {"-", "—", "–", "N/A", "NA"}:
        return None
    negative = token.startswith("(") and token.endswith(")")
    token = token.strip("()")
    token = token.replace("$", "").replace(",", "").strip()
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", token):
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    if negative:
        value = -abs(value)
    if not math.isfinite(value):
        return None
    return value


def _extract_eps_from_following_cells(cells, start_index, lookahead=12):
    """Extract the first plausible reported EPS value after an EPS row label.

    SEC filing tables frequently place a footnote marker in one cell and the
    actual first-year EPS in the next cell. Prefer decimal/parenthesized/
    currency-formatted values before falling back to a plain numeric value.
    """
    values = []
    for cell in cells[start_index + 1 : start_index + 1 + lookahead]:
        cell = cell.strip()
        if not cell:
            continue
        # Normal SEC tables may render a single cell with more than one token.
        for token in re.findall(
            r"(?:\(-?\$?\d[\d,]*(?:\.\d+)?\)|-?\$?\d[\d,]*(?:\.\d+)?)",
            cell,
        ):
            value = _parse_reported_number(token)
            if value is not None and abs(value) < 1_000_000:
                values.append((token, value))

    if not values:
        return None

    # Footnote/reference cells are commonly simple integers such as "2".
    # A reported EPS almost always carries decimal precision or currency/
    # parenthesis formatting. Prefer those tokens when available.
    for token, value in values:
        if "." in token or "$" in token or token.startswith("("):
            return value

    # Integer EPS is valid; use the first numeric value only when no formatted
    # EPS candidate exists.
    return values[0][1]


def parse_reported_eps_from_filing(text):
    """Extract directly reported annual EPS from an annual SEC filing.

    This is only a fallback when Company Facts does not expose the standard
    EarningsPerShareDiluted/Basic tag. No EPS is reconstructed from net income
    or shares. The parser is table-aware so footnote cells do not get confused
    with the first fiscal-year EPS value.
    """
    cells = _normalized_filing_cells(text)
    if not cells:
        return None

    label_patterns = [
        re.compile(
            r"^earnings\s*(?:\(loss\)\s*)?per\s+common\s+share\s*"
            r"[-–—]\s*assuming\s+dilution(?:\s*\(dollars\))?$",
            re.I,
        ),
        re.compile(
            r"^diluted\s+earnings\s+per\s+(?:common\s+)?share(?:\s*\(dollars\))?$",
            re.I,
        ),
        re.compile(
            r"^earnings\s*(?:\(loss\)\s*)?per\s+common\s+share(?:\s*\(dollars\))?$",
            re.I,
        ),
    ]

    # Prefer the explicitly diluted row, then generic/diluted variants.
    for pattern in label_patterns:
        for index, cell in enumerate(cells):
            if pattern.search(cell.strip()):
                value = _extract_eps_from_following_cells(cells, index)
                if value is not None:
                    return {
                        "value": value,
                        "tag": "filing:reported-eps",
                        "namespace": "filing",
                        "basis": "directly-reported-annual-sec-filing-eps",
                    }
    return None


def find_latest_annual_filing(submissions):
    """Return the most recent annual 10-K/20-F/40-F filing."""
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    accessions = recent.get("accessionNumber") or []
    documents = recent.get("primaryDocument") or []
    report_dates = recent.get("reportDate") or []
    filed_dates = recent.get("filingDate") or []
    best = None

    for idx, form in enumerate(forms):
        if form not in {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
            continue
        accession = accessions[idx] if idx < len(accessions) else None
        document = documents[idx] if idx < len(documents) else None
        report_date = report_dates[idx] if idx < len(report_dates) else None
        filed = filed_dates[idx] if idx < len(filed_dates) else ""
        if not accession or not document:
            continue
        candidate = {
            "form": form,
            "accession": accession,
            "document": document,
            "report_date": report_date,
            "filing_date": filed,
        }
        if best is None or candidate["filing_date"] > best["filing_date"]:
            best = candidate
    return best


def collect_valuation_one(session, row):
    ticker = row["ticker"]
    cik = row["cik"]
    snapshot = dict(row["snapshot"] or {})
    fiscal_end = snapshot.get("fiscal_end")
    if not fiscal_end:
        return None, "missing-fiscal-end"

    facts, submissions = load_company(session, ticker, cik)
    quote = load_market_quote(ticker)

    filing = find_latest_filing(submissions, fiscal_end)
    period_filing_shares = None
    current_filing_shares = None
    primary_text = None

    if filing:
        primary_text = filing_text(session, cik, filing)
        if primary_text:
            period_filing_shares = parse_fiscal_end_common_shares(primary_text, fiscal_end)
            if period_filing_shares is None:
                period_filing_shares = parse_common_shares_from_filing(
                    primary_text,
                    fiscal_end,
                    for_current=False,
                )

            current_filing_shares = parse_common_shares_from_filing(
                primary_text,
                fiscal_end,
                for_current=True,
            )

        # Filing-level XBRL cover report is the preferred current-share fallback.
        cover_text = filing_text(session, cik, filing, filename="R1.htm")
        if cover_text:
            current_filing_shares = parse_common_shares_from_filing(
                cover_text,
                fiscal_end,
                for_current=True,
            ) or current_filing_shares

    valuation = build_valuation_snapshot(
        facts,
        fiscal_end,
        quote,
        filing_shares=period_filing_shares,
        current_filing_shares=current_filing_shares,
    )

    # Company Facts occasionally omits the standard EPS fact even though the
    # annual filing visibly reports it. Always use the latest annual filing,
    # not the latest quarter filing, for PER's annual EPS fallback.
    if valuation.get("eps") is None:
        annual_filing = find_latest_annual_filing(submissions)
        annual_text = filing_text(session, cik, annual_filing) if annual_filing else None
        filing_eps = parse_reported_eps_from_filing(annual_text) if annual_text else None
        if filing_eps is not None:
            valuation["eps"] = filing_eps["value"]
            valuation["eps_source"] = filing_eps["tag"]
            valuation["eps_basis"] = filing_eps["basis"]
            price = valuation.get("price")
            eps = filing_eps["value"]
            valuation["per"] = price / eps if price is not None and eps and eps > 0 else None
            valuation["per_basis"] = "current-price/directly-reported-annual-sec-filing-eps" if valuation.get("per") is not None else None
            if annual_filing:
                valuation["eps_filing_form"] = annual_filing["form"]
                valuation["eps_filing_accession"] = annual_filing["accession"]
                valuation["eps_filing_document"] = annual_filing["document"]
                valuation["eps_filing_report_date"] = annual_filing.get("report_date")
                valuation["eps_filing_date"] = annual_filing.get("filing_date")

    if filing:
        valuation["filing_form"] = filing["form"]
        valuation["filing_accession"] = filing["accession"]
        valuation["filing_document"] = filing["document"]
        valuation["filing_date"] = filing.get("filing_date")

    valuation["period_filing_shares_found"] = period_filing_shares is not None
    valuation["current_filing_shares_found"] = current_filing_shares is not None
    valuation["updated_at"] = datetime.now(timezone.utc).isoformat()

    snapshot["market"] = quote
    snapshot["valuation"] = valuation

    return snapshot, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0 = all eligible rows")
    parser.add_argument("--ticker")
    parser.add_argument("--tickers")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.20)
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

    explicit_tickers = None
    if args.ticker:
        explicit_tickers = [args.ticker.upper().strip()]
    elif args.tickers:
        explicit_tickers = [x.upper().strip() for x in args.tickers.split(",") if x.strip()]

    rows = _load_standard_rows(sb, tickers=explicit_tickers, refresh=args.refresh)

    if args.limit > 0:
        rows = rows[: args.limit]

    print("============================================================")
    print("US STANDARD VALUATION-ONLY COLLECTOR")
    print("============================================================")
    print(f"Rows selected: {len(rows)}")
    print(f"Refresh mode : {args.refresh}")

    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    try:
        _close_series(BENCHMARK)
    except Exception:
        pass

    success = 0
    skipped = 0
    failed = 0

    for i, row in enumerate(rows, 1):
        ticker = row["ticker"]
        try:
            snapshot, reason = collect_valuation_one(session, row)
            if snapshot is None:
                skipped += 1
                print(f"[{i}/{len(rows)}] {ticker}: SKIPPED {reason}")
                continue

            (
                sb.table("US_Fundamental")
                .update({"snapshot": snapshot})
                .eq("ticker", ticker)
                .execute()
            )

            valuation = snapshot.get("valuation") or {}
            print(
                f"[{i}/{len(rows)}] {ticker}: "
                f"EPS={valuation.get('eps')} "
                f"BPS={valuation.get('bps')} "
                f"PER={valuation.get('per')} "
                f"PBR={valuation.get('pbr')} "
                f"marketCap={valuation.get('market_cap')}"
            )
            success += 1
        except Exception as exc:
            failed += 1
            print(f"[{i}/{len(rows)}] {ticker}: FAILED: {exc}")

        if args.sleep > 0:
            time.sleep(args.sleep)

    print("============================================================")
    print("Completed")
    print(f"Success : {success}")
    print(f"Skipped : {skipped}")
    print(f"Failed  : {failed}")
    print("============================================================")


if __name__ == "__main__":
    main()
