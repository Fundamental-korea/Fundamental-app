# rescore_final_grades.py
# ==========================================================================
# 최종 등급 재산정 스크립트 - 전체 재수집(B그룹) 완료 후 1회 실행.
# DART 재조회 전혀 없음. Supabase에 이미 저장된 total_score(scoring.py가 수집
# 시점에 이미 정확히 계산해둔 값)를 절대 건드리지 않고, 그 분포를 기준으로
# grade(S+~D 13단계) / sector_percentile / data_reliability만 덮어씀.
#
# ⚠️ rescore_a_group.py와 다른 점: 그 스크립트는 total_score까지 재계산했었지만,
#    지금은 scoring.py가 이미 최종 로직(가중치재배분/금융ROA보정)으로 total_score를
#    정확히 계산해서 저장하므로, 이 스크립트는 total_score/metric_scores를 절대
#    건드리지 않는다. grade/sector_percentile/data_reliability만 갱신.
#
# 실행 전 꼭 확인:
#   1. DRY_RUN = True 로 먼저 돌려서 등급 컷오프/분포를 콘솔로 확인
#   2. 문제 없으면 DRY_RUN = False 로 바꿔서 실제 Supabase에 반영
# ==========================================================================

import os
from collections import defaultdict

from supabase import create_client

# --------------------------------------------------------------------------
# 설정
# --------------------------------------------------------------------------
DRY_RUN = True  # True: DB에 쓰지 않고 통계/샘플만 출력. False: 실제 반영.

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

PERIODS = ["1y", "3y", "5y", "10y"]
MODES = ["avg", "worst"]

# 13단계 등급 - 상위 % 구간 (다른 값으로 바꾸고 싶으면 여기만 수정)
GRADE_TIERS = [
    ("S+", 0.02),
    ("S", 0.05),
    ("S-", 0.10),
    ("A+", 0.17),
    ("A", 0.25),
    ("A-", 0.35),
    ("B+", 0.45),
    ("B", 0.55),
    ("B-", 0.65),
    ("C+", 0.75),
    ("C", 0.85),
    ("C-", 0.95),
    ("D", 1.01),  # 나머지 전부
]


def fetch_all_rows():
    """PostgREST 1000행 기본 제한 페이지네이션 처리"""
    all_rows = []
    page_size = 1000
    start = 0
    while True:
        res = (
            supabase.table("Fundamental")
            .select("stock_code, wics_sector, period_scores")
            .not_.is_("period_scores", "null")
            .range(start, start + page_size - 1)
            .execute()
        )
        rows = res.data
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        start += page_size
    return all_rows


def compute_grade_cutoffs(scores):
    """실제 점수 리스트에서 13단계 등급별 컷오프(상위 % 경계값) 산출"""
    if not scores:
        return {}
    s = sorted(scores, reverse=True)  # 내림차순 - 1등이 맨 앞
    n = len(s)
    cutoffs = {}
    for grade, top_pct in GRADE_TIERS:
        idx = min(int(n * top_pct), n - 1)
        cutoffs[grade] = s[idx]
    return cutoffs


def assign_grade(total_score, cutoffs):
    for grade, _ in GRADE_TIERS:
        if total_score >= cutoffs[grade]:
            return grade
    return "D"


