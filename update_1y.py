"""
분기 자동갱신 파이프라인의 GitHub Actions 진입점.

.github/workflows/daily_update.yml 이 매일 이 스크립트를 실행한다.
collector.py의 sync_all_kor_stocks_1y_only()를 호출해서 annual baseline이
이미 있는 전체 종목의 1y(1년) 지표만 가볍게 갱신한다 (3y/5y/10y는 건드리지 않음).

멱등성(idempotent) 보장: 실제로 새로운 분기/반기 보고서가 아직 안 나온 날에 실행돼도,
DART에서 같은 보고서를 다시 확인만 하고 끝나므로 데이터가 틀어지지 않는다.
그래서 정확한 공시일을 맞추는 대신 매일 실행하는 지금 방식이 안전하다.
"""

from collector import sync_all_kor_stocks_1y_only

if __name__ == "__main__":
    succeeded, failed = sync_all_kor_stocks_1y_only()
    print(f"\n최종 결과: 성공 {len(succeeded)}개 / 실패 {len(failed)}개")

    # 실패가 있어도 워크플로우 자체를 실패 처리하진 않음 (일부 종목 실패는 흔한 일이고,
    # 다음날 재시도되므로). 다만 실패율이 지나치게 높으면(예: DART 자체 장애) 눈에 띄게 로그를 남김.
    if failed and succeeded and len(failed) / (len(succeeded) + len(failed)) > 0.5:
        print("⚠️ 실패율이 50%를 넘습니다 - DART API 장애나 인증 문제일 수 있으니 확인이 필요합니다.")
