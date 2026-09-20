# TECHNICAL_INDICATORS_V2 = 2026-09-20
# chart_indicators.py
# 초보자를 위한 기술적 지표 계산 + 자동 해설 생성 모듈.
# 외부 의존성 없음 - yfinance가 이미 주는 OHLCV(pandas)만으로 전부 계산.
# 기술적 지표: MA / Bollinger / RSI / MACD / Volume / ADX-DMI / ATR / OBV / MFI / Rolling VWAP

import pandas as pd


# --------------------------------------------------------------------------
# 지표 계산
# --------------------------------------------------------------------------

def compute_sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def compute_bollinger(series: pd.Series, window: int = 20, num_std: float = 2.0):
    mid = compute_sma(series, window)
    std = series.rolling(window=window, min_periods=window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def compute_rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)  # 초반 데이터 부족 구간은 중립값(50)으로 채움


def compute_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_volume_ma(volume: pd.Series, window: int = 20) -> pd.Series:
    return volume.rolling(window=window, min_periods=window).mean()


def compute_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
):
    """Average Directional Index (ADX) + Directional Indicators (+DI/-DI).

    Uses Wilder-style exponential smoothing (alpha=1/window), which is also
    consistent with the smoothing approach already used by this module's RSI.
    ADX measures trend strength; +DI/-DI provide directional context.
    """
    high = pd.to_numeric(high, errors="coerce")
    low = pd.to_numeric(low, errors="coerce")
    close = pd.to_numeric(close, errors="coerce")

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(0.0, index=high.index)
    minus_dm = pd.Series(0.0, index=high.index)
    plus_dm.loc[(up_move > down_move) & (up_move > 0)] = up_move.loc[
        (up_move > down_move) & (up_move > 0)
    ]
    minus_dm.loc[(down_move > up_move) & (down_move > 0)] = down_move.loc[
        (down_move > up_move) & (down_move > 0)
    ]

    true_range = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    alpha = 1 / window
    atr = true_range.ewm(alpha=alpha, min_periods=window, adjust=False).mean()
    plus_dm_smoothed = plus_dm.ewm(alpha=alpha, min_periods=window, adjust=False).mean()
    minus_dm_smoothed = minus_dm.ewm(alpha=alpha, min_periods=window, adjust=False).mean()

    plus_di = 100 * plus_dm_smoothed / atr.replace(0, pd.NA)
    minus_di = 100 * minus_dm_smoothed / atr.replace(0, pd.NA)

    di_sum = (plus_di + minus_di).replace(0, pd.NA)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx = dx.ewm(alpha=alpha, min_periods=window, adjust=False).mean()

    return adx, plus_di, minus_di



def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
) -> pd.Series:
    """Average True Range. Wilder-style smoothing으로 변동성의 절대 크기를 계산."""
    high = pd.to_numeric(high, errors="coerce")
    low = pd.to_numeric(low, errors="coerce")
    close = pd.to_numeric(close, errors="coerce")
    true_range = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(
        alpha=1 / window,
        min_periods=window,
        adjust=False,
    ).mean()


