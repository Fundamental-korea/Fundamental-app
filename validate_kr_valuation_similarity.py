"""Dry-run Naver-style Korean valuation validation for selected liquid/reference stocks.

This script does not update public.Fundamental. It force-reparses the cached DART
report metrics for the sample so attributable net income / owner equity fields are
available, then calculates:
- TTM net income from report-period deltas
- TTM EPS approximation using the latest report-period KRX listed shares
- BPS using latest attributable equity / latest report-period KRX listed shares
- PER/PBR from the current KRX market snapshot
"""

from __future__ import annotations

import json

import collector
from kor_market_pipeline import (
    fetch_market_snapshot_map,
    fetch_krx_listed_shares_on_or_before,
)


SAMPLES = (
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "035420",  # NAVER
    "105560",  # KB금융
    "021820",  # 세원정공
    "018500",  # 동원모빌리티
    "097870",  # 효성오앤비
    "099750",  # 이지케어텍
)


def main() -> int:
    market_map = fetch_market_snapshot_map(max_lookback_days=7)
    results = []

    for code in SAMPLES:
        latest = collector.fetch_latest_report_metrics(
            code,
            use_ofs_for_manufacturing=False,
            force_refresh=True,
        )
        if latest is None:
            results.append({"ticker": code, "error": "latest report unavailable"})
            continue

        n_more = 4
        reports = collector.fetch_recent_quarters_metrics(
            code,
            latest_report=latest,
            n_more=n_more,
            use_ofs_for_manufacturing=False,
        )
        ttm_net_income = collector.calculate_ttm_net_income(
            reports,
            latest_report=latest,
        )

        report_period_end = latest.get("report_period_end")
        period_shares = None
        period_shares_date = None
        if report_period_end:
            period_shares, period_shares_date = fetch_krx_listed_shares_on_or_before(
                code, report_period_end, max_lookback_days=7
            )

        equity = latest.get("equity_for_bps")
        ttm_eps = (
            ttm_net_income / period_shares
            if ttm_net_income is not None and period_shares
            else None
        )
        bps = (
            equity / period_shares
            if equity is not None and period_shares
            else None
        )

        snap = market_map.get(code) or {}
        price = snap.get("stock_price")
        per = price / ttm_eps if price and ttm_eps else None
        pbr = price / bps if price and bps and bps > 0 else None

        results.append({
            "ticker": code,
            "name": latest.get("_report_year"),
            "report_year": latest.get("_report_year"),
            "report_code": latest.get("_report_code"),
            "report_period_end": report_period_end,
            "period_shares": period_shares,
            "period_shares_date": period_shares_date,
            "parent_net_income_latest": latest.get("parent_net_income"),
            "latest_net_income": latest.get("net_income"),
            "equity_for_bps": equity,
            "ttm_net_income": round(ttm_net_income, 2) if ttm_net_income is not None else None,
            "ttm_eps_approx": round(ttm_eps, 2) if ttm_eps is not None else None,
            "bps": round(bps, 2) if bps is not None else None,
            "price": price,
            "per_ttm": round(per, 2) if per is not None else None,
            "pbr": round(pbr, 2) if pbr is not None else None,
            "snapshot_date": snap.get("market_snapshot_date"),
        })

    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
