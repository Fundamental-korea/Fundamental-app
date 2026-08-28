# rescore_a_group.py
# ==========================================================================
# A그룹 재채점 스크립트 — DART 재조회 없이 Supabase에 이미 저장된
# period_scores[*][avg/worst][metric_scores][*][value] 만으로 전체 재계산.
#
# 이 스크립트가 고치는 것:
#   - 버그4 (resume 버전 불일치): leverage_exempt를 collector.py의 현재 로직으로
#     다시 계산해서 모든 종목에 동일 버전의 판정을 강제 적용
#   - 버그3 부분완화: 금융섹터(wics_sector=='금융')는 opm/roic/sga_ratio를
#     채점 대상에서 빼고 나머지 7개 지표로 100점 리스케일
#   - 개선(a) 가중치 재배분: downturn_defense 10→20점, growth(revenue+eps)
#     각 10→5점, 나머지 7개 지표는 기존 10점 유지 (합계 100점 그대로)
#   - 개선(b) 등급 컷오프: 하드코딩 대신 재계산된 점수의 실제 분포에서 동적 산출
#   - 개선(c) 데이터 신뢰도: 1y avg 기준 결측 지표 개수로 높음/보통/낮음 부여
#   - 개선(d) 섹터 백분위: wics_sector 내에서 total_score 상대 순위(0~100)
#
# 이 스크립트가 못 고치는 것 (B그룹, DART 재조회 필요):
#   - 버그1(1년 growth 폭주), 버그2(interest_coverage 기본값),
#     버그6(자본총계 계정매칭 실패) — 저장된 value 자체가 틀렸을 수 있어서
#     재계산으로는 복구 불가. collector.py 계정 매칭 로직 수정 후 재수집 필요.
#
# 실행 전 꼭 확인:
#   1. DRY_RUN = True 로 먼저 돌려서 등급 컷오프/샘플 결과를 콘솔로 확인
#   2. 문제 없으면 DRY_RUN = False 로 바꿔서 실제 Supabase에 반영
# ==========================================================================

import os
import copy
from collections import defaultdict

from supabase import create_client

# collector.py와 동일한 판정 로직을 그대로 재사용 (버전 불일치 방지 핵심)
from collector import (
    is_financial_sector,
    is_holding_company,
    LEVERAGE_EXEMPT_WICS_SECTORS,
)
from scoring import calculate_metric_score, METRIC_KEYS

# --------------------------------------------------------------------------
# 설정
# --------------------------------------------------------------------------
DRY_RUN = True  # True: DB에 쓰지 않고 통계/샘플만 출력. False: 실제 반영.

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

PERIODS = ["1y", "3y", "5y", "10y"]
MODES = ["avg", "worst"]

# 새 가중치 (합계 100) — downturn_defense 2배, growth 지표 절반, 나머지 유지
NEW_WEIGHTS = {
    "revenue_growth": 5,
    "eps_growth": 5,
    "opm": 10,
    "roic": 10,
    "debt_rate": 10,
    "quick_ratio": 10,
    "interest_coverage": 10,
    "ocf_ratio": 10,
    "sga_ratio": 10,
    "downturn_defense": 20,
}
assert sum(NEW_WEIGHTS.values()) == 100

# 금융섹터에서 제외할 지표 (구조적으로 안 맞는 지표)
FINANCIAL_EXCLUDED_METRICS = {"opm", "roic", "sga_ratio"}
FINANCIAL_REMAINING_WEIGHT = sum(
    w for k, w in NEW_WEIGHTS.items() if k not in FINANCIAL_EXCLUDED_METRICS
)  # = 70


def weighted_score(metric_key, raw_score_0_10):
    """기존 0~10점 스코어를 새 가중치 기준 점수로 환산 (10점 만점 -> NEW_WEIGHTS[metric]점 만점)"""
    return raw_score_0_10 * (NEW_WEIGHTS[metric_key] / 10.0)


def recompute_leverage_exempt(row):
    """collector.py와 동일 로직으로 leverage_exempt를 현재 기준으로 재판정 (버그4 수정 핵심)"""
    sector = row.get("sector")
    wics_sector = row.get("wics_sector")
    stock_name = row.get("stock_name")
    stock_code = row.get("stock_code")

    financial_sector = is_financial_sector(sector, wics_sector=wics_sector)
    holding_company = is_holding_company(stock_code, sector, stock_name)
    leverage_exempt = financial_sector or holding_company or (wics_sector in LEVERAGE_EXEMPT_WICS_SECTORS)
    return leverage_exempt, financial_sector, holding_company