def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume. 가격 방향에 따라 거래량을 누적."""
    close = pd.to_numeric(close, errors="coerce")
    volume = pd.to_numeric(volume, errors="coerce").fillna(0.0)
    direction = close.diff()
    signed_volume = volume.where(direction > 0, -volume.where(direction < 0, 0.0))
    signed_volume.iloc[0] = 0.0
    return signed_volume.fillna(0.0).cumsum()


def compute_mfi(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    window: int = 14,
) -> pd.Series:
    """Money Flow Index. 전형가격과 거래량을 함께 사용하는 0~100 모멘텀 지표."""
    high = pd.to_numeric(high, errors="coerce")
    low = pd.to_numeric(low, errors="coerce")
    close = pd.to_numeric(close, errors="coerce")
    volume = pd.to_numeric(volume, errors="coerce").fillna(0.0)

    typical_price = (high + low + close) / 3.0
    raw_money_flow = typical_price * volume
    direction = typical_price.diff()

    positive_flow = raw_money_flow.where(direction > 0, 0.0)
    negative_flow = raw_money_flow.where(direction < 0, 0.0)

    positive_sum = positive_flow.rolling(window=window, min_periods=window).sum()
    negative_sum = negative_flow.rolling(window=window, min_periods=window).sum()

    ratio = positive_sum / negative_sum.replace(0, pd.NA)
    mfi = 100 - (100 / (1 + ratio))

    no_negative_flow = negative_sum.eq(0) & positive_sum.gt(0)
    no_positive_flow = positive_sum.eq(0) & negative_sum.gt(0)
    no_flow = positive_sum.eq(0) & negative_sum.eq(0)

    mfi = mfi.mask(no_negative_flow, 100.0)
    mfi = mfi.mask(no_positive_flow, 0.0)
    mfi = mfi.mask(no_flow, 50.0)
    return mfi


def compute_rolling_vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    window: int = 20,
) -> pd.Series:
    """Rolling VWAP. 일/주/월봉에서도 동일하게 비교할 수 있도록 최근 N개 봉 기준."""
    high = pd.to_numeric(high, errors="coerce")
    low = pd.to_numeric(low, errors="coerce")
    close = pd.to_numeric(close, errors="coerce")
    volume = pd.to_numeric(volume, errors="coerce").fillna(0.0)

    typical_price = (high + low + close) / 3.0
    pv = typical_price * volume
    pv_sum = pv.rolling(window=window, min_periods=window).sum()
    vol_sum = volume.rolling(window=window, min_periods=window).sum()
    return pv_sum / vol_sum.replace(0, pd.NA)


def compute_stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                        k_window: int = 14, d_window: int = 3, smooth_k: int = 3):
    """스토캐스틱 슬로우(Slow Stochastic) - 국내 HTS 기본값(14,3,3)과 동일."""
    lowest_low = low.rolling(window=k_window, min_periods=k_window).min()
    highest_high = high.rolling(window=k_window, min_periods=k_window).max()
    raw_k = (close - lowest_low) / (highest_high - lowest_low).replace(0, pd.NA) * 100
    k = raw_k.rolling(window=smooth_k, min_periods=smooth_k).mean()
    d = k.rolling(window=d_window, min_periods=d_window).mean()
    return k, d


def compute_ichimoku(high: pd.Series, low: pd.Series, close: pd.Series,
                      tenkan_window: int = 9, kijun_window: int = 26, senkou_b_window: int = 52):
    """일목균형표. 국내에서 흔히 쓰는 기본값(9,26,52)을 그대로 사용.
    선행스팬(구름)은 kijun_window만큼 '미래'로 밀어서 그리는 게 원래 정의라 shift(+)를 씀 -
    그래서 마지막 kijun_window개 지점엔 구름이 아직 안 그려진 것처럼 보이는 게 정상."""
    tenkan = (high.rolling(tenkan_window, min_periods=tenkan_window).max()
              + low.rolling(tenkan_window, min_periods=tenkan_window).min()) / 2
    kijun = (high.rolling(kijun_window, min_periods=kijun_window).max()
             + low.rolling(kijun_window, min_periods=kijun_window).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(kijun_window)
    senkou_b = ((high.rolling(senkou_b_window, min_periods=senkou_b_window).max()
                 + low.rolling(senkou_b_window, min_periods=senkou_b_window).min()) / 2).shift(kijun_window)
    chikou = close.shift(-kijun_window)
    return tenkan, kijun, senkou_a, senkou_b, chikou


# --------------------------------------------------------------------------
# 파라미터 커스터마이징 지원 - 사용자가 기간 등을 직접 조절할 수 있도록
# --------------------------------------------------------------------------

DEFAULT_PARAMS = {
    "sma_tiny": 5, "sma_short": 20, "sma_mid": 60, "sma_long": 120,
    "bb_window": 20, "bb_std": 2.0,
    "rsi_window": 14,
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    "vol_ma_window": 20,
    "adx_window": 14,
    "atr_window": 14,
    "mfi_window": 14,
    "vwap_window": 20,
    "stoch_k": 14, "stoch_d": 3, "stoch_smooth": 3,
    "ichimoku_tenkan": 9, "ichimoku_kijun": 26, "ichimoku_senkou_b": 52,
}


def compute_all_indicators(hist_df: pd.DataFrame, params: dict = None) -> dict:
    """hist_df: 'Open','High','Low','Close','Volume' 컬럼을 가진 OHLCV DataFrame (yfinance 포맷).
    params를 안 넘기면 DEFAULT_PARAMS(업계 표준값) 그대로 사용 - 기존 호출부는 그대로 동작함."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    close = hist_df["Close"]
    high = hist_df["High"]
    low = hist_df["Low"]
    volume = hist_df["Volume"]

    sma5 = compute_sma(close, p["sma_tiny"])
    sma20 = compute_sma(close, p["sma_short"])
    sma60 = compute_sma(close, p["sma_mid"])
    sma120 = compute_sma(close, p["sma_long"])
    bb_upper, bb_mid, bb_lower = compute_bollinger(close, p["bb_window"], p["bb_std"])
    rsi14 = compute_rsi(close, p["rsi_window"])
    macd_line, macd_signal, macd_hist = compute_macd(close, p["macd_fast"], p["macd_slow"], p["macd_signal"])
    vol_ma20 = compute_volume_ma(volume, p["vol_ma_window"])
    adx14, plus_di14, minus_di14 = compute_adx(high, low, close, p["adx_window"])
    atr14 = compute_atr(high, low, close, p["atr_window"])
    obv = compute_obv(close, volume)
    mfi14 = compute_mfi(high, low, close, volume, p["mfi_window"])
    rolling_vwap20 = compute_rolling_vwap(high, low, close, volume, p["vwap_window"])
    stoch_k, stoch_d = compute_stochastic(high, low, close, p["stoch_k"], p["stoch_d"], p["stoch_smooth"])
    tenkan, kijun, senkou_a, senkou_b, chikou = compute_ichimoku(
        high, low, close, p["ichimoku_tenkan"], p["ichimoku_kijun"], p["ichimoku_senkou_b"]
    )

    return {
        "sma5": sma5, "sma20": sma20, "sma60": sma60, "sma120": sma120,
        "bb_upper": bb_upper, "bb_mid": bb_mid, "bb_lower": bb_lower,
        "rsi14": rsi14,
        "macd_line": macd_line, "macd_signal": macd_signal, "macd_hist": macd_hist,
        "vol_ma20": vol_ma20,
        "adx14": adx14, "plus_di14": plus_di14, "minus_di14": minus_di14,
        "atr14": atr14, "obv": obv, "mfi14": mfi14, "rolling_vwap20": rolling_vwap20,
        "stoch_k": stoch_k, "stoch_d": stoch_d,
        "tenkan": tenkan, "kijun": kijun, "senkou_a": senkou_a, "senkou_b": senkou_b, "chikou": chikou,
        "params": p,  # UI 라벨링용(예: f"SMA{p['sma_short']}") - 커스텀 기간 반영해서 표시하려고 같이 반환
    }


