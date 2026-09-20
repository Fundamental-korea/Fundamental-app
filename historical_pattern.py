# historical_pattern.py
# Explainable historical analog statistics for chart indicators.
# This is not a price-forecast model: it finds past daily states similar to
# today's state and reports the actual forward 5/20/60-trading-day outcomes.

from __future__ import annotations

import math
from typing import Dict, Iterable, List, Tuple

import pandas as pd

HORIZONS = (5, 20, 60)
MIN_MATCHES = 12
LOOKBACK_YEARS = 10
MIN_SIMILARITY = 65.0
MIN_GAP_BARS = 5

_TOLERANCES = {
    "ma": {"price_ma20": 0.05, "price_ma60": 0.07, "ma20_ma60": 0.06, "ma60_ma120": 0.08, "return20": 0.12},
    "bollinger": {"percent_b": 0.12, "bandwidth": 0.025, "bandwidth_change5": 0.30, "return20": 0.12},
    "rsi": {"rsi": 7.0, "rsi_change5": 10.0, "return20": 0.12},
    "macd": {"macd_norm": 0.015, "signal_norm": 0.015, "hist_norm": 0.012, "hist_change5": 0.012, "return20": 0.12},
    "stochastic": {"k": 12.0, "d": 12.0, "k_minus_d": 10.0, "return20": 0.12},
    "ichimoku": {"cloud_position": 1.0, "tenkan_kijun": 0.04, "cloud_thickness": 0.06, "return20": 0.12},
    "volume": {"volume_ratio": 0.50, "volume_ratio_change5": 0.75, "return5": 0.08, "return20": 0.12},
}


def _finite(value) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _safe_float(value):
    return float(value) if _finite(value) else None


def _pct(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a - b) / b.replace(0, pd.NA)


def _ret(series: pd.Series, window: int) -> pd.Series:
    return series / series.shift(window).replace(0, pd.NA) - 1.0


def _features(hist_df: pd.DataFrame, ind: Dict[str, pd.Series], key: str) -> pd.DataFrame:
    close = pd.to_numeric(hist_df["Close"], errors="coerce")
    volume = pd.to_numeric(hist_df["Volume"], errors="coerce")
    out = pd.DataFrame(index=hist_df.index)
    out["close"] = close

    if key == "ma":
        s20, s60, s120 = (pd.to_numeric(ind[k], errors="coerce") for k in ("sma20", "sma60", "sma120"))
        out["price_ma20"] = _pct(close, s20)
        out["price_ma60"] = _pct(close, s60)
        out["ma20_ma60"] = _pct(s20, s60)
        out["ma60_ma120"] = _pct(s60, s120)
        out["return20"] = _ret(close, 20)

    elif key == "bollinger":
        upper = pd.to_numeric(ind["bb_upper"], errors="coerce")
        mid = pd.to_numeric(ind["bb_mid"], errors="coerce")
        lower = pd.to_numeric(ind["bb_lower"], errors="coerce")
        width = (upper - lower).replace(0, pd.NA)
        out["percent_b"] = (close - lower) / width
        out["bandwidth"] = width / mid.replace(0, pd.NA)
        out["bandwidth_change5"] = _ret(width, 5)
        out["return20"] = _ret(close, 20)

    elif key == "rsi":
        rsi = pd.to_numeric(ind["rsi14"], errors="coerce")
        out["rsi"] = rsi
        out["rsi_change5"] = rsi - rsi.shift(5)
        out["return20"] = _ret(close, 20)

    elif key == "macd":
        line = pd.to_numeric(ind["macd_line"], errors="coerce")
        signal = pd.to_numeric(ind["macd_signal"], errors="coerce")
        hist = pd.to_numeric(ind["macd_hist"], errors="coerce")
        nz = close.replace(0, pd.NA)
        out["macd_norm"] = line / nz
        out["signal_norm"] = signal / nz
        out["hist_norm"] = hist / nz
        out["hist_change5"] = hist - hist.shift(5)
        out["return20"] = _ret(close, 20)

    elif key == "stochastic":
        k = pd.to_numeric(ind["stoch_k"], errors="coerce")
        d = pd.to_numeric(ind["stoch_d"], errors="coerce")
        out["k"] = k
        out["d"] = d
        out["k_minus_d"] = k - d
        out["return20"] = _ret(close, 20)

    elif key == "ichimoku":
        tenkan = pd.to_numeric(ind["tenkan"], errors="coerce")
        kijun = pd.to_numeric(ind["kijun"], errors="coerce")
        a = pd.to_numeric(ind["senkou_a"], errors="coerce")
        b = pd.to_numeric(ind["senkou_b"], errors="coerce")
        top = pd.concat([a, b], axis=1).max(axis=1)
        bottom = pd.concat([a, b], axis=1).min(axis=1)
        pos = pd.Series(0.0, index=hist_df.index)
        pos.loc[close > top] = 1.0
        pos.loc[close < bottom] = -1.0
        nz = close.replace(0, pd.NA)
        out["cloud_position"] = pos
        out["tenkan_kijun"] = (tenkan - kijun) / nz
        out["cloud_thickness"] = (top - bottom) / nz
        out["return20"] = _ret(close, 20)

    elif key == "volume":
        ma20 = pd.to_numeric(ind["vol_ma20"], errors="coerce")
        ratio = volume / ma20.replace(0, pd.NA)
        out["volume_ratio"] = ratio
        out["volume_ratio_change5"] = _ret(ratio, 5)
        out["return5"] = _ret(close, 5)
        out["return20"] = _ret(close, 20)
    else:
        raise ValueError(f"Unsupported indicator_key: {key}")

    return out


