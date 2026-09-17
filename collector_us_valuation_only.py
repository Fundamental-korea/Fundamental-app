"""Incremental US valuation snapshot collector.

This collector preserves the existing US_Fundamental score and snapshot data.
It only enriches companies that already have a Standard snapshot and do not
already contain a valuation block, unless --refresh is requested.
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

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


def get_standard_rows(sb, refresh=False):
    """Load Standard rows that already have a fundamental snapshot."""
    rows = []
    offset = 0
    page_size = 1000
    while True:
        response = (
            sb.table("US_Fundamental")
            .select("ticker,cik,company_name,sector,snapshot")
            .in_("sector", list(STANDARD_SECTORS))
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    if refresh:
        return [r for r in rows if r.get("snapshot") and r["snapshot"].get("fiscal_end")]

    return [
        r
        for r in rows
        if r.get("snapshot")
        and r["snapshot"].get("fiscal_end")
        and not r["snapshot"].get("valuation")
    ]


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

    if explicit_tickers:
        response = (
            sb.table("US_Fundamental")
            .select("ticker,cik,company_name,sector,snapshot")
            .in_("ticker", explicit_tickers)
            .execute()
        )
        rows = response.data or []
        rows = [
            r for r in rows
            if r.get("sector") in STANDARD_SECTORS
            and r.get("snapshot")
            and r["snapshot"].get("fiscal_end")
            and (args.refresh or not r["snapshot"].get("valuation"))
        ]
    else:
        rows = get_standard_rows(sb, refresh=args.refresh)

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
