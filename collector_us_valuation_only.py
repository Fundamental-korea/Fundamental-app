"""Incremental US valuation snapshot collector.

This collector preserves the existing US_Fundamental score and snapshot data.
It only enriches companies that already have a Standard snapshot and do not
already contain a valuation block, unless --refresh is requested.
"""

from __future__ import annotations

import argparse
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html import unescape

import requests
from bs4 import BeautifulSoup
from supabase import create_client

from collector_us_fundamental import SEC_USER_AGENT, SUPABASE_KEY, SUPABASE_URL, load_company
from collector_us_valuation import (
    find_latest_filing,
    load_market_quote,
    parse_fiscal_end_common_shares,
)
from downturn_us import BENCHMARK, _close_series
from sec_filing_utils import filing_text_resilient, find_annual_filing_with_history, find_filing_with_history
from us_valuation import (
    build_valuation_snapshot,
    currencies_compatible,
    currency_from_text,
    parse_common_shares_from_filing,
)

STANDARD_SECTORS = (
    "technology",
    "healthcare",
    "consumer",
    "industrials",
    "energy",
    "materials",
    "communication",
)

EPS_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}


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


def _collapse_space(value):
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def _parse_reported_number(token):
    """Parse a single SEC-rendered numeric token, including parentheses negatives."""
    token = _collapse_space(token)
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
    return value if math.isfinite(value) else None


def _xml_local_name(tag):
    return str(tag or "").rsplit("}", 1)[-1].lower()


def find_eps_report_documents(session, cik, annual_filing):
    """Find SEC XBRL report files related to annual earnings per share."""
    if not annual_filing:
        return []

    summary_text = filing_text_resilient(session, cik, annual_filing, filename="FilingSummary.xml")
    if not summary_text:
        return []

    try:
        root = ET.fromstring(summary_text)
    except ET.ParseError:
        return []

    matches = []
    for report in root.iter():
        if _xml_local_name(report.tag) != "report":
            continue

        values = {}
        for child in list(report):
            values[_xml_local_name(child.tag)] = _collapse_space(child.text)

        short_name = (values.get("shortname") or "").lower()
        long_name = (values.get("longname") or "").lower()
        menu_category = (values.get("menucategory") or "").lower()
        html_file = values.get("htmlfilename") or values.get("htmlfile")

        if not html_file:
            continue

        searchable = f"{short_name} {long_name} {menu_category}"
        # Skip report families that are clearly supporting schedules/narratives.
        # "Details" alone is allowed because genuine XBRL rows are often
        # rendered with a "(Details)" suffix.
        if any(
            token in searchable
            for token in (
                "schedule",
                "supplemental",
                "narrative",
                "antidilutive",
                "reconciliation",
            )
        ):
            continue
        if "earnings per share" not in searchable and "net income per share" not in searchable:
            continue
        if "per share" not in searchable:
            continue

        details_rank = 0 if short_name in {
            "earnings per share",
            "earnings per share attributable to ordinary equity holders of the parent",
        } else 1
        exact_rank = 0 if short_name == "earnings per share" else 1
        matches.append((details_rank, exact_rank, html_file))

    if not matches:
        return []

    matches.sort(key=lambda item: (item[0], item[1], item[2]))
    return [item[2] for item in matches]


