"""Background earnings calendar collector.

Universe:
- US: S&P 500 + Nasdaq-100 + Russell 2000 (deduped; target ~2,500 issuers)
- KR: top 500 KRX companies by latest market cap in Fundamental

The UI reads only the Supabase snapshot, so external calls happen here in CI.
"""
from __future__ import annotations

import io
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
import yfinance as yf

from news_earnings import (
    fetch_dart_disclosures,
    build_earnings_events,
    _get_supabase_client,
)

SP500_URL = "https://raw.githubusercontent.com/Ate329/top-us-stock-tickers/main/tickers/sp500.csv"
NASDAQ100_URL = "https://raw.githubusercontent.com/Gary-Strauss/nasdaq100-scraper/main/data/nasdaq100_constituents.csv"
RUSSELL2000_URL = (
    "https://www.ishares.com/us/products/239710/ishares-russell-2000-etf/"
    "1467271812596.ajax?fileType=csv&fileName=IWM"
)
US_TARGET = 2500
KR_TARGET = 500
YAHOO_PAGE_SIZE = 100
YAHOO_MAX_PAGES = 50


def _num(value):
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _clean_ticker(value):
    t = str(value or "").strip().upper()
    if not t or t in {"NAN", "CASH", "USD", "TOTAL"}:
        return None
    return t.replace(".", "-")


def _get_csv(url, **kwargs):
    r = requests.get(
        url,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 Fundamental-app earnings collector"},
    )
    r.raise_for_status()
    return pd.read_csv(io.StringIO(r.text), **kwargs)


def _index_tickers():
    tickers = {}

    # S&P 500
    try:
        df = _get_csv(SP500_URL)
        col = next(c for c in df.columns if c.lower() in {"symbol", "ticker"})
        for raw in df[col]:
            t = _clean_ticker(raw)
            if t:
                tickers[t] = "S&P500"
    except Exception as exc:
        print(f"[Universe] S&P 500 fetch failed: {exc}")

    # Nasdaq-100
    try:
        df = _get_csv(NASDAQ100_URL)
        col = next(c for c in df.columns if c.lower() in {"ticker", "symbol"})
        for raw in df[col]:
            t = _clean_ticker(raw)
            if t:
                tickers.setdefault(t, "NASDAQ100")
    except Exception as exc:
        print(f"[Universe] Nasdaq-100 fetch failed: {exc}")

    # Russell 2000 via current IWM holdings.
    try:
        r = requests.get(
            RUSSELL2000_URL,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 Fundamental-app earnings collector",
                "Accept": "text/csv",
            },
        )
        r.raise_for_status()
        lines = r.text.splitlines()
        header_idx = next(
            (i for i, line in enumerate(lines) if line.startswith("Ticker,")),
            0,
        )
        df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))
        col = next(c for c in df.columns if "Ticker" in str(c) or "Symbol" in str(c))
        for raw in df[col]:
            t = _clean_ticker(raw)
            if t:
                tickers.setdefault(t, "RUSSELL2000")
    except Exception as exc:
        print(f"[Universe] Russell 2000 fetch failed: {exc}")

    return tickers


def _us_universe(client):
    sources = _index_tickers()

    # Keep only issuers present in our SEC company master.
    sec = (
        client.table("US_Companies")
        .select("ticker,company_name,is_active,is_fundamental_eligible")
        .eq("is_active", True)
        .eq("is_fundamental_eligible", True)
        .limit(10000)
        .execute()
    )
    rows = sec.data or []
    by_ticker = {
        _clean_ticker(r.get("ticker")): r
        for r in rows
        if _clean_ticker(r.get("ticker"))
    }

    universe = []
    for ticker, index_name in sources.items():
        if ticker in by_ticker:
            universe.append(
                {
                    "ticker": ticker,
                    "company_name": by_ticker[ticker].get("company_name") or ticker,
                    "universe_source": index_name,
                }
            )

    # If index sources temporarily return fewer than target, fill from the
    # SEC universe deterministically so the collector remains useful.
    if len(universe) < US_TARGET:
        existing = {r["ticker"] for r in universe}
        for ticker in sorted(by_ticker):
            if ticker in existing:
                continue
            r = by_ticker[ticker]
            universe.append(
                {
                    "ticker": ticker,
                    "company_name": r.get("company_name") or ticker,
                    "universe_source": "SEC_FILL",
                }
            )
            if len(universe) >= US_TARGET:
                break

    # Prefer index members; then SEC fill. Hard cap avoids accidental universe
    # explosions if a source starts returning non-issuer rows.
    return universe[:US_TARGET]


