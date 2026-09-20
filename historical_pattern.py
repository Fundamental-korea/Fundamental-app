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
    "williams_r": {"williams_r": 12.0, "williams_r_change5": 15.0, "return20": 0.12},
    "cci": {"cci": 80.0, "cci_change5": 100.0, "return20": 0.12},
    "roc": {"roc": 8.0, "roc_change5": 8.0, "return20": 0.12},
    "psar": {"psar_gap": 0.06, "psar_gap_change5": 0.08, "return20": 0.12},
    "cmf": {"cmf": 0.20, "cmf_change5": 0.20, "return20": 0.12},
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

    elif key == "williams_r":
        williams_r = pd.to_numeric(ind["williams_r"], errors="coerce")
        out["williams_r"] = williams_r
        out["williams_r_change5"] = williams_r - williams_r.shift(5)
        out["return20"] = _ret(close, 20)

    elif key == "cci":
        cci = pd.to_numeric(ind["cci"], errors="coerce")
        out["cci"] = cci
        out["cci_change5"] = cci - cci.shift(5)
        out["return20"] = _ret(close, 20)

    elif key == "roc":
        roc = pd.to_numeric(ind["roc"], errors="coerce")
        out["roc"] = roc
        out["roc_change5"] = roc - roc.shift(5)
        out["return20"] = _ret(close, 20)

    elif key == "psar":
        psar = pd.to_numeric(ind["psar"], errors="coerce")
        nz = close.replace(0, pd.NA)
        out["psar_gap"] = (close - psar) / nz
        out["psar_gap_change5"] = out["psar_gap"] - out["psar_gap"].shift(5)
        out["return20"] = _ret(close, 20)

    elif key == "cmf":
        cmf = pd.to_numeric(ind["cmf"], errors="coerce")
        out["cmf"] = cmf
        out["cmf_change5"] = cmf - cmf.shift(5)
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
    """Classify the latest daily technical state using the full indicator set.

    This is a presentation/context classifier, not a forecast.  Overbought/
    oversold is intentionally limited to oscillators and price-extreme signals;
    trend, momentum and money-flow indicators contribute separate context.
    """
    if hist_df is None or hist_df.empty or "Close" not in hist_df.columns:
        return {
            "state": "unknown",
            "label": "데이터 없음",
            "context": "데이터 없음",
            "primary_direction": "up",
            "signals": [],
            "trend_signals": [],
            "momentum_signals": [],
            "flow_signals": [],
        }

    close = _safe_float(pd.to_numeric(hist_df["Close"], errors="coerce").iloc[-1])

    def latest(key):
        return _latest_series_value(indicators, key)

    sma20, sma60, sma120 = latest("sma20"), latest("sma60"), latest("sma120")
    rsi, stoch_k, stoch_d = latest("rsi14"), latest("stoch_k"), latest("stoch_d")
    bb_upper, bb_lower = latest("bb_upper"), latest("bb_lower")
    adx, plus_di, minus_di = latest("adx14"), latest("plus_di14"), latest("minus_di14")
    mfi, rolling_vwap = latest("mfi14"), latest("rolling_vwap20")
    williams_r, cci, roc = latest("williams_r"), latest("cci"), latest("roc")
    psar, cmf = latest("psar"), latest("cmf")

    signals, trend_signals, momentum_signals, flow_signals = [], [], [], []
    overbought_count = oversold_count = 0

    def oscillator(value, high, low, high_label, low_label):
        nonlocal overbought_count, oversold_count
        if value is None:
            return
        if value >= high:
            overbought_count += 1
            signals.append(high_label)
        elif value <= low:
            oversold_count += 1
            signals.append(low_label)

    oscillator(rsi, 70.0, 30.0, "RSI 과매수", "RSI 과매도")
    oscillator(stoch_k, 80.0, 20.0, "스토캐스틱 과매수", "스토캐스틱 과매도")
    oscillator(mfi, 80.0, 20.0, "MFI 과매수", "MFI 과매도")
    oscillator(williams_r, -20.0, -80.0, "Williams %R 과매수", "Williams %R 과매도")
    oscillator(cci, 100.0, -100.0, "CCI 과매수", "CCI 과매도")

    bb_percent_b = None
    if close is not None and bb_upper is not None and bb_lower is not None and bb_upper != bb_lower:
        bb_percent_b = (close - bb_lower) / (bb_upper - bb_lower)
        if bb_percent_b >= 1.0:
            overbought_count += 1
            signals.append("볼린저 상단 돌파")
        elif bb_percent_b <= 0.0:
            oversold_count += 1
            signals.append("볼린저 하단 이탈")

    ma_gap = None
    if close is not None and sma20 not in (None, 0):
        ma_gap = close / sma20 - 1.0
        if ma_gap >= 0.08:
            overbought_count += 1
            signals.append(f"20일선 대비 +{ma_gap * 100:.1f}%")
        elif ma_gap <= -0.08:
            oversold_count += 1
            signals.append(f"20일선 대비 {ma_gap * 100:.1f}%")

    # Trend context: MA structure + ADX/DMI + Ichimoku + PSAR.
    if close is not None and sma20 is not None and sma60 is not None:
        if close > sma20 > sma60:
            trend = "uptrend"
            trend_signals.append("가격 > 20일선 > 60일선")
        elif close < sma20 < sma60:
            trend = "downtrend"
            trend_signals.append("가격 < 20일선 < 60일선")
        else:
            trend = "mixed"
    else:
        trend = "unknown"

    if adx is not None and plus_di is not None and minus_di is not None and adx >= 25:
        if plus_di > minus_di:
            trend_signals.append("ADX 강한 상승 +DI 우위")
        elif minus_di > plus_di:
            trend_signals.append("ADX 강한 하락 -DI 우위")

    cloud_a, cloud_b = latest("senkou_a"), latest("senkou_b")
    if close is not None and cloud_a is not None and cloud_b is not None:
        cloud_top, cloud_bottom = max(cloud_a, cloud_b), min(cloud_a, cloud_b)
        if close > cloud_top:
            trend_signals.append("일목균형표 구름 위")
        elif close < cloud_bottom:
            trend_signals.append("일목균형표 구름 아래")

    if close is not None and psar is not None:
        if close > psar:
            trend_signals.append("PSAR 상승 방향")
        elif close < psar:
            trend_signals.append("PSAR 하락 방향")

    if close is not None and rolling_vwap is not None:
        if close > rolling_vwap:
            trend_signals.append("VWAP 위")
        elif close < rolling_vwap:
            trend_signals.append("VWAP 아래")

    # Momentum context: RSI/Stochastic direction, MACD, ROC.
    momentum_bearish = momentum_bullish = 0
    rsi_series = indicators.get("rsi14")
    if rsi_series is not None and len(rsi_series) > 5:
        change = _safe_float(rsi_series.iloc[-1] - rsi_series.iloc[-6])
        if change is not None:
            if change >= 3:
                momentum_bullish += 1
                momentum_signals.append("RSI 5일 상승")
            elif change <= -3:
                momentum_bearish += 1
                momentum_signals.append("RSI 5일 둔화")

    if stoch_k is not None and stoch_d is not None:
        if stoch_k > stoch_d:
            momentum_bullish += 1
            momentum_signals.append("스토캐스틱 K>D")
        elif stoch_k < stoch_d:
            momentum_bearish += 1
            momentum_signals.append("스토캐스틱 K<D")

    macd_hist = indicators.get("macd_hist")
    if macd_hist is not None and len(macd_hist) > 5:
        hist_now = _safe_float(macd_hist.iloc[-1])
        hist_change = _safe_float(macd_hist.iloc[-1] - macd_hist.iloc[-6])
        if hist_now is not None:
            if hist_now > 0:
                momentum_bullish += 1
                momentum_signals.append("MACD 히스토그램 양수")
            else:
                momentum_bearish += 1
                momentum_signals.append("MACD 히스토그램 음수")
        if hist_change is not None:
            if hist_change > 0:
                momentum_bullish += 1
            elif hist_change < 0:
                momentum_bearish += 1

    if roc is not None:
        if roc > 0:
            momentum_bullish += 1
            momentum_signals.append(f"ROC +{roc:.1f}%")
        elif roc < 0:
            momentum_bearish += 1
            momentum_signals.append(f"ROC {roc:.1f}%")

    momentum = "strengthening" if momentum_bullish >= momentum_bearish + 2 else (
        "weakening" if momentum_bearish >= momentum_bullish + 2 else "mixed"
    )

    # Money-flow context: CMF and OBV.
    obv = indicators.get("obv")
    if cmf is not None:
        if cmf > 0.05:
            flow_signals.append(f"CMF 자금 유입 ({cmf:+.2f})")
        elif cmf < -0.05:
            flow_signals.append(f"CMF 자금 유출 ({cmf:+.2f})")
    if obv is not None and len(obv) > 5:
        obv_change = _safe_float(obv.iloc[-1] - obv.iloc[-6])
        if obv_change is not None:
            if obv_change > 0:
                flow_signals.append("OBV 5일 증가")
            elif obv_change < 0:
                flow_signals.append("OBV 5일 감소")

    if overbought_count >= 2 and oversold_count == 0:
        state = "overbought_strong" if overbought_count >= 3 else "overbought"
        primary_direction = "down"
    elif oversold_count >= 2 and overbought_count == 0:
        state = "oversold_strong" if oversold_count >= 3 else "oversold"
        primary_direction = "up"
    else:
        state = "neutral"
        primary_direction = "up"

    if state.startswith("overbought") and momentum == "weakening":
        context = "과매수 + 모멘텀 둔화"
    elif state.startswith("oversold") and momentum == "weakening":
        context = "과매도 + 모멘텀 둔화"
    elif state.startswith("overbought") and trend == "uptrend":
        context = "과매수 + 상승추세"
    elif state.startswith("oversold") and trend == "downtrend":
        context = "과매도 + 하락추세"
    elif trend == "uptrend" and momentum == "strengthening":
        context = "상승추세 + 모멘텀 강화"
    elif trend == "downtrend" and momentum == "weakening":
        context = "하락추세 + 모멘텀 둔화"
    elif trend == "uptrend":
        context = "상승추세"
    elif trend == "downtrend":
        context = "하락추세"
    elif momentum == "strengthening":
        context = "상승 모멘텀"
    elif momentum == "weakening":
        context = "하락 모멘텀"
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
        "trend_signals": trend_signals,
        "momentum": momentum,
        "momentum_signals": momentum_signals,
        "flow_signals": flow_signals,
        "signals": signals,
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di,
        "mfi": mfi,
        "rolling_vwap": rolling_vwap,
        "williams_r": williams_r,
        "cci": cci,
        "roc": roc,
        "psar": psar,
        "cmf": cmf,
        "rsi": rsi,
        "stoch_k": stoch_k,
        "bb_percent_b": round(bb_percent_b, 3) if bb_percent_b is not None else None,
        "ma_gap": ma_gap,
    }

