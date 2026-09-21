"""US fundamental scoring engine.

US scoring deliberately shares the scoring *engine shape* with Korea
(0-10 metric scores, weighted 0-100 total, coverage cap, provisional grade),
but keeps US-specific metric bands and profile weights here so changes to
Korea scoring.py cannot silently change US scores.

Profiles:
    standard  - ordinary operating companies
    financial - banks / insurers / asset managers / brokers
    reit      - REITs
    bdc       - business development companies
    defense   - defense / aerospace companies
    utility   - regulated / integrated utilities

This module is scoring-only. It does not collect data or write Supabase.
"""

from __future__ import annotations

import math
from typing import Callable

# ---------------------------------------------------------------------------
# Version / grades
# ---------------------------------------------------------------------------

US_SCORING_VERSION = "1.2"
US_PROFILES = {"standard", "financial", "reit", "bdc", "defense", "utility"}

US_GRADE_CUTOFFS = {
    "S": 76.0,
    "A": 64.0,
    "B": 53.0,
    "C": 42.0,
}

# ---------------------------------------------------------------------------
# US metric bands
#
# The bands below are an explicit US calibration choice, not a copy of the
# Korean threshold table. Percent metrics are stored as percentage values
# (e.g. OPM=20.0 means 20%), while ratio metrics such as Quick Ratio and
# OCF Ratio are stored as multiples (e.g. 1.20x).
# ---------------------------------------------------------------------------

STANDARD_BANDS = {
    "revenue_growth": [
        (20, 10), (15, 9), (12, 8), (10, 7), (7, 6),
        (5, 5), (3, 4), (0, 3), (-5, 2), (-15, 1),
    ],
    "eps_growth": [
        (25, 10), (20, 9), (15, 8), (10, 7), (5, 6),
        (0, 5), (-5, 4), (-15, 3), (-30, 2), (-50, 1),
    ],
    "opm": [
        (30, 10), (25, 9), (20, 8), (15, 7), (10, 6),
        (7, 5), (5, 4), (3, 3), (1, 2), (0, 1),
    ],
    "roic": [
        (20, 10), (15, 9), (12, 8), (10, 7), (8, 6),
        (6, 5), (4, 4), (2, 3), (0, 2), (-5, 1),
    ],
    "debt_rate": [
        (30, 10), (50, 9), (75, 8), (100, 7), (125, 6),
        (150, 5), (200, 4), (300, 3), (500, 2), (800, 1),
    ],
    "quick_ratio": [
        (2.0, 10), (1.5, 9), (1.2, 8), (1.0, 7), (0.8, 6),
        (0.6, 5), (0.4, 4), (0.25, 3), (0.15, 2), (0.05, 1),
    ],
    "interest_coverage": [
        (20, 10), (12, 9), (8, 8), (5, 7), (3, 6),
        (2, 5), (1.5, 4), (1.0, 3), (0.5, 2), (0, 1),
    ],
    "ocf_ratio": [
        (1.5, 10), (1.3, 9), (1.1, 8), (1.0, 7), (0.8, 6),
        (0.6, 5), (0.4, 4), (0.2, 3), (0, 2), (-0.5, 1),
    ],
    "sga_ratio": [
        (8, 10), (12, 9), (16, 8), (20, 7), (25, 6),
        (30, 5), (35, 4), (45, 3), (60, 2), (80, 1),
    ],
    "downturn_defense": [
        (15, 10), (10, 9), (5, 8), (2, 7), (0, 6),
        (-3, 5), (-6, 4), (-10, 3), (-15, 2), (-25, 1),
    ],
}

# Special-profile bands. These remain explicit because the economic meaning
# and normal ranges differ from a general operating company.
FINANCIAL_BANDS = {
    "revenue_growth": [
        (15, 10), (10, 9), (7, 8), (5, 7), (3, 6),
        (1, 5), (0, 4), (-5, 3), (-15, 2), (-30, 1),
    ],
    "eps_growth": [
        (20, 10), (15, 9), (10, 8), (7, 7), (5, 6),
        (0, 5), (-5, 4), (-10, 3), (-20, 2), (-35, 1),
    ],
    "roa": [
        (3.0, 10), (2.5, 9), (2.0, 8), (1.5, 7), (1.0, 6),
        (0.7, 5), (0.4, 4), (0.0, 3), (-0.5, 2), (-1.5, 1),
    ],
    "downturn_defense": [
        (15, 10), (10, 9), (5, 8), (2, 7), (0, 6),
        (-3, 5), (-6, 4), (-10, 3), (-15, 2), (-25, 1),
    ],
}

