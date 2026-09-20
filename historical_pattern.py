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
    "adx": {"adx": 10.0, "plus_di": 10.0, "minus_di": 10.0, "adx_change5": 8.0, "return20": 0.12},
    "atr": {"atr_pct": 0.025, "atr_change5": 0.40, "return20": 0.12},
    "obv": {"obv_change5_norm": 0.80, "obv_change20_norm": 1.20, "return20": 0.12},
    "mfi": {"mfi": 10.0, "mfi_change5": 12.0, "return20": 0.12},
    "vwap": {"vwap_gap": 0.05, "vwap_change5": 0.08, "return20": 0.12},
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

    elif key == "adx":
        adx = pd.to_numeric(ind["adx14"], errors="coerce")
        plus_di = pd.to_numeric(ind["plus_di14"], errors="coerce")
        minus_di = pd.to_numeric(ind["minus_di14"], errors="coerce")
        out["adx"] = adx
        out["plus_di"] = plus_di
        out["minus_di"] = minus_di
        out["adx_change5"] = adx - adx.shift(5)
        out["return20"] = _ret(close, 20)

    elif key == "atr":
        atr = pd.to_numeric(ind["atr14"], errors="coerce")
        nz = close.replace(0, pd.NA)
        out["atr_pct"] = atr / nz
        out["atr_change5"] = _ret(atr, 5)
        out["return20"] = _ret(close, 20)

    elif key == "obv":
        obv = pd.to_numeric(ind["obv"], errors="coerce")
        volume_sum_5 = volume.rolling(5, min_periods=5).sum().replace(0, pd.NA)
        volume_sum_20 = volume.rolling(20, min_periods=20).sum().replace(0, pd.NA)
        out["obv_change5_norm"] = (obv - obv.shift(5)) / volume_sum_5
        out["obv_change20_norm"] = (obv - obv.shift(20)) / volume_sum_20
        out["return20"] = _ret(close, 20)

    elif key == "mfi":
        mfi = pd.to_numeric(ind["mfi14"], errors="coerce")
        out["mfi"] = mfi
        out["mfi_change5"] = mfi - mfi.shift(5)
        out["return20"] = _ret(close, 20)

    elif key == "vwap":
        vwap = pd.to_numeric(ind["rolling_vwap20"], errors="coerce")
        nz = close.replace(0, pd.NA)
        out["vwap_gap"] = (close - vwap) / nz
        out["vwap_change5"] = _ret(vwap, 5)
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
    with_candidate_count: bool = False,
):
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

    candidate_count = len(scored)
    scored.sort(key=lambda x: x[1], reverse=True)

    selected = []
    for idx, sim in scored:
        if all(abs(idx - old_idx) >= MIN_GAP_BARS for old_idx, _ in selected):
            selected.append((idx, sim))
    if with_candidate_count:
        return selected, candidate_count
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
    down_count = int((s < 0).sum())
    n = int(len(s))
    up_ci_low, up_ci_high = _wilson_interval(up_count, n)
    down_ci_low, down_ci_high = _wilson_interval(down_count, n)

    return {
        "samples": n,
        "up_probability": round(float((s > 0).mean() * 100), 1),
        "up_probability_ci_low": up_ci_low,
        "up_probability_ci_high": up_ci_high,
        "down_probability": round(float((s < 0).mean() * 100), 1),
        "down_probability_ci_low": down_ci_low,
        "down_probability_ci_high": down_ci_high,
        "mean_return": round(float(s.mean() * 100), 2),
        "median_return": round(float(s.median() * 100), 2),
        "p25_return": round(float(s.quantile(0.25) * 100), 2),
        "p75_return": round(float(s.quantile(0.75) * 100), 2),
        "avg_similarity": round(float(pd.Series(sims).mean()), 1),
    }


# Current-state presentation thresholds.
# These are display-mode triggers, not predictions:
# 4 signals are checked symmetrically for overbought/oversold.
OVERBOUGHT_RSI = 70.0
OVERSOLD_RSI = 30.0
OVERBOUGHT_STOCH = 80.0
OVERSOLD_STOCH = 20.0
OVERBOUGHT_BB_PERCENT_B = 1.0
OVERSOLD_BB_PERCENT_B = 0.0
OVERBOUGHT_MA_GAP = 0.08
OVERSOLD_MA_GAP = -0.08


def _latest_series_value(indicators: Dict[str, pd.Series], key: str):
    series = indicators.get(key)
    if series is None or len(series) == 0:
        return None
    return _safe_float(series.iloc[-1])


