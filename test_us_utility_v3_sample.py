"""Dry-run sample for the proposed US Utility v3 scoring system.

No DB writes. No production scorer changes.
Uses the final PEG/AWR diagnostic values supplied for the utility audit.
"""
from __future__ import annotations

from math import isnan

# Proposed Utility v3: 100 points total.
WEIGHTS = {
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
}

# Higher is better except debt_capital (lower is better).
BANDS = {
    "revenue_growth": [(20, 10), (15, 9), (10, 8), (5, 7), (0, 6), (-5, 5), (-10, 4), (-20, 3), (-35, 2), (-50, 1)],
    "eps_growth": [(20, 10), (15, 9), (10, 8), (5, 7), (0, 6), (-5, 5), (-10, 4), (-20, 3), (-35, 2), (-50, 1)],
    "opm": [(35, 10), (30, 9), (25, 8), (20, 7), (15, 6), (10, 5), (5, 4), (0, 3), (-5, 2), (-15, 1)],
    "roa": [(8, 10), (6, 9), (5, 8), (4, 7), (3, 6), (2, 5), (1, 4), (0, 3), (-2, 2), (-5, 1)],
    "debt_capital": [(35, 10), (40, 9), (45, 8), (50, 7), (55, 6), (60, 5), (65, 4), (70, 3), (80, 2), (90, 1)],
    "ocf_debt": [(25, 10), (20, 9), (15, 8), (12, 7), (10, 6), (8, 5), (6, 4), (4, 3), (2, 2), (1, 1)],
    "fcf_debt": [(10, 10), (8, 9), (6, 8), (4, 7), (3, 6), (2, 5), (1, 4), (0, 3), (-2, 2), (-5, 1)],
    "dividend_coverage": [(2.5, 10), (2.0, 9), (1.75, 8), (1.5, 7), (1.25, 6), (1.0, 5), (0.9, 4), (0.8, 3), (0.7, 2), (0.5, 1)],
    "interest_coverage": [(8, 10), (6, 9), (5, 8), (4, 7), (3, 6), (2.5, 5), (2, 4), (1.5, 3), (1, 2), (0.5, 1)],
    "downturn_defense": [(20, 10), (15, 9), (10, 8), (5, 7), (0, 6), (-5, 5), (-10, 4), (-15, 3), (-25, 2), (-40, 1)],
}


def score(metric: str, value: float | None) -> int:
    if value is None:
        return 0
    if metric == "debt_capital":
        for threshold, points in BANDS[metric]:
            if value <= threshold:
                return points
        return 0
    for threshold, points in BANDS[metric]:
        if value >= threshold:
            return points
    return 0


def calculate(metrics: dict[str, float | None]) -> dict:
    raw = {metric: score(metric, metrics.get(metric)) for metric in WEIGHTS}
    available_weight = sum(weight for metric, weight in WEIGHTS.items() if metrics.get(metric) is not None)
    total_weight = sum(WEIGHTS.values())
    weighted = sum(raw[m] * (WEIGHTS[m] / 10) for m in WEIGHTS if metrics.get(m) is not None)
    normalized = weighted / available_weight * 100 if available_weight else 0

    # Same philosophy as the current US scorer: sparse extraction cannot create
    # an artificially high score.
    coverage = available_weight / total_weight
    cap = 100 if coverage >= .90 else 92 if coverage >= .75 else 82 if coverage >= .60 else 70
    total = min(normalized, cap)
    grade = "S" if total >= 76 else "A" if total >= 64 else "B" if total >= 53 else "C" if total >= 42 else "D"

    return {
        "raw": raw,
        "weighted": {m: round(raw[m] * WEIGHTS[m] / 10, 2) for m in WEIGHTS},
        "coverage_pct": round(coverage * 100, 1),
        "score_cap": cap,
        "total_score": round(total, 1),
        "grade": grade,
    }


# Values derived from the diagnostic supplied by the user.
# Revenue growth = YoY 2025 vs 2024. ROA = NI / average assets.
# FCF = OCF - capex. Dividend coverage = OCF / dividends.
SAMPLES = {
    "PEG": {
        "revenue_growth": (12.123 / 9.874 - 1) * 100,
        "eps_growth": None,  # EPS was not present in the final diagnostic.
        "opm": 2.980 / 12.123 * 100,
        "roa": 2.111 / ((54.640 + 57.576) / 2) * 100,
        "debt_capital": 57.03696207655526,
        "ocf_debt": 14.628520736315168,
        "fcf_debt": (3.298 - 3.272) / 22.545 * 100,
        "dividend_coverage": 3.298 / 1.258,
        "interest_coverage": None,
        "downturn_defense": None,  # filled by the existing downturn collector in production.
    },
    "AWR": {
        "revenue_growth": (136.742 / 126.404 - 1) * 100,
        "eps_growth": None,
        "opm": 203.275 / 136.742 * 100,
        "roa": 130.442 / ((2500.209 + 2715.092) / 2) * 100,
        "debt_capital": 42.81070579413121,
        "ocf_debt": 29.350964609684425,
        "fcf_debt": (229.730 - 236.822) / 782.700 * 100,
        "dividend_coverage": 229.730 / 74.658,
        "interest_coverage": None,
        "downturn_defense": None,
    },
}

# Deterministic placeholders ONLY for comparing the shape of the scoring system.
# They are not SEC-derived values and must not be copied into production data.
SAMPLES["PEG"]["downturn_defense"] = 6
SAMPLES["AWR"]["downturn_defense"] = 7


if __name__ == "__main__":
    print("UTILITY V3 SAMPLE — DRY RUN ONLY")
    print("weights:", WEIGHTS)
    print()
    for ticker, metrics in SAMPLES.items():
        result = calculate(metrics)
        print(f"{ticker}: score={result['total_score']} grade={result['grade']} coverage={result['coverage_pct']}% cap={result['score_cap']}")
        for metric in WEIGHTS:
            value = metrics.get(metric)
            print(f"  {metric:20s} value={value!s:>10s} raw={result['raw'][metric]:2d} weighted={result['weighted'][metric]:5.2f}")
        print()