REIT_BANDS = {
    "revenue_growth": [
        (10, 10), (7, 9), (5, 8), (3, 7), (1, 6),
        (0, 5), (-5, 4), (-10, 3), (-20, 2), (-35, 1),
    ],
    "eps_growth": [
        (10, 10), (7, 9), (5, 8), (3, 7), (1, 6),
        (0, 5), (-5, 4), (-10, 3), (-20, 2), (-35, 1),
    ],
    "roa": [
        (8, 10), (6, 9), (4, 8), (3, 7), (2, 6),
        (1.5, 5), (1, 4), (0.5, 3), (0, 2), (-2, 1),
    ],
    "debt_rate": [
        (30, 10), (45, 9), (60, 8), (75, 7), (90, 6),
        (110, 5), (130, 4), (160, 3), (200, 2), (300, 1),
    ],
    "ocf_ratio": [
        (1.5, 10), (1.3, 9), (1.1, 8), (1.0, 7), (0.8, 6),
        (0.6, 5), (0.4, 4), (0.2, 3), (0, 2), (-0.5, 1),
    ],
    "interest_coverage": [
        (12, 10), (8, 9), (6, 8), (4, 7), (3, 6),
        (2, 5), (1.5, 4), (1.0, 3), (0.5, 2), (0, 1),
    ],
    "downturn_defense": [
        (15, 10), (10, 9), (5, 8), (2, 7), (0, 6),
        (-3, 5), (-6, 4), (-10, 3), (-15, 2), (-25, 1),
    ],
}

BDC_BANDS = {
    "eps_growth": [
        (20, 10), (15, 9), (10, 8), (7, 7), (5, 6),
        (0, 5), (-5, 4), (-10, 3), (-20, 2), (-35, 1),
    ],
    "roa": [
        (8, 10), (6, 9), (5, 8), (4, 7), (3, 6),
        (2, 5), (1, 4), (0, 3), (-1, 2), (-3, 1),
    ],
    "debt_rate": [
        (50, 10), (70, 9), (90, 8), (110, 7), (130, 6),
        (150, 5), (180, 4), (220, 3), (300, 2), (400, 1),
    ],
    "ocf_ratio": [
        (1.5, 10), (1.3, 9), (1.1, 8), (1.0, 7), (0.8, 6),
        (0.6, 5), (0.4, 4), (0.2, 3), (0, 2), (-0.5, 1),
    ],
    "interest_coverage": [
        (12, 10), (8, 9), (6, 8), (4, 7), (3, 6),
        (2, 5), (1.5, 4), (1.0, 3), (0.5, 2), (0, 1),
    ],
    "downturn_defense": [
        (15, 10), (10, 9), (5, 8), (2, 7), (0, 6),
        (-3, 5), (-6, 4), (-10, 3), (-15, 2), (-25, 1),
    ],
}

# Defense is an industrial/manufacturing business model, so it reuses the
# Standard US metric bands. Its only specialization is the weight mix below.
DEFENSE_BANDS = STANDARD_BANDS