def _normalized_report_rows(text):
    """Read rows from a small SEC XBRL report such as R12.htm."""
    if not text:
        return []
    soup = BeautifulSoup(text, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        values = [_collapse_space(cell.get_text(" ", strip=True)) for cell in cells]
        values = [value for value in values if value]
        if values:
            rows.append(values)
    return rows


def _eps_row_label_kind(label):
    """Rank only direct EPS rows; exclude schedules, narratives and share-count helpers."""
    text = _collapse_space(label).lower()
    if not text:
        return None

    blocked = (
        "schedule",
        "supplemental",
        "narrative",
        "antidilutive",
        "reconciliation",
        "numerator",
        "denominator",
        "weighted average",
        "shares outstanding",
        "shares in ",
        "dilutive securities",
        "excluded from computation",
    )
    if any(token in text for token in blocked):
        return None

    direct_prefix = re.search(
        r"\b(?:earnings|net income)\s+per\s+(?:common\s+)?share\b",
        text,
    )
    attributable_prefix = re.search(
        r"\bearnings\s+per\s+share\s+attributable\b",
        text,
    )
    if not direct_prefix and not attributable_prefix:
        return None

    if "per common share" in text and "assuming dilution" in text:
        return 0
    if "per common share" in text and "diluted" in text:
        return 0
    if "per share" in text and "diluted" in text:
        return 0
    if "per common share" in text:
        return 1
    if "per share" in text and "basic" in text:
        return 2
    return 3


def is_safe_reported_eps_label(label):
    """Public gate shared with the valuation target selector."""
    return _eps_row_label_kind(label) is not None


def parse_reported_eps_from_report(text):
    """Extract directly reported annual EPS from the SEC's dedicated XBRL report."""
    rows = _normalized_report_rows(text)
    if not rows:
        return None

    candidates = []
    for row in rows:
        for index, cell in enumerate(row):
            kind = _eps_row_label_kind(cell)
            if kind is None:
                continue

            values = []
            detected_currency = currency_from_text(cell)
            for later_cell in row[index + 1 :]:
                detected_currency = detected_currency or currency_from_text(later_cell)
                for token in re.findall(
                    r"(?:\(-?\$?\d[\d,]*(?:\.\d+)?\)|-?\$?\d[\d,]*(?:\.\d+)?)",
                    later_cell,
                ):
                    value = _parse_reported_number(token)
                    if value is not None and abs(value) < 1_000_000:
                        values.append(value)

            if values:
                candidates.append((kind, values[0], cell, detected_currency))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    kind, value, label, detected_currency = candidates[0]
    return {
        "value": value,
        "tag": "filing:xbrl-reported-eps",
        "namespace": "filing",
        "basis": "directly-reported-annual-sec-xbrl-eps",
        "report_label": label,
        "currency": detected_currency,
    }


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
        if form not in EPS_ANNUAL_FORMS:
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

    # Resolve the filing from the current issuer first. If the requested fiscal
    # period is absent (as with successor issuers such as XOM), inspect CIKs
    # represented by recent accession numbers and search their submissions.
    filing = find_filing_with_history(
        session,
        cik,
        submissions,
        fiscal_end,
        find_latest_filing,
    )
    period_filing_shares = None
    current_filing_shares = None
    primary_text = None

    if filing:
        primary_text = filing_text_resilient(session, cik, filing)
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

        cover_text = filing_text_resilient(session, cik, filing, filename="R1.htm")
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
        security_ticker=ticker,
    )

    # Company Facts is the first source for annual EPS. Some issuers do not
    # expose the annual EPS observation there in a usable form, so fall back to
    # SEC XBRL annual reports discovered through FilingSummary.xml. The annual
    # filing itself is resolved across successor/predecessor CIK history.
    if valuation.get("eps") is None:
        annual_filing = find_annual_filing_with_history(
            session,
            cik,
            submissions,
            fiscal_end=fiscal_end,
        )
        if annual_filing:
            report_documents = find_eps_report_documents(session, cik, annual_filing)
            for report_document in report_documents:
                report_text = filing_text_resilient(session, cik, annual_filing, filename=report_document)
                filing_eps = parse_reported_eps_from_report(report_text) if report_text else None
                if filing_eps is None:
                    continue

                valuation["eps"] = filing_eps["value"]
                valuation["eps_source"] = filing_eps["tag"]
                valuation["eps_basis"] = filing_eps["basis"]
                valuation["eps_report_document"] = report_document
                valuation["eps_report_label"] = filing_eps.get("report_label")
                valuation["eps_filing_source_cik"] = annual_filing.get("source_cik")

                eps_currency = filing_eps.get("currency") or valuation.get("reporting_currency")
                valuation["eps_currency"] = eps_currency

                price = valuation.get("price")
                eps = filing_eps["value"]
                quote_currency = valuation.get("quote_currency")
                per_currency_ok = currencies_compatible(eps_currency, quote_currency)
                valuation["per_currency_compatible"] = per_currency_ok
                valuation["per"] = (
                    price / eps
                    if price is not None and eps > 0 and per_currency_ok
                    else None
                )
                valuation["per_basis"] = (
                    "current-price/directly-reported-annual-sec-xbrl-eps"
                    if valuation.get("per") is not None
                    else None
                )
                valuation["per_status"] = (
                    "currency-mismatch"
                    if valuation.get("per") is None
                    and price is not None
                    and eps > 0
                    and not per_currency_ok
                    else None
                )
                valuation["eps_filing_form"] = annual_filing["form"]
                valuation["eps_filing_accession"] = annual_filing["accession"]
                valuation["eps_filing_document"] = annual_filing["document"]
                valuation["eps_filing_report_date"] = annual_filing.get("report_date")
                valuation["eps_filing_date"] = annual_filing.get("filing_date")
                break

    if filing:
        valuation["filing_form"] = filing["form"]
        valuation["filing_accession"] = filing["accession"]
        valuation["filing_document"] = filing["document"]
        valuation["filing_date"] = filing.get("filing_date")
        valuation["filing_source_cik"] = filing.get("source_cik")

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
