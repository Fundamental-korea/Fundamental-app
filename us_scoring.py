"""US fundamental scoring by business profile.

The collector provides normalized annual SEC metrics. This module maps them to
business-model-specific profiles. Missing facts no longer inflate a company's
score to 100 merely because the remaining metrics were rescaled.
"""
from __future__ import annotations

from scoring import METRIC_SCORE_BANDS, calculate_metric_score, evaluate_defense_grade

US_PROFILES = {"standard", "financial", "reit", "bdc", "defense", "utility"}

PROFILE_METRICS = {
    # General operating companies.
    "standard": {
        "revenue_growth": 5, "eps_growth": 5, "opm": 10, "roic": 10,
        "debt_rate": 10, "quick_ratio": 10, "interest_coverage": 10,
        "ocf_ratio": 10, "sga_ratio": 10, "downturn_defense": 20,
    },
    # Financial institutions: do not use ordinary-company OPM, ROIC, SG&A,
    # quick ratio, interest coverage or OCF/NI as primary health measures.
    # Those ratios are not economically comparable for banks/insurers/asset
    # managers. ROA is the common profitability anchor available today.
    "financial": {
        "revenue_growth": 15,
        "eps_growth": 25,
        "roa": 25,
        "downturn_defense": 35,
    },
    # REIT profile is deliberately conservative until FFO/AFFO extraction is
    # added. Leverage and cash generation remain useful proxies.
    "reit": {
        "revenue_growth": 10, "eps_growth": 10, "roa": 10,
        "debt_rate": 15, "ocf_ratio": 15, "interest_coverage": 10,
        "downturn_defense": 30,
    },
    # BDC profile remains a provisional proxy until NII/NAV-specific facts are
    # collected. It avoids ordinary-company margin metrics.
    "bdc": {
        "eps_growth": 10, "roa": 15, "debt_rate": 15,
        "ocf_ratio": 15, "interest_coverage": 10, "downturn_defense": 35,
    },
    # Defense / aerospace: backlog and book-to-bill are future additions, so
    # v2 uses the best currently collected operating proxies without treating
    # defense companies as generic industrials.
    "defense": {
        "revenue_growth": 10, "eps_growth": 10, "opm": 15, "roic": 15,
        "debt_rate": 10, "interest_coverage": 10, "ocf_ratio": 15,
        "downturn_defense": 15,
    },
    # Utility scoring is intentionally left unchanged for this phase.
    "utility": {
        "revenue_growth": 5, "eps_growth": 5, "opm": 10, "roic": 10,
        "debt_rate": 15, "interest_coverage": 10, "ocf_ratio": 15,
        "sga_ratio": 5, "downturn_defense": 25,
    },
}

# Structural exemptions are limited to metrics that are clearly inappropriate
# for the profile. Missing values still remain missing and never receive 10/10.
LEVERAGE_EXEMPT = {
    "standard": set(),
    "financial": set(),
    "reit": set(),
    "bdc": set(),
    "defense": set(),
    "utility": set(),
}


def _bands(metric):
    return METRIC_SCORE_BANDS.get(metric, [])


def _score(metric, value, profile):
    if value is None:
        return 0
    if metric in LEVERAGE_EXEMPT.get(profile, set()):
        return 10
    return calculate_metric_score(metric, value, leverage_exempt=False)


def _coverage_cap(available_weight: float, total_weight: float) -> float:
    """Cap scores when too much of the profile is missing.

    This keeps the score comparable without rewarding sparse SEC extraction.
    Full coverage has no cap; sparse coverage gets a transparent ceiling.
    """
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


def calculate_us_score(metrics: dict, profile: str = "standard") -> dict:
    """Return a US 0-100 score plus coverage metadata."""
    profile = profile if profile in US_PROFILES else "standard"
    weights = PROFILE_METRICS[profile]
    total_weight = float(sum(weights.values()))

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

    if available_weight:
        normalized = weighted_total / available_weight * 100.0
    else:
        normalized = 0.0

    cap = _coverage_cap(available_weight, total_weight)
    total = round(min(normalized, cap), 1)
    grade, grade_desc = evaluate_defense_grade(total)

    growth_keys = {"revenue_growth", "eps_growth"}
    growth_weighted = sum(
        entry["weighted_score"]
        for metric, entry in scores.items()
        if metric in growth_keys and not entry.get("excluded_from_total")
    )
    defense_weighted = sum(
        entry["weighted_score"]
        for metric, entry in scores.items()
        if metric not in growth_keys and not entry.get("excluded_from_total")
    )
    scale = 100.0 / available_weight if available_weight else 0.0
    growth = round(growth_weighted * scale, 1)
    defense = round(defense_weighted * scale, 1)

    missing = sum(1 for metric in weights if metrics.get(metric) is None)
    coverage_pct = round((available_weight / total_weight) * 100.0, 1) if total_weight else 0.0

    return {
        "profile": profile,
        "metric_scores": scores,
        "total_score": total,
        "grade": grade,
        "grade_desc": grade_desc,
        "sub_scores": {"growth": growth, "defense": defense},
        "available_weight": available_weight,
        "coverage_pct": coverage_pct,
        "score_cap": cap,
        "missing_metric_count": missing,
    }


def validate_profiles() -> dict:
    return {
        profile: {"weight_total": sum(weights.values()), "metrics": list(weights)}
        for profile, weights in PROFILE_METRICS.items()
    }


if __name__ == "__main__":
    for profile, info in validate_profiles().items():
        print(f"{profile}: weight={info['weight_total']} metrics={info['metrics']}")
