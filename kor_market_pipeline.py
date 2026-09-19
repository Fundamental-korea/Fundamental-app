"""Production integration for KRX market snapshots + DART financial snapshots.

Source policy:
- KRX Open API: current close, current listed shares, current market cap.
- DART: latest confirmed financial report, reported EPS, equity and reporting-period basis.
- BPS: latest-report equity divided by reporting-period share basis. Prefer KRX listed
  shares on the report period end when DART stock-total is unavailable.
- Current market-cap shares are never substituted with an old DART share count.

The daily pipeline fetches KOSPI/KOSDAQ/KONEX once for the current snapshot. Historical KRX
share counts are fetched per unique report-period date only when needed and cached in
memory for the run.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional
import re

import collector
from kor_market_snapshot import _request_daily_trade, _to_int


MARKET_API_IDS = ("stk_bydd_trd", "ksq_bydd_trd", "knx_bydd_trd")
_HISTORICAL_KRX_SHARE_CACHE: Dict[str, Dict[str, int]] = {}


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
    """Fetch KOSPI + KOSDAQ + KONEX daily market data once and return code -> snapshot."""
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


def _load_krx_share_map_for_date(date_str: str) -> Dict[str, int]:
    """Load KRX listed shares for one date, cached by date.

    The KRX daily endpoint is market-wide, so one date requires at most one request
    per supported market. This keeps the daily 1y job from making one API request
    per company when many companies share the same report period.
    """
    key = str(date_str)
    if key in _HISTORICAL_KRX_SHARE_CACHE:
        return _HISTORICAL_KRX_SHARE_CACHE[key]

    bas_dd = key.replace("-", "")
    result: Dict[str, int] = {}
    for api_id in ("stk_bydd_trd", "ksq_bydd_trd", "knx_bydd_trd"):
        try:
            rows = _request_daily_trade(api_id, bas_dd)
        except Exception as exc:
            print(f"  ⚠️ KRX {api_id} {key} 과거 상장주식수 조회 실패: {exc}")
            continue
        for row in rows:
            code = str(row.get("ISU_CD") or row.get("isu_cd") or "").strip().zfill(6)
            listed = _to_int(row.get("LIST_SHRS") or row.get("list_shrs"))
            if code and listed and listed > 0:
                result[code] = listed

    _HISTORICAL_KRX_SHARE_CACHE[key] = result
    if result:
        print(f"  📚 KRX historical listed shares: {key} / {len(result):,}개")
    return result


def fetch_krx_listed_shares_on_or_before(stock_code: str, date_str: str, max_lookback_days: int = 7):
    """Return KRX listed shares on the report date, or nearest prior trading day."""
    target = datetime.strptime(date_str, "%Y-%m-%d").date()
    for offset in range(max_lookback_days + 1):
        d = target - timedelta(days=offset)
        shares = _load_krx_share_map_for_date(d.isoformat()).get(str(stock_code).zfill(6))
        if shares:
            source_date = d.isoformat()
            return shares, source_date
    return None, None


def _extract_company_value(company_info, *keys):
    if company_info is None:
        return None
    if hasattr(company_info, "to_dict"):
        data = company_info.to_dict()
    elif isinstance(company_info, dict):
        data = company_info
    else:
        try:
            data = dict(company_info)
        except Exception:
            return None
    for key in keys:
        value = data.get(key)
        if value not in (None, "", "-", "nan"):
            return str(value).strip()
    return None


def _get_dart_fiscal_month(stock_code: str) -> Optional[int]:
    """Read DART company settlement month only when an explicit report date is missing."""
    try:
        info = collector.dart.company(str(stock_code).zfill(6))
        raw = _extract_company_value(info, "acc_mt", "accMt")
        if raw:
            return int(raw)
    except Exception as exc:
        print(f"  ⚠️ [{stock_code}] DART 결산월 조회 실패: {exc}")
    return None


def _derive_calendar_report_period_end(report_year: int, report_code: str, stock_code: str) -> Optional[str]:
    """Derive actual report period-end from fiscal month and DART report code.

    DART's bsns_year is the business-year label. For non-December fiscal years,
    quarterly/half-year periods can end in the following calendar year, so deriving
    the period-end from report code alone is not safe.
    """
    fiscal_month = _get_dart_fiscal_month(stock_code)
    if fiscal_month is None or report_year is None:
        return None

    code = str(report_code)
    if code == "11011":
        year = int(report_year)
        month = int(fiscal_month)
    else:
        offset_months = {"11013": 3, "11012": 6, "11014": 9}.get(code)
        if offset_months is None:
            return None

        start_year = int(report_year) if fiscal_month == 12 else int(report_year) - 1
        start_month = int(fiscal_month) % 12 + 1
        zero_based = (start_month - 1) + offset_months
        year = start_year + zero_based // 12
        month = zero_based % 12 + 1

    import calendar
    day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{day:02d}"

def _get_latest_report_basis(stock_code: str) -> Optional[dict]:
    """Read the already-cached latest DART report metrics when possible."""
    try:
        return collector.fetch_latest_report_metrics(
            stock_code,
            use_ofs_for_manufacturing=False,
            force_refresh=False,
        )
    except Exception as exc:
        print(f"  ⚠️ [{stock_code}] 최신 DART 보고기간 조회 실패: {exc}")
        return None


def _resolve_report_period_end(stock_code: str, latest: Optional[dict]) -> tuple[Optional[str], str]:
    if not latest:
        return None, "unavailable"

    explicit = latest.get("report_period_end")
    if explicit:
        return explicit, "DART explicit period-end"

    derived = _derive_calendar_report_period_end(
        latest.get("_report_year"),
        latest.get("_report_code"),
        stock_code,
    )
    if derived:
        return derived, "derived from DART report code after confirming December fiscal year"

    return None, "unavailable"


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
    """Install the production KRX layer around collector.sync_1y_only.

    The underlying DART/score calculation still runs first. This wrapper then replaces
    only the market snapshot fields and recalculates PER/PBR using the latest EPS/BPS.
    BPS is explicitly rebuilt from latest-report equity + report-period share basis.
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
            print(f"  ⚠️ [{stock_name}] KRX snapshot 없음 - 시장 필드는 기존값 유지")
            return True

        row_res = (
            collector.supabase.table("Fundamental")
            .select("eps,bps,issued_shares,data_basis_label")
            .eq("stock_code", stock_code)
            .limit(1)
            .execute()
        )
        row = (row_res.data or [None])[0] or {}

        latest = _get_latest_report_basis(stock_code)
        report_period_end, report_period_basis = _resolve_report_period_end(stock_code, latest)

        eps = row.get("eps")
        if eps is None and latest is not None:
            eps = latest.get("reported_eps")

        equity = None
        if latest is not None:
            equity = latest.get("equity_for_bps", latest.get("total_equity"))

        if equity is None:
            bps_existing = row.get("bps")
            if bps_existing is not None:
                try:
                    equity = float(bps_existing) * float(row.get("issued_shares") or 0)
                except (TypeError, ValueError):
                    equity = None

        period_end_shares = None
        shares_source = None
        shares_basis_date = None
        if report_period_end:
            period_end_shares, shares_basis_date = fetch_krx_listed_shares_on_or_before(
                stock_code, report_period_end
            )
            if period_end_shares:
                shares_source = (
                    "KRX listed shares on report period end"
                    if shares_basis_date == report_period_end
                    else "KRX listed shares on nearest prior trading day"
                )

        # DART annual stock-total may be used only when the latest financial report is
        # itself an annual report. For quarter/semiannual snapshots, never silently reuse
        # an older annual share count as the period-end denominator.
        if (
            not period_end_shares
            and latest is not None
            and latest.get("_report_code") == "11011"
            and row.get("issued_shares")
        ):
            try:
                legacy_dart_shares = int(row["issued_shares"])
                if legacy_dart_shares > 0:
                    period_end_shares = legacy_dart_shares
                    shares_basis_date = None
                    shares_source = "DART_STOCK_TOTAL_FALLBACK"
            except (TypeError, ValueError):
                pass

        bps = None
        if equity is not None and period_end_shares and period_end_shares > 0:
            bps = equity / period_end_shares

        per, pbr = _recalculate_valuation(
            snapshot.get("stock_price"), eps, bps
        )

        payload = {
            "stock_code": str(stock_code).zfill(6),
            "stock_price": snapshot.get("stock_price"),
            "listed_shares": snapshot.get("listed_shares"),
            "market_cap": snapshot.get("market_cap"),
            "market_snapshot_date": snapshot.get("market_snapshot_date"),
            "market_data_source": snapshot.get("market_data_source"),
            "period_end_shares": period_end_shares,
            "shares_basis_date": shares_basis_date,
            "shares_data_source": shares_source,
            "per": per,
            "pbr": pbr,
            "eps": round(float(eps), 2) if eps is not None else None,
            "bps": round(float(bps), 2) if bps is not None else None,
        }

        if period_end_shares and shares_source:
            payload["bps_basis_label"] = (
                f"latest financial equity / {shares_source}"
                + (f" ({shares_basis_date})" if shares_basis_date else "")
            )

        # Keep legacy issued_shares untouched. It is retained for backward compatibility;
        # listed_shares is the only field used for current market-cap calculations.
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
            f"상장주식수 {listed_shares if listed_shares is not None else 0:,} / "
            f"시총 {market_cap if market_cap is not None else 0:,} / "
            f"기간말주식수 {period_end_shares if period_end_shares is not None else 0:,} / "
            f"기준일 {shares_basis_date or 'DART fallback'}"
        )
        return True

    collector.sync_1y_only = sync_1y_only_with_market_snapshot
    return {
        "market_snapshot_count": len(market_map),
        "original_sync": original_sync,
    }
