"""Automated market-overview snapshot collector.

The Overview page reads from this table first:
public.market_overview_snapshot

Source policy:
- US indices/risk/rates and shared commodities/FX: Yahoo Finance via yfinance.
- Korea core indices: FinanceDataReader (KRX-backed path) with yfinance fallback.
- The collector never deletes an existing snapshot when one instrument fails.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import yfinance as yf

try:
    import FinanceDataReader as fdr
except Exception:
    fdr = None

from supabase import create_client


SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    or os.environ.get("SUPABASE_KEY", "").strip()
)

MARKET_SPECS = {
    "US": [
        {"symbol": "^GSPC", "label": "S&P 500", "group": "market", "kind": "index"},
        {"symbol": "^IXIC", "label": "Nasdaq", "group": "market", "kind": "index"},
        {"symbol": "^DJI", "label": "Dow Jones", "group": "market", "kind": "index"},
        {"symbol": "^RUT", "label": "Russell 2000", "group": "market", "kind": "index"},
        {"symbol": "^VIX", "label": "VIX", "group": "conditions", "kind": "vix"},
        {"symbol": "^TNX", "label": "US 10Y", "group": "conditions", "kind": "yield"},
        {"symbol": "GC=F", "label": "Gold", "group": "conditions", "kind": "commodity"},
        {"symbol": "CL=F", "label": "WTI Oil", "group": "conditions", "kind": "commodity"},
    ],
    "KR": [
        {"symbol": "^KS11", "fdr_symbol": "KS11", "label": "KOSPI", "group": "market", "kind": "index"},
        {"symbol": "^KQ11", "fdr_symbol": "KQ11", "label": "KOSDAQ", "group": "market", "kind": "index"},
        {"symbol": "KRW=X", "label": "USD/KRW", "group": "conditions", "kind": "fx"},
        {"symbol": "GC=F", "label": "Gold", "group": "conditions", "kind": "commodity"},
        {"symbol": "CL=F", "label": "WTI Oil", "group": "conditions", "kind": "commodity"},
    ],
}


def _require_supabase():
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL/SUPABASE_SECRET_KEY(or SUPABASE_KEY)가 필요합니다.")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def _extract_history_result(
    history: pd.DataFrame,
    *,
    market: str,
    spec: dict[str, Any],
    source: str,
) -> dict[str, Any] | None:
    if history is None or history.empty or "Close" not in history.columns:
        return None

    close = pd.to_numeric(history["Close"], errors="coerce").dropna()
    if len(close) < 1:
        return None

    latest = float(close.iloc[-1])
    previous = float(close.iloc[-2]) if len(close) >= 2 else latest
    if not pd.notna(latest):
        return None

    change_abs = latest - previous
    change_pct = (change_abs / previous * 100.0) if previous else 0.0

    idx = history.index[-1]
    try:
        asof_date = pd.Timestamp(idx).date().isoformat()
    except Exception:
        return None

    return {
        "market": market,
        "symbol": spec["symbol"],
        "label": spec["label"],
        "metric_group": spec["group"],
        "value": latest,
        "change_pct": change_pct,
        "change_abs": change_abs,
        "asof_date": asof_date,
        "source": source,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _fetch_yfinance(market: str, spec: dict[str, Any]) -> dict[str, Any] | None:
    ticker = spec["symbol"]
    try:
        history = yf.Ticker(ticker).history(
            period="10d",
            interval="1d",
            auto_adjust=False,
            actions=False,
        )
        return _extract_history_result(
            history,
            market=market,
            spec=spec,
            source="yfinance",
        )
    except Exception as exc:
        print(f"[MARKET_OVERVIEW] yfinance 실패 {ticker}: {exc}")
        return None


def _fetch_fdr(market: str, spec: dict[str, Any]) -> dict[str, Any] | None:
    if fdr is None or not spec.get("fdr_symbol"):
        return None

    try:
        history = fdr.DataReader(spec["fdr_symbol"])
        return _extract_history_result(
            history,
            market=market,
            spec=spec,
            source="FinanceDataReader",
        )
    except Exception as exc:
        print(f"[MARKET_OVERVIEW] FinanceDataReader 실패 {spec['symbol']}: {exc}")
        return None


def fetch_market_overview(market: str) -> list[dict[str, Any]]:
    market = str(market).upper()
    specs = MARKET_SPECS.get(market)
    if not specs:
        raise ValueError(f"지원하지 않는 시장: {market}")

    results = []
    for spec in specs:
        row = None
        if spec.get("fdr_symbol"):
            # Korea core indices: query both sources and keep the freshest trading date.
            # FDR can occasionally lag for several sessions, so source priority is
            # determined by asof_date rather than by provider name.
            fdr_row = _fetch_fdr(market, spec)
            yahoo_row = _fetch_yfinance(market, spec)
            candidates = [item for item in (fdr_row, yahoo_row) if item is not None]
            if candidates:
                row = max(
                    candidates,
                    key=lambda item: str(item.get("asof_date") or ""),
                )
        else:
            row = _fetch_yfinance(market, spec)

        if row is not None:
            results.append(row)

    return results


def persist_market_overview(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    client = _require_supabase()
    result = (
        client.table("market_overview_snapshot")
        .upsert(rows, on_conflict="market,symbol")
        .execute()
    )
    return len(result.data or rows)




def load_market_overview(market: str) -> list[dict[str, Any]]:
    """Load the latest persisted snapshot for an Overview market."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []

    try:
        client = _require_supabase()
        result = (
            client.table("market_overview_snapshot")
            .select(
                "market,symbol,label,metric_group,value,change_pct,change_abs,"
                "asof_date,source,updated_at"
            )
            .eq("market", str(market).upper())
            .order("metric_group")
            .order("symbol")
            .execute()
        )
        return list(result.data or [])
    except Exception as exc:
        print(f"[MARKET_OVERVIEW] DB load failed: {exc}")
        return []


def collect_and_persist(market: str | None = None) -> int:
    markets = [str(market).upper()] if market else ["US", "KR"]
    total = 0
    for item in markets:
        rows = fetch_market_overview(item)
        saved = persist_market_overview(rows)
        print(f"[MARKET_OVERVIEW] {item}: fetched={len(rows)} saved={saved}")
        total += saved
    return total


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["US", "KR"], default=None)
    args = parser.parse_args()

    count = collect_and_persist(args.market)
    print(f"[MARKET_OVERVIEW] total saved: {count}")
