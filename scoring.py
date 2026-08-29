# scoring.py
# v3: v2(ROE→ROIC, 유동비율→당좌비율, downturn_defense 추가)에 이어,
#     최종 데이터 분석 결과를 반영한 가중치 재배분 + 금융섹터 지표 보정을 scoring 단계에 직접 내장.
#     (등급 컷오프는 전체 종목이 모여야 계산 가능한 상대적 개념이라 여기엔 포함하지 않음 -
#      수집 완료 후 별도 rescore 패스에서 실제 분포 기준으로 부여)

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
    "roa": "higher",  # 금융섹터 전용 대체지표 - roic 자리를 대신함
}

# --------------------------------------------------------------------------
# 최종 가중치 (합계 100) — downturn_defense를 2배로(10→20), 성장성 2개는 절반으로(각 10→5),
# 나머지 7개 지표는 원래 10점 유지. "하락장 방어" 목적에 맞게 방어력 신호 비중을 강화.
# --------------------------------------------------------------------------
METRIC_WEIGHTS = {
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
assert sum(METRIC_WEIGHTS.values()) == 100

# 금융섹터(은행/보험/증권 등)는 매출액/영업이익 개념 자체가 일반기업과 달라 이 3개 지표가
# 구조적으로 안 맞음 -> 총점에서 제외하고 나머지로 리스케일
FINANCIAL_EXCLUDED_METRICS = {"opm", "roic", "sga_ratio"}
FINANCIAL_REMAINING_WEIGHT = sum(
    w for k, w in METRIC_WEIGHTS.items() if k not in FINANCIAL_EXCLUDED_METRICS
)  # = 70

# 금융섹터는 roic(투하자본이익률) 대신 ROA(총자산이익률)를 대체 지표로 채점 -
# 은행/보험은 "투하자본" 개념 자체가 안 맞아 roic을 그냥 빼기만 했더니 실질 변별 지표가
# 4개(revenue_growth/eps_growth/ocf_ratio/downturn_defense)뿐이었음. ROA를 추가해 5개로 보강.
# ⚠️ 아래 브라켓은 잠정치 - 한국 은행/보험 ROA 실제 분포(대략 0.3~1.5%대로 알려짐)를 참고해 초안
#    설정했고, 전체 수집 후 실제 금융주 ROA 분포로 재보정 필요.
ROA_WEIGHT = 10
FINANCIAL_ACHIEVABLE_WEIGHT = FINANCIAL_REMAINING_WEIGHT + ROA_WEIGHT  # 70 + 10 = 80

# 잠정 등급 컷오프 (참고용) - 실제 최종 등급은 전체 수집 완료 후 실제 점수 분포의
# 백분위 기준으로 재산정됨 (rescore 패스). 수집 도중 임시로 참고할 값일 뿐 최종이 아님.
PROVISIONAL_GRADE_CUTOFFS = {"S": 76, "A": 64, "B": 53, "C": 42}


def worst_value(metric_name, values):
    """연도별 수치 리스트에서 해당 지표 기준 '가장 나빴던 값'을 반환 (None은 제외)"""
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    if METRIC_DIRECTION.get(metric_name) == "lower":
        return max(clean)  # 낮을수록 좋은 지표 -> 가장 큰 값이 최악
    return min(clean)  # 높을수록 좋은 지표 -> 가장 작은 값이 최악


def calculate_metric_score(metric_name, value, leverage_exempt=False):
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
        if leverage_exempt: return 10  # 금융/유틸리티 등 레버리지 구조가 다른 업종 예외
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
        if leverage_exempt: return 10  # 금융/유틸리티 등 레버리지 구조가 다른 업종 예외
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
        if leverage_exempt: return 10  # 금융/유틸리티 등 레버리지 구조가 다른 업종 예외
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

    elif metric_name == "roa":
        # 금융섹터 전용 (roic 대체) - 총자산이익률. 잠정 브라켓, 실제 분포로 재보정 예정.
        if value >= 1.5: return 10
        elif value >= 1.2: return 9
        elif value >= 1.0: return 8
        elif value >= 0.8: return 7
        elif value >= 0.6: return 6
        elif value >= 0.4: return 5
        elif value >= 0.2: return 4
        elif value >= 0.0: return 3
        elif value >= -0.5: return 2
        elif value >= -1.5: return 1
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
    """
    총점(0~100)에 따른 잠정 등급(S~D) 부여.
    ⚠️ 이 컷오프는 잠정값(PROVISIONAL_GRADE_CUTOFFS)이며, 전체 종목 수집이 끝난 뒤
    실제 점수 분포의 백분위 기준으로 다시 계산되어 덮어써질 예정. 수집 도중 참고용으로만 사용.
    """
    c = PROVISIONAL_GRADE_CUTOFFS
    if total_score >= c["S"]: return "S", "방어력 최상 (잠정)"
    elif total_score >= c["A"]: return "A", "우량 (잠정)"
    elif total_score >= c["B"]: return "B", "보통 (잠정)"
    elif total_score >= c["C"]: return "C", "주의 (잠정)"
    else: return "D", "위험 (잠정)"


def calculate_fundamental_score(metrics: dict, leverage_exempt: bool = False, is_financial: bool = False) -> dict:
    """
    metrics 딕셔너리(METRIC_KEYS 10개 키)를 받아 지표별 점수, 총점(0~100), 잠정등급을 반환.
    값이 없는 지표는 0점 처리되므로 collector.py에서 10개 지표를 모두 채워서 넘기는 것을 전제로 함.

    - leverage_exempt: 금융/지주회사/유틸리티 -> debt_rate/quick_ratio/interest_coverage 3개 지표 만점 처리
    - is_financial: 금융섹터(wics_sector=='금융') -> opm/roic/sga_ratio 3개 지표를 총점에서 제외하고
      대신 roic 자리에 ROA(총자산이익률)를 추가 채점. 나머지 7개(70점)+ROA(10점)=80점 만점을
      100점으로 리스케일. leverage_exempt와 별개 축이라 둘 다 True일 수 있음
      (금융업은 보통 둘 다 True가 됨 - is_financial_sector가 leverage_exempt 판정에도 들어가므로).
      is_financial=True인 경우 metrics 딕셔너리에 "roa" 키가 포함되어 있어야 함.
    """
    scores = {}
    total_weighted = 0.0

    excluded = FINANCIAL_EXCLUDED_METRICS if is_financial else set()

    for key in METRIC_KEYS:
        value = metrics.get(key)
        raw_score = calculate_metric_score(key, value, leverage_exempt)
        weight = METRIC_WEIGHTS[key]
        weighted = raw_score * (weight / 10.0)

        entry = {"value": value, "score": raw_score, "weighted_score": round(weighted, 2)}
        if key in excluded:
            entry["excluded_from_total"] = True
        else:
            total_weighted += weighted
        scores[key] = entry

    # 금융섹터: roic 자리를 대신할 ROA를 추가로 채점 (roic/opm/sga_ratio는 위에서 이미 제외됨)
    if is_financial:
        roa_value = metrics.get("roa")
        roa_score = calculate_metric_score("roa", roa_value, leverage_exempt=False)
        roa_weighted = roa_score * (ROA_WEIGHT / 10.0)
        scores["roa"] = {
            "value": roa_value, "score": roa_score, "weighted_score": round(roa_weighted, 2),
            "financial_only": True,
        }
        total_weighted += roa_weighted
        total_score = round(total_weighted * (100.0 / FINANCIAL_ACHIEVABLE_WEIGHT), 1)
    else:
        total_score = round(total_weighted, 1)

    grade, grade_desc = evaluate_defense_grade(total_score)

    growth_keys = {"revenue_growth", "eps_growth"}
    defense_keys = set(METRIC_KEYS) - growth_keys - excluded
    growth_sub = sum(scores[k]["weighted_score"] for k in growth_keys if k not in excluded)
    defense_sub = sum(scores[k]["weighted_score"] for k in defense_keys)
    if is_financial:
        defense_sub += scores["roa"]["weighted_score"]  # ROA는 방어/수익성 서브스코어에 포함
        rescale = 100.0 / FINANCIAL_ACHIEVABLE_WEIGHT
        growth_sub *= rescale
        defense_sub *= rescale

    missing_count = sum(1 for k in METRIC_KEYS if metrics.get(k) is None)
    if is_financial and metrics.get("roa") is None:
        missing_count += 1

    return {
        "metric_scores": scores,   # Supabase jsonb 컬럼 저장 추천
        "total_score": total_score,
        "grade": grade,            # 잠정 등급 - 전체 수집 완료 후 재산정 예정
        "grade_desc": grade_desc,
        "sub_scores": {"growth": round(growth_sub, 1), "defense": round(defense_sub, 1)},
        "financial_adjusted": is_financial,
        "missing_metric_count": missing_count,
    }
