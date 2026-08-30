"""
update_1y.py
분기 자동갱신 파이프라인 진입점 - GitHub Actions(daily_update.yml)가 이 스크립트를 실행.
실적발표(분기/반기/사업보고서) 나올 때마다 전체 종목의 1y 점수만 가볍게 갱신.
3y/5y/10y(연간 사업보고서 기반)는 건드리지 않음 - 그건 연 1회 전체 재수집(collector.py의
sync_all_kor_stocks_b_group)이 담당.

필요 환경변수 (GitHub Secrets로 설정):
  DART_API_KEY, SUPABASE_URL, SUPABASE_KEY
"""

import collector

if __name__ == "__main__":
    succeeded, failed = collector.sync_all_kor_stocks_1y_only()
    print(f"\n최종 결과: 성공 {len(succeeded)} / 실패 {len(failed)}")
