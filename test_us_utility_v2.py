"""Dry-run validator for the proposed US utility scoring model.

Reads existing US_Fundamental rows only. It NEVER writes to Supabase.

Usage:
    python test_us_utility_v2.py
    python test_us_utility_v2.py --tickers DUK,NEE,VST,CEG,AWR,SO,D,AEP

The proposed utility v2 weights are intentionally based only on metrics that
are already collected today. FFO/AFFO, FFO/debt, capex/OCF and debt maturity
are NOT invented here; they can be added after extraction is implemented.
"""
from __future__ import annotations

import argparse
import json
import os

from supabase import create_client

from scoring import calculate_metric_score, evaluate_defense_grade

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")

# Candidate Utility v2: 100 points total.
UTILITY_V2_WEIGHTS = {
    "revenue_growth": 5,
    "eps_growth": 5,
    "opm": 10,
    "roic": 10,
    "debt_rate": 15,
    "interest_coverage": 15,
    "ocf_ratio": 20,
    "downturn_defense": 20,
}

DEFAULT_TICKERS = "DUK,NEE,VST,CEG,AWR,SO,D,AEP,EXC,ETR,PEG"


def score_one(metrics: dict) -> dict:
    scores = {}
    weighted_total = 0.0
    available_weight = 0.0
    total_weight = sum(UTILITY_V2_WEIGHTS.values())

    for metric, weight in UTILITY_V2_WEIGHTS.items():
        value = metrics.get(metric)
        if value is None:
            scores[metric] = {
                "value": None,
                "score": 0,
                "weight": weight,
                "weighted_score": 0,
                "excluded_from_total": True,
            }
            continue

        raw = calculate_metric_score(metric, value, leverage_exempt=False)
        weighted = raw * (weight / 10.0)
        scores[metric] = {
            "value": value,
            "score": raw,
            "weight": weight,
            "weighted_score": round(weighted, 2),
        }
        weighted_total += weighted
        available_weight += weight

    coverage = available_weight / total_weight if total_weight else 0
    normalized = weighted_total / available_weight * 100 if available_weight else 0

    # Same anti-sparse-data principle as the refined US profiles.
    if coverage >= 0.90:
        cap = 100
    elif coverage >= 0.75:
        cap = 92
    elif coverage >= 0.60:
        cap = 82
    else:
        cap = 70

    total = round(min(normalized, cap), 1)
    grade, grade_desc = evaluate_defense_grade(total)

    return {
        "total_score": total,
        "grade": grade,
        "grade_desc": grade_desc,
        "coverage_pct": round(coverage * 100, 1),
        "score_cap": cap,
        "missing_metric_count": sum(1 for m in UTILITY_V2_WEIGHTS if metrics.get(m) is None),
        "metric_scores": scores,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=DEFAULT_TICKERS)
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows = (
        sb.table("US_Fundamental")
        .select("ticker,company_name,sector,period_scores,total_score,grade")
        .in_("ticker", tickers)
        .execute()
        .data
    )

    found = {r["ticker"]: r for r in rows}
    print("UTILITY V2 DRY RUN (NO DB WRITES)")
    print("weights:", json.dumps(UTILITY_V2_WEIGHTS, ensure_ascii=False))
    print()

    for ticker in tickers:
        row = found.get(ticker)
        if not row:
            print(f"{ticker}: NOT FOUND")
            continue

        period_scores = row.get("period_scores") or {}
        print(f"=== {ticker} | {row.get('company_name')} | current={row.get('total_score')}/{row.get('grade')} ===")

        for period in ("1", "3", "5", "10"):
            payload = period_scores.get(period)
            if not payload:
                print(f"  {period}Y: unavailable")
                continue
            metrics = payload.get("metrics") or {}
            result = score_one(metrics)
            print(
                f"  {period}Y: v2={result['total_score']}/{result['grade']} "
                f"coverage={result['coverage_pct']}% cap={result['score_cap']} "
                f"missing={result['missing_metric_count']}"
            )
            detail = ", ".join(
                f"{m}={e['score']}/10" for m, e in result["metric_scores"].items()
            )
            print(f"        {detail}")
        print()


if __name__ == "__main__":
    main()
