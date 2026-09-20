from __future__ import annotations

from datetime import datetime
import math
import pandas as pd
import streamlit as st


INDICATOR_LABELS = {
    "ma": "이동평균선 (MA)",
    "bollinger": "볼린저 밴드",
    "rsi": "RSI",
    "stochastic": "스토캐스틱",
    "ichimoku": "일목균형표",
    "macd": "MACD",
    "adx": "ADX / DMI",
    "atr": "ATR",
    "obv": "OBV",
    "mfi": "MFI",
    "vwap": "Rolling VWAP",
    "volume": "거래량",
    "williams_r": "Williams %R",
    "cci": "CCI",
    "roc": "ROC",
    "psar": "Parabolic SAR",
    "cmf": "CMF",
}

REPORT_ORDER = tuple(INDICATOR_LABELS)


def _num(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _latest(indicators, key):
    series = indicators.get(key)
    if series is None or len(series) == 0:
        return None
    return _num(series.iloc[-1])


def _fmt(value, digits=2, suffix=""):
    value = _num(value)
    if value is None:
        return "—"
    return f"{value:.{digits}f}{suffix}"


def _indicator_value(key, ind, close):
    mapping = {
        "rsi": ("rsi14", 1, ""),
        "stochastic": ("stoch_k", 1, ""),
        "adx": ("adx14", 1, ""),
        "atr": ("atr14", 2, ""),
        "obv": ("obv", 0, ""),
        "mfi": ("mfi14", 1, ""),
        "vwap": ("rolling_vwap20", 2, ""),
        "williams_r": ("williams_r", 1, ""),
        "cci": ("cci", 1, ""),
        "roc": ("roc", 1, "%"),
        "psar": ("psar", 2, ""),
        "cmf": ("cmf", 3, ""),
        "macd": ("macd_hist", 3, ""),
    }
    if key in mapping:
        source, digits, suffix = mapping[key]
        return _fmt(_latest(ind, source), digits, suffix)
    if key == "ma":
        return f"20D {_fmt(_latest(ind, 'sma20'))} / 60D {_fmt(_latest(ind, 'sma60'))} / 120D {_fmt(_latest(ind, 'sma120'))}"
    if key == "bollinger":
        return f"중심 {_fmt(_latest(ind, 'bb_mid'))} / 상단 {_fmt(_latest(ind, 'bb_upper'))}"
    if key == "ichimoku":
        return f"전환 {_fmt(_latest(ind, 'tenkan'))} / 기준 {_fmt(_latest(ind, 'kijun'))}"
    if key == "volume":
        return _fmt(_latest(ind, "vol_ma20"), 0)
    return "—"


def _state_text(key, ind, close):
    c = _num(close)
    if key == "rsi":
        v = _latest(ind, "rsi14")
        return "과매수" if v is not None and v >= 70 else ("과매도" if v is not None and v <= 30 else "중립")
    if key == "stochastic":
        v = _latest(ind, "stoch_k")
        return "과매수" if v is not None and v >= 80 else ("과매도" if v is not None and v <= 20 else "중립")
    if key == "mfi":
        v = _latest(ind, "mfi14")
        return "과매수" if v is not None and v >= 80 else ("과매도" if v is not None and v <= 20 else "중립")
    if key == "williams_r":
        v = _latest(ind, "williams_r")
        return "과매수" if v is not None and v >= -20 else ("과매도" if v is not None and v <= -80 else "중립")
    if key == "cci":
        v = _latest(ind, "cci")
        return "강세" if v is not None and v >= 100 else ("약세" if v is not None and v <= -100 else "중립")
    if key == "roc":
        v = _latest(ind, "roc")
        return "상승 모멘텀" if v is not None and v > 0 else ("하락 모멘텀" if v is not None and v < 0 else "중립")
    if key == "cmf":
        v = _latest(ind, "cmf")
        return "자금 유입" if v is not None and v > 0.05 else ("자금 유출" if v is not None and v < -0.05 else "중립")
    if key == "macd":
        v = _latest(ind, "macd_hist")
        return "상승 모멘텀" if v is not None and v > 0 else ("하락 모멘텀" if v is not None and v < 0 else "중립")
    if key == "adx":
        adx = _latest(ind, "adx14")
        pdi = _latest(ind, "plus_di14")
        mdi = _latest(ind, "minus_di14")
        if adx is None:
            return "데이터 없음"
        if adx >= 25 and pdi is not None and mdi is not None:
            return "상승 추세 강화" if pdi > mdi else "하락 추세 강화"
        return "추세 약함"
    if key in ("ma", "ichimoku", "vwap", "psar"):
        if c is None:
            return "데이터 없음"
        if key == "ma":
            s20, s60 = _latest(ind, "sma20"), _latest(ind, "sma60")
            return "상승 배열" if s20 is not None and s60 is not None and c > s20 > s60 else ("하락 배열" if s20 is not None and s60 is not None and c < s20 < s60 else "혼조")
        if key == "ichimoku":
            a, b = _latest(ind, "senkou_a"), _latest(ind, "senkou_b")
            if a is None or b is None:
                return "데이터 없음"
            return "구름 상단" if c > max(a, b) else ("구름 하단" if c < min(a, b) else "구름 내부")
        if key == "vwap":
            v = _latest(ind, "rolling_vwap20")
            return "VWAP 상회" if v is not None and c > v else ("VWAP 하회" if v is not None and c < v else "VWAP 부근")
        v = _latest(ind, "psar")
        return "PSAR 상회" if v is not None and c > v else ("PSAR 하회" if v is not None and c < v else "PSAR 부근")
    if key == "bollinger":
        u, l = _latest(ind, "bb_upper"), _latest(ind, "bb_lower")
        if c is None or u is None or l is None or u == l:
            return "데이터 없음"
        b = (c - l) / (u - l)
        return "상단 돌파" if b >= 1 else ("하단 이탈" if b <= 0 else f"밴드 내부 ({b*100:.0f}%)")
    if key == "atr":
        v = _latest(ind, "atr14")
        return "변동성 높음" if v is not None and c and v / c >= 0.04 else "일반 변동성"
    if key == "obv":
        s = ind.get("obv")
        if s is None or len(s) < 6:
            return "데이터 없음"
        a, b = _num(s.iloc[-1]), _num(s.iloc[-6])
        return "거래량 누적 증가" if a is not None and b is not None and a > b else "거래량 누적 감소"
    if key == "volume":
        v, ma = _latest(ind, "volume"), _latest(ind, "vol_ma20")
        if v is None or ma in (None, 0):
            return "데이터 없음"
        return "평균 상회" if v > ma else "평균 하회"
    return "—"


def _history_cells(pattern):
    if not pattern or pattern.get("status") != "ok":
        return ("—", "—", "—", "—")
    hs = pattern.get("horizons", {})
    vals = []
    for h in ("5", "20", "60"):
        s = hs.get(h) or {}
        vals.append(f"{s.get('up_probability', '—')}% / {s.get('down_probability', '—')}%")
    return (*vals, str(pattern.get("matches", "—")))


def render_technical_report(
    ticker,
    name,
    hist_df,
    indicators,
    pattern_results,
    market_condition=None,
    interval_label="일봉",
):
    if hist_df is None or hist_df.empty:
        st.info("보고서 작성에 필요한 차트 데이터가 없습니다.")
        return

    close = _num(pd.to_numeric(hist_df["Close"], errors="coerce").iloc[-1])
    last_date = pd.to_datetime(hist_df.index[-1], errors="coerce")
    date_text = last_date.strftime("%Y-%m-%d") if not pd.isna(last_date) else "—"
    condition = market_condition or {}
    context = condition.get("context", "중립/혼조")
    trend = condition.get("trend", "unknown")
    momentum = condition.get("momentum", "mixed")

    ok_patterns = [pattern_results.get(k) for k in REPORT_ORDER if pattern_results and pattern_results.get(k, {}).get("status") == "ok"]
    total_matches = sum(p.get("matches", 0) for p in ok_patterns)
    avg_sim = (
        sum(p.get("avg_similarity", 0) for p in ok_patterns) / len(ok_patterns)
        if ok_patterns else None
    )

    st.markdown("""
    <style>
    .gov-report {border:1px solid #D8DDE5;border-top:5px solid #D97706;border-radius:6px;background:var(--background-color,#fff);padding:28px 30px;margin:18px 0 28px;}
    .gov-title {font-size:25px;font-weight:800;letter-spacing:-.5px;margin-bottom:4px;}
    .gov-subtitle {font-size:13px;color:#6B7280;margin-bottom:22px;}
    .gov-section {border-left:4px solid #D97706;padding-left:12px;margin:25px 0 12px;font-size:17px;font-weight:800;}
    .gov-note {font-size:12px;color:#6B7280;line-height:1.7;}
    .gov-kpi {border:1px solid #E5E7EB;border-radius:5px;padding:13px 15px;background:rgba(249,250,251,.7);min-height:76px;}
    .gov-kpi-label {font-size:11px;color:#6B7280}.gov-kpi-value {font-size:19px;font-weight:800;margin-top:5px}
    .gov-table {width:100%;border-collapse:collapse;font-size:12px;margin:8px 0 10px;}
    .gov-table th {background:#F3F4F6;font-weight:800;border-top:1px solid #D1D5DB;border-bottom:1px solid #D1D5DB;padding:9px 7px;text-align:center;}
    .gov-table td {border-bottom:1px solid #E5E7EB;padding:8px 7px;text-align:center;vertical-align:middle;}
    .gov-table td:first-child {text-align:left;font-weight:700;}
    .gov-highlight {background:#FFF7ED;border:1px solid #FED7AA;border-radius:5px;padding:13px 15px;line-height:1.75;font-size:13px;}
    </style>
    """, unsafe_allow_html=True)

    st.markdown("<div class='gov-report'>", unsafe_allow_html=True)
    st.markdown(f"<div class='gov-title'>기술적 시장분석 종합보고서</div><div class='gov-subtitle'>Technical Market Analysis Report · {ticker}</div>", unsafe_allow_html=True)

    meta = pd.DataFrame([
        ["분석대상", f"{name} ({ticker})", "분석기준일", date_text],
        ["분석주기", interval_label, "현재가격", _fmt(close, 2)],
        ["적용지표", "17개 기술지표", "역사분석기간", "최근 10년"],
        ["유사조건 기준", "유사도 ≥ 65 / 100", "최소 표본", "12건"],
    ])
    st.markdown("<table class='gov-table'>" + "".join(
        f"<tr><th style='width:16%'>{a}</th><td>{b}</td><th style='width:16%'>{c}</th><td>{d}</td></tr>"
        for a,b,c,d in meta.values
    ) + "</table>", unsafe_allow_html=True)

    st.markdown("<div class='gov-section'>Ⅰ. 요약 및 현재 기술적 상태</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='gov-highlight'>현재 종목의 기술적 상태는 <b>{context}</b>로 분류됩니다. "
        f"추세 상태는 <b>{trend}</b>, 모멘텀 상태는 <b>{momentum}</b>입니다. "
        f"17개 지표 중 현재 상태와 과거 유사사례 통계가 모두 산출된 지표는 <b>{len(ok_patterns)}개</b>이며, "
        f"해당 사례의 누적 관측 건수는 <b>{total_matches:,}건</b>입니다. "
        f"{'평균 유사도는 ' + f'{avg_sim:.1f}/100입니다.' if avg_sim is not None else '역사적 유사사례 통계가 충분하지 않은 지표가 존재합니다.'}"
        "</div>",
        unsafe_allow_html=True,
    )

    kpi_cols = st.columns(4)
    kpis = [
        ("현재 기술상태", condition.get("label", "일반")),
        ("과매수 신호", f"{condition.get('overbought_count', 0)}건"),
        ("과매도 신호", f"{condition.get('oversold_count', 0)}건"),
        ("역사분석 가능 지표", f"{len(ok_patterns)} / 17"),
    ]
    for col, (label, value) in zip(kpi_cols, kpis):
        with col:
            st.markdown(f"<div class='gov-kpi'><div class='gov-kpi-label'>{label}</div><div class='gov-kpi-value'>{value}</div></div>", unsafe_allow_html=True)

    st.markdown("<div class='gov-section'>Ⅱ. 17개 기술지표 종합 현황</div>", unsafe_allow_html=True)
    rows = []
    for key in REPORT_ORDER:
        pattern = pattern_results.get(key, {}) if pattern_results else {}
        h5, h20, h60, matches = _history_cells(pattern)
        rows.append([
            INDICATOR_LABELS[key],
            _indicator_value(key, indicators, close),
            _state_text(key, indicators, close),
            h5, h20, h60,
            matches,
        ])
    df = pd.DataFrame(rows, columns=["지표","현재값","현재 상태","+5D 상승/하락"," +20D 상승/하락","+60D 상승/하락","사례수"])
    st.dataframe(df, use_container_width=True, hide_index=True, height=620)

    st.markdown("<div class='gov-section'>Ⅲ. 역사적 유사조건 분석</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='gov-note'>아래 확률은 미래 예측확률이 아닙니다. 최근 10년의 일봉 자료에서 현재 상태와 유사도 65 이상인 과거 날짜를 "
        "선별하고, 해당 날짜 이후 실제 종가가 상승 또는 하락한 비율을 계산한 통계입니다. 각 사례는 최소 5거래일 간격을 두어 중복 영향을 줄였습니다.</div>",
        unsafe_allow_html=True,
    )

    hist_rows = []
    for key in REPORT_ORDER:
        p = pattern_results.get(key, {}) if pattern_results else {}
        if p.get("status") != "ok":
            continue
        for h in ("5","20","60"):
            s = p.get("horizons", {}).get(h) or {}
            hist_rows.append([
                INDICATOR_LABELS[key],
                f"{h}거래일",
                s.get("samples", "—"),
                f"{s.get('up_probability', '—')}%",
                f"{s.get('down_probability', '—')}%",
                f"{s.get('mean_return', '—')}%",
                f"{s.get('median_return', '—')}%",
                f"{s.get('up_probability_ci_low', '—')}–{s.get('up_probability_ci_high', '—')}%",
            ])
    if hist_rows:
        st.dataframe(pd.DataFrame(hist_rows, columns=["지표","기간","표본","상승 비율","하락 비율","평균 수익률","중앙값","상승비율 95% 구간"]), use_container_width=True, hide_index=True, height=620)
    else:
        st.info("현재 데이터에서는 최소 표본 기준을 충족하는 역사적 유사조건 결과가 없습니다.")

    st.markdown("<div class='gov-section'>Ⅳ. 분석상 유의사항 및 데이터 품질</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='gov-note'>① 본 보고서는 기술적 지표와 과거 유사조건의 실제 결과를 정리한 통계자료입니다. "
        "② 과거 발생빈도는 미래 가격의 결과를 보장하지 않습니다. "
        "③ 표본 수가 적거나 신뢰구간이 넓은 경우 통계적 불확실성이 커집니다. "
        "④ 분봉·주봉 등 화면의 차트 주기와 관계없이 역사적 유사조건 분석은 거래일 단위 일봉을 기준으로 합니다. "
        "⑤ 결측 또는 계산 불가능한 지표는 임의의 값으로 대체하지 않습니다.</div>",
        unsafe_allow_html=True,
    )
    st.markdown("<div class='gov-section'>Ⅴ. 분석기준</div>", unsafe_allow_html=True)
    st.markdown(
        "<div class='gov-note'>사용 지표: MA, Bollinger Bands, RSI, Stochastic, Ichimoku, MACD, ADX/DMI, ATR, OBV, MFI, "
        "Rolling VWAP, Volume, Williams %R, CCI, ROC, Parabolic SAR, CMF. "
        "역사적 분석의 기준기간은 최근 10년, 최소 유사사례는 12건, 최소 유사도는 65/100입니다. "
        "분석 결과는 관측자료의 통계적 요약이며 투자판단을 자동으로 결정하지 않습니다.</div>",
        unsafe_allow_html=True,
    )
    st.markdown("</div>", unsafe_allow_html=True)