# --------------------------------------------------------------------------
# 초보자용 자동 해설 - "지금 이 종목 기준으로" 실제 숫자를 넣어 문장 생성
# --------------------------------------------------------------------------

def _fmt(v, nd=0):
    if v is None or pd.isna(v):
        return "N/A"
    return f"{v:,.{nd}f}"


def generate_ma_commentary(close: pd.Series, sma20: pd.Series, sma60: pd.Series, sma120: pd.Series) -> str:
    price = close.iloc[-1]
    s20, s60, s120 = sma20.iloc[-1], sma60.iloc[-1], sma120.iloc[-1]
    if pd.isna(s120):
        return "데이터가 아직 충분히 쌓이지 않아 120일선은 계산되지 않았어요. 60일선까지만 참고해주세요."

    if s20 > s60 > s120:
        arrangement = "정배열(단기>중기>장기)로, 상승 추세가 이어지고 있다는 신호예요."
    elif s20 < s60 < s120:
        arrangement = "역배열(단기<중기<장기)로, 하락 추세가 이어지고 있다는 신호예요."
    else:
        arrangement = "이동평균선들이 서로 얽혀있어 뚜렷한 추세 없이 방향을 탐색하는 구간으로 보여요."

    if price > s20:
        pos = f"현재가({_fmt(price)})가 20일선({_fmt(s20)}) 위에 있어 단기적으로는 강세예요."
    else:
        pos = f"현재가({_fmt(price)})가 20일선({_fmt(s20)}) 아래에 있어 단기적으로는 약세예요."

    return f"{arrangement} {pos}"


def generate_bollinger_commentary(close: pd.Series, bb_upper: pd.Series, bb_mid: pd.Series, bb_lower: pd.Series) -> str:
    price = close.iloc[-1]
    upper, mid, lower = bb_upper.iloc[-1], bb_mid.iloc[-1], bb_lower.iloc[-1]
    if pd.isna(upper):
        return "데이터가 아직 충분히 쌓이지 않아 볼린저 밴드를 계산할 수 없어요."

    band_width = upper - lower
    if band_width <= 0:
        return "변동성이 거의 없어 밴드 폭이 매우 좁은 상태예요."

    position_pct = (price - lower) / band_width * 100

    if position_pct >= 90:
        zone = (f"상단 밴드({_fmt(upper)}) 근처에 붙어있어 단기 과매수 구간으로 볼 수 있어요. "
                "추세가 강하면 계속 상단을 타고 오를 수도 있지만, 단기 조정 가능성도 함께 염두에 두세요.")
    elif position_pct <= 10:
        zone = (f"하단 밴드({_fmt(lower)}) 근처에 붙어있어 단기 과매도 구간으로 볼 수 있어요. "
                "반등 가능성이 있지만, 하락 추세가 강하면 더 내려갈 수도 있어요.")
    else:
        zone = f"밴드 중간({_fmt(mid)}) 부근에서 움직이고 있어 뚜렷한 과열·침체 신호는 없는 상태예요."

    return f"현재가는 밴드 내에서 {position_pct:.0f}% 위치에 있어요. {zone}"


def generate_rsi_commentary(rsi14: pd.Series) -> str:
    value = rsi14.iloc[-1]
    if pd.isna(value):
        return "데이터가 아직 충분히 쌓이지 않아 RSI를 계산할 수 없어요."

    if value >= 70:
        return f"현재 RSI는 {value:.1f}로 70을 넘어 과매수 구간이에요. 최근 상승폭이 컸다는 뜻이라, 단기 조정 가능성을 염두에 둘 만해요."
    elif value <= 30:
        return f"현재 RSI는 {value:.1f}로 30 아래인 과매도 구간이에요. 최근 하락폭이 컸다는 뜻이라, 단기 반등 가능성을 염두에 둘 만해요."
    else:
        return f"현재 RSI는 {value:.1f}로 30~70 사이 중립 구간이에요. 뚜렷한 과열이나 침체 신호는 없는 상태예요."


