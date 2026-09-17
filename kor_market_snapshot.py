"""Korean market snapshot adapter.

Market-data policy:
- KRX Open API is the preferred source for close, listed shares and market cap.
- FinanceDataReader is retained only as a fallback for price when KRX is unavailable.
- DART reporting-period share counts must never be used as the current market-cap share count.

KRX Open API requires an approved AUTH_KEY. Set KRX_API_KEY in the runtime environment.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

import requests

try:
    import FinanceDataReader as fdr
except Exception:  # pragma: no cover
    fdr = None

KRX_API_KEY = os.environ.get("KRX_API_KEY", "").strip()
KRX_BASE_URL = os.environ.get(
    "KRX_API_BASE_URL", "https://data-dbg.krx.co.kr/svc/apis/sto"
).rstrip("/")
KRX_TIMEOUT = int(os.environ.get("KRX_API_TIMEOUT", "10"))


def _to_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "None", "nan", "NaN"}:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _unwrap_rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("OutBlock_1", "outBlock_1", "OutBlock1", "data", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    # Some KRX responses wrap blocks under a result object.
    for value in payload.values():
        if isinstance(value, dict):
            rows = _unwrap_rows(value)
            if rows:
                return rows
    return []


def _request_daily_trade(api_id: str, bas_dd: str) -> list[dict]:
    if not KRX_API_KEY:
        return []
    url = f"{KRX_BASE_URL}/{api_id}"
    response = requests.get(
        url,
        params={"basDd": bas_dd},
        headers={"AUTH_KEY": KRX_API_KEY},
        timeout=KRX_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    return _unwrap_rows(payload)


def _latest_business_day(max_lookback: int = 7) -> str:
    d = date.today()
    for _ in range(max_lookback + 1):
        if d.weekday() < 5:
            return d.strftime("%Y%m%d")
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def fetch_krx_market_snapshot(stock_code: str) -> Optional[Dict[str, Any]]:
    """Fetch one stock from the latest KRX daily trading snapshot.

    The endpoint returns all listed securities for the requested market/date, so callers
    should cache/batch this result for whole-universe collection. This one-code helper is
    intentionally conservative and retries previous business days when the latest day is
    not yet published.
    """
    if not KRX_API_KEY:
        return None

    requested = str(stock_code).zfill(6)
    d = date.today()
    for _ in range(7):
        if d.weekday() < 5:
            bas_dd = d.strftime("%Y%m%d")
            for api_id in ("stk_bydd_trd", "ksq_bydd_trd", "knx_bydd_trd"):
                try:
                    rows = _request_daily_trade(api_id, bas_dd)
                except Exception:
                    continue
                for row in rows:
                    code = str(row.get("ISU_CD") or row.get("isu_cd") or "").zfill(6)
                    if code != requested:
                        continue
                    price = _to_int(row.get("TDD_CLSPRC") or row.get("tdd_cls_prc"))
                    shares = _to_int(row.get("LIST_SHRS") or row.get("list_shrs"))
                    market_cap = _to_int(row.get("MKTCAP") or row.get("mktcap"))
                    if price is None:
                        continue
                    return {
                        "stock_price": price,
                        "listed_shares": shares,
                        "market_cap": market_cap,
                        "market_snapshot_date": datetime.strptime(bas_dd, "%Y%m%d").date().isoformat(),
                        "market_data_source": "KRX_OPEN_API",
                    }
        d -= timedelta(days=1)
    return None


def fetch_fdr_price_fallback(stock_code: str) -> Optional[Dict[str, Any]]:
    """Legacy price-only fallback. Never supplies listed shares or market cap."""
    if fdr is None:
        return None
    try:
        df = fdr.DataReader(str(stock_code).zfill(6))
        if df is None or df.empty or "Close" not in df.columns:
            return None
        value = _to_int(df["Close"].iloc[-1])
        if value is None:
            return None
        idx = df.index[-1]
        snapshot_date = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
        return {
            "stock_price": value,
            "listed_shares": None,
            "market_cap": None,
            "market_snapshot_date": snapshot_date,
            "market_data_source": "FDR_PRICE_FALLBACK",
        }
    except Exception:
        return None


def fetch_market_snapshot(stock_code: str) -> Optional[Dict[str, Any]]:
    """Public entry point: KRX first, FDR price-only fallback second."""
    snapshot = fetch_krx_market_snapshot(stock_code)
    if snapshot is not None:
        return snapshot
    return fetch_fdr_price_fallback(stock_code)
