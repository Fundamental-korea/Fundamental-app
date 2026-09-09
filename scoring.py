# scoring.py
# v4: v3의 채점 로직(if/elif 체인)을 METRIC_SCORE_BANDS 데이터 구조로 전환.
#     계산 로직(calculate_metric_score)과 UI 표시(app.py)가 이제 이 데이터를 공통으로
#     참조하므로, 구간을 바꾸고 싶으면 여기 한 곳(METRIC_SCORE_BANDS)만 고치면
#     채점기와 app.py의 "채점 기준표" 화면이 자동으로 같이 바뀐다 (진짜 단일 소스).
#     ⚠️ 채점 결과 자체(v3 대비)는 완전히 동일하도록 값 하나하나 대조 검증했음.

METRIC_KEYS = [
    "revenue_growth", "eps_growth", "opm", "roic", "debt_rate", "quick_ratio",
    "interest_coverage", "ocf_ratio", "sga_ratio", "downturn_defense",
]

METRIC_DIRECTION = {
    "revenue_growth": "higher", "eps_growth": "higher", "opm": "higher", "roic": "higher",
    "debt_rate": "lower", "quick_ratio": "higher", "interest_coverage": "higher",
    "ocf_ratio": "higher", "sga_ratio": "lower", "downturn_defense": "higher",
    "roa": "higher",
}

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
    # quick_ratio is stored/calculated as a ratio (e.g. 0.826), not percent.
    # The previous 180/150/... thresholds were a 100x unit mismatch.
    "quick_ratio": [
        (1.80, ">=", 10), (1.50, ">=", 9), (1.20, ">=", 8), (1.00, ">=", 7), (0.80, ">=", 6),
        (0.60, ">=", 5), (0.40, ">=", 4), (0.25, ">=", 3), (0.15, ">=", 2), (0.05, ">=", 1),
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

LEVERAGE_EXEMPT_METRICS = {"debt_rate", "quick_ratio", "interest_coverage"}
PROVISIONAL_GRADE_CUTOFFS = {"S": 76, "A": 64, "B": 53, "C": 42}


def worst_value(metric_name, values):
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    if METRIC_DIRECTION.get(metric_name) == "lower":
        return max(clean)
    return min(clean)


def _check_band(value, threshold, op):
    if op == ">=": return value >= threshold
    elif op == "<=": return value <= threshold
    elif op == ">": return value > threshold
    elif op == "==": return value == threshold
    return False


def calculate_metric_score(metric_name, value, leverage_exempt=False):
    if value is None:
        return 0
    if leverage_exempt and metric_name in LEVERAGE_EXEMPT_METRICS:
        return 10
    bands = METRIC_SCORE_BANDS.get(metric_name)
    if not bands:
        return 0
    for threshold, op, score in bands:
        if _check_band(value, threshold, op):
            return score
    return 0


def evaluate_defense_grade(total_score):
    c = PROVISIONAL_GRADE_CUTOFFS
    if total_score >= c["S"]: return "S", "방어력 최상 (잠정)"
    elif total_score >= c["A"]: return "A", "우량 (잠정)"
    elif total_score >= c["B"]: return "B", "보통 (잠정)"
    elif total_score >= c["C"]: return "C", "주의 (잠정)"
    else: return "D", "위험 (잠정)"


METRIC_WEIGHTS = {
    "revenue_growth": 5, "eps_growth": 5, "opm": 10, "roic": 10, "debt_rate": 10,
    "quick_ratio": 10, "interest_coverage": 10, "ocf_ratio": 10, "sga_ratio": 10,
    "downturn_defense": 20,
}
assert sum(METRIC_WEIGHTS.values()) == 100

FINANCIAL_EXCLUDED_METRICS = {"opm", "roic", "sga_ratio"}
FINANCIAL_REMAINING_WEIGHT = sum(w for k, w in METRIC_WEIGHTS.items() if k not in FINANCIAL_EXCLUDED_METRICS)
ROA_WEIGHT = 10
FINANCIAL_ACHIEVABLE_WEIGHT = FINANCIAL_REMAINING_WEIGHT + ROA_WEIGHT


def calculate_fundamental_score(metrics: dict, leverage_exempt: bool = False, is_financial: bool = False) -> dict:
    scores = {}
    total_weighted = 0.0
    excluded = set(FINANCIAL_EXCLUDED_METRICS) if is_financial else set()
    GROWTH_KEYS = {"revenue_growth", "eps_growth"}
    for gk in GROWTH_KEYS:
        if metrics.get(gk) is None:
            excluded.add(gk)
    achievable_weight = sum(w for k, w in METRIC_WEIGHTS.items() if k not in excluded)
    if is_financial:
        achievable_weight += ROA_WEIGHT
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
        scores["roa"] = {"value": roa_value, "score": roa_score, "weighted_score": round(roa_weighted, 2), "financial_only": True}
        total_weighted += roa_weighted
    rescale = (100.0 / achievable_weight) if achievable_weight else 0.0
    total = round(total_weighted * rescale, 1)
    grade, grade_desc = evaluate_defense_grade(total)
    return {"scores": scores, "total_score": total, "grade": grade, "grade_desc": grade_desc, "available_weight": achievable_weight}