# Utility model v2.4 is kept as the integrated utility profile.
UTILITY_BANDS = {
    "revenue_growth": [(20,10),(15,9),(10,8),(5,7),(0,6),(-5,5),(-10,4),(-20,3),(-35,2),(-50,1)],
    "eps_growth": [(20,10),(15,9),(10,8),(5,7),(0,6),(-5,5),(-10,4),(-20,3),(-35,2),(-50,1)],
    "opm": [(35,10),(30,9),(25,8),(20,7),(15,6),(10,5),(5,4),(0,3),(-5,2),(-15,1)],
    "roa": [(8,10),(6,9),(5,8),(4,7),(3,6),(2,5),(1,4),(0,3),(-2,2),(-5,1)],
    "debt_capital": [(35,10),(40,9),(45,8),(50,7),(55,6),(60,5),(65,4),(70,3),(80,2),(90,1)],
    "ocf_debt": [(25,10),(20,9),(15,8),(12,7),(10,6),(8,5),(6,4),(4,3),(2,2),(1,1)],
    "fcf_debt": [(10,10),(8,9),(6,8),(4,7),(3,6),(2,5),(1,4),(0,3),(-2,2),(-5,1)],
    "interest_coverage": [(8,10),(6,9),(5,8),(4,7),(3,6),(2.5,5),(2,4),(1.5,3),(1,2),(.5,1)],
    "dividend_coverage": [(6,10),(4.5,9),(3.5,8),(2.75,7),(2.25,6),(1.75,5),(1.5,4),(1.25,3),(1,2),(.75,1)],
    "dividend_payout": [(30,10),(40,9),(50,8),(60,7),(70,6),(80,5),(90,4),(100,3),(110,2),(120,1)],
    "downturn_defense": [(20,10),(15,9),(10,8),(5,7),(0,6),(-5,5),(-10,4),(-15,3),(-25,2),(-40,1)],
}

PROFILE_METRICS = {
    "standard": {
        # US standard: emphasize core operating efficiency/cash generation while
        # keeping sparse Interest Coverage / SG&A fields low-impact.
        "revenue_growth": 5,
        "eps_growth": 5,
        "opm": 10,
        "roic": 15,
        "debt_rate": 10,
        "quick_ratio": 10,
        "interest_coverage": 5,
        "ocf_ratio": 15,
        "sga_ratio": 5,
        "downturn_defense": 20,
    },
    "financial": {
        "revenue_growth": 15,
        "eps_growth": 25,
        "roa": 25,
        "downturn_defense": 35,
    },
    "reit": {
        "revenue_growth": 10,
        "eps_growth": 10,
        "roa": 10,
        "debt_rate": 15,
        "ocf_ratio": 15,
        "interest_coverage": 10,
        "downturn_defense": 30,
    },
    "bdc": {
        "eps_growth": 10,
        "roa": 15,
        "debt_rate": 15,
        "ocf_ratio": 15,
        "interest_coverage": 10,
        "downturn_defense": 35,
    },
    "defense": {
        # Standard-variant: no separate threshold table, only different emphasis.
        "revenue_growth": 7,
        "eps_growth": 7,
        "opm": 12,
        "roic": 15,
        "debt_rate": 10,
        "quick_ratio": 7,
        "interest_coverage": 3,
        "ocf_ratio": 16,
        "sga_ratio": 5,
        "downturn_defense": 18,
    },    "utility": {
        "revenue_growth": 7,
        "eps_growth": 7,
        "opm": 10,
        "roa": 8,
        "debt_capital": 15,
        "ocf_debt": 10,
        "fcf_debt": 8,
        "interest_coverage": 10,
        "dividend_coverage": 6,
        "dividend_payout": 3,
        "downturn_defense": 16,
    },
}

PROFILE_LABELS = {
    "standard": "Standard",
    "financial": "Financial",
    "reit": "REIT",
    "bdc": "BDC",
    "defense": "Defense · Standard Variant",
    "utility": "Utility · Specialized",
}

PROFILE_DESCRIPTIONS = {
    "standard": "일반적인 미국 상장 기업용 공통 모델",
    "financial": "은행·보험·자산운용·브로커 등 금융업 특화",
    "reit": "부동산 임대업의 레버리지·현금흐름 특화",
    "bdc": "BDC의 자산수익성·레버리지·현금흐름 특화",
    "defense": "방산·항공우주 제조업 — Standard 지표를 재가중",
    "utility": "규제/통합 유틸리티 전용 세부 모델",
}
PROFILE_BANDS = {
    "standard": STANDARD_BANDS,
    "financial": FINANCIAL_BANDS,
    "reit": REIT_BANDS,
    "bdc": BDC_BANDS,
    "defense": DEFENSE_BANDS,
    "utility": UTILITY_BANDS,
}