def generate_macd_commentary(macd_line: pd.Series, macd_signal: pd.Series, macd_hist: pd.Series) -> str:
    if len(macd_line) < 2 or pd.isna(macd_line.iloc[-1]) or pd.isna(macd_signal.iloc[-1]):
        return "데이터가 아직 충분히 쌓이지 않아 MACD를 계산할 수 없어요."

    line, sig = macd_line.iloc[-1], macd_signal.iloc[-1]
    prev_line, prev_sig = macd_line.iloc[-2], macd_signal.iloc[-2]

    crossed_up = prev_line <= prev_sig and line > sig
    crossed_down = prev_line >= prev_sig and line < sig

    if crossed_up:
        return ("바로 직전에 MACD선이 시그널선을 아래에서 위로 뚫고 올라가는 '골든크로스'가 발생했어요 — "
                "상승 전환 신호로 해석하는 경우가 많아요.")
    elif crossed_down:
        return ("바로 직전에 MACD선이 시그널선을 위에서 아래로 뚫고 내려가는 '데드크로스'가 발생했어요 — "
                "하락 전환 신호로 해석하는 경우가 많아요.")
    elif line > sig:
        return "MACD선이 시그널선 위에 있어 상승 모멘텀이 유지되고 있는 상태예요."
    else:
        return "MACD선이 시그널선 아래에 있어 하락 모멘텀이 유지되고 있는 상태예요."


def generate_volume_commentary(volume: pd.Series, vol_ma20: pd.Series) -> str:
    v, v_ma = volume.iloc[-1], vol_ma20.iloc[-1]
    if pd.isna(v_ma) or v_ma == 0:
        return "데이터가 아직 충분히 쌓이지 않아 거래량 평균을 계산할 수 없어요."

    ratio = v / v_ma
    if ratio >= 2.0:
        return f"오늘 거래량이 최근 20일 평균의 {ratio:.1f}배로, 평소보다 훨씬 활발하게 거래되고 있어요. 가격 움직임에 대한 시장의 관심이 크다는 신호예요."
    elif ratio <= 0.5:
        return f"오늘 거래량이 최근 20일 평균의 {ratio:.1f}배로, 평소보다 한산한 편이에요. 지금 가격 움직임은 신뢰도가 다소 낮을 수 있어요."
    else:
        return f"오늘 거래량은 최근 20일 평균과 비슷한 수준({ratio:.1f}배)이에요. 특별히 튀는 수급 신호는 없는 상태예요."


def generate_adx_commentary(
    adx: pd.Series,
    plus_di: pd.Series,
    minus_di: pd.Series,
) -> str:
    if any(
        len(series) == 0 or pd.isna(series.iloc[-1])
        for series in (adx, plus_di, minus_di)
    ):
        return "데이터가 아직 충분히 쌓이지 않아 ADX를 계산할 수 없어요."

    adx_value = float(adx.iloc[-1])
    plus_value = float(plus_di.iloc[-1])
    minus_value = float(minus_di.iloc[-1])

    if adx_value >= 40:
        strength = "매우 강한 추세"
    elif adx_value >= 25:
        strength = "뚜렷한 추세"
    elif adx_value >= 20:
        strength = "추세가 형성되는 구간"
    else:
        strength = "약한 추세 또는 횡보에 가까운 구간"

    if plus_value > minus_value:
        direction = f"+DI {plus_value:.1f}가 -DI {minus_value:.1f}보다 높아 상승 방향성이 우세해요."
    elif minus_value > plus_value:
        direction = f"-DI {minus_value:.1f}가 +DI {plus_value:.1f}보다 높아 하락 방향성이 우세해요."
    else:
        direction = f"+DI와 -DI가 {plus_value:.1f}로 비슷해 방향성이 팽팽해요."

    return f"현재 ADX는 {adx_value:.1f}로 {strength}예요. {direction} ADX는 방향 자체보다 추세의 힘을 보는 지표라 RSI나 MACD 같은 모멘텀 지표와 함께 확인하는 게 좋아요."



def generate_atr_commentary(atr14: pd.Series, close: pd.Series) -> str:
    if len(atr14) == 0 or len(close) == 0 or pd.isna(atr14.iloc[-1]) or pd.isna(close.iloc[-1]) or close.iloc[-1] == 0:
        return "데이터가 아직 충분히 쌓이지 않아 ATR을 계산할 수 없어요."

    atr = float(atr14.iloc[-1])
    price = float(close.iloc[-1])
    atr_pct = atr / price * 100.0

    if atr_pct >= 8:
        level = "매우 큰"
    elif atr_pct >= 5:
        level = "큰"
    elif atr_pct >= 2:
        level = "보통 수준의"
    else:
        level = "작은"

    return (
        f"현재 ATR(14)은 {_fmt(atr, 2)}로 현재가의 약 {atr_pct:.1f}% 수준이에요. "
        f"최근 하루 가격 변동폭을 감안하면 {level} 변동성이 나타나는 구간이에요. "
        "ATR은 상승·하락 방향보다 움직임의 크기를 보는 지표라 방향성 지표와 함께 확인하는 게 좋아요."
    )


