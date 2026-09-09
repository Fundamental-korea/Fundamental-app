# rescore_final_grades.py
# ==========================================================================
# 최종 등급 재산정 스크립트 - 전체 재수집(B그룹) 완료 후 1회 실행.
# DART 재조회 전혀 없음. Supabase에 이미 저장된 total_score(scoring.py가 수집
# 시점에 이미 정확히 계산해둔 값)를 절대 건드리지 않고, 그 분포를 기준으로
# grade(S+~D 13단계) / sector_percentile / data_reliability만 덮어씀.
#
# ⚠️ 2026-09: .update()가 Supabase RLS(UPDATE 정책 없음/UPSERT만 허용)에 막혀
#    에러 없이 0건 처리되고 "완료"로 잘못 보고되던 버그를 발견 (rescore_metric_
#    percentiles.py와 동일 버그). 실측 결과 2,591개 중 27개만 13단계 등급이
#    반영되어 있었고 나머지는 전부 수집 시점 5단계 임시 등급 그대로였음.
#    .upsert()로 변경 + 실제 반영 건수 검증 로직 추가.
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
    """PostgREST 1000행 기본 제한 페이지네이션 처리.
    period_scores가 큰 jsonb 컬럼이라 페이지 크기를 작게(200) 잡고,
    statement timeout 발생 시 페이지 크기를 더 줄여 재시도한다."""
    all_rows = []
    page_size = 200
    start = 0
    while True:
        attempt_size = page_size
        while True:
            try:
                res = (
                    supabase.table("Fundamental")
                    .select("stock_code, wics_sector, period_scores")
                    .not_.is_("period_scores", "null")
                    .range(start, start + attempt_size - 1)
                    .execute()
                )
                break
            except Exception as e:
                if attempt_size <= 25:
                    raise
                attempt_size = max(25, attempt_size // 2)
                print(f"   ⚠️ 타임아웃 발생, 페이지 크기를 {attempt_size}로 줄여 재시도합니다... ({e})")
        rows = res.data
        if not rows:
            break
        all_rows.extend(rows)
        print(f"   ...{len(all_rows)}개 조회됨")
        if len(rows) < attempt_size:
            break
        start += attempt_size
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
        print(f"\n  {key} (n={len(scores)}):")
        for grade, _ in GRADE_TIERS:
            print(f"    {grade}: ≥{cutoffs[grade]}")

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
    actually_updated = 0
    zero_row_codes = []
    for i, u in enumerate(updates, 1):
        for attempt in range(3):
            try:
                # ⚠️ .update()에서 .upsert()로 변경 (RLS가 UPDATE는 막고 UPSERT만
                # 허용하는 경우 .update()가 에러 없이 0건 처리되던 버그 수정)
                res = (
                    supabase.table("Fundamental")
                    .upsert(
                        {
                            "stock_code": u["stock_code"],
                            "period_scores": u["period_scores"],
                            "data_reliability": u["data_reliability"],
                        },
                        on_conflict="stock_code",
                    )
                    .execute()
                )
                if res.data:
                    actually_updated += 1
                else:
                    zero_row_codes.append(u["stock_code"])
                break
            except Exception as e:
                if attempt == 2:
                    print(f"   ⚠️ [{u['stock_code']}] 업데이트 실패(3회 재시도 후 포기): {e}")
                else:
                    print(f"   ⚠️ [{u['stock_code']}] 업데이트 재시도 중... ({e})")
        if i % 200 == 0:
            print(f"   {i}/{len(updates)} 완료... (실제 반영 확인 {actually_updated}건)")

    print(f"\n🎉 전체 {len(updates)}개 종목 중 실제 반영 확인된 건: {actually_updated}개")
    if zero_row_codes:
        print(f"⚠️ 응답이 비어있던(실제 반영 안 됐을 가능성) 종목 {len(zero_row_codes)}개, 예시: {zero_row_codes[:10]}")


if __name__ == "__main__":
    main()
