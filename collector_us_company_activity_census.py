"""SEC submissions activity census for eligible US companies.

Read-only. This second-stage census checks each eligible issuer's SEC
submissions record for current ticker/exchange identity and recent reporting
activity. It does not change eligibility or database rows.

Heuristics are intentionally descriptive:
- ACTIVE_REPORTING: a qualifying periodic report was filed recently.
- STALE_REPORTING: filings exist, but no qualifying periodic report in the
  recent window.
- NO_RECENT_SEC_FILINGS: submissions record has no filing in the stale window.
- SEC_404/REQUEST_ERROR: source could not be fetched.
These are review flags, not automatic delisting decisions.
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
UA = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
PAGE_SIZE = 1000
RATE_SECONDS = 0.20
RECENT_DAYS = 548  # ~18 months
STALE_DAYS = 1095   # ~3 years
PERIODIC_FORMS = {
    "10-K", "10-K/A", "10-Q", "10-Q/A",
    "20-F", "20-F/A", "40-F", "40-F/A",
}
OUT = Path("artifacts")
OUT.mkdir(exist_ok=True)


def norm(value: str | None) -> str:
    return (value or "").strip().upper()


def cik10(value: str | int | None) -> str:
    if value is None:
        return ""
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits.zfill(10) if digits else ""


def get_eligible(sb):
    rows = []
    offset = 0
    while True:
        page = (
            sb.table("US_Companies")
            .select(
                "ticker,cik,company_name,is_active,is_fundamental_eligible,"
                "exchange,security_type,company_type,scoring_profile"
            )
            .eq("is_fundamental_eligible", True)
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


class SecClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": UA,
            "Accept-Encoding": "gzip, deflate",
        })
        self.last = 0.0

    def get_json(self, url):
        wait = RATE_SECONDS - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        self.last = time.monotonic()

        last_exc = None
        for attempt in range(5):
            try:
                r = self.s.get(url, timeout=45)
                if r.status_code == 200:
                    return r.json(), None
                if r.status_code == 404:
                    return None, "SEC_404"
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(min(2**attempt, 16))
                    continue
                r.raise_for_status()
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < 4:
                    time.sleep(min(2**attempt, 16))
                    continue
        return None, f"REQUEST_ERROR:{type(last_exc).__name__ if last_exc else 'unknown'}"


def recent_rows(doc):
    recent = ((doc or {}).get("filings") or {}).get("recent") or {}
    fields = [
        "accessionNumber", "filingDate", "reportDate", "form",
        "primaryDocument", "primaryDocDescription",
    ]
    n = len(recent.get("accessionNumber") or [])
    rows = []
    for i in range(n):
        rows.append({key: (recent.get(key) or [None] * n)[i] for key in fields})
    return rows


def iso_date(value):
    try:
        return date.fromisoformat(value)
    except Exception:
        return None


def classify(doc, local):
    tickers = {norm(x) for x in ((doc or {}).get("tickers") or []) if x}
    exchanges = {norm(x) for x in ((doc or {}).get("exchanges") or []) if x}
    local_ticker = norm(local.get("ticker"))
    rows = recent_rows(doc)
    today = date.today()
    recent_cutoff = today - timedelta(days=RECENT_DAYS)
    stale_cutoff = today - timedelta(days=STALE_DAYS)

    periodic = []
    for row in rows:
        d = iso_date(row.get("filingDate"))
        if d and row.get("form") in PERIODIC_FORMS:
            periodic.append((d, row))

    last_filing = max(
        (iso_date(x.get("filingDate")) for x in rows if iso_date(x.get("filingDate"))),
        default=None,
    )
    last_periodic = max(periodic, key=lambda x: x[0])[1] if periodic else None
    last_periodic_date = max((x[0] for x in periodic), default=None)

    periodic_forms = sorted({x[1].get("form") for x in periodic if x[1].get("form")})
    us_periodic_forms = {"10-K", "10-K/A", "10-Q", "10-Q/A"}
    foreign_periodic_forms = {"20-F", "20-F/A", "40-F", "40-F/A"}
    has_us_periodic = bool(set(periodic_forms) & us_periodic_forms)
    has_foreign_periodic = bool(set(periodic_forms) & foreign_periodic_forms)

    sec_entity_type = norm((doc or {}).get("entityType"))
    sic_description = norm((doc or {}).get("sicDescription"))
    adr_sic_signal = "AMERICAN DEPOSITARY RECEIPTS" in sic_description
    listed_exchange_signal = bool(exchanges & {"NYSE", "NASDAQ", "CBOE"})
    otc_signal = "OTC" in exchanges

    if local_ticker in tickers:
        identity = "SUBMISSIONS_TICKER_MATCH"
    elif tickers:
        identity = "SUBMISSIONS_TICKER_MISMATCH"
    else:
        identity = "SUBMISSIONS_NO_TICKER"

    if last_periodic_date and last_periodic_date >= recent_cutoff:
        activity = "ACTIVE_REPORTING"
    elif last_filing and last_filing >= recent_cutoff:
        activity = "RECENT_FILING_NON_PERIODIC"
    elif last_filing and last_filing >= stale_cutoff:
        activity = "STALE_REPORTING"
    else:
        activity = "NO_RECENT_SEC_FILINGS"

    if identity != "SUBMISSIONS_TICKER_MATCH":
        review_bucket = "IDENTITY_REVIEW"
    elif adr_sic_signal:
        review_bucket = "FOREIGN_ADR_SIGNAL"
    elif otc_signal:
        review_bucket = "OTC"
    elif listed_exchange_signal and has_foreign_periodic and not has_us_periodic:
        review_bucket = "FOREIGN_ISSUER_EXCHANGE"
    elif listed_exchange_signal and sec_entity_type == "OPERATING" and has_us_periodic:
        review_bucket = "US_EXCHANGE_STANDARD_REPORTING"
    elif listed_exchange_signal and has_us_periodic:
        review_bucket = "US_EXCHANGE_OTHER_ENTITY"
    elif listed_exchange_signal:
        review_bucket = "EXCHANGE_NON_STANDARD"
    else:
        review_bucket = "NO_SEC_EXCHANGE"

    return {
        "activity_status": activity,
        "review_bucket": review_bucket,
        "identity_status": identity,
        "sec_entity_name": (doc or {}).get("name"),
        "sec_entity_type": (doc or {}).get("entityType"),
        "sec_sic": (doc or {}).get("sic"),
        "sec_sic_description": (doc or {}).get("sicDescription"),
        "sec_state_of_incorporation": (doc or {}).get("stateOfIncorporation"),
        "sec_state_of_incorporation_description": (doc or {}).get("stateOfIncorporationDescription"),
        "sec_tickers": sorted(tickers),
        "sec_exchanges": sorted(exchanges),
        "periodic_forms_seen": periodic_forms,
        "has_us_periodic": has_us_periodic,
        "has_foreign_periodic": has_foreign_periodic,
        "adr_sic_signal": adr_sic_signal,
        "sec_fiscal_year_end": (doc or {}).get("fiscalYearEnd"),
        "last_filing_date": last_filing.isoformat() if last_filing else None,
        "last_periodic_form": last_periodic.get("form") if last_periodic else None,
        "last_periodic_date": last_periodic_date.isoformat() if last_periodic_date else None,
        "recent_filings_count": sum(
            1 for r in rows
            if iso_date(r.get("filingDate")) and iso_date(r.get("filingDate")) >= recent_cutoff
        ),
    }


def main():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    companies = get_eligible(sb)
    client = SecClient()

    results = []
    counts = Counter()
    identity_counts = Counter()
    review_counts = Counter()
    exchange_counts = Counter()
    errors = []

    print(f"=== US COMPANY ACTIVITY CENSUS v1 ===")
    print(f"eligible={len(companies)}")

    for i, local in enumerate(companies, 1):
        cik = cik10(local.get("cik"))
        url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        doc, error = client.get_json(url)

        base = {
            "ticker": local.get("ticker"),
            "cik": cik,
            "company_name": local.get("company_name"),
            "local_exchange": local.get("exchange"),
            "local_company_type": local.get("company_type"),
            "local_scoring_profile": local.get("scoring_profile"),
        }

        if error:
            item = {**base, "activity_status": error, "identity_status": error}
            errors.append(item)
            counts[error] += 1
            results.append(item)
            print(f"[{i}/{len(companies)}] {local.get('ticker')}: {error}")
            continue

        info = classify(doc, local)
        item = {**base, **info}
        results.append(item)

        counts[info["activity_status"]] += 1
        identity_counts[info["identity_status"]] += 1
        review_counts[info["review_bucket"]] += 1
        for ex in info["sec_exchanges"] or ["<NONE>"]:
            exchange_counts[ex] += 1

        if i % 100 == 0 or info["activity_status"] != "ACTIVE_REPORTING":
            print(
                f"[{i}/{len(companies)}] {local.get('ticker')}: "
                f"activity={info['activity_status']} identity={info['identity_status']} "
                f"last_periodic={info['last_periodic_date'] or '-'}"
            )

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eligible": len(companies),
        "activity_status": dict(counts),
        "identity_status": dict(identity_counts),
        "review_bucket": dict(review_counts),
        "sec_exchange_distribution": dict(exchange_counts),
        "source": "SEC submissions endpoint",
        "recent_days": RECENT_DAYS,
        "stale_days": STALE_DAYS,
    }
    report = {"summary": summary, "companies": results}
    out = OUT / "us_company_activity_census_v1.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n[ACTIVITY STATUS]")
    for key, value in counts.most_common():
        print(f"{key}={value}")
    print("\n[IDENTITY STATUS]")
    for key, value in identity_counts.most_common():
        print(f"{key}={value}")
    print("\n[REVIEW BUCKET]")
    for key, value in review_counts.most_common():
        print(f"{key}={value}")

    print("\n[SEC EXCHANGE]")
    for key, value in exchange_counts.most_common():
        print(f"{key}={value}")
    print(f"\n[ERRORS]={len(errors)}")
    print(f"[REPORT] {out}")


if __name__ == "__main__":
    main()