def classify_current_condition(
    hist_df: pd.DataFrame,
    indicators: Dict[str, pd.Series],
) -> Dict:
    """Classify the latest daily technical state for probability presentation.

    The classifier decides which historical outcome direction is shown first.
    It does not change the historical-match calculation and does not predict
    future prices.
    """
    if hist_df is None or hist_df.empty or "Close" not in hist_df.columns:
        return {"state": "unknown", "primary_direction": "up", "signals": []}

    close = _safe_float(pd.to_numeric(hist_df["Close"], errors="coerce").iloc[-1])
    sma20 = _latest_series_value(indicators, "sma20")
    sma60 = _latest_series_value(indicators, "sma60")
    rsi = _latest_series_value(indicators, "rsi14")
    stoch_k = _latest_series_value(indicators, "stoch_k")
    stoch_d = _latest_series_value(indicators, "stoch_d")
    bb_upper = _latest_series_value(indicators, "bb_upper")
    bb_lower = _latest_series_value(indicators, "bb_lower")
    bb_mid = _latest_series_value(indicators, "bb_mid")
    adx = _latest_series_value(indicators, "adx14")
    plus_di = _latest_series_value(indicators, "plus_di14")
    minus_di = _latest_series_value(indicators, "minus_di14")
    mfi = _latest_series_value(indicators, "mfi14")
    rolling_vwap = _latest_series_value(indicators, "rolling_vwap20")

    signals = []
    overbought_count = 0
    oversold_count = 0

    if rsi is not None:
        if rsi >= OVERBOUGHT_RSI:
            overbought_count += 1
            signals.append("RSI 과매수")
        elif rsi <= OVERSOLD_RSI:
            oversold_count += 1
            signals.append("RSI 과매도")

    if stoch_k is not None:
        if stoch_k >= OVERBOUGHT_STOCH:
            overbought_count += 1
            signals.append("스토캐스틱 과매수")
        elif stoch_k <= OVERSOLD_STOCH:
            oversold_count += 1
            signals.append("스토캐스틱 과매도")

    if mfi is not None:
        if mfi >= 80.0:
            overbought_count += 1
            signals.append("MFI 과매수")
        elif mfi <= 20.0:
            oversold_count += 1
            signals.append("MFI 과매도")

    if (
        close is not None
        and bb_upper is not None
        and bb_lower is not None
        and bb_upper != bb_lower
    ):
        percent_b = (close - bb_lower) / (bb_upper - bb_lower)
        if percent_b >= OVERBOUGHT_BB_PERCENT_B:
            overbought_count += 1
            signals.append("볼린저 상단 돌파")
        elif percent_b <= OVERSOLD_BB_PERCENT_B:
            oversold_count += 1
            signals.append("볼린저 하단 이탈")

    ma_gap = None
    if close is not None and sma20 not in (None, 0):
        ma_gap = close / sma20 - 1.0
        if ma_gap >= OVERBOUGHT_MA_GAP:
            overbought_count += 1
            signals.append(f"20일선 대비 +{ma_gap * 100:.1f}%")
        elif ma_gap <= OVERSOLD_MA_GAP:
            oversold_count += 1
            signals.append(f"20일선 대비 {ma_gap * 100:.1f}%")

    if close is not None and sma20 is not None and sma60 is not None:
        if close > sma20 > sma60:
            trend = "uptrend"
        elif close < sma20 < sma60:
            trend = "downtrend"
        else:
            trend = "mixed"
    else:
        trend = "unknown"

    rsi_change5 = None
    rsi_series = indicators.get("rsi14")
    if rsi_series is not None and len(rsi_series) > 5:
        rsi_change5 = _safe_float(rsi_series.iloc[-1] - rsi_series.iloc[-6])

    hist_change5 = None
    macd_hist = indicators.get("macd_hist")
    if macd_hist is not None and len(macd_hist) > 5:
        hist_change5 = _safe_float(macd_hist.iloc[-1] - macd_hist.iloc[-6])

    weakness_signals = 0
    weakness_reasons = []
    if rsi_change5 is not None and rsi_change5 <= -3.0:
        weakness_signals += 1
        weakness_reasons.append("RSI 5일 둔화")
    if stoch_k is not None and stoch_d is not None and stoch_k < stoch_d:
        weakness_signals += 1
        weakness_reasons.append("스토캐스틱 하향")
    if hist_change5 is not None and hist_change5 < 0:
        weakness_signals += 1
        weakness_reasons.append("MACD 히스토그램 둔화")
    momentum = "weakening" if weakness_signals >= 2 else "not_weakening"

    if overbought_count >= 2 and oversold_count == 0:
        state = "overbought_strong" if overbought_count >= 3 else "overbought"
        primary_direction = "down"
    elif oversold_count >= 2 and overbought_count == 0:
        state = "oversold_strong" if oversold_count >= 3 else "oversold"
        primary_direction = "up"
    else:
        state = "neutral"
        primary_direction = "up"

    adx_trend_context = None
    if adx is not None and plus_di is not None and minus_di is not None and adx >= 25:
        if plus_di > minus_di:
            adx_trend_context = "강한 상승추세"
        elif minus_di > plus_di:
            adx_trend_context = "강한 하락추세"

    if state.startswith("overbought") and momentum == "weakening":
        context = "과매수 + 모멘텀 둔화"
    elif state.startswith("oversold") and momentum == "weakening":
        context = "과매도 + 모멘텀 둔화"
    elif state.startswith("overbought") and adx_trend_context == "강한 상승추세":
        context = "과매수 + 강한 상승추세"
    elif state.startswith("oversold") and adx_trend_context == "강한 하락추세":
        context = "과매도 + 강한 하락추세"
    elif state.startswith("overbought") and trend == "uptrend":
        context = "과매수 + 상승추세"
    elif state.startswith("oversold") and trend == "downtrend":
        context = "과매도 + 하락추세"
    elif state.startswith("overbought"):
        context = "과매수"
    elif state.startswith("oversold"):
        context = "과매도"
    elif adx_trend_context is not None:
        context = adx_trend_context
    elif trend == "uptrend":
        context = "상승추세"
    elif trend == "downtrend":
        context = "하락추세"
    else:
        context = "중립/혼조"

    label_map = {
        "overbought": "과매수",
        "overbought_strong": "강한 과매수",
        "oversold": "과매도",
        "oversold_strong": "강한 과매도",
        "neutral": "일반",
    }
    return {
        "state": state,
        "label": label_map.get(state, "알 수 없음"),
        "context": context,
        "primary_direction": primary_direction,
        "signal_count": max(overbought_count, oversold_count),
        "overbought_count": overbought_count,
        "oversold_count": oversold_count,
        "trend": trend,
        "momentum": momentum,
        "adx_trend_context": adx_trend_context,
        "signals": signals,
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "mfi": mfi,
        "rolling_vwap": rolling_vwap,
        "weakness_reasons": weakness_reasons,
        "rsi": rsi,
        "stoch_k": stoch_k,
        "bb_percent_b": (
            round((close - bb_lower) / (bb_upper - bb_lower), 3)
            if close is not None and bb_upper is not None and bb_lower is not None and bb_upper != bb_lower
            else None
        ),
        "ma_gap": ma_gap,
    }