def analyze_indicator_pattern(hist_df: pd.DataFrame, indicators: Dict[str, pd.Series], indicator_key: str) -> Dict:
    if hist_df is None or hist_df.empty or "Close" not in hist_df.columns:
        return {"status": "no_data", "matches": 0, "horizons": {}}
    features = _features(hist_df, indicators, indicator_key)
    data_bars = len(features)
    minimum_bars = 120
    outcome_required_bars = max(HORIZONS)
    if data_bars < minimum_bars:
        return {
            "status": "insufficient_data",
            "matches": 0,
            "candidate_matches": 0,
            "horizons": {},
            "min_required": MIN_MATCHES,
            "lookback_years": LOOKBACK_YEARS,
            "min_similarity": MIN_SIMILARITY,
            "data_bars": data_bars,
            "required_bars": minimum_bars,
            "message": f"과거 일봉 데이터가 부족합니다 ({data_bars}봉 / 최소 {minimum_bars}봉).",
        }

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
            "data_bars": data_bars,
            "required_bars": minimum_bars,
            "message": (
                f"과거 데이터는 충분하지만 현재 조건과 유사한 사례가 "
                f"{match_count}회로 최소 {MIN_MATCHES}회에 미달합니다."
            ),
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
    keys = ("ma", "bollinger", "rsi", "stochastic", "ichimoku", "macd", "adx", "atr", "obv", "mfi", "vwap", "williams_r", "cci", "roc", "psar", "cmf", "volume")
    results = {key: analyze_indicator_pattern(hist_df, indicators, key) for key in keys}
    results["_market_condition"] = classify_current_condition(hist_df, indicators)
    return results
