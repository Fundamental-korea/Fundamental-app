"""US-only fundamental scoring engine.

This is intentionally separate from the Korea/shared scoring engine.
The collector supplies normalized annual metrics from SEC Company Facts,
and this module decides how those metrics should be scored by US company
profile: standard, financial, REIT, BDC, utility.

Important: this v1 does not pretend to have FFO/AFFO or BDC-specific NAV/NII
when those facts are not collected yet. Instead, it uses only metrics that are
actually available and excludes structurally inappropriate metrics per profile.
"""

from __future__ import annotations

from scoring import (
    METRIC_SCORE_BANDS,
    METRIC_WEIGHTS,
    calculate_metric_score,
    evaluate_defense_grade,
)

US_PROFILES = {"standard", "financial", "reit", "bdc", "utility"}

# Base metric weights are the existing 100-point framework.
# Each profile selects an appropriate subset and is rescaled to 100.
PROFILE_METRICS = {
    # Normal operating companies.
    "standard": {
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
    },
    # Banks/insurers/asset managers/brokers: margin and SG&A are not treated
    # as ordinary-company operating metrics; ROA substitutes for ROIC.
    "financial": {
        "revenue_growth": 5,
        "eps_growth": 5,
        "roa": 10,
        "debt_rate": 10,
        "quick_ratio": 10,
        "interest_coverage": 10,
        "ocf_ratio": 10,
        "downturn_defense": 20,
    },
    # REITs: conventional OPM/ROIC/SG&A and current-ratio tests are poor
    # general-purpose measures. Until FFO/AFFO extraction is added, use
    # earnings/cash-flow stability, conservative leverage, and defense.
    "reit": {
        "revenue_growth": 10,
        "eps_growth": 10,
        "roa": 10,
        "debt_rate": 15,
        "ocf_ratio": 15,
        "interest_coverage": 10,
        "downturn_defense": 30,
    },
    # BDCs: current SEC extraction does not yet include NII/NAV. Avoid
    # pretending Revenue/OPM/SG&A are meaningful. Emphasize earnings growth,
    # ROA, leverage proxy, cash generation and downturn behavior.
    "bdc": {
        "eps_growth": 10,
        "roa": 15,
        "debt_rate": 15,
        "ocf_ratio": 15,
        "interest_coverage": 10,
        "downturn_defense": 35,
    },
    # Utilities: stable cash generation and balance-sheet resilience matter
    # more than short-term growth. Revenue/earnings growth remain useful but
    # receive less weight than cash flow and defense.
    "utility": {
        "revenue_growth": 5,
        "eps_growth": 5,
        "opm": 10,
        "roic": 10,
        "debt_rate": 15,
        "interest_coverage": 10,
        "ocf_ratio": 15,
        "sga_ratio": 5,
        "downturn_defense": 25,
    },
}

# Profile-specific scoring behavior. These metrics receive a structural 10/10
# where a conventional ratio is not meaningful for that profile.
LEVERAGE_EXEMPT = {
    "financial": {"debt_rate", "quick_ratio", "interest_coverage"},
    "reit": set(),
    "bdc": set(),
    "utility": set(),
    "standard": set(),
}


def _bands(metric):
    return METRIC_SCORE_BANDS.get(metric, [])


def _score(metric, value, profile):
    if value is None:
        return 0
    if metric in LEVERAGE_EXEMPT.get(profile, set()):
        return 10
    return calculate_metric_score(metric, value, leverage_exempt=False)


def _available_weight(profile, metrics):
    weights = PROFILE_METRICS[profile]
    return sum(weight for metric, weight in weights.items() if metrics.get(metric) is not None)


def calculate_us_score(metrics: dict, profile: str = "standard") -> dict:
    """Return a normalized 0-100 US score for one company/period."""
    profile = profile if profile in US_PROFILES else "standard"
    weights = PROFILE_METRICS[profile]

    scores = {}
    weighted_total = 0.0
    available_weight = 0.0

    for metric, weight in weights.items():
        value = metrics.get(metric)
        raw = _score(metric, value, profile)
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

    # Dynamic re-scaling means missing SEC facts do not automatically become
    # zero points, but only if at least one usable metric exists.
    rescale = 100.0 / available_weight if available_weight else 0.0
    total = round(weighted_total * rescale, 1)
    grade, grade_desc = evaluate_defense_grade(total)

    growth_keys = {"revenue_growth", "eps_growth"}
    growth = sum(
        entry["weighted_score"]
        for metric, entry in scores.items()
        if metric in growth_keys and not entry.get("excluded_from_total")
    )
    defense = sum(
        entry["weighted_score"]
        for metric, entry in scores.items()
        if metric not in growth_keys and not entry.get("excluded_from_total")
    )
    growth = round(growth * rescale, 1)
    defense = round(defense * rescale, 1)

    missing = sum(1 for metric in weights if metrics.get(metric) is None)

    return {
        "profile": profile,
        "metric_scores": scores,
        "total_score": total,
        "grade": grade,
        "grade_desc": grade_desc,
        "sub_scores": {"growth": growth, "defense": defense},
        "available_weight": available_weight,
        "missing_metric_count": missing,
    }


def validate_profiles() -> dict:
    """Static sanity checks for development/debugging."""
    result = {}
    for profile, weights in PROFILE_METRICS.items():
        result[profile] = {
            "weight_total": sum(weights.values()),
            "metrics": list(weights),
        }
    return result


if __name__ == "__main__":
    for profile, info in validate_profiles().items():
        print(f"{profile}: weight={info['weight_total']} metrics={info['metrics']}")