def generate_obv_commentary(obv: pd.Series) -> str:
    if len(obv) < 6 or pd.isna(obv.iloc[-1]) or pd.isna(obv.iloc[-6]):
        return "데이터가 아직 충분히 쌓이지 않아 OBV 흐름을 계산할 수 없어요."

    current = float(obv.iloc[-1])
    prev5 = float(obv.iloc[-6])
    change = current - prev5

    if change > 0:
        direction = "최근 5기간 동안 상승"
        note = "가격 상승과 함께 움직이면 상승 흐름을 뒷받침하는 거래량 흐름으로 해석할 수 있어요."
    elif change < 0:
        direction = "최근 5기간 동안 하락"
        note = "가격이 오르는데 OBV가 약해진다면 거래량 측면의 다이버전스를 확인해볼 만해요."
    else:
        direction = "최근 5기간 동안 거의 보합"
        note = "가격 방향과 함께 보면 거래량이 추세를 얼마나 뒷받침하는지 확인할 수 있어요."

    return f"현재 OBV는 {current:,.0f}이고 {direction}이에요. {note}"


def generate_mfi_commentary(mfi14: pd.Series) -> str:
    if len(mfi14) == 0 or pd.isna(mfi14.iloc[-1]):
        return "데이터가 아직 충분히 쌓이지 않아 MFI를 계산할 수 없어요."

    value = float(mfi14.iloc[-1])
    if value >= 80:
        zone = "80 이상 과매수 구간"
    elif value <= 20:
        zone = "20 이하 과매도 구간"
    else:
        zone = "20~80 사이 중립 구간"

    return (
        f"현재 MFI(14)는 {value:.1f}으로 {zone}이에요. "
        "RSI와 비슷하지만 거래량까지 반영해서 가격 움직임에 거래량이 얼마나 동반되는지 함께 살펴볼 수 있어요."
    )


def generate_vwap_commentary(close: pd.Series, rolling_vwap20: pd.Series) -> str:
    if len(close) == 0 or len(rolling_vwap20) == 0 or pd.isna(close.iloc[-1]) or pd.isna(rolling_vwap20.iloc[-1]) or rolling_vwap20.iloc[-1] == 0:
        return "데이터가 아직 충분히 쌓이지 않아 Rolling VWAP을 계산할 수 없어요."

    price = float(close.iloc[-1])
    vwap = float(rolling_vwap20.iloc[-1])
    gap = (price / vwap - 1.0) * 100.0

    if gap >= 3:
        position = "거래량가중 평균가보다 꽤 높은"
    elif gap >= 0:
        position = "거래량가중 평균가보다 높은"
    elif gap <= -3:
        position = "거래량가중 평균가보다 꽤 낮은"
    else:
        position = "거래량가중 평균가보다 낮은"

    return (
        f"현재가는 Rolling VWAP(20) {_fmt(vwap, 2)} 대비 {gap:+.1f}%로, "
        f"{position} 위치에 있어요. 일봉에서는 최근 20개 봉의 거래량을 반영한 평균가격으로 해석하면 됩니다."
    )


def generate_stochastic_commentary(stoch_k: pd.Series, stoch_d: pd.Series) -> str:
    if pd.isna(stoch_k.iloc[-1]) or pd.isna(stoch_d.iloc[-1]):
        return "데이터가 아직 충분히 쌓이지 않아 스토캐스틱을 계산할 수 없어요."

    k, d = stoch_k.iloc[-1], stoch_d.iloc[-1]
    if k >= 80:
        zone = f"%K가 {k:.1f}로 80 이상인 과매수 구간이에요."
    elif k <= 20:
        zone = f"%K가 {k:.1f}로 20 이하인 과매도 구간이에요."
    else:
        zone = f"%K가 {k:.1f}로 20~80 사이 중립 구간이에요."

    momentum = "%K가 %D 위에 있어 단기 상승 모멘텀이 우세해요." if k > d else "%K가 %D 아래에 있어 단기 하락 모멘텀이 우세해요."
    return f"{zone} {momentum} RSI보다 더 민감하게 반응해서, 짧은 스윙 타이밍을 잡는 데 자주 쓰여요."


def generate_ichimoku_commentary(close: pd.Series, tenkan: pd.Series, kijun: pd.Series,
                                  senkou_a: pd.Series, senkou_b: pd.Series) -> str:
    price = close.iloc[-1]
    t, k = tenkan.iloc[-1], kijun.iloc[-1]
    span_a, span_b = senkou_a.iloc[-1], senkou_b.iloc[-1]
    if pd.isna(t) or pd.isna(k) or pd.isna(span_a) or pd.isna(span_b):
        return "데이터가 아직 충분히 쌓이지 않아 일목균형표를 계산할 수 없어요 (구름을 그리려면 최소 52일 이상의 데이터가 필요해요)."

    cloud_top, cloud_bottom = max(span_a, span_b), min(span_a, span_b)
    if price > cloud_top:
        cloud_note = "현재가가 구름대 위에 있어 추세가 상승 국면으로 해석돼요."
    elif price < cloud_bottom:
        cloud_note = "현재가가 구름대 아래에 있어 추세가 하락 국면으로 해석돼요."
    else:
        cloud_note = "현재가가 구름대 안에 있어 방향을 못 정한 혼조 구간으로 해석돼요."

    tenkan_note = "전환선이 기준선 위에 있어 단기 흐름이 긍정적이에요." if t > k else "전환선이 기준선 아래에 있어 단기 흐름이 부정적이에요."
    return f"{cloud_note} {tenkan_note}"


