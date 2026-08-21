# scoring.py
# 콜랩 노트북("Fundamental code")의 calculate_metric_score / evaluate_defense_grade /
# run_fundamental_analysis 로직을 그대로 이식한 버전.

METRIC_KEYS = [
    "revenue_growth",
    "eps_growth",
    "opm",
    "roe",
    "debt_rate",
    "current_ratio",
    "interest_coverage",
    "ocf_ratio",
    "retained_earnings",
    "sga_ratio",
]


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

    elif metric_name == "roe":
        if value >= 20: return 10
        elif value >= 17: return 9
        elif value >= 14: return 8
        elif value >= 11: return 7
        elif value >= 9:  return 6
        elif value >= 7:  return 5
        elif value >= 5:  return 4
        elif value >= 3:  return 3
        elif value >= 1:  return 2
        elif value >= 0:  return 1
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

    elif metric_name == "current_ratio":
        if is_financial_sector: return 10  # 금융업은 유동비율 예외
        if value >= 250: return 10
        elif value >= 220: return 9
        elif value >= 190: return 8
        elif value >= 160: return 7
        elif value >= 140: return 6
        elif value >= 120: return 5
        elif value >= 100: return 4
        elif value >= 85:  return 3
        elif value >= 70:  return 2
        elif value >= 50:  return 1
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

    elif metric_name == "retained_earnings":
        # 정의: (자본총계 - 자본금) / 자본금 x 100
        if value >= 2000: return 10
        elif value >= 1500: return 9
        elif value >= 1200: return 8
        elif value >= 900:  return 7
        elif value >= 600:  return 6
        elif value >= 400:  return 5
        elif value >= 250:  return 4
        elif value >= 150:  return 3
        elif value >= 100:  return 2
        elif value >= 50:   return 1
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
    콜랩의 run_fundamental_analysis와 동일한 방식 - 값이 없는 지표는 0점 처리되므로
    collector.py에서 10개 지표를 모두 채워서 넘기는 것을 전제로 함.
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
