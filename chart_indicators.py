# chart_indicators.py
# 초보자를 위한 기술적 지표 계산 + 자동 해설 생성 모듈.
# 외부 의존성 없음 - yfinance가 이미 주는 OHLCV(pandas)만으로 전부 계산.
# 1차 버전 지표 5종: 이동평균선(MA) / 볼린저밴드 / RSI / MACD / 거래량

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
    "sma_short": 20, "sma_mid": 60, "sma_long": 120,
    "bb_window": 20, "bb_std": 2.0,
    "rsi_window": 14,
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    "vol_ma_window": 20,
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

    sma20 = compute_sma(close, p["sma_short"])
    sma60 = compute_sma(close, p["sma_mid"])
    sma120 = compute_sma(close, p["sma_long"])
    bb_upper, bb_mid, bb_lower = compute_bollinger(close, p["bb_window"], p["bb_std"])
    rsi14 = compute_rsi(close, p["rsi_window"])
    macd_line, macd_signal, macd_hist = compute_macd(close, p["macd_fast"], p["macd_slow"], p["macd_signal"])
    vol_ma20 = compute_volume_ma(volume, p["vol_ma_window"])
    stoch_k, stoch_d = compute_stochastic(high, low, close, p["stoch_k"], p["stoch_d"], p["stoch_smooth"])
    tenkan, kijun, senkou_a, senkou_b, chikou = compute_ichimoku(
        high, low, close, p["ichimoku_tenkan"], p["ichimoku_kijun"], p["ichimoku_senkou_b"]
    )

    return {
        "sma20": sma20, "sma60": sma60, "sma120": sma120,
        "bb_upper": bb_upper, "bb_mid": bb_mid, "bb_lower": bb_lower,
        "rsi14": rsi14,
        "macd_line": macd_line, "macd_signal": macd_signal, "macd_hist": macd_hist,
        "vol_ma20": vol_ma20,
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