def analyze_indicator_pattern(hist_df: pd.DataFrame, indicators: Dict[str, pd.Series], indicator_key: str) -> Dict:
    if hist_df is None or hist_df.empty or "Close" not in hist_df.columns:
        return {"status": "no_data", "matches": 0, "horizons": {}}
    features = _features(hist_df, indicators, indicator_key)
    matches, candidate_count = _select_matches(
        features,
        indicator_key,
        with_candidate_count=True,
    )
    match_count = len(matches)
    if match_count < MIN_MATCHES:
        return {
            "status": "insufficient_matches",
            "matches": match_count,
            "candidate_matches": candidate_count,
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
            "candidate_matches": candidate_count,
            "horizons": {},
            "lookback_years": LOOKBACK_YEARS,
            "min_similarity": MIN_SIMILARITY,
        }
    sims = [v["avg_similarity"] for v in horizons.values() if v.get("avg_similarity") is not None]
    return {
        "status": "ok",
        "matches": match_count,
        "candidate_matches": candidate_count,
        "avg_similarity": round(sum(sims) / len(sims), 1) if sims else None,
        "horizons": horizons,
        "lookback_years": LOOKBACK_YEARS,
        "min_similarity": MIN_SIMILARITY,
    }


def analyze_all_indicator_patterns(hist_df: pd.DataFrame, indicators: Dict[str, pd.Series]) -> Dict[str, Dict]:
    keys = ("ma", "bollinger", "rsi", "stochastic", "ichimoku", "macd", "adx", "atr", "obv", "mfi", "vwap", "volume")
    results = {key: analyze_indicator_pattern(hist_df, indicators, key) for key in keys}
    results["_market_condition"] = classify_current_condition(hist_df, indicators)
    return results
