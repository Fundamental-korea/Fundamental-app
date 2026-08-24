# scoring.py
# v2: ROE→ROIC(레버리지 배제), 유동비율→당좌비율(재고 제외), 유보율 제거 후
#     실제 주가 기반 '하락장 방어력' 지표를 새로 추가한 10개 지표 채점 로직.

METRIC_KEYS = [
    "revenue_growth",
    "eps_growth",
    "opm",
    "roic",
    "debt_rate",
    "quick_ratio",
    "interest_coverage",
    "ocf_ratio",
    "sga_ratio",
    "downturn_defense",
]

# 다년간 데이터를 집계할 때 "최악의 해"가 어느 쪽인지 판별하기 위한 방향성
# higher = 클수록 좋음 (최악값 = 최소값) / lower = 작을수록 좋음 (최악값 = 최대값)
METRIC_DIRECTION = {
    "revenue_growth": "higher",
    "eps_growth": "higher",
    "opm": "higher",
    "roic": "higher",
    "debt_rate": "lower",
    "quick_ratio": "higher",
    "interest_coverage": "higher",
    "ocf_ratio": "higher",
    "sga_ratio": "lower",
    "downturn_defense": "higher",
}


def worst_value(metric_name, values):
    """연도별 수치 리스트에서 해당 지표 기준 '가장 나빴던 값'을 반환 (None은 제외)"""
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    if METRIC_DIRECTION.get(metric_name) == "lower":
        return max(clean)  # 낮을수록 좋은 지표 -> 가장 큰 값이 최악
    return min(clean)  # 높을수록 좋은 지표 -> 가장 작은 값이 최악


