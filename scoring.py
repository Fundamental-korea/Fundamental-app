# scoring.py

# 10개 지표의 정식 키 이름 (collector.py에서 이 이름으로 metrics dict를 채워야 함)
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


def calculate_metric_score(metric_name, value, is_manufacturing=False):
    """10대 펀더멘탈 지표별 점수 산출 (각 10점 만점, 총 100점)"""
    if value is None:
        return None  # 0점이 아니라 '미계산'으로 구분 (None)
    if metric_name == "opm":
        if is_manufacturing:
            if value >= 12: return 10
            elif value >= 10: return 9
            elif value >= 8:  return 8
            elif value >= 6:  return 7
            elif value >= 4:  return 5
            elif value >= 2:  return 3
            else: return 1
        else:
            if value >= 25: return 10
            elif value >= 20: return 9
            elif value >= 15: return 8
            elif value >= 10: return 6
            elif value >= 5:  return 4
            else: return 1
    elif metric_name == "revenue_growth":
        if value >= 20: return 10
        elif value >= 10: return 8
        elif value >= 5:  return 7
        elif value >= 0:  return 5
        elif value >= -5: return 3
        elif value >= -10: return 2
        else: return 0
    elif metric_name == "eps_growth":
        if value >= 15: return 10
        elif value >= 8:  return 8
        elif value >= 0:  return 5
        elif value >= -5: return 3
        else: return 0
    elif metric_name == "roe":
        if value >= 20: return 10
        elif value >= 15: return 8
        elif value >= 10: return 6
        elif value >= 5:  return 4
        else: return 1
    elif metric_name == "debt_rate":
        if value <= 30: return 10
        elif value <= 60: return 9
        elif value <= 90: return 8
        elif value <= 120: return 7
        elif value <= 150: return 5
        elif value <= 200: return 3
        else: return 1
    elif metric_name == "current_ratio":
        if value >= 200: return 10
        elif value >= 150: return 8
        elif value >= 100: return 6
        elif value >= 70:  return 3
        else: return 1
    elif metric_name == "interest_coverage":
        if value >= 15: return 10
        elif value >= 10: return 8
        elif value >= 5:  return 6
        elif value >= 2:  return 4
        else: return 1
    elif metric_name == "ocf_ratio":
        if value >= 1.3: return 10
        elif value >= 1.0: return 8
        elif value >= 0.7: return 6
        elif value >= 0.4: return 4
        else: return 1
    elif metric_name == "retained_earnings":
        if value >= 1000: return 10
        elif value >= 500: return 8
        elif value >= 200: return 6
        elif value >= 100: return 4
        else: return 1
    elif metric_name == "sga_ratio":
        if value <= 10: return 10
        elif value <= 20: return 8
        elif value <= 30: return 6
        elif value <= 40: return 4
        else: return 1
    return 0


def evaluate_defense_grade(total_score):
    if total_score >= 85: return "S", "하락장 무적 (방어력 최상급)"
    elif total_score >= 70: return "A", "우량 방어주 (체력 견고)"
    elif total_score >= 55: return "B", "보통 (지수 연동형)"
    elif total_score >= 40: return "C", "주의 (펀더멘탈 균열)"
    else: return "D", "위험 (하락장 충격 취약)"


def calculate_fundamental_score(metrics: dict, is_manufacturing: bool = True) -> dict:
    """
    metrics 딕셔너리(예: {"opm": 12.3, "roe": 15.1, ...})를 받아
    지표별 점수, 총점(100점 환산), 등급을 반환한다.

    - 아직 계산되지 않은 지표는 metrics에 키가 없거나 값이 None이면 자동으로 건너뜀.
    - 총점은 '실제로 채점된 지표 개수' 기준 100점 만점으로 환산되므로,
      collector.py가 10개 지표를 다 채우기 전에도 항상 정상적인 점수가 나온다.
    """
    metric_scores = {}
    for key in METRIC_KEYS:
        value = metrics.get(key)
        if key == "opm":
            metric_scores[key] = calculate_metric_score(key, value, is_manufacturing)
        else:
            metric_scores[key] = calculate_metric_score(key, value)

    scored_values = [v for v in metric_scores.values() if v is not None]
    covered_count = len(scored_values)

    if covered_count == 0:
        return {
            "metric_scores": metric_scores,
            "covered_count": 0,
            "total_score": None,
            "grade": "N/A",
            "grade_desc": "채점 가능한 지표 없음",
        }

    raw_sum = sum(scored_values)
    total_score = round(raw_sum / (covered_count * 10) * 100)
    grade, grade_desc = evaluate_defense_grade(total_score)

    return {
        "metric_scores": metric_scores,   # Supabase jsonb 컬럼 저장 추천
        "covered_count": covered_count,   # 10개 중 몇 개 지표로 채점했는지
        "total_score": total_score,
        "grade": grade,
        "grade_desc": grade_desc,
    }
