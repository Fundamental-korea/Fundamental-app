# dart_report_field_check.py
# ==========================================================================
# 배당/자기주식 API의 실제 필드 구조를 확인하기 위한 진단 스크립트.
# 재무제표(finstate) API와는 완전히 다른 엔드포인트라, 재무제표 계정명 확인했던 것과
# 똑같은 이유로 - 실제 컬럼명/값 형태를 먼저 봐야 collector.py 파싱 로직을 정확히 짤 수 있음.
#
# DART 호출 몇 건 안 됨 (샘플 3종목 x 2종류 = 6건) - 전체 재수집 전에 먼저 돌려서
# 결과를 그대로 공유해주면, 그 기준으로 정확한 파싱 로직을 만들 수 있음.
# ==========================================================================

import collector as c

SAMPLE_CODES = [
    ("005930", "삼성전자"),  # 대형주, 배당 활발
    ("035420", "NAVER"),     # 배당 적은 성장주
    ("105560", "KB금융"),    # 금융지주, 배당수익률 높은 편
]


def try_report(stock_code, keyword_candidates, year):
    for kw in keyword_candidates:
        try:
            df = c.dart.report(stock_code, kw, year)
            if df is not None and not df.empty:
                print(f"   ✅ 키워드 '{kw}' 성공")
                return kw, df
        except Exception as e:
            print(f"   ❌ 키워드 '{kw}' 실패: {e}")
    return None, None


def main():
    year = c.get_latest_annual_year()
    print(f"기준 연도: {year}\n")

    for code, name in SAMPLE_CODES:
        print(f"\n{'=' * 60}\n[{name} ({code})]\n{'=' * 60}")

        print("\n--- 배당 정보 ---")
        kw, df = try_report(code, ["배당", "alotMatter", "dividend"], year)
        if df is not None:
            print(f"컬럼: {list(df.columns)}")
            print(df.to_string())
        else:
            print("배당 정보 조회 실패 (모든 키워드 시도함)")

        print("\n--- 자기주식 정보 ---")
        kw, df = try_report(code, ["자기주식", "tesstkAcqsDspsSttus", "treasury"], year)
        if df is not None:
            print(f"컬럼: {list(df.columns)}")
            print(df.to_string())
        else:
            print("자기주식 정보 조회 실패 (모든 키워드 시도함)")


if __name__ == "__main__":
    main()