# --------------------------------------------------------------------------
# 강의형 고정 콘텐츠 - 지표별 개념/계산법/실전활용/흔한 실수 (종목과 무관한 일반 설명)
# 자동 해설(generate_*_commentary)과 합쳐서 "지금 이 종목" 맥락까지 완성됨
# --------------------------------------------------------------------------

INDICATOR_LESSONS = {
    "atr": {
        "concept": "ATR은 최근 가격이 하루(한 봉) 기준으로 얼마나 크게 움직였는지를 보여주는 변동성 지표예요. 방향은 알려주지 않고 움직임의 크기를 보여줘요.",
        "calculation": "고가-저가, 전일 종가와 고가의 차이, 전일 종가와 저가의 차이 중 가장 큰 값을 True Range로 구한 뒤 Wilder 방식으로 평활화합니다.",
        "how_to_use": "ATR이 높아지면 최근 가격 움직임이 커졌다는 뜻이고, 낮아지면 조용한 구간에 가까워요. ATR을 현재가로 나눈 ATR%를 같이 보면 가격 수준이 다른 종목끼리 변동성을 비교하기 쉬워요.",
        "common_mistakes": "ATR이 높다고 상승한다는 뜻은 아니에요. 급등과 급락 모두 ATR을 높일 수 있으므로 방향은 MA, ADX, MACD 같은 다른 지표로 따로 확인해야 해요.",
    },
    "obv": {
        "concept": "OBV는 가격이 오른 날의 거래량은 더하고 내린 날의 거래량은 빼서 누적한 값이에요. 거래량이 가격 움직임을 얼마나 뒷받침하는지 살펴보는 데 쓰여요.",
        "calculation": "종가가 전 봉보다 오르면 해당 봉 거래량을 OBV에 더하고, 내리면 빼고, 같으면 그대로 유지합니다.",
        "how_to_use": "가격과 OBV가 함께 상승하면 거래량이 상승 흐름을 뒷받침하는지 확인할 수 있고, 가격과 OBV의 방향이 엇갈리면 다이버전스 후보로 볼 수 있어요.",
        "common_mistakes": "OBV 숫자 자체의 절대 크기는 종목별 거래량 규모에 따라 크게 달라요. 절대값끼리 비교하기보다 한 종목 안에서의 방향과 변화폭을 보는 게 중요해요.",
    },
    "mfi": {
        "concept": "MFI는 가격뿐 아니라 거래량까지 함께 반영해 자금 흐름의 강약을 0~100으로 표시하는 지표예요.",
        "calculation": "전형가격(고가+저가+종가의 평균)에 거래량을 곱한 Money Flow를 구하고, 일정 기간의 양(+)·음(-) 자금 흐름 비율로 0~100 값을 계산합니다.",
        "how_to_use": "80 이상은 과매수, 20 이하는 과매도 구간으로 많이 참고해요. RSI와 함께 보면 가격 모멘텀과 거래량 동반 여부를 구분해서 볼 수 있어요.",
        "common_mistakes": "80을 넘었다고 바로 하락하거나 20 아래라고 바로 반등하는 것은 아니에요. 강한 추세에서는 과매수·과매도 구간이 오래 지속될 수 있어요.",
    },
    "vwap": {
        "concept": "VWAP은 거래량을 많이 동반한 가격에 더 큰 비중을 주어 평균가격을 계산하는 방식이에요. 여기서는 일봉·주봉 등 모든 차트 주기에서 사용할 수 있도록 최근 20개 봉 Rolling VWAP을 표시합니다.",
        "calculation": "각 봉의 전형가격(고가+저가+종가의 평균)에 거래량을 곱한 값을 최근 20개 봉에서 합산한 뒤, 같은 기간 거래량 합계로 나눕니다.",
        "how_to_use": "현재가가 Rolling VWAP 위에 있으면 최근 거래량이 반영된 평균가격보다 높은 위치, 아래면 낮은 위치라는 식으로 상대적인 가격 위치를 확인할 수 있어요.",
        "common_mistakes": "장중 정식 VWAP은 보통 거래일마다 시작점이 초기화되므로 Rolling VWAP과 동일하지 않아요. 이 앱의 값은 일봉 이상의 모든 주기를 공통으로 분석하기 위한 Rolling 버전이라는 점을 기억하세요.",
    },
    "ma": {
        "concept": "일정 기간 동안의 종가를 평균 내서 이어놓은 선이에요. 하루하루의 등락(노이즈)을 지우고, 가격이 전체적으로 어느 방향으로 가고 있는지를 보여주는 게 목적이에요.",
        "calculation": "예를 들어 20일선이면, 오늘을 포함한 최근 20일간의 종가를 모두 더해서 20으로 나눈 값이에요. 하루가 지나면 가장 오래된 날짜는 빠지고 오늘 날짜가 새로 들어가면서 매일 다시 계산돼요.",
        "how_to_use": "짧은 기간선(예: 20일)이 긴 기간선(60일, 120일) 위에 있으면 '정배열'이라 부르며 상승 추세로, 반대는 '역배열'이라 부르며 하락 추세로 해석하는 경우가 많아요. 가격이 이동평균선을 아래에서 위로 뚫는 '골든크로스', 위에서 아래로 뚫는 '데드크로스'를 매매 시점 참고로 쓰기도 해요.",
        "common_mistakes": "이동평균선은 '과거 평균'이라 방향이 바뀌어도 뒤늦게 따라오는 후행지표예요. 이동평균선만 보고 막 반등한 저점을 잡으려 하면 이미 늦은 경우가 많아서, 거래량이나 RSI 같은 다른 지표와 같이 보는 게 안전해요.",
    },
    "bollinger": {
        "concept": "가격의 변동성(등락폭)을 기준으로, 지금 가격이 평소 대비 비싼지 싼지를 시각적인 밴드(띠)로 보여주는 지표예요.",
        "calculation": "가운데 선은 20일 이동평균선이고, 위/아래 밴드는 거기서 최근 20일간의 표준편차(가격이 평균에서 얼마나 흩어져 있는지)의 2배만큼 떨어진 위치예요. 변동성이 커지면 밴드 폭이 넓어지고, 줄어들면 좁아져요.",
        "how_to_use": "가격이 상단 밴드에 자주 닿으면 단기 과열(과매수), 하단 밴드에 자주 닿으면 단기 침체(과매도)로 해석하는 경우가 많아요. 밴드 폭이 아주 좁아졌다가('스퀴즈') 다시 벌어지기 시작하면 큰 변동성 장세의 시작 신호로 보기도 해요.",
        "common_mistakes": "상단 밴드에 닿았다고 무조건 떨어지는 건 아니에요. 강한 상승 추세에서는 가격이 상단 밴드를 타고 계속 올라가는 경우도 흔해서(밴드 워크), 추세를 무시하고 밴드만 보고 역매매하면 손실로 이어지기 쉬워요.",
    },
    "rsi": {
        "concept": "최근 일정 기간 동안 가격이 오른 폭과 내린 폭의 비율을 0~100 사이 숫자 하나로 압축해서, 지금 상승압력이 센지 하락압력이 센지를 보여주는 지표예요.",
        "calculation": "최근 14일 동안 오른 날들의 평균 상승폭과 내린 날들의 평균 하락폭을 각각 구해서 그 비율로 계산해요. 상승폭이 하락폭보다 압도적으로 크면 100에 가까워지고, 반대면 0에 가까워져요.",
        "how_to_use": "70 이상이면 과매수(단기 조정 가능성), 30 이하면 과매도(단기 반등 가능성)로 흔히 해석해요. 가격은 신고가를 갱신하는데 RSI는 이전 고점보다 낮아지는 '다이버전스'를 추세 전환의 힌트로 보기도 해요.",
        "common_mistakes": "강한 추세장에서는 RSI가 70을 넘긴 채로 며칠씩 유지되기도 해요(과매수 지속). '70 넘었으니 무조건 판다'는 식으로 기계적으로 대응하면 상승장 초반에 너무 일찍 나오게 되는 경우가 많아요.",
    },
    "adx": {
        "concept": "ADX(평균방향성지수)는 가격이 어느 방향으로 움직이는지보다, 현재 추세가 얼마나 강한지를 0~100 사이 숫자로 보여주는 지표예요. +DI와 -DI를 같이 보면 상승·하락 중 어느 쪽의 방향성이 우세한지도 확인할 수 있어요.",
        "calculation": "고가·저가의 움직임에서 +DM과 -DM을 구하고, True Range로 변동성을 보정한 +DI와 -DI를 만든 뒤 두 값의 차이를 비율로 계산합니다. 이 방향성 지표(DX)를 다시 14기간 기준으로 평활한 값이 ADX예요. 보통 14기간을 사용합니다.",
        "how_to_use": "ADX가 낮으면 추세가 약하거나 횡보에 가까운 구간으로, 높을수록 한 방향의 추세가 강한 구간으로 해석하는 경우가 많아요. 흔히 20~25 부근을 추세가 형성되는 기준으로 참고하고, +DI가 -DI보다 높으면 상승 방향성, 반대면 하락 방향성이 우세하다고 봐요. ADX 자체는 방향을 알려주는 지표가 아니라 '추세의 힘'을 보는 보조지표예요.",
        "common_mistakes": "ADX가 높다고 무조건 상승하는 건 아니에요. 강한 하락 추세에서도 ADX가 높아질 수 있어요. 그래서 +DI/-DI를 같이 보지 않고 ADX 숫자만으로 방향을 판단하면 안 되고, 이미 진행된 강한 추세를 새 신호처럼 받아들이는 것도 주의해야 해요.",
    },
    "stochastic": {
        "concept": "일정 기간의 최고가~최저가 범위 안에서, 오늘 종가가 어디쯤 위치하는지를 %로 보여주는 지표예요. RSI와 비슷하지만 훨씬 더 민감하게 움직여요.",
        "calculation": "최근 14일 동안의 최고가~최저가 범위 안에서 오늘 종가의 위치를 0~100%로 나타낸 값이 %K이고, 그 %K를 다시 3일 평균낸 게 %D예요.",
        "how_to_use": "80 이상은 과매수, 20 이하는 과매도로 해석하고, %K가 %D를 아래에서 위로 뚫으면 매수 신호, 위에서 아래로 뚫으면 매도 신호로 참고하는 경우가 많아요. 민감하게 움직여서 짧은 스윙 타이밍을 잡는 데 자주 쓰여요.",
        "common_mistakes": "민감한 만큼 신호가 너무 자주 나와서('잦은 교차') 매번 반응하면 손실이 잦아질 수 있어요. 이동평균선 정배열/역배열 같은 큰 추세 방향과 같이 놓고, 그 방향에 맞는 신호만 골라 쓰는 게 일반적이에요.",
    },
    "ichimoku": {
        "concept": "전환선·기준선·구름(선행스팬)·후행스팬까지, 여러 선을 한 번에 그려서 추세의 방향과 강도를 종합적으로 보여주는 일본식 지표예요.",
        "calculation": "전환선은 최근 9일 최고가·최저가의 평균, 기준선은 최근 26일 최고가·최저가의 평균이에요. 구름(선행스팬 A/B)은 이 값들을 26일 '미래' 방향으로 밀어서 그린 영역이라, 지금 화면에 보이는 구름은 사실 26일 전 데이터로 만들어진 거예요.",
        "how_to_use": "가격이 구름 위에 있으면 상승추세, 구름 아래에 있으면 하락추세, 구름 안에 있으면 방향 탐색 구간으로 해석해요. 구름이 두꺼울수록 그 구간을 뚫고 지나가기 어려운 지지·저항으로 보기도 해요.",
        "common_mistakes": "선이 5개나 되고 개념이 복합적이라 처음엔 헷갈리기 쉬워요. 처음엔 '가격이 구름 위/아래/안'이라는 큰 그림 하나만 보는 것부터 시작하고, 전환선·기준선 교차 같은 세부 신호는 익숙해진 다음에 봐도 늦지 않아요.",
    },
    "macd": {
        "concept": "단기 이동평균과 장기 이동평균의 차이를 이용해서, 추세가 바뀌는 시점을 좀 더 빠르게 포착하려는 지표예요.",
        "calculation": "단기(12일) 지수이동평균에서 장기(26일) 지수이동평균을 뺀 값이 MACD선이고, 그 MACD선을 다시 9일 지수이동평균 낸 게 시그널선이에요. 두 선의 차이는 막대(히스토그램)로도 같이 보여줘요.",
        "how_to_use": "MACD선이 시그널선을 아래에서 위로 뚫으면 '골든크로스'(매수 참고), 위에서 아래로 뚫으면 '데드크로스'(매도 참고)로 흔히 해석해요. 히스토그램이 0선 위/아래로 커지는 정도로 모멘텀의 강도를 가늠하기도 해요.",
        "common_mistakes": "MACD는 이동평균 기반이라 태생적으로 후행성이 있어요. 골든크로스가 뜬 시점엔 이미 상승이 꽤 진행된 뒤인 경우가 많아서, '크로스 뜨자마자 무조건 진입'하면 고점에 물릴 위험이 있어요.",
    },
    "volume": {
        "concept": "그날 실제로 체결된 주식 수예요. 가격의 움직임에 얼마나 많은 사람이 동참했는지를 보여줘서, 그 움직임의 신뢰도를 판단하는 데 써요.",
        "calculation": "거래량 자체는 계산 없이 그날 체결된 수량 그대로고, 거래량 이동평균(예: 20일)은 최근 거래량들의 평균으로 '평소 거래량 수준'을 보여줘요.",
        "how_to_use": "가격이 크게 움직였는데 거래량도 평소보다 훨씬 많으면 그 움직임에 힘이 실려있다고(신뢰도가 높다고) 보고, 거래량이 적은데 가격만 튀면 '휩쏘(가짜 신호)'일 가능성을 의심해볼 수 있어요.",
        "common_mistakes": "거래량 자체만으로는 오를지 내릴지 방향을 알려주지 않아요. 항상 가격의 움직임과 같이 놓고 '이 가격 움직임이 진짜냐'를 확인하는 보조 지표로 쓰는 게 맞아요.",
    },
}
