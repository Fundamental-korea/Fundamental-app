# rescore_metric_percentiles.py
# ==========================================================================
# 지표별(10개 + 금융 전용 roa) 업종(WICS) 내 상대적 우위 백분위 계산 스크립트.
# DART 재조회 전혀 없음. Supabase에 이미 저장된 metric_scores[key]["value"](수집 시점에
# scoring.py가 이미 계산해둔 값)를 절대 건드리지 않고, 그 분포를 업종 단위로 나눠서
# metric_scores[key]["sector_percentile"]만 추가.
#
# ⚠️ 정확한 채점 구간표(scoring.py의 METRIC_SCORE_BANDS) 대신 "업종 내 상위 X%"만
#    보여주는 용도. 절대 임계값을 노출하지 않기 위한 설계 - app.py에서 이 값을 쓴다.
#
# 방향성 처리: revenue_growth처럼 '높을수록 좋은' 지표든 debt_rate처럼 '낮을수록 좋은'
# 지표든, sector_percentile은 항상 "높을수록 업종 내에서 더 우수하다"는 동일한 의미가
# 되도록 계산한다 (scoring.METRIC_DIRECTION 참고해서 방향 보정).
#
# 기존 sector_percentile(총점 기준, "1y"-"avg"에만 존재)과 동일한 관례를 따라
# 이 지표별 percentile도 "1y"-"avg" 조합에만 계산해서 대표값 1개로 저장한다.
#
# 실행 전 꼭 확인:
#   1. DRY_RUN = True 로 먼저 돌려서 분포/샘플을 콘솔로 확인
#   2. 문제 없으면 DRY_RUN = False 로 바꿔서 실제 Supabase에 반영
# ==========================================================================

import os
from collections import defaultdict

from supabase import create_client

from scoring import METRIC_KEYS, METRIC_DIRECTION

# --------------------------------------------------------------------------
# 설정
# --------------------------------------------------------------------------
DRY_RUN = True  # True: DB에 쓰지 않고 통계/샘플만 출력. False: 실제 반영.

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

MIN_SECTOR_SAMPLE = 5  # 업종 표본이 이보다 적으면 percentile 계산 건너뜀

# 금융섹터는 roic 대신 roa를 채점하므로, roic이 있는 자리에 roa도 같이 계산 대상에 포함
ALL_PERCENTILE_METRICS = METRIC_KEYS + ["roa"]


def fetch_all_rows():
    """PostgREST 1000행 기본 제한 페이지네이션 처리."""
    all_rows = []
    page_size = 300
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
        print(f"   ...{len(all_rows)}개 조회됨")
        if len(rows) < page_size:
            break
        start += page_size
    return all_rows


def compute_percentile(value, sorted_values, direction):
    """sorted_values(오름차순) 안에서 value의 '업종 내 우위' 백분위(%) 계산.
    direction과 무관하게 percentile이 높을수록 = 업종 내에서 더 우수한 것으로 통일."""
    n = len(sorted_values)
    if n < MIN_SECTOR_SAMPLE:
        return None
    if direction == "lower":
        # 낮을수록 좋은 지표 -> 내 값보다 크거나 같은(=더 나쁜) 종목 비율이 곧 내 우위 백분위
        better_or_equal = sum(1 for v in sorted_values if v >= value)
    else:
        # 높을수록 좋은 지표 -> 내 값보다 작거나 같은(=더 나쁜) 종목 비율이 곧 내 우위 백분위
        better_or_equal = sum(1 for v in sorted_values if v <= value)
    return round(100 * better_or_equal / n, 1)


def main():
    print("📥 Supabase에서 전체 종목 조회 중...")
    rows = fetch_all_rows()
    print(f"   period_scores 있는 종목: {len(rows)}개")

    # ---- 1차 패스: 업종 x 지표별 value 분포 수집 (1y-avg 기준) ----
    value_pool = defaultdict(list)  # (sector, metric_key) -> [values]
    for row in rows:
        sector = row.get("wics_sector")
        if not sector:
            continue
        pdata = (row.get("period_scores") or {}).get("1y")
        if not pdata:
            continue
        metric_scores = (pdata.get("avg") or {}).get("metric_scores") or {}
        for metric_key in ALL_PERCENTILE_METRICS:
            entry = metric_scores.get(metric_key)
            if entry and entry.get("value") is not None:
                value_pool[(sector, metric_key)].append(entry["value"])

    for key in value_pool:
        value_pool[key].sort()

    print(f"\n📊 업종×지표 조합 {len(value_pool)}개 수집 (표본 {MIN_SECTOR_SAMPLE}개 미만은 건너뜀)")

    # ---- 2차 패스: 종목별 지표별 percentile 계산 ----
    updates = []
    sample_check = None

    for row in rows:
        code = row["stock_code"]
        sector = row.get("wics_sector")
        period_scores = row.get("period_scores") or {}
        pdata = period_scores.get("1y")
        if not pdata or not sector:
            continue
        metric_scores = (pdata.get("avg") or {}).get("metric_scores") or {}

        changed = False
        for metric_key in ALL_PERCENTILE_METRICS:
            entry = metric_scores.get(metric_key)
            if not entry or entry.get("value") is None:
                continue
            direction = METRIC_DIRECTION.get(metric_key, "higher")
            pool = value_pool.get((sector, metric_key), [])
            pct = compute_percentile(entry["value"], pool, direction)
            if pct is not None:
                entry["sector_percentile"] = pct
                changed = True

        if changed:
            updates.append({"stock_code": code, "period_scores": period_scores})
            if code == "005930":
                sample_check = metric_scores

    print(f"\n✅ 계산 완료: {len(updates)}개 종목 갱신 대상")

    if sample_check:
        print("\n🔍 샘플 결과 (005930, 참고용):")
        for metric_key in ALL_PERCENTILE_METRICS:
            entry = sample_check.get(metric_key)
            if entry and "sector_percentile" in entry:
                print(f"   {metric_key}: 값={entry['value']}, 업종 내 우위 백분위={entry['sector_percentile']}%")

    if DRY_RUN:
        print("\n🛑 DRY_RUN=True 라서 실제 DB에는 반영하지 않았습니다.")
        print("   위 샘플을 확인하고 문제 없으면 DRY_RUN=False로 바꿔 다시 실행하세요.")
        return

    print("\n💾 Supabase에 반영 중...")
    for i, u in enumerate(updates, 1):
        for attempt in range(3):
            try:
                supabase.table("Fundamental").update({
                    "period_scores": u["period_scores"],
                }).eq("stock_code", u["stock_code"]).execute()
                break
            except Exception as e:
                if attempt == 2:
                    print(f"   ⚠️ [{u['stock_code']}] 업데이트 실패(3회 재시도 후 포기): {e}")
                else:
                    print(f"   ⚠️ [{u['stock_code']}] 업데이트 재시도 중... ({e})")
        if i % 200 == 0:
            print(f"   {i}/{len(updates)} 완료...")

    print(f"\n🎉 전체 {len(updates)}개 종목 지표별 백분위 반영 완료!")


if __name__ == "__main__":
    main()