PROFILE_SUBGROUPS = {
    "standard": {
        "growth": {"revenue_growth", "eps_growth"},
        "profitability": {"opm", "roic"},
        "financial_strength": {
            "debt_rate", "quick_ratio", "interest_coverage",
            "ocf_ratio", "sga_ratio",
        },
        "defense": {"downturn_defense"},
    },
    "financial": {
        "growth": {"revenue_growth", "eps_growth"},
        "defense": {"roa", "downturn_defense"},
    },
    "reit": {
        "growth": {"revenue_growth", "eps_growth"},
        "financial_strength": {
            "debt_rate", "ocf_ratio", "interest_coverage",
        },
        "profitability": {"roa"},
        "defense": {"downturn_defense"},
    },
    "bdc": {
        "growth": {"eps_growth"},
        "financial_strength": {
            "debt_rate", "ocf_ratio", "interest_coverage",
        },
        "profitability": {"roa"},
        "defense": {"downturn_defense"},
    },
    "defense": {
        "growth": {"revenue_growth", "eps_growth"},
        "profitability": {"opm", "roic"},
        "financial_strength": {
            "debt_rate", "quick_ratio", "interest_coverage",
            "ocf_ratio", "sga_ratio",
        },
        "defense": {"downturn_defense"},
    },
    "utility": {
        "growth": {"revenue_growth", "eps_growth"},
        "profitability": {"opm", "roa"},
        "financial_strength": {
            "debt_capital", "ocf_debt", "fcf_debt", "interest_coverage",
        },
        "dividend_safety": {"dividend_coverage", "dividend_payout"},
        "defense": {"downturn_defense"},
    },
}

# Reference-only flags. They never alter the score.
EXTREME_THRESHOLDS = {
    "quick_ratio": 20.0,
    "debt_rate": 1000.0,
    "ocf_ratio": 20.0,
    "interest_coverage": 100.0,
}


def _clean_value(value):
    if value is None:
        return None
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _score_from_bands(value: float | None, bands: list[tuple[float, int]], lower_better: bool = False) -> int:
    if value is None:
        return 0
    for threshold, score in bands:
        if lower_better:
            if value <= threshold:
                return score
        elif value >= threshold:
            return score
    return 0


LOWER_BETTER = {"debt_rate", "sga_ratio", "debt_capital", "dividend_payout"}


def calculate_metric_score_us(
    metric: str,
    value,
    profile: str = "standard",
) -> int:
    """Score one metric using the US profile's explicit bands."""
    profile = profile if profile in US_PROFILES else "standard"
    clean = _clean_value(value)
    if clean is None:
        return 0

    bands = PROFILE_BANDS[profile].get(metric)
    if not bands:
        return 0

    return _score_from_bands(
        clean,
        bands,
        lower_better=metric in LOWER_BETTER,
    )


def _coverage_cap(available_weight: float, total_weight: float) -> float:
    if total_weight <= 0:
        return 0.0
    coverage = available_weight / total_weight
    if coverage >= 0.90:
        return 100.0
    if coverage >= 0.75:
        return 92.0
    if coverage >= 0.60:
        return 82.0
    return 70.0


def score_confidence_level(coverage_pct: float) -> str:
    """Describe score data completeness without implying forecast accuracy."""
    if coverage_pct >= 90.0:
        return "high"
    if coverage_pct >= 75.0:
        return "medium"
    if coverage_pct >= 60.0:
        return "low"
    return "insufficient"


def evaluate_us_grade(total_score: float):
    if total_score >= US_GRADE_CUTOFFS["S"]:
        return "S", "방어력 최상 (잠정)"
    if total_score >= US_GRADE_CUTOFFS["A"]:
        return "A", "우량 (잠정)"
    if total_score >= US_GRADE_CUTOFFS["B"]:
        return "B", "보통 (잠정)"
    if total_score >= US_GRADE_CUTOFFS["C"]:
        return "C", "주의 (잠정)"
    return "D", "위험 (잠정)"


