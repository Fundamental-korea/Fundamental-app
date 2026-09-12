"""US fundamental scoring by business profile.

The collector provides normalized annual SEC metrics. This module maps them to
business-model-specific profiles. Missing facts no longer inflate a company's
score to 100 merely because the remaining metrics were rescaled.
"""
from __future__ import annotations

from scoring import METRIC_SCORE_BANDS, calculate_metric_score, evaluate_defense_grade

US_PROFILES = {"standard", "financial", "reit", "bdc", "defense", "utility"}

PROFILE_METRICS = {
    "standard": {
        "revenue_growth": 5, "eps_growth": 5, "opm": 10, "roic": 10,
        "debt_rate": 10, "quick_ratio": 10, "interest_coverage": 10,
        "ocf_ratio": 10, "sga_ratio": 10, "downturn_defense": 20,
    },
    "financial": {
        "revenue_growth": 15, "eps_growth": 25, "roa": 25,
        "downturn_defense": 35,
    },
    "reit": {
        "revenue_growth": 10, "eps_growth": 10, "roa": 10,
        "debt_rate": 15, "ocf_ratio": 15, "interest_coverage": 10,
        "downturn_defense": 30,
    },
    "bdc": {
        "eps_growth": 10, "roa": 15, "debt_rate": 15,
        "ocf_ratio": 15, "interest_coverage": 10, "downturn_defense": 35,
    },
    "defense": {
        "revenue_growth": 10, "eps_growth": 10, "opm": 15, "roic": 15,
        "debt_rate": 10, "interest_coverage": 10, "ocf_ratio": 15,
        "downturn_defense": 15,
    },
    # Utility metrics are utility-specific rather than generic-company proxies.
    # Debt service and cash funding capacity matter more than absolute leverage.
    "utility": {
        "revenue_growth": 5,
        "eps_growth": 5,
        "opm": 10,
        "roa": 10,
        "debt_capital": 15,
        "ocf_debt": 15,
        "fcf_debt": 10,
        "dividend_coverage": 10,
        "interest_coverage": 10,
        "downturn_defense": 10,
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
    "dividend_coverage": [(2.5,10),(2,9),(1.75,8),(1.5,7),(1.25,6),(1,5),(.9,4),(.8,3),(.7,2),(.5,1)],
    "interest_coverage": [(8,10),(6,9),(5,8),(4,7),(3,6),(2.5,5),(2,4),(1.5,3),(1,2),(.5,1)],
    "downturn_defense": [(20,10),(15,9),(10,8),(5,7),(0,6),(-5,5),(-10,4),(-15,3),(-25,2),(-40,1)],
}

LEVERAGE_EXEMPT = {
    "standard": set(), "financial": set(), "reit": set(),
    "bdc": set(), "defense": set(), "utility": set(),
}


def _bands(metric):
    return METRIC_SCORE_BANDS.get(metric, [])


def _score(metric, value, profile):
    if value is None:
        return 0
    if metric in LEVERAGE_EXEMPT.get(profile, set()):
        return 10
    return calculate_metric_score(metric, value, leverage_exempt=False)


def _score_utility_metric(metric, value):
    if value is None:
        return 0
    for threshold, score in UTILITY_BANDS[metric]:
        if metric == "debt_capital":
            if value <= threshold:
                return score
        elif value >= threshold:
            return score
    return 0


def _coverage_cap(available_weight: float, total_weight: float) -> float:
    if not total_weight:
        return 0.0
    coverage = available_weight / total_weight
    if coverage >= 0.90:
        return 100.0
    if coverage >= 0.75:
        return 92.0
    if coverage >= 0.60:
        return 82.0
    return 70.0


def _calculate_profile_score(metrics: dict, profile: str, scorer):
    weights = PROFILE_METRICS[profile]
    total_weight = float(sum(weights.values()))
    scores = {}
    weighted_total = 0.0
    available_weight = 0.0

    for metric, weight in weights.items():
        value = metrics.get(metric)
        raw = scorer(metric, value)
        weighted = raw * (weight / 10.0)
        entry = {"value": value, "score": raw, "weight": weight, "weighted_score": round(weighted, 2)}
        if value is None:
            entry["excluded_from_total"] = True
        else:
            weighted_total += weighted
            available_weight += weight
        scores[metric] = entry

    normalized = weighted_total / available_weight * 100.0 if available_weight else 0.0
    cap = _coverage_cap(available_weight, total_weight)
    total = round(min(normalized, cap), 1)
    grade, grade_desc = evaluate_defense_grade(total)

    growth_keys = {"revenue_growth", "eps_growth"}
    growth_weighted = sum(e["weighted_score"] for m, e in scores.items() if m in growth_keys and not e.get("excluded_from_total"))
    defense_weighted = sum(e["weighted_score"] for m, e in scores.items() if m not in growth_keys and not e.get("excluded_from_total"))
    scale = 100.0 / available_weight if available_weight else 0.0
    missing = sum(1 for metric in weights if metrics.get(metric) is None)

    return {
        "profile": profile,
        "metric_scores": scores,
        "total_score": total,
        "grade": grade,
        "grade_desc": grade_desc,
        "sub_scores": {"growth": round(growth_weighted * scale, 1), "defense": round(defense_weighted * scale, 1)},
        "available_weight": available_weight,
        "coverage_pct": round((available_weight / total_weight) * 100.0, 1) if total_weight else 0.0,
        "score_cap": cap,
        "missing_metric_count": missing,
    }


def calculate_us_score(metrics: dict, profile: str = "standard") -> dict:
    """Return a US 0-100 score plus coverage metadata."""
    profile = profile if profile in US_PROFILES else "standard"
    if profile == "utility":
        return _calculate_profile_score(metrics, profile, _score_utility_metric)
    return _calculate_profile_score(metrics, profile, lambda metric, value: _score(metric, value, profile))


def validate_profiles() -> dict:
    return {
        profile: {"weight_total": sum(weights.values()), "metrics": list(weights)}
        for profile, weights in PROFILE_METRICS.items()
    }


if __name__ == "__main__":
    for profile, info in validate_profiles().items():
        print(f"{profile}: weight={info['weight_total']} metrics={info['metrics']}")
