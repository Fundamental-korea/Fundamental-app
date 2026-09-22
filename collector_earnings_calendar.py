"""Background earnings calendar collector.

Collects external earnings schedules into Supabase so Streamlit never waits on
DART/Yahoo Finance during page rendering.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from news_earnings import (
    fetch_dart_disclosures,
    build_earnings_events,
    _get_supabase_client,
)

KR_WATCHLIST = [
    ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("005380", "현대차"),
    ("000270", "기아"), ("035420", "NAVER"), ("035720", "카카오"),
    ("051910", "LG화학"), ("006400", "삼성SDI"), ("105560", "KB금융"),
    ("055550", "신한지주"), ("000810", "삼성화재"), ("012330", "현대모비스"),
    ("028260", "삼성물산"), ("034730", "SK"), ("003550", "LG"),
    ("096770", "SK이노베이션"), ("009150", "삼성전기"), ("066570", "LG전자"),
    ("068270", "셀트리온"), ("012450", "한화에어로스페이스"), ("042700", "한미반도체"),
    ("086520", "에코프로"), ("247540", "에코프로비엠"), ("352820", "하이브"),
    ("259960", "크래프톤"),
]

def _num(value):
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None

def _yahoo_us():
    start = date.today() - timedelta(days=60)
    end = date.today() + timedelta(days=120)
    cal = yf.Calendars(start=start, end=end)
    df = cal.get_earnings_calendar(filter_most_active=True, limit=100)
    rows = []
    if df is None or df.empty:
        return rows
    for _, row in df.reset_index().iterrows():
        raw = row.get("Event Start Date")
        if pd.isna(raw):
            continue
        d = pd.to_datetime(raw).date()
        reported = _num(row.get("Reported EPS"))
        rows.append({
            "market": "US",
            "stock_code": str(row.get("Symbol", "")).strip().upper(),
            "stock_name": str(row.get("Company", row.get("Company Name", ""))),
            "event_date": d.isoformat(),
            "event_type": "earnings_calendar",
            "report_name": "Yahoo Finance Earnings Calendar",
            "receipt_no": f"YF-US-{str(row.get('Symbol','')).strip().upper()}-{d.isoformat()}",
            "source_url": f"https://finance.yahoo.com/quote/{str(row.get('Symbol','')).strip().upper()}/analysis/",
            "announced_at": None,
            "is_primary_event": True,
            "metadata": {
                "source": "Yahoo Finance",
                "status": "reported" if reported is not None else "upcoming",
                "timing": str(row.get("Timing", "") or ""),
                "eps_estimate": _num(row.get("EPS Estimate")),
                "actual": reported,
                "surprise": _num(row.get("Surprise(%)")),
            },
        })
    return rows

def _yahoo_kr():
    today = date.today()
    end = today + timedelta(days=120)
    rows = []
    for code, name in KR_WATCHLIST:
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
                rows.append({
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
                        "status": "upcoming",
                        "timing": "예정",
                        "eps_estimate": estimate,
                        "actual": None,
                        "surprise": None,
                    },
                })
        except Exception as exc:
            print(f"[Yahoo KR] {code} failed: {exc}")
    return rows

def main():
    client = _get_supabase_client()
    today = date.today()
    start = today - timedelta(days=60)
    end = today + timedelta(days=120)

    # Refresh only Yahoo calendar rows in the active display window. DART rows are untouched.
    (
        client.table("earnings_events")
        .delete()
        .eq("event_type", "earnings_calendar")
        .gte("event_date", start.isoformat())
        .lte("event_date", end.isoformat())
        .execute()
    )

    dart = fetch_dart_disclosures(
        start_date=start,
        end_date=today,
        page_count=100,
        max_pages=20,
    )
    dart_events = build_earnings_events(dart)
    rows = [{
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
    } for e in dart_events if e.receipt_no]

    rows.extend(_yahoo_us())
    rows.extend(_yahoo_kr())

    if rows:
        client.table("earnings_events").upsert(rows, on_conflict="market,receipt_no").execute()

    print(f"[EARNINGS] DART={len(dart_events)} Yahoo={len(rows)-len(dart_events)} total_upsert={len(rows)}")

if __name__ == "__main__":
    main()
