"""
매일 자동 실행되는 분기 갱신 파이프라인의 실제 진입점.
.github/workflows/daily_update.yml이 매일 이 스크립트를 실행함.

collector.py의 sync_all_kor_stocks_1y_only()를 호출하되, 2026-09부터는
KRX market snapshot integration layer를 먼저 설치한다.

시장 데이터:
- KRX: 현재 종가 / 상장주식수 / 시가총액
- DART collector: 최신 확정 재무자료 / EPS / BPS / 회계 기준 주식수

annual baseline(3y/5y/10y)은 이 경로에서 안 건드리고, 1y 점수 + 최신 재무 스냅샷을 갱신한다.
"""

import sys

from collector import sync_all_kor_stocks_1y_only
from kor_market_pipeline import install_market_snapshot_integration


if __name__ == "__main__":
    print("🔄 일일 1y 자동 갱신 시작")

    integration = install_market_snapshot_integration()
    print(
        f"🏦 KRX market snapshot integration 준비 완료 "
        f"({integration['market_snapshot_count']:,}개 종목)"
    )

    succeeded, failed = sync_all_kor_stocks_1y_only(max_workers=4)

    print(f"\n=== 완료 ===")
    print(f"성공: {len(succeeded)}개")
    print(f"실패: {len(failed)}개")
    if failed:
        print("실패 종목코드:", failed)

    # 전체가 다 실패한 경우(예: DART_API_KEY 문제, Supabase 연결 실패 등)만 CI 실패로 표시.
    # 개별 종목 실패는 정상적인 부분 실패라 워크플로우 자체를 빨갛게 만들지 않음.
    if succeeded == [] and failed:
        print("\n❌ 전체 실패 - 환경변수/연결 상태를 확인할 것")
        sys.exit(1)