def _subgroup_score(scores: dict, subgroup: set[str], available_weight: float) -> float:
    if not subgroup or available_weight <= 0:
        return 0.0
    weighted = sum(
        scores[m]["weighted_score"]
        for m in subgroup
        if m in scores and not scores[m].get("excluded_from_total")
    )
    return round(weighted * (100.0 / available_weight), 1)


def calculate_us_score(metrics: dict, profile: str = "standard") -> dict:
    """Calculate one US company score without database side effects."""
    profile = profile if profile in US_PROFILES else "standard"
    weights = PROFILE_METRICS[profile]
    bands = PROFILE_BANDS[profile]
    total_weight = float(sum(weights.values()))

    scores = {}
    weighted_total = 0.0
    available_weight = 0.0

    for metric, weight in weights.items():
        raw_value = _clean_value(metrics.get(metric))
        raw_score = calculate_metric_score_us(metric, raw_value, profile)
        weighted = raw_score * (weight / 10.0)

        entry = {
            "value": raw_value,
            "score": raw_score,
            "weight": weight,
            "weighted_score": round(weighted, 2),
        }

        extreme_threshold = EXTREME_THRESHOLDS.get(metric)
        if (
            raw_value is not None
            and extreme_threshold is not None
            and raw_value >= extreme_threshold
        ):
            entry["is_extreme"] = True

        if raw_value is None:
            entry["excluded_from_total"] = True
        else:
            available_weight += weight
            weighted_total += weighted

        scores[metric] = entry

    normalized = (
        weighted_total / available_weight * 100.0
        if available_weight
        else 0.0
    )
    cap = _coverage_cap(available_weight, total_weight)
    total_score = round(min(normalized, cap), 1)
    grade, grade_desc = evaluate_us_grade(total_score)

    subgroup_scores = {}
    for name, subgroup in PROFILE_SUBGROUPS[profile].items():
        subgroup_scores[name] = _subgroup_score(
            scores, subgroup, available_weight
        )

    coverage_pct = round(
        available_weight / total_weight * 100.0,
        1,
    ) if total_weight else 0.0
    confidence_level = score_confidence_level(coverage_pct)

    return {
        "scoring_version": US_SCORING_VERSION,
        "profile": profile,
        "metric_scores": scores,
        "total_score": total_score,
        "grade": grade,
        "grade_desc": grade_desc,
        "sub_scores": subgroup_scores,
        "available_weight": available_weight,
        "coverage_pct": coverage_pct,
        "score_cap": cap,
        "confidence_level": confidence_level,
        "missing_metric_count": sum(
            1 for metric in weights if metrics.get(metric) is None
        ),
    }


def validate_profiles() -> dict:
    """Return structural validation information for all US profiles."""
    result = {}
    for profile, weights in PROFILE_METRICS.items():
        missing_bands = [m for m in weights if m not in PROFILE_BANDS[profile]]
        extra_bands = [m for m in PROFILE_BANDS[profile] if m not in weights]
        result[profile] = {
            "weight_total": sum(weights.values()),
            "metrics": list(weights),
            "missing_bands": missing_bands,
            "extra_bands": extra_bands,
        }
    return result


def _assert_valid_structure() -> None:
    for profile, weights in PROFILE_METRICS.items():
        assert sum(weights.values()) == 100, (
            f"{profile} weights must sum to 100"
        )
        bands = PROFILE_BANDS[profile]
        assert set(weights) == set(bands), (
            f"{profile} metrics and bands must match"
        )
        for metric, entries in bands.items():
            assert len(entries) == 10, (
                f"{profile}:{metric} must have 10 score bands"
            )
            assert [score for _, score in entries] == list(range(10, 0, -1)), (
                f"{profile}:{metric} score bands must be 10..1"
            )

    assert all(
        metric in STANDARD_BANDS
        for metric in PROFILE_METRICS["standard"]
    )


_assert_valid_structure()


if __name__ == "__main__":
    for profile, info in validate_profiles().items():
        print(
            f"{profile}: "
            f"weight={info['weight_total']} "
            f"metrics={len(info['metrics'])} "
            f"missing_bands={info['missing_bands']}"
        )
