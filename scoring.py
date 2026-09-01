# scoring.py
# v4: v3의 채점 로직(if/elif 체인)을 METRIC_SCORE_BANDS 데이터 구조로 전환.
#     계산 로직(calculate_metric_score)과 UI 표시(app.py)가 이제 이 데이터를 공통으로
#     참조하므로, 구간을 바꾸고 싶으면 여기 한 곳(METRIC_SCORE_BANDS)만 고치면
#     채점기와 app.py의 "채점 기준표" 화면이 자동으로 같이 바뀐다 (진짜 단일 소스).
#     ⚠️ 채점 결과 자체(v3 대비)는 완전히 동일하도록 값 하나하나 대조 검증했음.

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
# 채점 구간표 - calculate_metric_score()와 app.py의 "채점 기준표" 화면이 공통으로 참조하는
# 단일 소스. 각 항목은 (threshold, 비교연산자, score) 튜플이고, 위에서부터 순서대로 검사해서
# 처음 조건을 만족하는 항목의 score를 반환한다 (원래 if/elif 체인과 동일한 단락평가 순서).
# 비교연산자: ">=" (이상), "<=" (이하), ">" (초과), "==" (정확히 일치)
# 리스트에 없는 값(모든 조건 불만족)은 0점.
# --------------------------------------------------------------------------
METRIC_SCORE_BANDS = {
    "revenue_growth": [
        (25, ">=", 10), (20, ">=", 9), (16, ">=", 8), (12, ">=", 7), (8, ">=", 6),
        (5, ">=", 5), (2, ">=", 4), (0, ">=", 3), (-5, ">=", 2), (-10, ">=", 1),
    ],
    "eps_growth": [
        (20, ">=", 10), (16, ">=", 9), (12, ">=", 8), (9, ">=", 7), (6, ">=", 6),
        (3, ">=", 5), (1, ">=", 4), (0, ">=", 3), (-5, ">=", 2), (-15, ">=", 1),
    ],
    "opm": [
        (25, ">=", 10), (20, ">=", 9), (16, ">=", 8), (13, ">=", 7), (10, ">=", 6),
        (7, ">=", 5), (5, ">=", 4), (3, ">=", 3), (1, ">=", 2), (0, ">=", 1),
    ],
    "roic": [
        (15, ">=", 10), (12, ">=", 9), (9, ">=", 8), (7, ">=", 7), (5, ">=", 6),
        (3, ">=", 5), (1, ">=", 4), (0, ">=", 3), (-5, ">=", 2), (-15, ">=", 1),
    ],
    "roa": [
        (1.5, ">=", 10), (1.2, ">=", 9), (1.0, ">=", 8), (0.8, ">=", 7), (0.6, ">=", 6),
        (0.4, ">=", 5), (0.2, ">=", 4), (0.0, ">=", 3), (-0.5, ">=", 2), (-1.5, ">=", 1),
    ],
    "debt_rate": [
        (20, "<=", 10), (40, "<=", 9), (60, "<=", 8), (80, "<=", 7), (100, "<=", 6),
        (120, "<=", 5), (150, "<=", 4), (180, "<=", 3), (200, "<=", 2), (300, "<=", 1),
    ],
    "quick_ratio": [
        (180, ">=", 10), (150, ">=", 9), (120, ">=", 8), (100, ">=", 7), (80, ">=", 6),
        (60, ">=", 5), (40, ">=", 4), (25, ">=", 3), (15, ">=", 2), (5, ">=", 1),
    ],
    "interest_coverage": [
        (20, ">=", 10), (15, ">=", 9), (11, ">=", 8), (8, ">=", 7), (5, ">=", 6),
        (3, ">=", 5), (2, ">=", 4), (1.5, ">=", 3), (1.0, ">=", 2), (0, ">", 1),
    ],
    "ocf_ratio": [
        (1.5, ">=", 10), (1.3, ">=", 9), (1.1, ">=", 8), (1.0, ">=", 7), (0.8, ">=", 6),
        (0.6, ">=", 5), (0.4, ">=", 4), (0.2, ">=", 3), (0, ">", 2), (0, "==", 1),
    ],
    "sga_ratio": [
        (8, "<=", 10), (12, "<=", 9), (16, "<=", 8), (20, "<=", 7), (25, "<=", 6),
        (30, "<=", 5), (35, "<=", 4), (40, "<=", 3), (50, "<=", 2), (65, "<=", 1),
    ],
    "downturn_defense": [
        (20, ">=", 10), (15, ">=", 9), (10, ">=", 8), (5, ">=", 7), (0, ">=", 6),
        (-5, ">=", 5), (-10, ">=", 4), (-15, ">=", 3), (-25, ">=", 2), (-40, ">=", 1),
    ],
}