def _kr_universe(client):
    # Fundamental is the existing KRX company universe. Select the 500 largest
    # by latest market cap; this avoids maintaining a second Korean security master.
    resp = (
        client.table("Fundamental")
        .select("stock_code,stock_name,market,market_cap")
        .eq("market", "KOR")
        .not_.is_("market_cap", "null")
        .order("market_cap", desc=True)
        .limit(KR_TARGET)
        .execute()
    )
    return [
        {
            "stock_code": str(r["stock_code"]).zfill(6),
            "stock_name": r.get("stock_name") or str(r["stock_code"]),
        }
        for r in (resp.data or [])
    ]


def _yahoo_us(universe):
    """Fetch Yahoo earnings pages and keep only our ~2,500-company universe.

    Yahoo caps each calendar request at 100 rows. Pagination is used in the
    background job, never from Streamlit.
    """
    start = date.today() - timedelta(days=60)
    end = date.today() + timedelta(days=120)
    target = {r["ticker"]: r for r in universe}
    rows = []
    seen = set()

    for page in range(YAHOO_MAX_PAGES):
        offset = page * YAHOO_PAGE_SIZE
        try:
            cal = yf.Calendars(start=start, end=end)
            df = cal.get_earnings_calendar(
                filter_most_active=False,
                limit=YAHOO_PAGE_SIZE,
                offset=offset,
            )
        except Exception as exc:
            print(f"[Yahoo US] page={page} offset={offset} failed: {exc}")
            break

        if df is None or df.empty:
            break

        for _, row in df.reset_index().iterrows():
            ticker = _clean_ticker(row.get("Symbol"))
            if not ticker or ticker not in target:
                continue

            raw = row.get("Event Start Date")
            if pd.isna(raw):
                continue
            d = pd.to_datetime(raw).date()
            key = (ticker, d.isoformat())
            if key in seen:
                continue
            seen.add(key)

            reported = _num(row.get("Reported EPS"))
            rows.append(
                {
                    "market": "US",
                    "stock_code": ticker,
                    "stock_name": target[ticker]["company_name"],
                    "event_date": d.isoformat(),
                    "event_type": "earnings_calendar",
                    "report_name": "Yahoo Finance Earnings Calendar",
                    "receipt_no": f"YF-US-{ticker}-{d.isoformat()}",
                    "source_url": f"https://finance.yahoo.com/quote/{ticker}/analysis/",
                    "announced_at": None,
                    "is_primary_event": True,
                    "metadata": {
                        "source": "Yahoo Finance",
                        "universe_source": target[ticker]["universe_source"],
                        "status": "reported" if reported is not None else "upcoming",
                        "timing": str(row.get("Timing", "") or ""),
                        "eps_estimate": _num(row.get("EPS Estimate")),
                        "actual": reported,
                        "surprise": _num(row.get("Surprise(%)")),
                    },
                }
            )

        if len(df) < YAHOO_PAGE_SIZE:
            break

    print(f"[Yahoo US] universe={len(universe)} events={len(rows)}")
    return rows


def _yahoo_kr(universe):
    today = date.today()
    end = today + timedelta(days=120)
    rows = []

    for item in universe:
        code = item["stock_code"]
        name = item["stock_name"]
        try:
            cal = yf.Ticker(f"{code}.KS").calendar or {}
            dates = cal.get("Earnings Date") or []
            estimate = _num(cal.get("Earnings Average"))
            for raw in dates:
                try:
                    d = pd.to_datetime(raw).date()
                except Exception:
                    continue
                if not (today <= d <= end):
                    continue
                rows.append(
                    {
                        "market": "KR",
                        "stock_code": code,
                        "stock_name": name,
                        "event_date": d.isoformat(),
                        "event_type": "earnings_calendar",
                        "report_name": "Yahoo Finance Earnings Calendar",
                        "receipt_no": f"YF-KR-{code}-{d.isoformat()}",
                        "source_url": f"https://finance.yahoo.com/quote/{code}.KS/analysis/",
                        "announced_at": None,
                        "is_primary_event": True,
                        "metadata": {
                            "source": "Yahoo Finance",
                            "universe_source": "KRX_TOP500_MARKET_CAP",
                            "status": "upcoming",
                            "timing": "예정",
                            "eps_estimate": estimate,
                            "actual": None,
                            "surprise": None,
                        },
                    }
                )
        except Exception as exc:
            print(f"[Yahoo KR] {code} failed: {exc}")

    print(f"[Yahoo KR] universe={len(universe)} events={len(rows)}")
    return rows


