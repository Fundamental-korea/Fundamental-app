"""US Utility scoring v2.1 (dry-run candidate).

This module intentionally does not replace the production utility scorer yet.
It is used to compare a revised 100-point utility model against the current
model before any production rewrite.
"""
from __future__ import annotations

from scoring import evaluate_defense_grade

US_PROFILES = {"utility"}

PROFILE_METRICS = {
    "utility": {
        "revenue_growth": 7,
        "eps_growth": 7,
        "opm": 10,
        "roa": 8,
        "debt_capital": 15,
        "ocf_debt": 10,
        "fcf_debt": 10,
        "interest_coverage": 10,
        "dividend_coverage": 5,
        "fcf_dividend": 3,
        "dividend_payout": 3,
        "downturn_defense": 12,
    },
}

UTILITY_BANDS = {
    "revenue_growth": [(20,10),(15,9),(10,8),(5,7),(0,6),(-5,5),(-10,4),(-20,3),(-35,2),(-50,1)],
    "eps_growth": [(20,10),(15,9),(10,8),(5,7),(0,6),(-5,5),(-10,4),(-20,3),(-35,2),(-50,1)],
    "opm": [(35,10),(30,9),(25,8),(20,7),(15,6),(10,5),(5,4),(0,3),(-5,2),(-15,1)],
    "roa": [(8,10),(6,9),(5,8),(4,7),(3,6),(2,5),(1,4),(0,3),(-2,2),(-5,1)],
    "debt_capital": [(35,10),(40,9),(45,8),(50,7),(55,6),(60,5),(65,4),(70,3),(80,2),(90,1)],
    "ocf_debt": [(25,10),(20,9),(15,8),(12,7),(10,6),(8,5),(6,4),(4,3),(2,2),(1,1)],
    "fcf_debt": [(10,10),(8,9),(6,8),(4,7),(3,6),(2,5),(1,4),(0,3),(-2,2),(-5,1)],
    "interest_coverage": [(8,10),(6,9),(5,8),(4,7),(3,6),(2.5,5),(2,4),(1.5,3),(1,2),(.5,1)],
    "dividend_coverage": [(6,10),(4.5,9),(3.5,8),(2.75,7),(2.25,6),(1.75,4),(1.25,2),(1,1)],
    "fcf_dividend": [(2.5,10),(2.0,9),(1.75,8),(1.5,7),(1.25,6),(1.0,5),(.75,4),(.5,3),(.25,2),(0,1)],
    "dividend_payout": [(40,10),(50,9),(60,8),(70,6),(80,4),(90,2),(100,1)],
    "downturn_defense": [(20,10),(15,9),(10,8),(5,7),(0,6),(-5,5),(-10,4),(-15,3),(-25,2),(-40,1)],
}


def _score(metric: str, value):
    if value is None:
        return 0
    for threshold, score in UTILITY_BANDS[metric]:
        if metric in {"debt_capital", "dividend_payout"}:
            if value <= threshold:
                return score
        else:
            if value >= threshold:
                return score
    return 0


def calculate_us_utility_score_v2(metrics: dict) -> dict:
    """Return the revised utility score without changing production scoring."""
    weights = PROFILE_METRICS["utility"]
    total_weight = float(sum(weights.values()))
    scores = {}
    weighted_total = 0.0
    available_weight = 0.0

    for metric, weight in weights.items():
        value = metrics.get(metric)
        raw = _score(metric, value)
        weighted = raw * (weight / 10.0)
        entry = {
            "value": value,
            "score": raw,
            "weight": weight,
            "weighted_score": round(weighted, 2),
        }
        if value is None:
            entry["excluded_from_total"] = True
        else:
            weighted_total += weighted
            available_weight += weight
        scores[metric] = entry

    normalized = weighted_total / available_weight * 100.0 if available_weight else 0.0
    if available_weight / total_weight >= 0.90:
        cap = 100.0
    elif available_weight / total_weight >= 0.75:
        cap = 92.0
    elif available_weight / total_weight >= 0.60:
        cap = 82.0
    else:
        cap = 70.0

    total = round(min(normalized, cap), 1)
    grade, grade_desc = evaluate_defense_grade(total)
    scale = 100.0 / available_weight if available_weight else 0.0

    def sub_total(metrics_list):
        return round(
            sum(scores[m]["weighted_score"] for m in metrics_list if not scores[m].get("excluded_from_total")) * scale,
            1,
        )

    return {
        "profile": "utility_v2_1",
        "metric_scores": scores,
        "total_score": total,
        "grade": grade,
        "grade_desc": grade_desc,
        "available_weight": available_weight,
        "coverage_pct": round(available_weight / total_weight * 100.0, 1) if total_weight else 0.0,
        "score_cap": cap,
        "missing_metric_count": sum(1 for metric in weights if metrics.get(metric) is None),
        "sub_scores": {
            "growth": sub_total(("revenue_growth", "eps_growth")),
            "profitability": sub_total(("opm", "roa")),
            "financial_strength": sub_total(("debt_capital", "ocf_debt", "fcf_debt", "interest_coverage")),
            "dividend_safety": sub_total(("dividend_coverage", "fcf_dividend", "dividend_payout")),
            "downturn": sub_total(("downturn_defense",)),
        },
    }


if __name__ == "__main__":
    print("utility_v2_1 weight total:", sum(PROFILE_METRICS["utility"].values()))
