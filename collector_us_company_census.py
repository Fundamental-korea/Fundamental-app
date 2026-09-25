"""SEC-backed census of the US company universe.

Read-only: this job does not change Supabase. It compares the local US_Companies
registry with the SEC's current ticker/CIK/exchange association file and emits
a machine-readable report for the next validation stage.

The SEC notes that the association file is periodically updated and does not
guarantee completeness/accuracy, so this is a screening census, not an
automatic eligibility decision.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import requests
from supabase import create_client

SEC_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
UA = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
PAGE_SIZE = 1000
OUT_DIR = Path("artifacts")
OUT_DIR.mkdir(exist_ok=True)


def norm_ticker(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", "", value.strip().upper())


def norm_cik(value: str | int | None) -> str:
    if value is None:
        return ""
    digits = re.sub(r"\D", "", str(value))
    return digits.zfill(10) if digits else ""


def fetch_json(url: str):
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def fetch_all_companies(sb):
    rows = []
    offset = 0
    while True:
        page = (
            sb.table("US_Companies")
            .select(
                "ticker,cik,company_name,is_active,is_fundamental_eligible,"
                "exchange,security_type,entity_type,company_type,scoring_profile,"
                "exclusion_reason,sector_common"
            )
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def sec_records(doc):
    fields = doc.get("fields") or []
    index = {name: i for i, name in enumerate(fields)}
    out = []
    for row in doc.get("data") or []:
        item = {field: row[i] if i < len(row) else None for field, i in index.items()}
        out.append(item)
    return out


def main():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    local = fetch_all_companies(sb)
    sec = sec_records(fetch_json(SEC_URL))

    by_pair = {}
    by_ticker = {}
    by_cik = {}
    sec_exchange_counts = Counter()

    for item in sec:
        ticker = norm_ticker(item.get("ticker"))
        cik = norm_cik(item.get("cik"))
        exchange = (item.get("exchange") or "").strip()
        if not ticker or not cik:
            continue
        by_pair[(ticker, cik)] = item
        by_ticker.setdefault(ticker, []).append(item)
        by_cik.setdefault(cik, []).append(item)
        sec_exchange_counts[exchange or "<NULL>"] += 1

    counts = Counter()
    exchange_counts = Counter()
    candidates = []

    for row in local:
        ticker = norm_ticker(row.get("ticker"))
        cik = norm_cik(row.get("cik"))
        pair = by_pair.get((ticker, cik))

        if pair:
            status = "SEC_PAIR_MATCH"
        elif ticker in by_ticker:
            status = "TICKER_MATCH_CIK_MISMATCH"
        elif cik in by_cik:
            status = "CIK_MATCH_TICKER_MISMATCH"
        else:
            status = "NOT_IN_SEC_TICKER_EXCHANGE"

        sec_exchange = ((pair or {}).get("exchange") or "").strip() if pair else ""
        if status == "SEC_PAIR_MATCH":
            exchange_counts[sec_exchange or "<NULL>"] += 1

        if sec_exchange.upper() == "OTC":
            venue_class = "OTC"
        elif sec_exchange:
            venue_class = "EXCHANGE_LISTED"
        else:
            venue_class = "NO_SEC_EXCHANGE"

        counts[status] += 1
        counts[f"VENUE:{venue_class}"] += 1

        candidates.append(
            {
                **row,
                "local_ticker": ticker,
                "local_cik": cik,
                "sec_status": status,
                "sec_exchange": sec_exchange or None,
                "sec_name": (pair or {}).get("name"),
                "sec_ticker": (pair or {}).get("ticker"),
                "sec_cik": norm_cik((pair or {}).get("cik")) if pair else None,
            }
        )

    eligible = [r for r in candidates if r.get("is_fundamental_eligible")]
    eligible_counts = Counter(r["sec_status"] for r in eligible)
    eligible_venue = Counter(
        (r.get("sec_exchange") or "<NO_SEC_EXCHANGE>") for r in eligible
    )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "local_total": len(local),
        "local_eligible": len(eligible),
        "sec_ticker_exchange_records": len(sec),
        "all_status": dict(counts),
        "eligible_status": dict(eligible_counts),
        "eligible_exchange": dict(eligible_venue),
        "sec_exchange_distribution": dict(sec_exchange_counts),
    }

    report = {
        "summary": summary,
        "companies": candidates,
    }

    out = OUT_DIR / "us_company_universe_census_v1.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=== US COMPANY UNIVERSE CENSUS v1 ===")
    for key in [
        "local_total",
        "local_eligible",
        "sec_ticker_exchange_records",
    ]:
        print(f"{key}={summary[key]}")

    print("\n[ALL STATUS]")
    for key, value in counts.most_common():
        print(f"{key}={value}")

    print("\n[ELIGIBLE STATUS]")
    for key, value in eligible_counts.most_common():
        print(f"{key}={value}")

    print("\n[ELIGIBLE EXCHANGE]")
    for key, value in eligible_venue.most_common():
        print(f"{key}={value}")

    print(f"\n[REPORT] {out}")


if __name__ == "__main__":
    main()
