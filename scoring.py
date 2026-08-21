# scoring.py

def calculate_metric_score(metric_name, value, is_manufacturing=False):
    """10대 펀더멘탈 지표별 점수 산출 (각 10점 만점, 총 100점)"""
    if value is None: return 0

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