def _similarity(current: pd.Series, candidate: pd.Series, key: str) -> float | None:
    parts = []
    for feature, tolerance in _TOLERANCES[key].items():
        a, b = _safe_float(current.get(feature)), _safe_float(candidate.get(feature))
        if a is None or b is None:
            continue
        if feature == "cloud_position":
            parts.append(1.0 if a == b else 0.0)
        else:
            parts.append(max(0.0, 1.0 - abs(a - b) / tolerance))
    if len(parts) < max(2, len(_TOLERANCES[key]) // 2):
        return None
    return round(100.0 * sum(parts) / len(parts), 2)


def _lookback_start_index(features: pd.DataFrame, years: int = LOOKBACK_YEARS) -> int:
    """Return the first row included in the rolling calendar-year lookback."""
    if features.empty:
        return 0

    index = pd.to_datetime(features.index, errors="coerce")
    if getattr(index, "isna", lambda: pd.Series([], dtype=bool))().all():
        return max(0, len(features) - years * 252)

    current_date = index[-1]
    if pd.isna(current_date):
        return max(0, len(features) - years * 252)

    cutoff = current_date - pd.DateOffset(years=years)
    valid_positions = [i for i, value in enumerate(index) if pd.notna(value) and value >= cutoff]
    return valid_positions[0] if valid_positions else max(0, len(features) - years * 252)


def _select_matches(
    features: pd.DataFrame,
    key: str,
    lookback_years: int = LOOKBACK_YEARS,
    min_similarity: float = MIN_SIMILARITY,
) -> List[Tuple[int, float]]:
    """Select all sufficiently similar, outcome-observable historical cases.

    There is deliberately no fixed 100-case cap. A date is eligible only when
    all configured forward horizons can still be observed, and a match must
    clear the similarity threshold. This makes sample size an actual property
    of the historical data rather than a UI-imposed constant.
    """
    if len(features) < 120:
        return []

    last_candidate = len(features) - 1 - max(HORIZONS)
    current = features.iloc[-1]
    first_candidate = _lookback_start_index(features, lookback_years)

    scored = []
    for idx in range(first_candidate, last_candidate + 1):
        sim = _similarity(current, features.iloc[idx], key)
        if sim is not None and sim >= min_similarity:
            scored.append((idx, sim))

    scored.sort(key=lambda x: x[1], reverse=True)

    selected = []
    for idx, sim in scored:
        if all(abs(idx - old_idx) >= MIN_GAP_BARS for old_idx, _ in selected):
            selected.append((idx, sim))
    return selected


def _wilson_interval(successes: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """95% Wilson interval for a binomial proportion, returned as percentages."""
    if n <= 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1.0 + (z * z) / n
    center = (p + (z * z) / (2.0 * n)) / denom
    spread = (
        z
        * math.sqrt((p * (1.0 - p) / n) + (z * z) / (4.0 * n * n))
        / denom
    )
    return (round(max(0.0, (center - spread) * 100.0), 1),
            round(min(100.0, (center + spread) * 100.0), 1))


def _outcome_stats(close: pd.Series, matches: Iterable[Tuple[int, float]], horizon: int):
    returns, sims = [], []
    for idx, sim in matches:
        j = idx + horizon
        if j >= len(close):
            continue
        start, end = _safe_float(close.iloc[idx]), _safe_float(close.iloc[j])
        if start is None or end is None or start == 0:
            continue
        returns.append(end / start - 1.0)
        sims.append(sim)
    if not returns:
        return None
    s = pd.Series(returns, dtype="float64")
    up_count = int((s > 0).sum())
    n = int(len(s))
    ci_low, ci_high = _wilson_interval(up_count, n)

    return {
        "samples": n,
        "up_probability": round(float((s > 0).mean() * 100), 1),
        "up_probability_ci_low": ci_low,
        "up_probability_ci_high": ci_high,
        "down_probability": round(float((s < 0).mean() * 100), 1),
        "mean_return": round(float(s.mean() * 100), 2),
        "median_return": round(float(s.median() * 100), 2),
        "p25_return": round(float(s.quantile(0.25) * 100), 2),
        "p75_return": round(float(s.quantile(0.75) * 100), 2),
        "avg_similarity": round(float(pd.Series(sims).mean()), 1),
    }


def analyze_indicator_pattern(hist_df: pd.DataFrame, indicators: Dict[str, pd.Series], indicator_key: str) -> Dict:
    if hist_df is None or hist_df.empty or "Close" not in hist_df.columns:
        return {"status": "no_data", "matches": 0, "horizons": {}}
    features = _features(hist_df, indicators, indicator_key)
    matches = _select_matches(features, indicator_key)
    match_count = len(matches)
    if match_count < MIN_MATCHES:
        return {
            "status": "insufficient_matches",
            "matches": match_count,
            "horizons": {},
            "min_required": MIN_MATCHES,
            "lookback_years": LOOKBACK_YEARS,
            "min_similarity": MIN_SIMILARITY,
        }
    close = pd.to_numeric(hist_df["Close"], errors="coerce").reset_index(drop=True)
    matches = [(i, s) for i, s in matches if _finite(close.iloc[i])]
    horizons = {str(h): _outcome_stats(close, matches, h) for h in HORIZONS}
    horizons = {k: v for k, v in horizons.items() if v is not None}
    if not horizons:
        return {
            "status": "no_outcomes",
            "matches": match_count,
            "horizons": {},
            "lookback_years": LOOKBACK_YEARS,
            "min_similarity": MIN_SIMILARITY,
        }
    sims = [v["avg_similarity"] for v in horizons.values() if v.get("avg_similarity") is not None]
    return {
        "status": "ok",
        "matches": match_count,
        "avg_similarity": round(sum(sims) / len(sims), 1) if sims else None,
        "horizons": horizons,
        "lookback_years": LOOKBACK_YEARS,
        "min_similarity": MIN_SIMILARITY,
    }


def analyze_all_indicator_patterns(hist_df: pd.DataFrame, indicators: Dict[str, pd.Series]) -> Dict[str, Dict]:
    keys = ("ma", "bollinger", "rsi", "stochastic", "ichimoku", "macd", "volume")
    return {key: analyze_indicator_pattern(hist_df, indicators, key) for key in keys}
