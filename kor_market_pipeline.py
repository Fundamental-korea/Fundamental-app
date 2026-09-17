"""Safe integration layer for KRX market snapshots and the existing Korean collector.

The existing collector remains the source of DART financial data and 1y scoring.
This module adds the market-data layer without rewriting the large collector.py in one step:

KRX -> current close / listed shares / market cap
DART collector -> financial snapshot / EPS / BPS / period-end share basis

The KRX daily endpoints return the whole market for a date, so we fetch KOSPI/KOSDAQ once
per run and keep an in-memory lookup instead of making one API request per stock.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

import collector
from kor_market_snapshot import _request_daily_trade, _to_int


MARKET_API_IDS = ("stk_bydd_trd", "ksq_bydd_trd")


def _parse_market_row(row: dict, snapshot_date: str) -> Optional[dict]:
    code = str(row.get("ISU_CD") or row.get("isu_cd") or "").strip().zfill(6)
    if not code or code == "000000":
        return None

    price = _to_int(row.get("TDD_CLSPRC") or row.get("tdd_cls_prc"))
    listed_shares = _to_int(row.get("LIST_SHRS") or row.get("list_shrs"))
    market_cap = _to_int(row.get("MKTCAP") or row.get("mktcap"))
    if price is None:
        return None

    return {
        "stock_price": price,
        "listed_shares": listed_shares,
        "market_cap": market_cap,
        "market_snapshot_date": snapshot_date,
        "market_data_source": "KRX_OPEN_API",
    }


def fetch_market_snapshot_map(max_lookback_days: int = 7) -> Dict[str, dict]:
    """Fetch KOSPI + KOSDAQ daily market data once and return code -> snapshot."""
    if not collector.os.environ.get("KRX_API_KEY", "").strip():
        print("⚠️ KRX_API_KEY가 없어 KRX market snapshot을 건너뜁니다.")
        return {}

    result: Dict[str, dict] = {}
    today = date.today()

    for api_id in MARKET_API_IDS:
        market_loaded = False
        d = today
        for _ in range(max_lookback_days + 1):
            if d.weekday() >= 5:
                d -= timedelta(days=1)
                continue

            bas_dd = d.strftime("%Y%m%d")
            try:
                rows = _request_daily_trade(api_id, bas_dd)
            except Exception as exc:
                print(f"⚠️ KRX {api_id} {bas_dd} 요청 실패: {exc}")
                rows = []

            if rows:
                snapshot_date = datetime.strptime(bas_dd, "%Y%m%d").date().isoformat()
                for row in rows:
                    snapshot = _parse_market_row(row, snapshot_date)
                    if snapshot:
                        result[str(row.get("ISU_CD") or row.get("isu_cd")).strip().zfill(6)] = snapshot
                print(f"📈 KRX {api_id}: {len(rows):,}건 로드 ({snapshot_date})")
                market_loaded = True
                break

            d -= timedelta(days=1)

        if not market_loaded:
            print(f"⚠️ KRX {api_id}: 최근 {max_lookback_days + 1}개 평일에서 데이터를 찾지 못했습니다.")

    print(f"📊 KRX market snapshot cache: {len(result):,}개 종목")
    return result


def _recalculate_valuation(price: Optional[int], eps: Any, bps: Any) -> tuple[Optional[float], Optional[float]]:
    try:
        eps_value = float(eps) if eps is not None else None
    except (TypeError, ValueError):
        eps_value = None
    try:
        bps_value = float(bps) if bps is not None else None
    except (TypeError, ValueError):
        bps_value = None

    per = round(price / eps_value, 2) if price is not None and eps_value else None
    pbr = round(price / bps_value, 2) if price is not None and bps_value and bps_value > 0 else None
    return per, pbr


def install_market_snapshot_integration() -> dict:
    """Monkey-patch the existing daily 1y worker with a KRX post-processing layer.

    This is intentionally isolated from collector.py for the first production test. The
    original DART/score calculation still runs unchanged; after a successful stock update,
    KRX values overwrite only market snapshot fields and PER/PBR are recalculated from the
    already-stored EPS/BPS.
    """
    market_map = fetch_market_snapshot_map()
    original_sync = collector.sync_1y_only

    def sync_1y_only_with_market_snapshot(
        stock_code,
        stock_name,
        sector,
        wics_sector,
        holding_company,
        existing_period_scores,
        kospi_mdd_cache,
        use_ofs_for_manufacturing=True,
        force_refresh=False,
    ):
        ok = original_sync(
            stock_code,
            stock_name,
            sector,
            wics_sector,
            holding_company,
            existing_period_scores,
            kospi_mdd_cache,
            use_ofs_for_manufacturing=use_ofs_for_manufacturing,
            force_refresh=force_refresh,
        )
        if not ok:
            return False

        snapshot = market_map.get(str(stock_code).zfill(6))
        if snapshot is None:
            print(f"  ⚠️ [{stock_name}] KRX snapshot 없음 - 기존 DART/FDR 결과 유지")
            return True

        row_res = (
            collector.supabase.table("Fundamental")
            .select("eps,bps,issued_shares,data_basis_label")
            .eq("stock_code", stock_code)
            .limit(1)
            .execute()
        )
        row = (row_res.data or [None])[0] or {}

        per, pbr = _recalculate_valuation(
            snapshot.get("stock_price"), row.get("eps"), row.get("bps")
        )

        issued_shares = row.get("issued_shares")
        payload = {
            "stock_price": snapshot.get("stock_price"),
            "listed_shares": snapshot.get("listed_shares"),
            "market_cap": snapshot.get("market_cap"),
            "market_snapshot_date": snapshot.get("market_snapshot_date"),
            "market_data_source": snapshot.get("market_data_source"),
            "period_end_shares": issued_shares,
            "shares_data_source": "DART_STOCK_TOTAL",
            "per": per,
            "pbr": pbr,
        }

        # Keep the legacy issued_shares field for compatibility. Its semantic role is now
        # explicitly the DART/reporting-period share basis; listed_shares is the live market basis.
        if issued_shares is not None:
            payload["bps_basis_label"] = "latest financial equity / DART stock-total share basis"

        collector.supabase.table("Fundamental").upsert(
            collector._sanitize_json(payload), on_conflict="stock_code"
        ).execute()

        market_cap = snapshot.get("market_cap")
        listed_shares = snapshot.get("listed_shares")
        if market_cap and listed_shares and snapshot.get("stock_price"):
            implied = snapshot["stock_price"] * listed_shares
            gap = abs(implied - market_cap) / market_cap if market_cap else 0
            if gap > 0.02:
                print(
                    f"  ⚠️ [{stock_name}] KRX 시총 검증 경고: "
                    f"price×listed_shares와 MKTCAP 차이 {gap:.2%}"
                )

        print(
            f"  🏦 [{stock_name}] KRX 적용: "
            f"주가 {snapshot.get('stock_price'):,} / "
            f"상장주식수 {listed_shares:,} / "
            f"시총 {market_cap:,} / "
            f"기준일 {snapshot.get('market_snapshot_date')}"
        )
        return True

    collector.sync_1y_only = sync_1y_only_with_market_snapshot
    return {
        "market_snapshot_count": len(market_map),
        "original_sync": original_sync,
    }
