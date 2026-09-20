"""Targeted repair for selected Korean stocks that failed the 1y refresh batch.

This is intentionally a small, repeatable repair job. It reuses the production
collector and market-snapshot integration, but only touches the selected codes.
"""

from __future__ import annotations

import sys

import collector
from collector import get_1y_update_targets, get_kospi_mdd_cache
from kor_market_pipeline import install_market_snapshot_integration


REPAIR_CODES = (
    "001720",  # 신영증권
    "395400",  # SK리츠
    "448730",  # 삼성FN리츠
    "001570",  # 금양
    "001080",  # 만호제강
    "950170",  # JTC
    "021820",  # 세원정공
    "334970",  # 프레스티지바이오로직스
    "217950",  # 파마리서치바이오 (KONEX snapshot path)
    "018500",  # 동원모빌리티
    "190650",  # 코리아에셋투자증권
    "050860",  # 아세아텍
    "097870",  # 효성오앤비
)


def main() -> int:
    print("🔧 선택 종목 개별 복구 시작")
    print("대상:", ", ".join(REPAIR_CODES))

    # Install the same production KRX wrapper used by update_dart.py.
    integration = install_market_snapshot_integration()
    print(f"🏦 KRX snapshot integration 준비 완료 ({integration['market_snapshot_count']:,}개)")

    rows = {}
    for row in get_1y_update_targets():
        code = str(row.get("stock_code") or "").zfill(6)
        if code in REPAIR_CODES:
            rows[code] = row

    missing = [code for code in REPAIR_CODES if code not in rows]
    if missing:
        print("⚠️ Fundamental에 없는 대상:", missing)

    kospi_mdd_cache = get_kospi_mdd_cache()
    succeeded = []
    failed = []

    for code in REPAIR_CODES:
        row = rows.get(code)
        if not row:
            failed.append(code)
            continue

        name = row.get("stock_name") or code
        try:
            ok = collector.sync_1y_only(
                code,
                name,
                sector=row.get("sector"),
                wics_sector=row.get("wics_sector"),
                holding_company=row.get("holding_company") or False,
                existing_period_scores=row.get("period_scores") or {},
                kospi_mdd_cache=kospi_mdd_cache,
                use_ofs_for_manufacturing=False,
                force_refresh=True,
            )
        except Exception as exc:
            print(f"❌ [{name}] 복구 실행 예외: {exc}")
            ok = False

        if ok:
            succeeded.append(code)
        else:
            failed.append(code)

    print("\n=== 선택 종목 개별 복구 완료 ===")
    print(f"성공: {len(succeeded)}개 / 실패: {len(failed)}개")
    print("성공 종목코드:", succeeded)
    print("실패 종목코드:", failed)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