def recompute_period_mode(metric_scores, leverage_exempt, is_financial):
    """
    metric_scores(기존 {metric: {value, score}}) 딕셔너리를 받아서
    - leverage_exempt 재적용
    - 금융섹터면 opm/roic/sga_ratio 제외 + 리스케일
    - 새 가중치 적용
    을 거친 새 total_score / metric_scores(새 score 포함) / sub_scores 를 반환.
    """
    new_metric_scores = {}
    total_weighted = 0.0
    max_weighted = 0.0

    excluded = FINANCIAL_EXCLUDED_METRICS if is_financial else set()

    for key in METRIC_KEYS:
        entry = metric_scores.get(key) or {}
        value = entry.get("value")

        # 원래 0~10점 스코어를 현재 leverage_exempt 기준으로 재계산 (버그4 수정)
        raw_score = calculate_metric_score(key, value, leverage_exempt=leverage_exempt)

        if key in excluded:
            # 금융섹터: 이 지표는 총점에서 제외하되, 참고용으로 원점수는 남겨둠
            new_metric_scores[key] = {"value": value, "score": raw_score, "excluded_from_total": True}
            continue

        w_score = weighted_score(key, raw_score)
        new_metric_scores[key] = {"value": value, "score": raw_score, "weighted_score": round(w_score, 2)}
        total_weighted += w_score
        max_weighted += NEW_WEIGHTS[key]

    if is_financial:
        # 제외된 30점 만큼을 나머지(70점)로 리스케일해서 100점 만점으로 정규화
        total_score = round(total_weighted * (100.0 / FINANCIAL_REMAINING_WEIGHT), 1)
    else:
        total_score = round(total_weighted, 1)

    # 방어/성장 서브스코어 (참고용 — 금융섹터는 defense에서 제외 지표 자연스레 빠짐)
    growth_keys = {"revenue_growth", "eps_growth"}
    defense_keys = set(METRIC_KEYS) - growth_keys - excluded
    growth_sub = sum(new_metric_scores[k]["weighted_score"] for k in growth_keys if k in new_metric_scores and "weighted_score" in new_metric_scores[k])
    defense_sub = sum(new_metric_scores[k]["weighted_score"] for k in defense_keys if k in new_metric_scores and "weighted_score" in new_metric_scores[k])

    missing_count = sum(1 for k in METRIC_KEYS if (metric_scores.get(k) or {}).get("value") is None)

    return {
        "metric_scores": new_metric_scores,
        "total_score": total_score,
        "sub_scores": {"growth": round(growth_sub, 1), "defense": round(defense_sub, 1)},
        "missing_metric_count": missing_count,
        "financial_adjusted": is_financial,
    }


def compute_dynamic_grade_cutoffs(scores):
    """실제 점수 리스트에서 백분위 기준 등급 컷오프 산출 (상위 5%=S, 25%=A, 50%=B, 75%=C, 나머지=D)"""
    if not scores:
        return {"S": 76, "A": 64, "B": 53, "C": 42}
    s = sorted(scores, reverse=True)
    n = len(s)

    def pct(p):
        idx = min(int(n * p), n - 1)
        return s[idx]

    return {
        "S": pct(0.05),
        "A": pct(0.25),
        "B": pct(0.50),
        "C": pct(0.75),
    }


def assign_grade(total_score, cutoffs):
    if total_score >= cutoffs["S"]:
        return "S"
    elif total_score >= cutoffs["A"]:
        return "A"
    elif total_score >= cutoffs["B"]:
        return "B"
    elif total_score >= cutoffs["C"]:
        return "C"
    return "D"