# debt_rate/quick_ratio/interest_coverage는 금융/지주회사/유틸리티 등 레버리지 구조가
# 다른 업종에서 leverage_exempt=True일 때 자동 만점(10점) 처리되는 지표
LEVERAGE_EXEMPT_METRICS = {"debt_rate", "quick_ratio", "interest_coverage"}

PROVISIONAL_GRADE_CUTOFFS = {"S": 76, "A": 64, "B": 53, "C": 42}


def worst_value(metric_name, values):
    """연도별 수치 리스트에서 해당 지표 기준 '가장 나빴던 값'을 반환 (None은 제외)"""
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    if METRIC_DIRECTION.get(metric_name) == "lower":
        return max(clean)  # 낮을수록 좋은 지표 -> 가장 큰 값이 최악
    return min(clean)  # 높을수록 좋은 지표 -> 가장 작은 값이 최악


def _check_band(value, threshold, op):
    if op == ">=":
        return value >= threshold
    elif op == "<=":
        return value <= threshold
    elif op == ">":
        return value > threshold
    elif op == "==":
        return value == threshold
    return False


def calculate_metric_score(metric_name, value, leverage_exempt=False):
    """각 재무 지표 수치(value)를 입력받아 0~10점 사이의 점수를 반환.
    METRIC_SCORE_BANDS를 위에서부터 순서대로 검사해 처음 만족하는 구간의 점수를 반환한다."""
    if value is None:
        return 0  # 데이터 부재 시 기본값

    if leverage_exempt and metric_name in LEVERAGE_EXEMPT_METRICS:
        return 10  # 금융/지주회사/유틸리티 등 레버리지 구조가 다른 업종 예외

    bands = METRIC_SCORE_BANDS.get(metric_name)
    if not bands:
        return 0

    for threshold, op, score in bands:
        if _check_band(value, threshold, op):
            return score

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


# --------------------------------------------------------------------------
# 최종 가중치 (합계 100)
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

FINANCIAL_EXCLUDED_METRICS = {"opm", "roic", "sga_ratio"}
FINANCIAL_REMAINING_WEIGHT = sum(
    w for k, w in METRIC_WEIGHTS.items() if k not in FINANCIAL_EXCLUDED_METRICS
)  # = 70

ROA_WEIGHT = 10
FINANCIAL_ACHIEVABLE_WEIGHT = FINANCIAL_REMAINING_WEIGHT + ROA_WEIGHT  # 70 + 10 = 80


def calculate_fundamental_score(metrics: dict, leverage_exempt: bool = False, is_financial: bool = False) -> dict:
    """
    metrics 딕셔너리(METRIC_KEYS 10개 키)를 받아 지표별 점수, 총점(0~100), 잠정등급을 반환.
    값이 없는 지표는 0점 처리되므로 collector.py에서 10개 지표를 모두 채워서 넘기는 것을 전제로 함.
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
        defense_sub += scores["roa"]["weighted_score"]
        rescale = 100.0 / FINANCIAL_ACHIEVABLE_WEIGHT
        growth_sub *= rescale
        defense_sub *= rescale

    missing_count = sum(1 for k in METRIC_KEYS if metrics.get(k) is None)
    if is_financial and metrics.get("roa") is None:
        missing_count += 1

    return {
        "metric_scores": scores,
        "total_score": total_score,
        "grade": grade,
        "grade_desc": grade_desc,
        "sub_scores": {"growth": round(growth_sub, 1), "defense": round(defense_sub, 1)},
        "financial_adjusted": is_financial,
        "missing_metric_count": missing_count,
    }
