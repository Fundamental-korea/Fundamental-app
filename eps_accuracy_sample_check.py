# eps_accuracy_sample_check.py
# ==========================================================================
# 근사치 EPS(순이익÷발행주식수, backfill_eps_bps.py로 채운 값) vs 실제 공시 EPS
# ("기본주당순이익" 계정, DART 재무제표에서 직접 추출)를 표본 20개 종목으로 비교.
#
# 전체 재수집(2~3일) 여부를 결정하기 전에, 오차가 실제로 얼마나 나는지 데이터로
# 먼저 확인하기 위한 스크립트. DART 호출은 종목당 2회 정도라 전체 40~50건 수준 -
# 몇 초~몇 분이면 끝남.
#
# collector.py가 같은 디렉토리에 있어야 함 (dart 클라이언트, _find_account_value,
# _fetch_full_statement_df, get_latest_annual_year를 그대로 재사용).
# ==========================================================================

import os

from supabase import create_client

import collector as c

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# 대형주~중견주 섞어서 20개 (자사주 비중이 다양할 것으로 예상되는 종목들 위주)
SAMPLE_CODES = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "005380",  # 현대차
    "035420",  # NAVER
    "035720",  # 카카오
    "051910",  # LG화학
    "006400",  # 삼성SDI
    "207940",  # 삼성바이오로직스
    "105560",  # KB금융
    "055550",  # 신한지주
    "012330",  # 현대모비스
    "028260",  # 삼성물산
    "066570",  # LG전자
    "096770",  # SK이노베이션
    "034730",  # SK
    "003550",  # LG
    "017670",  # SK텔레콤
    "030200",  # KT
    "015760",  # 한국전력
    "010130",  # 고려아연
]


def main():
    base_year = c.get_latest_annual_year()
    print(f"기준 연도(사업보고서): {base_year}\n")

    results = []
    for code in SAMPLE_CODES:
        res = supabase.table("Fundamental").select("stock_name, eps").eq("stock_code", code).execute()
        if not res.data:
            print(f"[{code}] DB에 데이터 없음, 스킵")
            continue
        name = res.data[0]["stock_name"]
        approx_eps = res.data[0].get("eps")

        try:
            fin_data = c.dart.finstate(code, base_year, reprt_code="11011")
        except Exception as e:
            print(f"[{name}({code})] DART 조회 실패: {e}")
            continue
        if fin_data is None or fin_data.empty:
            print(f"[{name}({code})] 재무제표 없음")
            continue

        ofs = fin_data[fin_data["fs_div"] == "OFS"]
        cfs = fin_data[fin_data["fs_div"] == "CFS"]
        df = ofs if not ofs.empty else cfs
        if df.empty:
            print(f"[{name}({code})] OFS/CFS 둘 다 없음")
            continue

        df_full = c._fetch_full_statement_df(code, base_year, reprt_code="11011")
        detail_df = df_full if df_full is not None else df
        reported_eps = c._find_account_value(
            detail_df, ["기본주당순이익", "기본주당이익", "주당순이익", "보통주기본주당순이익"],
            field="thstrm_amount",
        )

        if reported_eps is None:
            print(f"[{name}({code})] 공시 EPS 계정을 못 찾음")
            continue

        if approx_eps and reported_eps:
            error_pct = round((approx_eps - reported_eps) / abs(reported_eps) * 100, 2)
        else:
            error_pct = None

        results.append({
            "종목": f"{name}({code})", "근사치": approx_eps,
            "공시값": reported_eps, "오차(%)": error_pct,
        })
        print(f"[{name}({code})] 근사치={approx_eps} / 공시값={reported_eps} / 오차={error_pct}%")

    print("\n=== 요약 ===")
    errors = [abs(r["오차(%)"]) for r in results if r["오차(%)"] is not None]
    if errors:
        print(f"비교 가능 종목: {len(errors)}개")
        print(f"평균 절대오차: {sum(errors) / len(errors):.2f}%")
        print(f"최대 오차: {max(errors):.2f}%")
        print(f"최소 오차: {min(errors):.2f}%")
        print(f"10% 넘게 틀린 종목 수: {sum(1 for e in errors if e > 10)}개")
    else:
        print("비교 가능한 결과가 없습니다.")


if __name__ == "__main__":
    main()