def calculate_metric_score(metric_name, value, is_financial_sector=False):
    """각 재무 지표 수치(value)를 입력받아 0~10점 사이의 점수를 반환"""
    if value is None:
        return 0  # 데이터 부재 시 기본값

    if metric_name == "revenue_growth":
        if value >= 25: return 10
        elif value >= 20: return 9
        elif value >= 16: return 8
        elif value >= 12: return 7
        elif value >= 8:  return 6
        elif value >= 5:  return 5
        elif value >= 2:  return 4
        elif value >= 0:  return 3
        elif value >= -5: return 2
        elif value >= -10: return 1
        else: return 0

    elif metric_name == "eps_growth":
        if value >= 20: return 10
        elif value >= 16: return 9
        elif value >= 12: return 8
        elif value >= 9:  return 7
        elif value >= 6:  return 6
        elif value >= 3:  return 5
        elif value >= 1:  return 4
        elif value >= 0:  return 3
        elif value >= -5: return 2
        elif value >= -15: return 1
        else: return 0

    elif metric_name == "opm":
        if value >= 25: return 10
        elif value >= 20: return 9
        elif value >= 16: return 8
        elif value >= 13: return 7
        elif value >= 10: return 6
        elif value >= 7:  return 5
        elif value >= 5:  return 4
        elif value >= 3:  return 3
        elif value >= 1:  return 2
        elif value >= 0:  return 1
        else: return 0

    elif metric_name == "roic":
        # ROE보다 낮게 나오는 게 정상(레버리지 효과 없음)이라 구간을 ROE 대비 낮춰 잡음
        if value >= 15: return 10
        elif value >= 12: return 9
        elif value >= 9:  return 8
        elif value >= 7:  return 7
        elif value >= 5:  return 6
        elif value >= 3:  return 5
        elif value >= 1:  return 4
        elif value >= 0:  return 3
        elif value >= -5: return 2
        elif value >= -15: return 1
        else: return 0

    elif metric_name == "debt_rate":
        if is_financial_sector: return 10  # 금융업은 부채비율 예외
        if value <= 20: return 10
        elif value <= 40: return 9
        elif value <= 60: return 8
        elif value <= 80: return 7
        elif value <= 100: return 6
        elif value <= 120: return 5
        elif value <= 150: return 4
        elif value <= 180: return 3
        elif value <= 200: return 2
        elif value <= 300: return 1
        else: return 0

    elif metric_name == "quick_ratio":
        # 정의: (유동자산 - 재고자산) / 유동부채 x 100 - 유동비율보다 낮게 나오는 게 정상
        if is_financial_sector: return 10  # 금융업은 당좌비율 예외
        if value >= 180: return 10
        elif value >= 150: return 9
        elif value >= 120: return 8
        elif value >= 100: return 7
        elif value >= 80:  return 6
        elif value >= 60:  return 5
        elif value >= 40:  return 4
        elif value >= 25:  return 3
        elif value >= 15:  return 2
        elif value >= 5:   return 1
        else: return 0

    elif metric_name == "interest_coverage":
        if is_financial_sector: return 10  # 금융업 예외
        if value >= 20: return 10
        elif value >= 15: return 9
        elif value >= 11: return 8
        elif value >= 8:  return 7
        elif value >= 5:  return 6
        elif value >= 3:  return 5
        elif value >= 2:  return 4
        elif value >= 1.5: return 3
        elif value >= 1.0: return 2
        elif value > 0:   return 1
        else: return 0

    elif metric_name == "ocf_ratio":
        # 정의: 영업활동현금흐름 / 당기순이익
        if value >= 1.5: return 10
        elif value >= 1.3: return 9
        elif value >= 1.1: return 8
        elif value >= 1.0: return 7
        elif value >= 0.8: return 6
        elif value >= 0.6: return 5
        elif value >= 0.4: return 4
        elif value >= 0.2: return 3
        elif value > 0:    return 2
        elif value == 0:   return 1
        else: return 0

    elif metric_name == "sga_ratio":
        if value <= 8: return 10
        elif value <= 12: return 9
        elif value <= 16: return 8
        elif value <= 20: return 7
        elif value <= 25: return 6
        elif value <= 30: return 5
        elif value <= 35: return 4
        elif value <= 40: return 3
        elif value <= 50: return 2
        elif value <= 65: return 1
        else: return 0

    elif metric_name == "downturn_defense":
        # 정의: (종목 MDD - 코스피 MDD), 과거 하락장 구간(코로나/2022년 긴축) 평균, 단위 %p
        # 양수 = 코스피보다 덜 빠짐(방어적) / 음수 = 코스피보다 더 빠짐(취약)
        if value >= 20: return 10
        elif value >= 15: return 9
        elif value >= 10: return 8
        elif value >= 5:  return 7
        elif value >= 0:  return 6
        elif value >= -5: return 5
        elif value >= -10: return 4
        elif value >= -15: return 3
        elif value >= -25: return 2
        elif value >= -40: return 1
        else: return 0

    return 0


def evaluate_defense_grade(total_score):
    """총점(0~100)에 따른 최종 하락장 방어 등급(S~D) 부여"""
    if total_score >= 85: return "S", "방어력 최상"
    elif total_score >= 70: return "A", "우량"
    elif total_score >= 55: return "B", "보통"
    elif total_score >= 40: return "C", "주의"
    else: return "D", "위험"


def calculate_fundamental_score(metrics: dict, is_financial_sector: bool = False) -> dict:
    """
    metrics 딕셔너리(METRIC_KEYS 10개 키)를 받아 지표별 점수, 총점(0~100), 등급을 반환.
    값이 없는 지표는 0점 처리되므로 collector.py에서 10개 지표를 모두 채워서 넘기는 것을 전제로 함.
    """
    scores = {}
    total_score = 0

    for key in METRIC_KEYS:
        value = metrics.get(key)
        score = calculate_metric_score(key, value, is_financial_sector)
        scores[key] = {"value": value, "score": score}
        total_score += score

    grade, grade_desc = evaluate_defense_grade(total_score)

    return {
        "metric_scores": scores,   # Supabase jsonb 컬럼 저장 추천
        "total_score": total_score,
        "grade": grade,
        "grade_desc": grade_desc,
    }