def main():
    client = _get_supabase_client()
    today = date.today()
    start = today - timedelta(days=60)
    end = today + timedelta(days=120)

    us_universe = _us_universe(client)
    kr_universe = _kr_universe(client)
    print(f"[Universe] US={len(us_universe)} KR={len(kr_universe)}")

    # Collect first. Only replace the active Yahoo snapshot after collection
    # succeeds, so a transient Yahoo/DART outage cannot blank the calendar.
    dart = fetch_dart_disclosures(
        start_date=start,
        end_date=today,
        page_count=100,
        max_pages=20,
    )
    dart_events = build_earnings_events(dart)
    rows = [
        {
            "market": "KR",
            "stock_code": e.stock_code,
            "stock_name": e.corp_name,
            "event_date": e.event_date,
            "event_type": e.event_type,
            "report_name": e.report_name,
            "receipt_no": e.receipt_no,
            "source_url": e.source_url,
            "announced_at": None,
            "is_primary_event": e.event_type == "preliminary_earnings",
            "metadata": {"source": "DART", "status": "reported", "timing": "발표"},
        }
        for e in dart_events
        if e.receipt_no
    ]

    yahoo_us_rows = _yahoo_us(us_universe)
    yahoo_kr_rows = _yahoo_kr(kr_universe)
    rows.extend(yahoo_us_rows)
    rows.extend(yahoo_kr_rows)

    yahoo_rows = len(yahoo_us_rows) + len(yahoo_kr_rows)
    if yahoo_rows == 0:
        raise RuntimeError("No Yahoo earnings rows collected; refusing to modify snapshot.")

    # 기존 스냅샷을 통째로 삭제하지 않는다.
    # 발표 직후 Yahoo 응답에서 EPS Estimate가 일시적으로 비어도
    # 이전에 저장해 둔 컨센서스를 보존하고, Actual/Surprise만 추가한다.
    existing = (
        client.table("earnings_events")
        .select("id,market,stock_code,event_date,receipt_no,metadata,report_name")
        .eq("event_type", "earnings_calendar")
        .gte("event_date", start.isoformat())
        .lte("event_date", end.isoformat())
        .limit(10000)
        .execute()
    )
    existing_rows = existing.data or []
    existing_by_receipt = {
        (str(r.get("market") or ""), str(r.get("receipt_no") or "")): r
        for r in existing_rows
        if r.get("receipt_no")
    }

    merged_rows = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for row in rows:
        old = existing_by_receipt.get(
            (str(row.get("market") or ""), str(row.get("receipt_no") or ""))
        )
        old_meta = (old or {}).get("metadata") or {}
        meta = dict(row.get("metadata") or {})

        # 발표 전 처음 잡힌 컨센서스는 별도 snapshot 필드로 보존한다.
        consensus_snapshot = (
            old_meta.get("consensus_eps_snapshot")
            if old_meta.get("consensus_eps_snapshot") is not None
            else old_meta.get("eps_estimate")
        )
        incoming_estimate = meta.get("eps_estimate")
        if consensus_snapshot is None and incoming_estimate is not None:
            consensus_snapshot = incoming_estimate

        # Yahoo가 발표 후 estimate를 생략/변경해도 화면 비교에는 최초 컨센서스를 사용한다.
        if consensus_snapshot is not None:
            meta["consensus_eps_snapshot"] = consensus_snapshot
            meta["eps_estimate"] = consensus_snapshot

        # Actual / Surprise가 이번 응답에 없으면 기존 값을 유지한다.
        if meta.get("actual") is None and old_meta.get("actual") is not None:
            meta["actual"] = old_meta.get("actual")
        if meta.get("surprise") is None and old_meta.get("surprise") is not None:
            meta["surprise"] = old_meta.get("surprise")

        if meta.get("actual") is not None:
            meta["status"] = "reported"
            meta["reported_at"] = old_meta.get("reported_at") or now_iso
        elif old_meta.get("status") == "reported":
            meta["status"] = "reported"
        else:
            meta["status"] = meta.get("status") or "upcoming"

        if not meta.get("timing") and old_meta.get("timing"):
            meta["timing"] = old_meta.get("timing")
        meta["source_last_checked_at"] = now_iso

        merged = dict(row)
        merged["metadata"] = meta
        merged_rows.append(merged)

    # 같은 종목의 예정일이 Yahoo에서 변경된 경우 새 날짜를 추가한다.
    # 기존 데이터를 먼저 지우지 않으므로 부분 장애가 발생해도 기존 일정이 사라지지 않는다.
    client.table("earnings_events").upsert(
        merged_rows,
        on_conflict="market,receipt_no",
    ).execute()

    print(
        f"[EARNINGS] US_UNIVERSE={len(us_universe)} "
        f"KR_UNIVERSE={len(kr_universe)} "
        f"DART={len(dart_events)} Yahoo={yahoo_rows} "
        f"total_upsert={len(merged_rows)} preserved_snapshot_rows={len(existing_rows)}"
    )


if __name__ == "__main__":
    main()