def main():
    print("📥 Supabase에서 전체 종목 조회 중...")
    rows = fetch_all_rows()
    print(f"   period_scores 있는 종목: {len(rows)}개")

    # ---- 1차 패스: (period, mode)별 점수 분포 수집 ----
    score_pool = defaultdict(list)
    for row in rows:
        for period in PERIODS:
            pdata = (row["period_scores"] or {}).get(period)
            if not pdata:
                continue
            for mode in MODES:
                mdata = pdata.get(mode) or {}
                ts = mdata.get("total_score")
                if ts is not None:
                    score_pool[(period, mode)].append(ts)

    # ---- (period, mode)별 13단계 컷오프 산출 ----
    cutoffs_by_key = {}
    print("\n📊 최종 등급 컷오프 (상위 % 경계값):")
    for key, scores in score_pool.items():
        cutoffs = compute_grade_cutoffs(scores)
        cutoffs_by_key[key] = cutoffs
        print(f"\n   {key} (n={len(scores)}):")
        for grade, _ in GRADE_TIERS:
            print(f"      {grade}: ≥{cutoffs[grade]}")

    # ---- 섹터별 total_score 리스트 (1y avg 기준 대표 백분위) ----
    sector_scores = defaultdict(list)
    for row in rows:
        pdata = (row["period_scores"] or {}).get("1y")
        if pdata and pdata.get("avg", {}).get("total_score") is not None:
            sector_scores[row.get("wics_sector")].append(pdata["avg"]["total_score"])
    for sector in sector_scores:
        sector_scores[sector].sort()

    def sector_percentile(sector, score):
        arr = sector_scores.get(sector)
        if not arr or len(arr) < 5:
            return None
        below = sum(1 for s in arr if s <= score)
        return round(100 * below / len(arr), 1)

    # ---- 2차 패스: grade/sector_percentile/data_reliability 갱신 ----
    updates = []
    grade_dist_check = defaultdict(int)  # 검증용 - 1y avg 등급 분포 카운트

    for row in rows:
        code = row["stock_code"]
        period_scores = row["period_scores"]
        missing_1y = None

        for period in PERIODS:
            pdata = period_scores.get(period)
            if not pdata:
                continue
            for mode in MODES:
                mdata = pdata.get(mode)
                if not mdata:
                    continue
                ts = mdata.get("total_score")
                if ts is None:
                    continue
                cutoffs = cutoffs_by_key[(period, mode)]
                new_grade = assign_grade(ts, cutoffs)
                mdata["grade"] = new_grade  # total_score/metric_scores는 절대 안 건드림

                if period == "1y" and mode == "avg":
                    grade_dist_check[new_grade] += 1
                    missing_1y = mdata.get("missing_metric_count")
                    mdata["sector_percentile"] = sector_percentile(row.get("wics_sector"), ts)

        if missing_1y is None:
            reliability = None
        elif missing_1y == 0:
            reliability = "높음"
        elif missing_1y <= 2:
            reliability = "보통"
        else:
            reliability = "낮음"

        updates.append({
            "stock_code": code,
            "period_scores": period_scores,
            "data_reliability": reliability,
        })

    print(f"\n✅ 재계산 완료: {len(updates)}개 종목")
    print("\n📊 1y avg 등급 분포 (검증용):")
    for grade, _ in GRADE_TIERS:
        cnt = grade_dist_check.get(grade, 0)
        pct = round(100 * cnt / len(updates), 1) if updates else 0
        print(f"   {grade}: {cnt}개 ({pct}%)")

    sample = next((u for u in updates if u["stock_code"] == "005930"), updates[0] if updates else None)
    if sample:
        print("\n🔍 샘플 결과 (참고용):")
        print(f"   stock_code: {sample['stock_code']}")
        print(f"   data_reliability: {sample['data_reliability']}")
        one_y_avg = sample["period_scores"].get("1y", {}).get("avg", {})
        print(f"   1y avg total_score: {one_y_avg.get('total_score')} / grade: {one_y_avg.get('grade')}")
        print(f"   sector_percentile: {one_y_avg.get('sector_percentile')}")

    if DRY_RUN:
        print("\n🛑 DRY_RUN=True 라서 실제 DB에는 반영하지 않았습니다.")
        print("   위 등급 분포/샘플을 확인하고 문제 없으면 DRY_RUN=False로 바꿔 다시 실행하세요.")
        return

    print("\n💾 Supabase에 반영 중...")
    for i, u in enumerate(updates, 1):
        try:
            supabase.table("Fundamental").update({
                "period_scores": u["period_scores"],
                "data_reliability": u["data_reliability"],
            }).eq("stock_code", u["stock_code"]).execute()
        except Exception as e:
            print(f"   ⚠️ [{u['stock_code']}] 업데이트 실패: {e}")
        if i % 200 == 0:
            print(f"   {i}/{len(updates)} 완료...")

    print(f"\n🎉 전체 {len(updates)}개 종목 최종 등급 반영 완료!")


if __name__ == "__main__":
    main()