def fetch_all_rows():
    """PostgREST 1000행 기본 제한 페이지네이션 처리 (예전에 겪었던 문제 재발 방지)"""
    all_rows = []
    page_size = 1000
    start = 0
    while True:
        res = (
            supabase.table("Fundamental")
            .select("stock_code, stock_name, sector, wics_sector, holding_company, period_scores")
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


def main():
    print("📥 Supabase에서 전체 종목 조회 중...")
    rows = fetch_all_rows()
    print(f"   총 {len(rows)}개 row 조회됨")

    rows = [r for r in rows if r.get("period_scores")]
    print(f"   period_scores 있는 종목: {len(rows)}개")

    # ---- 1차 패스: 새 로직으로 전체 재계산 (아직 등급은 안 매김, 점수 분포부터 모음) ----
    recomputed = {}  # stock_code -> {period: {mode: result_dict}}
    score_pool = defaultdict(list)  # (period, mode) -> [total_score, ...]

    for row in rows:
        code = row["stock_code"]
        leverage_exempt, is_financial, is_holding = recompute_leverage_exempt(row)

        recomputed[code] = {}
        for period in PERIODS:
            pdata = (row["period_scores"] or {}).get(period)
            if not pdata:
                continue
            recomputed[code][period] = {"years_used": pdata.get("years_used")}
            for mode in MODES:
                mdata = pdata.get(mode) or {}
                metric_scores = mdata.get("metric_scores") or {}
                result = recompute_period_mode(metric_scores, leverage_exempt, is_financial)
                recomputed[code][period][mode] = result
                score_pool[(period, mode)].append(result["total_score"])

    # ---- 등급 컷오프를 (period, mode)별로 실제 분포에서 산출 ----
    cutoffs_by_key = {}
    print("\n📊 재계산된 점수 분포 기준 등급 컷오프:")
    for key, scores in score_pool.items():
        cutoffs = compute_dynamic_grade_cutoffs(scores)
        cutoffs_by_key[key] = cutoffs
        print(f"   {key}: S≥{cutoffs['S']} A≥{cutoffs['A']} B≥{cutoffs['B']} C≥{cutoffs['C']}  (n={len(scores)})")

    # ---- 섹터별 total_score 리스트 (백분위 계산용, 1y avg 기준 대표로 사용) ----
    sector_scores = defaultdict(list)
    for row in rows:
        code = row["stock_code"]
        sector = row.get("wics_sector")
        if code in recomputed and "1y" in recomputed[code] and "avg" in recomputed[code]["1y"]:
            sector_scores[sector].append(recomputed[code]["1y"]["avg"]["total_score"])
    for sector in sector_scores:
        sector_scores[sector].sort()

    def sector_percentile(sector, score):
        arr = sector_scores.get(sector)
        if not arr or len(arr) < 5:  # 표본 5개 미만이면 신뢰도 낮음 -> None
            return None
        below = sum(1 for s in arr if s <= score)
        return round(100 * below / len(arr), 1)

    # ---- 2차 패스: 등급 부여 + 섹터 백분위 + 신뢰도 등급 부여, 최종 payload 조립 ----
    updates = []
    for row in rows:
        code = row["stock_code"]
        if code not in recomputed:
            continue

        new_period_scores = copy.deepcopy(row["period_scores"])  # years_used 등 기존 필드 보존
        missing_1y = None

        for period in PERIODS:
            if period not in recomputed[code]:
                continue
            for mode in MODES:
                if mode not in recomputed[code][period]:
                    continue
                result = recomputed[code][period][mode]
                cutoffs = cutoffs_by_key[(period, mode)]
                grade = assign_grade(result["total_score"], cutoffs)

                target = new_period_scores[period][mode]
                target["metric_scores"] = result["metric_scores"]
                target["total_score"] = result["total_score"]
                target["grade"] = grade
                target["sub_scores"] = result["sub_scores"]
                target["financial_adjusted"] = result["financial_adjusted"]

                if period == "1y" and mode == "avg":
                    missing_1y = result["missing_metric_count"]
                    target["sector_percentile"] = sector_percentile(row.get("wics_sector"), result["total_score"])

        # 데이터 신뢰도 (1y avg 결측 개수 기준)
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
            "period_scores": new_period_scores,
            "data_reliability": reliability,
            "missing_metric_count": missing_1y,
        })

    print(f"\n✅ 재계산 완료: {len(updates)}개 종목")

    # ---- 샘플 출력 (삼성전자가 있으면 확인용으로) ----
    sample = next((u for u in updates if u["stock_code"] == "005930"), updates[0] if updates else None)
    if sample:
        print("\n🔍 샘플 결과 (참고용):")
        print(f"   stock_code: {sample['stock_code']}")
        print(f"   data_reliability: {sample['data_reliability']}")
        one_y_avg = sample["period_scores"].get("1y", {}).get("avg", {})
        print(f"   1y avg total_score: {one_y_avg.get('total_score')} / grade: {one_y_avg.get('grade')}")
        print(f"   sub_scores: {one_y_avg.get('sub_scores')}")
        print(f"   sector_percentile: {one_y_avg.get('sector_percentile')}")

    if DRY_RUN:
        print("\n🛑 DRY_RUN=True 라서 실제 DB에는 반영하지 않았습니다.")
        print("   위 등급 컷오프/샘플 결과를 확인하고 문제 없으면 DRY_RUN=False로 바꿔 다시 실행하세요.")
        return

    # ---- 실제 반영 ----
    print("\n💾 Supabase에 반영 중...")
    for i, u in enumerate(updates, 1):
        try:
            supabase.table("Fundamental").update({
                "period_scores": u["period_scores"],
                "data_reliability": u["data_reliability"],
                "missing_metric_count": u["missing_metric_count"],
            }).eq("stock_code", u["stock_code"]).execute()
        except Exception as e:
            print(f"   ⚠️ [{u['stock_code']}] 업데이트 실패: {e}")
        if i % 200 == 0:
            print(f"   {i}/{len(updates)} 완료...")

    print(f"\n🎉 전체 {len(updates)}개 종목 재채점 반영 완료!")


if __name__ == "__main__":
    main()
