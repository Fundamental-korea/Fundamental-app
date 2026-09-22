import json
import random
import FinanceDataReader as fdr
import pandas as pd
import altair as alt
import streamlit as st
import streamlit.components.v1 as components
from supabase import create_client
import yfinance as yf
import base64
import calendar as pycalendar
from datetime import date, timedelta

from search_aliases import aliases_for

from scoring import METRIC_WEIGHTS, ROA_WEIGHT  # 지표별 가중치 - "총점 기여도" 표시에 사용 (scoring.py가 단일 소스)
from us_scoring import PROFILE_DESCRIPTIONS, PROFILE_LABELS
from historical_pattern import analyze_all_indicator_patterns
from news_earnings import fetch_dart_disclosures, fetch_macro_news, fetch_stock_news, build_earnings_events
import importlib
import chart_indicators as _chart_indicators
_chart_indicators = importlib.reload(_chart_indicators)

compute_all_indicators = _chart_indicators.compute_all_indicators
generate_ma_commentary = _chart_indicators.generate_ma_commentary
generate_bollinger_commentary = _chart_indicators.generate_bollinger_commentary
generate_rsi_commentary = _chart_indicators.generate_rsi_commentary
generate_macd_commentary = _chart_indicators.generate_macd_commentary
generate_volume_commentary = _chart_indicators.generate_volume_commentary
generate_stochastic_commentary = _chart_indicators.generate_stochastic_commentary
generate_ichimoku_commentary = _chart_indicators.generate_ichimoku_commentary
generate_adx_commentary = _chart_indicators.generate_adx_commentary
generate_atr_commentary = _chart_indicators.generate_atr_commentary
generate_obv_commentary = _chart_indicators.generate_obv_commentary
generate_mfi_commentary = _chart_indicators.generate_mfi_commentary
generate_vwap_commentary = _chart_indicators.generate_vwap_commentary
generate_williams_r_commentary = getattr(_chart_indicators, "generate_williams_r_commentary", lambda s: "")
generate_cci_commentary = getattr(_chart_indicators, "generate_cci_commentary", lambda s: "")
generate_roc_commentary = getattr(_chart_indicators, "generate_roc_commentary", lambda s: "")
generate_psar_commentary = getattr(_chart_indicators, "generate_psar_commentary", lambda close, psar: "")
generate_cmf_commentary = getattr(_chart_indicators, "generate_cmf_commentary", lambda s: "")
INDICATOR_LESSONS = _chart_indicators.INDICATOR_LESSONS
import requests
import streamlit as st

# GitHub의 실제 Raw 이미지 URL
RAW_LOGO_URL = "https://raw.githubusercontent.com/Fundamental-korea/Fundamental-app/main/logo.png"

# 이미지를 가져와 Base64로 변환하는 함수
@st.cache_data
def get_logo_base64(url):
    try:
        response = requests.get(url)
        if response.status_code == 200:
            return base64.b64encode(response.content).decode("utf-8")
    except Exception:
        pass
    return ""

logo_base64 = get_logo_base64(RAW_LOGO_URL)

# ==========================================
# 1. 페이지 및 커스텀 디자인 설정
# ==========================================
st.set_page_config(
    page_title="Fundamental Analyzer - 하락장 방어 플랫폼",
    page_icon="🛡️",
    layout="wide",
)

THEME_PALETTES = {
    "light": {
        "page": "#FFFFFF", "surface": "#FFFFFF", "surface_warm": "#FFFDF9", "surface_muted": "#FAFAFA",
        "text": "#1A1A1A", "text_muted": "#6B7280", "text_secondary": "#4B5563",
        "border": "#E5E7EB", "border_soft": "#F0E4D8", "accent": "#F4A261", "accent_strong": "#D97706",
        "positive": "#D93025", "negative": "#2563EB", "success": "#047857",
        "warning_bg": "#FFF7ED", "warning_text": "#92400E", "danger_bg": "#FEF2F2",
        "danger_border": "#FCA5A5", "success_bg": "#ECFDF5", "success_border": "#A7F3D0",
        "plot_bg": "#FFFFFF", "plot_grid": "#E2E8F0", "plot_axis": "#6B7280", "plot_text": "#1A1A1A",
        "toolbar_bg": "#F1F3F6",
    },
    "dark": {
        "page": "#0F1115", "surface": "#171A1F", "surface_warm": "#1C1A17", "surface_muted": "#20242A",
        "text": "#F3F4F6", "text_muted": "#A8B0BC", "text_secondary": "#C1C7D0",
        "border": "#374151", "border_soft": "#4B5563", "accent": "#F4A261", "accent_strong": "#FDBA74",
        "positive": "#F87171", "negative": "#60A5FA", "success": "#6EE7B7",
        "warning_bg": "#2A2117", "warning_text": "#FDBA74", "danger_bg": "#2A181B",
        "danger_border": "#7F1D1D", "success_bg": "#10231B", "success_border": "#166534",
        "plot_bg": "#0F1115", "plot_grid": "#374151", "plot_axis": "#9CA3AF", "plot_text": "#E5E7EB",
        "toolbar_bg": "#1B2027",
    },
}

def get_theme_mode():
    mode = st.query_params.get("theme", "light")
    return "dark" if str(mode).lower() == "dark" else "light"

THEME_MODE = get_theme_mode()
THEME = THEME_PALETTES[THEME_MODE]

def render_theme_toggle(key="theme_toggle"):
    current_dark = THEME_MODE == "dark"
    selected = st.toggle(
        "🌙 다크모드" if not current_dark else "☀️ 라이트모드",
        value=current_dark,
        key=key,
        help="눈의 피로를 줄이기 위한 어두운 화면으로 전환합니다.",
    )
    desired = "dark" if selected else "light"
    if desired != THEME_MODE:
        st.query_params["theme"] = desired
        st.rerun()


st.markdown(
    """
    <style>
    html, body, [data-testid="stAppViewContainer"], .stApp {
        background-color: #FFFFFF !important;
        color: #1A1A1A !important;
        background-image: 
            radial-gradient(circle at 0% 0%, rgba(248, 190, 140, 0.2) 0%, transparent 45%),
            radial-gradient(circle at 100% 0%, rgba(248, 190, 140, 0.2) 0%, transparent 45%),
            radial-gradient(circle at 0% 100%, rgba(248, 190, 140, 0.2) 0%, transparent 45%),
            radial-gradient(circle at 100% 100%, rgba(248, 190, 140, 0.2) 0%, transparent 45%) !important;
        background-repeat: no-repeat !important;
        background-attachment: fixed !important;
    }

    p, span, div, label, h1, h2, h3, h4, h5, h6 {
        color: #1A1A1A !important;
    }
    """
    + f"""
    .logo-box {{
        border: 2px solid #F4A261;
        border-radius: 14px !important;
        background-color: #ffffff;
        /* Raw 이미지를 Base64 데이터로 직접 주입 */
        background-image: url("data:image/png;base64,{logo_base64}") !important;
        background-size: 105% !important;
        background-repeat: no-repeat !important;
        background-position: center !important;
        color: transparent !important; /* 기존 글자 숨김 */
        width: 140px !important;
        min-width: 140px !important;
        height: 140px !important;
        min-height: 140px !important;
        /* 잔상 및 드래그 문제 완벽 해결 */
        color: transparent !important;
        font-size: 0 !important; /* 내부 텍스트 크기를 0으로 만들어 숨김 */
        user-select: none !important; /* 마우스 드래그 선택 차단 */
        -webkit-user-drag: none; /* 이미지 자체 드래그 차단 */
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 3px 10px rgba(0,0,0,0.03);
        overflow: hidden;
    }}
    """
    + """
    /* 살짝 둥근 폰트(나눔스퀘어라운드) 불러오기 */
@import url('https://cdn.jsdelivr.net/gh/projectnoonnu/noonfonts_two@1.0/NanumSquareRound.woff');

    .quote-box-v2 {
        font-family: 'NanumSquareRound', 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; /* 둥근 폰트 적용 */
        background-color: #FFFDF9;
        border: 2px solid #F4A261;
        border-radius: 16px;
        min-height: 168px !important;
        height: auto;
        display: flex;
        flex-direction: row;
        align-items: center;
        gap: 16px;
        padding: 14px 22px 14px 12px;
        box-shadow: 0 4px 12px rgba(244, 162, 97, 0.12);
        box-sizing: border-box;
    }
        @media (max-width: 1200px) {
        .quote-box-v2 {
            flex-direction: column !important; /* 화면이 좁아지면 세로로 정렬 */
            align-items: stretch !important;
            padding: 16px !important;
        }
        .logo-box {
            width: 100% !important; /* 모바일에서 로고 박스를 가로 꽉 차게 조절 */
            min-width: 100% !important;
            height: 100px !important; /* 높이는 살짝 줄여서 비율 맞춤 */
            min-height: 100px !important;
            margin-right: 0 !important;
            margin-bottom: 12px !important; /* 아래 명언 박스와의 간격 확보 */
        }
    }

    .quote-photo-wrap {
        flex: 0 0 auto;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    
    }

    .quote-photo {
        width: 100px;
        height: 140px;
        object-fit: cotain;
        border-radius: 10px;
        border: 1px solid #F0E4D8;
        display: block;
    }

    .quote-photo-fallback {
        width: 100px;
        height: 140px;
        border-radius: 10px;
        border: 1px solid #F0E4D8;
        background-color: #FFF3E4;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 32px;
    }

    .quote-content {
        flex: 1 1 auto;
        min-width: 0;
        display: flex;
        flex-direction: column;
        justify-content: center;
        gap: 4px;
    } 

    .quote-en-row {
        display: flex;
        align-items: flex-start;
        gap: 6px;
    }

/* 닫는 괄호 오류 수정 및 중복 코드 제거 */
    .quote-mark {
        display: none !important;
    }

    .quote-en {
        font-size: 17px;
        font-weight: 700;
        color: #4B5563 !important;
        line-height: 1.35;
        overflow: hidden;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
    }

/* 영문 쌍따옴표 정상 작동 */
    .quote-en::before,
    .quote-en::after {
        content: '"';
    }

/* 괄호 오류가 수정되어 주황색(#F4A261)이 정상 적용됩니다 */
    .quote-divider {
        border: none;
        border-top: 2px solid #F4A261;
        margin: 6px 0;
    }

    .quote-ko {
        font-size: 15px;
        font-weight: 600;
        color: #4B5563 !important;
        line-height: 1.45;
        margin-left: 0;
        overflow: hidden;
        display: -webkit-box;           
        -webkit-line-clamp: 2;          
        -webkit-box-orient: vertical;
    }

/* 국문 쌍따옴표 정상 작동 */
    .quote-ko::before,
    .quote-ko::after {
        content: '"';
    }

    .quote-author {
        font-size: 13px;
        font-weight: 800;
        color: #D97706 !important;
        margin-left: 0;
        margin-top: 4px;
        flex-shrink: 0; 
        white-space: nowrap;
        overflow: hidden;
        line-height: 1.6; 
        padding-bottom: 4px; 
    }

/* 이름 뒤에 빼기표(-) 추가 */
    .quote-author::after {
        content: " —";
    }

    .ad-box-tall {
        background-color: #F8F9FA;
        border: 2px dashed #D0D0D0;
        border-radius: 12px;
        text-align: center;
        color: #888888 !important;
        font-weight: bold;
        font-size: 15px;
        min-height: 580px;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        box-sizing: border-box;
    }

    div[data-testid="stButton"] > button, div.stButton > button,
    div[data-testid="stLinkButton"] > a {
        background-color: #FFFFFF !important;
        color: #1A1A1A !important;
        border: 1.5px solid #D1D5DB !important;
        border-radius: 10px !important;
        font-size: 16px !important;
        font-weight: 800 !important;
        box-shadow: 0 2px 5px rgba(0,0,0,0.04) !important;
        transition: all 0.2s ease !important;
    }

    div[data-testid="stButton"] > button:hover, div.stButton > button:hover,
    div[data-testid="stLinkButton"] > a:hover {
        border-color: #F4A261 !important;
        color: #D97706 !important;
        background-color: #FFFDF9 !important;
    }

    /* 브라우저/OS 다크모드에서도 네이티브 위젯(표/차트/expander)이 항상 라이트로
       보이도록 강제 - config.toml 테마 설정이 반영 안 되는 경우의 보조 장치 */
    div[data-testid="stExpander"] {
        background-color: #FFFFFF !important;
        border: 1px solid #E5E7EB !important;
        border-radius: 10px !important;
    }
    div[data-testid="stExpander"] summary {
        background-color: #FAFAFA !important;
        color: #1A1A1A !important;
    }
    div[data-testid="stDataFrame"], div[data-testid="stDataFrame"] * {
        background-color: #FFFFFF !important;
        color: #1A1A1A !important;
    }
    div[data-testid="stVegaLiteChart"], div[data-testid="stArrowVegaLiteChart"] {
        background-color: #FFFFFF !important;
    }

    div[data-testid="stTabs"] [data-baseweb="tab-list"] {
        gap: 20px !important;
        border-bottom: 2px solid #E5E7EB !important;
        padding-bottom: 2px !important;
        background-color: transparent !important;
    }

    div[data-testid="stTabs"] [data-baseweb="tab"] {
        height: 50px !important;
        background-color: transparent !important;
        border: none !important;
        border-radius: 0px !important;
        padding: 0px 8px !important;
        margin: 0px !important;
        box-shadow: none !important;
    }

    div[data-testid="stTabs"] [data-baseweb="tab"] p,
    div[data-testid="stTabs"] [data-baseweb="tab"] span,
    div[data-testid="stTabs"] [data-baseweb="tab"] div {
        font-size: 19px !important;
        font-weight: 900 !important;
        color: #4B5563 !important;
        letter-spacing: -0.3px !important;
    }

    div[data-testid="stTabs"] [aria-selected="true"] {
        background-color: transparent !important;
        border-bottom: 4px solid #F4A261 !important;
    }

    div[data-testid="stTabs"] [aria-selected="true"] p,
    div[data-testid="stTabs"] [aria-selected="true"] span,
    div[data-testid="stTabs"] [aria-selected="true"] div {
        color: #D97706 !important;
        font-size: 20px !important;
        font-weight: 900 !important;
    }

    /* 홈 5개 네비게이션을 실제 클릭 가능한 탭처럼 보이게 한다. */
    div[data-testid="stRadio"] {
        margin-bottom: 4px;
    }
    div[data-testid="stRadio"] [role="radiogroup"] {
        display: flex !important;
        flex-wrap: wrap !important;
        gap: 0 !important;
        border-bottom: 1px solid #E5E7EB;
    }
    div[data-testid="stRadio"] [role="radiogroup"] > label {
        padding: 9px 14px 10px !important;
        margin: 0 !important;
        border-radius: 0 !important;
        border-bottom: 3px solid transparent !important;
        cursor: pointer !important;
        font-weight: 850 !important;
        color: #4B5563 !important;
    }
    div[data-testid="stRadio"] [role="radiogroup"] > label[data-checked="true"] {
        color: #D97706 !important;
        border-bottom-color: #F4A261 !important;
        background: transparent !important;
    }
    div[data-testid="stRadio"] [role="radiogroup"] > label > div:first-child {
        display: none !important;
    }

    .live-news-section {
        margin-top: 20px;
        margin-bottom: 8px;
    }
    .live-news-section-title {
        font-size: 20px;
        font-weight: 850;
        color: #1A1A1A !important;
        margin-bottom: 4px;
    }
    .live-news-section-subtitle {
        font-size: 12px;
        color: #6B7280 !important;
        margin-bottom: 14px;
    }
    .live-news-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 12px;
        margin-bottom: 10px;
    }
    .live-news-card {
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 10px;
        padding: 15px 16px 14px;
        min-height: 154px;
        box-shadow: 0 2px 7px rgba(15, 23, 42, 0.035);
        transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
        box-sizing: border-box;
    }
    .live-news-card:hover {
        border-color: #D1D5DB;
        box-shadow: 0 5px 14px rgba(15, 23, 42, 0.06);
        transform: translateY(-1px);
    }
    .live-news-meta {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        margin-bottom: 9px;
        color: #6B7280 !important;
        font-size: 11px;
        font-weight: 700;
    }
    .live-news-source {
        color: #4B5563 !important;
        font-weight: 800;
    }
    .live-news-category {
        color: #6B7280 !important;
        font-weight: 700;
    }
    .live-news-title {
        color: #1A1A1A !important;
        font-size: 15px;
        line-height: 1.42;
        font-weight: 800;
        text-decoration: none !important;
        display: -webkit-box;
        -webkit-line-clamp: 3;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    .live-news-desc {
        color: #6B7280 !important;
        font-size: 12px;
        line-height: 1.45;
        margin-top: 7px;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
    }
    .live-news-footer {
        color: #9CA3AF !important;
        font-size: 10px;
        margin-top: 10px;
    }
    .news-empty-state {
        background: #FAFAFA;
        border: 1px solid #E5E7EB;
        border-radius: 10px;
        padding: 18px;
        color: #6B7280 !important;
        font-size: 13px;
    }
    @media (max-width: 900px) {
        .live-news-grid { grid-template-columns: 1fr; }
    }

    .earnings-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 16px;
        padding: 13px 14px;
        margin-bottom: 7px;
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 10px;
    }
    .earnings-name {
        color: #1A1A1A !important;
        font-size: 14px;
        font-weight: 850;
    }
    .earnings-report {
        color: #6B7280 !important;
        font-size: 11px;
        margin-top: 4px;
    }
    .earnings-date {
        color: #4B5563 !important;
        font-size: 13px;
        font-weight: 750;
        white-space: nowrap;
    }
    .earnings-compare {
        color: #6B7280 !important;
        font-size: 11px;
        margin-top: 5px;
        white-space: nowrap;
    }
    .earnings-compare strong {
        color: #374151 !important;
    }
    .earnings-upcoming-title {
        margin: 20px 0 9px;
        color: #111827 !important;
        font-size: 15px;
        font-weight: 850;
    }
    .earnings-note {
        color: #6B7280 !important;
        font-size: 11px;
        margin: 6px 0 12px;
    }
    .earnings-consensus-chip {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 30px;
        padding: 0 11px;
        border-radius: 7px;
        background: #FFF7ED;
        border: 1px solid #FED7AA;
        color: #111827 !important;
        box-sizing: border-box;
        font-size: 12px;
        font-weight: 900;
        white-space: nowrap;
    }
    .earnings-consensus-chip strong {
        color: #111827 !important;
        font-size: 13px;
        margin-left: 3px;
    }
    .earnings-date-wrap {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 8px;
        flex: 0 0 auto;
        min-width: 230px;
    }
    .earnings-date-wrap .earnings-date {
        white-space: nowrap;
    }
    .earnings-calendar-shell {
        background: rgba(255,255,255,0.94);
        border: 1px solid #E5E7EB;
        border-radius: 16px;
        padding: 18px;
        box-shadow: 0 8px 24px rgba(15,23,42,0.05);
        margin-top: 10px;
    }
    .earnings-calendar-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 12px;
    }
    .earnings-calendar-month {
        color: #111827 !important;
        font-size: 22px;
        font-weight: 900;
        letter-spacing: -0.5px;
    }
    .earnings-calendar-sub {
        color: #6B7280 !important;
        font-size: 11px;
        margin-top: 3px;
    }
    .earnings-calendar-week {
        color: #6B7280 !important;
        font-size: 11px;
        font-weight: 850;
        text-align: center;
        padding: 8px 0;
        border-bottom: 1px solid #E5E7EB;
    }
    .earnings-calendar-cell {
        min-height: 122px;
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 10px;
        padding: 8px;
        margin-bottom: 8px;
        box-sizing: border-box;
        overflow: hidden;
    }
    .earnings-calendar-cell.is-today {
        border: 2px solid #F4A261;
        background: #FFFDF9;
    }
    .earnings-calendar-day {
        color: #111827 !important;
        font-size: 13px;
        font-weight: 900;
        margin-bottom: 6px;
    }
    .earnings-calendar-cell button {
        width: 100% !important;
        min-height: 30px !important;
        padding: 2px 4px !important;
        border: 0 !important;
        background: transparent !important;
        color: #111827 !important;
        box-shadow: none !important;
        justify-content: flex-start !important;
        font-size: 13px !important;
        font-weight: 900 !important;
        margin: 0 0 4px 0 !important;
    }
    .earnings-calendar-cell button:hover {
        background: #FFF7ED !important;
        color: #D97706 !important;
    }
    .earnings-calendar-cell.selected {
        border: 2px solid #F4A261;
        background: #FFFDF9;
    }
    .earnings-calendar-empty {
        color: #D1D5DB !important;
        font-size: 12px;
    }
    .earnings-calendar-event {
        display: block;
        padding: 4px 6px;
        margin-top: 4px;
        border-radius: 6px;
        background: #F8FAFC;
        border-left: 3px solid #F4A261;
        color: #1F2937 !important;
        font-size: 10px;
        line-height: 1.25;
        font-weight: 750;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .earnings-calendar-event.kr {
        border-left-color: #D97706;
        background: #FFF7ED;
    }
    .earnings-calendar-event.us {
        border-left-color: #9CA3AF;
        background: #F8FAFC;
    }
    .earnings-calendar-more {
        color: #6B7280 !important;
        font-size: 9px;
        font-weight: 800;
        margin-top: 4px;
    }
    .earnings-open-calendar {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-height: 42px;
        padding: 0 18px;
        border: 1.5px solid #F4A261;
        border-radius: 10px;
        background: #FFFDF9;
        color: #D97706 !important;
        text-decoration: none !important;
        font-size: 14px;
        font-weight: 900;
        box-shadow: 0 3px 8px rgba(244,162,97,0.10);
    }
    .earnings-open-calendar:hover {
        background: #F4A261;
        color: #FFFFFF !important;
    }
    .earnings-page-title {
        color: #111827 !important;
        font-size: 27px;
        font-weight: 900;
        letter-spacing: -0.7px;
        margin-bottom: 2px;
    }
    .earnings-page-subtitle {
        color: #6B7280 !important;
        font-size: 12px;
        margin-bottom: 16px;
    }
    .earnings-summary-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0,1fr));
        gap: 10px;
        margin: 14px 0 16px;
    }
    .earnings-summary-card {
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 11px;
        padding: 12px 14px;
    }
    .earnings-summary-label {
        color: #6B7280 !important;
        font-size: 10px;
        font-weight: 800;
    }
    .earnings-summary-value {
        color: #111827 !important;
        font-size: 20px;
        font-weight: 900;
        margin-top: 3px;
    }
    .earnings-detail-card {
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 11px;
        padding: 13px 15px;
        margin-bottom: 8px;
    }
    .earnings-detail-top {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 12px;
    }
    .earnings-detail-name {
        color: #111827 !important;
        font-size: 14px;
        font-weight: 900;
    }
    .earnings-detail-meta {
        color: #6B7280 !important;
        font-size: 11px;
        margin-top: 4px;
        line-height: 1.45;
    }
    .earnings-detail-market {
        flex: 0 0 auto;
        border-radius: 999px;
        padding: 3px 8px;
        font-size: 10px;
        font-weight: 900;
    }
    .earnings-detail-market.us {
        background: #F3F4F6;
        color: #374151 !important;
    }
    .earnings-detail-market.kr {
        background: #FFF7ED;
        color: #9A3412 !important;
    }
    .earnings-detail-compare {
        margin-top: 9px;
        padding-top: 9px;
        border-top: 1px dashed #E5E7EB;
        color: #374151 !important;
        font-size: 11px;
        font-weight: 750;
    }
    .earnings-detail-compare strong {
        color: #111827 !important;
        font-weight: 900;
    }
    @media (max-width: 900px) {
        .earnings-summary-grid { grid-template-columns: repeat(2, minmax(0,1fr)); }
    }
    @media (max-width: 700px) {
        .earnings-calendar-cell { min-height: 96px; padding: 6px; }
        .earnings-calendar-event { font-size: 9px; }
        .earnings-calendar-month { font-size: 19px; }
        .earnings-detail-top { flex-direction: column; }
    }
    .earnings-date a {
        color: #D97706 !important;
        text-decoration: none !important;
        font-weight: 800;
    }
    .earnings-primary,
    .earnings-secondary {
        display: inline-block;
        margin-left: 7px;
        padding: 2px 7px;
        border-radius: 999px;
        font-size: 10px;
        font-weight: 800;
    }
    .earnings-primary {
        background: #FFF7ED;
        color: #9A3412 !important;
        border: 1px solid #FED7AA;
    }
    .earnings-secondary {
        background: #F3F4F6;
        color: #4B5563 !important;
        border: 1px solid #E5E7EB;
    }
    @media (max-width: 700px) {
        .earnings-row { flex-direction: column; align-items: flex-start; }
        .earnings-date { white-space: normal; }
    }

    .bottom-cards-wrapper {
        margin-top: 25px;
    }
    .sketch-card {
        background-color: #FAFAFA;
        border: 1.5px solid #E5E5E5;
        border-radius: 12px;
        padding: 18px 20px;
        min-height: 290px;
        box-shadow: 0 4px 10px rgba(0,0,0,0.02);
    }
    .card-item-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 6px 0;
        border-bottom: 1px dashed #E2E8F0;
    }
    .card-item-row:last-child {
        border-bottom: none;
    }
    .stock-link {
        color: #D97706 !important;
        text-decoration: none !important;
        font-weight: 600;
        font-size: 13px;
    }
    .stock-link:hover {
        text-decoration: underline !important;
    }
    .search-count-badge {
        font-size: 11px;
        color: #64748B;
        background-color: #F1F5F9;
        padding: 2px 8px;
        border-radius: 12px;
        font-weight: 600;
    }

    .sketch-item-box {
        background-color: #FFFFFF;
        border: 1.5px solid #E5E7EB;
        border-radius: 12px;
        padding: 16px 20px;
        margin-bottom: 12px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.02);
        transition: transform 0.15s ease, border-color 0.15s ease;
    }
    .sketch-item-box:hover {
        border-color: #F4A261;
        transform: translateY(-2px);
    }
    .sketch-item-box.excluded {
        opacity: 0.55;
        background-color: #FAFAFA;
    }
    .sketch-item-title {
        font-size: 17px;
        font-weight: 800;
        color: #111827 !important;
        min-width: 210px;
    }
    .sketch-item-desc {
        font-size: 14px;
        color: #4B5563 !important;
        flex: 1;
        padding: 0 20px;
        line-height: 1.4;
    }
    .sketch-item-score {
        font-size: 18px;
        font-weight: 900;
        color: #D97706 !important;
        background-color: #FFFBEB;
        border: 1px solid #FCD34D;
        padding: 6px 14px;
        border-radius: 8px;
        white-space: nowrap;
    }
    .sketch-item-score.excluded {
        font-size: 13px;
        font-weight: 700;
        color: #64748B !important;
        background-color: #F1F5F9;
        border: 1px solid #E2E8F0;
    }

    .grade-hero-box {
        background-color: #FFFDF9;
        border: 2px solid #F4A261;
        border-radius: 16px;
        padding: 24px 28px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 12px;
        box-shadow: 0 4px 12px rgba(244, 162, 97, 0.12);
        flex-wrap: wrap;
        gap: 16px;
    }
    .grade-hero-score {
        font-size: 42px;
        font-weight: 900;
        color: #D97706 !important;
    }
    .grade-hero-badge {
        font-size: 26px;
        font-weight: 900;
        padding: 8px 20px;
        border-radius: 10px;
        background-color: #D97706;
        color: #FFFFFF !important;
    }
    .grade-hero-sub {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
    }
    .mini-stat-badge {
        font-size: 12px;
        font-weight: 700;
        padding: 6px 12px;
        border-radius: 8px;
        background-color: #FFFFFF;
        border: 1px solid #F4A261;
        color: #92400E !important;
        white-space: nowrap;
        cursor: help;
    }

    .row-status-bar {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        margin-bottom: 14px;
    }
    .status-pill {
        font-size: 12px;
        font-weight: 700;
        padding: 5px 12px;
        border-radius: 20px;
        white-space: nowrap;
    }
    .status-pill.reliability-good { background:#ECFDF5; color:#047857 !important; border:1px solid #A7F3D0; }
    .status-pill.reliability-mid { background:#FFFBEB; color:#92400E !important; border:1px solid #FDE68A; }
    .status-pill.reliability-low { background:#FEF2F2; color:#B91C1C !important; border:1px solid #FECACA; }
    .status-pill.impairment-warn { background:#FEF2F2; color:#B91C1C !important; border:1px solid #FECACA; }
    .status-pill.neutral { background:#F1F5F9; color:#334155 !important; border:1px solid #E2E8F0; }
    .status-pill.tier-a { background:#ECFDF5; color:#047857 !important; border:1px solid #A7F3D0; }
    .status-pill.tier-b { background:#F1F5F9; color:#334155 !important; border:1px solid #E2E8F0; }
    .status-pill.tier-c { background:#FFF7ED; color:#9A3412 !important; border:1px solid #FED7AA; }

    .finstat-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 12px;
        margin-top: 10px;
        margin-bottom: 6px;
    }
    .finstat-item {
        background-color: #FAFAFA;
        border: 1.5px solid #E5E5E5;
        border-radius: 10px;
        padding: 12px 16px;
    }
    .finstat-label {
        font-size: 12px;
        color: #6B7280 !important;
        font-weight: 700;
        margin-bottom: 4px;
    }
    .finstat-value {
        font-size: 16px;
        color: #111827 !important;
        font-weight: 800;
    }
    @media (max-width: 900px) {
        .finstat-grid { grid-template-columns: repeat(2, 1fr); }
    }

    .overview-grid {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 10px;
        margin-top: 8px;
        margin-bottom: 6px;
    }
    .overview-cell {
        background-color: #FAFAFA;
        border: 1.5px solid #E5E5E5;
        border-radius: 10px;
        padding: 10px 14px;
    }
    .overview-label {
        font-size: 12px;
        color: #6B7280 !important;
        font-weight: 700;
        margin-bottom: 4px;
    }
    .overview-value {
        font-size: 15px;
        color: #111827 !important;
        font-weight: 800;
    }
    .overview-value.value-up { color: #D93025 !important; }
    .overview-value.value-down { color: #2563EB !important; }
    .overview-subvalue {
        font-size: 11.5px;
        font-weight: 700;
        margin-top: 4px;
    }
    .overview-subvalue.vol-high { color: #D97706 !important; }
    .overview-subvalue.vol-low { color: #64748B !important; }
    @media (max-width: 900px) {
        .overview-grid { grid-template-columns: repeat(2, 1fr); }
    }

    .indicator-card {
        background-color: #FFFFFF;
        border: 1.5px solid #E5E7EB;
        border-left: 5px solid #F4A261;
        border-radius: 10px;
        padding: 14px 18px;
        margin-bottom: 12px;
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.02);
    }
    .indicator-card-title {
        font-size: 15px;
        font-weight: 800;
        color: #111827 !important;
        margin-bottom: 4px;
    }
    .indicator-card-def {
        font-size: 12.5px;
        color: #6B7280 !important;
        margin-bottom: 8px;
    }
    .indicator-card-desc {
        font-size: 13.5px;
        color: #374151 !important;
        line-height: 1.55;
    }
    </style>

""",
    unsafe_allow_html=True,
)

# Dark-mode overrides are injected as real HTML/CSS only when dark mode is active.
# The CSS is built with explicit placeholder replacement so CSS braces and percent
# values cannot interfere with Python string parsing/formatting.
if THEME_MODE == "dark":
    dark_css = """
        <style>
        html, body, [data-testid="stAppViewContainer"], .stApp,
        [data-testid="stHeader"], [data-testid="stToolbar"],
        [data-testid="stMain"], [data-testid="stSidebar"] {
            background-color: __THEME_PAGE__ !important;
            color: __THEME_TEXT__ !important;
        }

        p, span, div, label, h1, h2, h3, h4, h5, h6,
        a, b, strong, small, summary {
            color: __THEME_TEXT__ !important;
        }

        [data-testid="stMetric"],
        [data-testid="stMetric"] *,
        [data-testid="stWidgetLabel"],
        [data-testid="stWidgetLabel"] *,
        [data-testid="stRadio"],
        [data-testid="stRadio"] *,
        [data-testid="stCheckbox"],
        [data-testid="stCheckbox"] *,
        [data-testid="stToggle"],
        [data-testid="stToggle"] *,
        [data-testid="stSelectbox"],
        [data-testid="stSelectbox"] *,
        [data-testid="stNumberInput"],
        [data-testid="stNumberInput"] *,
        [data-testid="stForm"],
        [data-testid="stForm"] *,
        [data-baseweb="radio"],
        [data-baseweb="checkbox"],
        [data-baseweb="select"],
        [role="radio"],
        [role="switch"] {
            color: __THEME_TEXT__ !important;
        }

        input, textarea {
            background-color: __THEME_SURFACE__ !important;
            color: __THEME_TEXT__ !important;
            border-color: __THEME_BORDER__ !important;
            caret-color: __THEME_ACCENT__ !important;
        }

        [data-baseweb="select"] > div {
            background-color: __THEME_SURFACE__ !important;
            color: __THEME_TEXT__ !important;
            border-color: __THEME_BORDER__ !important;
        }

        [data-testid="stButton"] button,
        [data-testid="stLinkButton"] a,
        [data-testid="stDownloadButton"] button {
            background-color: __THEME_SURFACE__ !important;
            color: __THEME_TEXT__ !important;
            border-color: __THEME_BORDER__ !important;
        }

        [data-testid="stButton"] button *,
        [data-testid="stLinkButton"] a *,
        [data-testid="stDownloadButton"] button * {
            color: __THEME_TEXT__ !important;
        }

        [data-testid="stButton"] button:hover,
        [data-testid="stLinkButton"] a:hover,
        [data-testid="stDownloadButton"] button:hover {
            background-color: __THEME_SURFACE_WARM__ !important;
            color: __THEME_ACCENT_STRONG__ !important;
            border-color: __THEME_ACCENT__ !important;
        }

        [data-testid="stButton"] button:hover *,
        [data-testid="stLinkButton"] a:hover * {
            color: __THEME_ACCENT_STRONG__ !important;
        }

        [data-testid="stButton"] button[kind="primary"],
        [data-testid="stButton"] button[kind="primary"] *,
        [data-testid="stButton"] button[data-testid="baseButton-primary"],
        [data-testid="stButton"] button[data-testid="baseButton-primary"] * {
            background-color: __THEME_ACCENT_STRONG__ !important;
            color: #FFFFFF !important;
            border-color: __THEME_ACCENT__ !important;
        }

        [data-testid="stToggle"] label,
        [data-testid="stToggle"] label *,
        [role="switch"] {
            color: __THEME_TEXT__ !important;
        }

        [role="switch"][aria-checked="true"] {
            background-color: __THEME_ACCENT__ !important;
            border-color: __THEME_ACCENT__ !important;
        }

        [data-testid="stExpander"] summary,
        [data-testid="stExpander"] summary *,
        [data-testid="stExpander"] details,
        [data-testid="stTabs"] *,
        [data-testid="stDataFrame"] * {
            color: __THEME_TEXT__ !important;
        }

        [data-testid="stExpander"] details,
        [data-testid="stExpander"] summary {
            background-color: __THEME_SURFACE__ !important;
            border-color: __THEME_BORDER__ !important;
        }

        /* Advanced indicator settings: prevent Streamlit expander/form clipping or nested scrolling. */
        [data-testid="stExpander"] details,
        [data-testid="stExpander"] [data-testid="stExpanderDetails"],
        [data-testid="stExpander"] [data-testid="stExpanderDetails"] > div,
        [data-testid="stExpander"] [data-testid="stForm"],
        [data-testid="stExpander"] [data-testid="stForm"] > div {
            max-height: none !important;
            height: auto !important;
            min-height: 0 !important;
            overflow: visible !important;
        }
        [data-testid="stExpander"] [data-testid="stForm"] [data-testid="column"] {
            min-width: 0 !important;
            overflow: visible !important;
        }
        [data-testid="stExpander"] [data-testid="stNumberInput"] {
            width: 100% !important;
            min-width: 0 !important;
        }

        [data-testid="stTabs"] [aria-selected="true"] {
            border-bottom-color: __THEME_ACCENT__ !important;
        }

        .logo-box,
        .quote-box-v2,
        .sketch-card,
        .sketch-item-box,
        .grade-hero-box,
        .finstat-item,
        .overview-cell,
        .indicator-card {
            background-color: __THEME_SURFACE__ !important;
            color: __THEME_TEXT__ !important;
            border-color: __THEME_BORDER__ !important;
        }

        .quote-box-v2,
        .grade-hero-box {
            background-color: __THEME_SURFACE_WARM__ !important;
            border-color: __THEME_ACCENT__ !important;
        }

        .quote-box-v2 *,
        .grade-hero-box *,
        .sketch-card *,
        .sketch-item-box *,
        .finstat-item *,
        .overview-cell *,
        .indicator-card * {
            color: __THEME_TEXT__ !important;
        }

        .overview-label,
        .finstat-label,
        .sketch-item-desc,
        .indicator-card-def,
        .overview-subvalue {
            color: __THEME_TEXT_MUTED__ !important;
        }

        .quote-divider {
            border-top-color: __THEME_ACCENT__ !important;
        }

        .status-pill,
        .mini-stat-badge {
            border-color: __THEME_BORDER__ !important;
            background-color: __THEME_SURFACE_MUTED__ !important;
            color: __THEME_TEXT__ !important;
        }

        .value-up,
        .reliability-good,
        .vol-high {
            color: __THEME_POSITIVE__ !important;
        }

        .value-down {
            color: __THEME_NEGATIVE__ !important;
        }

        .vol-low,
        .neutral,
        .reliability-mid,
        .reliability-low {
            color: __THEME_TEXT_MUTED__ !important;
        }

        .impairment-warn,
        .tier-c {
            color: __THEME_WARNING_TEXT__ !important;
            background-color: __THEME_WARNING_BG__ !important;
            border-color: __THEME_ACCENT__ !important;
        }

        .ad-box-tall {
            background-color: __THEME_SURFACE_MUTED__ !important;
            color: __THEME_TEXT_MUTED__ !important;
            border-color: __THEME_BORDER__ !important;
        }

        [style*="#1A1A1A"],
        [style*="#111827"],
        [style*="#0F172A"],
        [style*="#4B5563"] {
            color: __THEME_TEXT__ !important;
        }

        [style*="#92400E"],
        [style*="#9A3412"] {
            color: __THEME_WARNING_TEXT__ !important;
        }

        [style*="#DC2626"],
        [style*="#D93025"] {
            color: __THEME_POSITIVE__ !important;
        }

        [style*="#2563EB"] {
            color: __THEME_NEGATIVE__ !important;
        }

        [style*="#16A34A"],
        [style*="#047857"] {
            color: __THEME_SUCCESS__ !important;
        }

        [style*="#64748B"],
        [style*="#6B7280"],
        [style*="#475569"],
        [style*="#334155"],
        [style*="#94A3B8"],
        [style*="#888888"] {
            color: __THEME_TEXT_MUTED__ !important;
        }

        [style*="#D97706"] {
            color: __THEME_ACCENT_STRONG__ !important;
        }

        [style*="#FFFFFF"],
        [style*="#ffffff"],
        [style*="#FFFDF9"],
        [style*="#FAFAFA"],
        [style*="#F8FAFC"],
        [style*="#F1F5F9"] {
            background-color: __THEME_SURFACE__ !important;
        }
        </style>
    """
    theme_replacements = {
        "__THEME_PAGE__": THEME["page"],
        "__THEME_SURFACE__": THEME["surface"],
        "__THEME_SURFACE_WARM__": THEME["surface_warm"],
        "__THEME_SURFACE_MUTED__": THEME["surface_muted"],
        "__THEME_TEXT__": THEME["text"],
        "__THEME_TEXT_MUTED__": THEME["text_muted"],
        "__THEME_BORDER__": THEME["border"],
        "__THEME_ACCENT__": THEME["accent"],
        "__THEME_ACCENT_STRONG__": THEME["accent_strong"],
        "__THEME_POSITIVE__": THEME["positive"],
        "__THEME_NEGATIVE__": THEME["negative"],
        "__THEME_SUCCESS__": THEME["success"],
        "__THEME_WARNING_BG__": THEME["warning_bg"],
        "__THEME_WARNING_TEXT__": THEME["warning_text"],
    }
    for placeholder, value in theme_replacements.items():
        dark_css = dark_css.replace(placeholder, value)

    st.markdown(dark_css, unsafe_allow_html=True)

# ==========================================
# 2. 데이터 및 세션 상태 초기화
# ==========================================
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "YOUR_SUPABASE_URL")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "YOUR_SUPABASE_KEY")


@st.cache_resource
def init_supabase():
    try:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception:
        return None


supabase = init_supabase()

def get_investor_quotes():
    """investor_quotes 테이블에서 active=true인 명언 전체를 가져옴
    (RLS: anon/authenticated는 active=true 행만 SELECT 가능하도록 이미 정책 설정돼 있음).
    1시간 캐싱 후, 렌더링할 때마다 random.choice()로 하나씩 뽑아 보여줌."""
    fallback = [
        {
             "investor_name": "Warren Buffett",
             "investor_name_ko": "워런 버핏",
             "investor_affiliation": "Berkshire Hathaway",
             "investor_affiliation_ko": "버크셔 해서웨이",
             "quote_en": "Be fearful when others are greedy, and greedy when others are fearful.",
             "quote_ko": "남들이 탐욕스러워할 때 두려워하고, 남들이 두려워할 때 탐욕스러워져라.",
             "image_url": None,
        }
    ]
    if not supabase:
        return fallback
    try:
        res = (
            supabase.table("investor_quotes")
            .select(    
                "investor_name,"
                "investor_name_ko,"
                "investor_affiliation,"
                "investor_affiliation_ko,"
                "quote_en,"
                "quote_ko,"
                "image_url"
            )
            .eq("active", True)
            .execute()
        )
        if res.data and len(res.data) > 0:
            return res.data
    except Exception:
        pass
    return fallback


def render_quote_box():
    """상단 명언 박스 렌더링 (인물 사진 + 영문 명언 + 국문 번역 + 출처)"""
    quotes = get_investor_quotes()
    q = random.choice(quotes)

    name = q.get("investor_name_ko") or q.get("investor_name", "")
    affiliation = q.get("investor_affiliation_ko") or q.get("investor_affiliation", "")
    quote_en = q.get("quote_en", "")
    quote_ko = q.get("quote_ko", "")
    image_url = q.get("image_url")

    if image_url:
        photo_html = (
            f"<img class='quote-photo' src='{image_url}' alt='{name}' "
            f"onerror=\"this.style.display='none'; this.nextElementSibling.style.display='flex';\"/>"
            f"<div class='quote-photo-fallback' style='display:none;'>👨‍💼</div>"
        )
    else:
        photo_html = "<div class='quote-photo-fallback'>👨‍💼</div>"

    st.markdown(
        f"""
        <div class='quote-box-v2'>
            <div class='quote-photo-wrap'>{photo_html}</div>
            <div class='quote-content'>
                <div class='quote-en-row'>
                    <span class='quote-mark'>&ldquo;</span>
                    <span class='quote-en'>{quote_en}</span>
                </div>
                <hr class='quote-divider' />
                <div class='quote-ko'>{quote_ko}</div>
                <div class='quote-author'>&mdash; {name}</div>
                <div class='quote-affiliation'>{affiliation}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=3600)
def get_combined_stock_db():
    us_stocks = []
    try:
        # SEC 분류 DB의 전체 eligible US 종목을 검색창에 사용한다.
        # PostgREST 1000행 제한을 피하기 위해 페이지네이션한다.
        all_us_rows = []
        page_size = 1000
        start = 0
        while True:
            res = (
                supabase.table("US_Companies")
                .select(
                    "ticker, company_name, company_name_ko, exchange, is_fundamental_eligible"
                )
                .eq("is_fundamental_eligible", True)
                .range(start, start + page_size - 1)
                .execute()
            )
            rows = res.data
            if not rows:
                break
            all_us_rows.extend(rows)
            if len(rows) < page_size:
                break
            start += page_size

        for row in all_us_rows:
            exchange = row.get("exchange") or "US"
            flag = "🇺🇸"
            ticker = str(row.get("ticker") or "")
            company_name = str(row.get("company_name") or ticker or "")
            company_name_ko = str(row.get("company_name_ko") or "")
            us_stocks.append(
                {
                    "ticker": ticker,
                    "name": company_name,
                    "aliases": aliases_for(ticker, company_name, company_name_ko),
                    "exch": f"Equities - {exchange}",
                    "flag": flag,
                }
            )

        us_stocks = [row for row in us_stocks if row["ticker"]]
        if not us_stocks:
            raise ValueError("US_Companies에서 검색 가능한 종목이 없습니다.")
    except Exception:
        us_stocks = [
            {"ticker": "AAPL", "name": "Apple Inc.", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
            {"ticker": "NVDA", "name": "NVIDIA Corporation", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
            {"ticker": "TSLA", "name": "Tesla Inc.", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
            {"ticker": "MSFT", "name": "Microsoft Corp.", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
            {"ticker": "AMZN", "name": "Amazon.com Inc.", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
            {"ticker": "GOOGL", "name": "Alphabet Inc.", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
            {"ticker": "META", "name": "Meta Platforms Inc.", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
            {"ticker": "PLTR", "name": "Palantir Technologies", "exch": "Equities - NYSE", "flag": "🇺🇸"},
            {"ticker": "P", "name": "Pure Storage Inc", "exch": "Equities - NYSE", "flag": "🇺🇸"},
        ]
        us_stocks = [
            {**row, "aliases": aliases_for(row["ticker"], row["name"])}
            for row in us_stocks
        ]

    kr_stocks = []
    try:
        # ⚠️ 2026-09: KRX 정보데이터시스템이 로그인 필수 정책으로 바뀌면서 fdr.StockListing("KRX")
        # 호출이 막힘 (Dart_Raw_Cache 삭제와는 무관한 별개 이슈). 이미 Supabase Fundamental
        # 테이블에 전체 KRX 종목의 stock_code/stock_name이 있으므로, KRX/fdr/pykrx 없이
        # 여기서 바로 가져온다. 반환 형식(ticker/name/exch/flag)은 기존과 완전히 동일하게 유지.
        # PostgREST 1000행 기본 제한 페이지네이션 처리 (전체 약 2,876개 종목).
        all_rows = []
        page_size = 1000
        start = 0
        while True:
            res = (
                supabase.table("Fundamental")
                .select("stock_code, stock_name")
                .range(start, start + page_size - 1)
                .execute()
            )
            rows = res.data
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            start += page_size

        if not all_rows:
            raise ValueError("Supabase Fundamental 테이블에서 종목을 하나도 가져오지 못함")

        for row in all_rows:
            ticker = str(row["stock_code"])
            company_name = str(row["stock_name"])
            kr_stocks.append(
                {
                    "ticker": ticker,
                    "name": company_name,
                    "aliases": aliases_for(ticker, company_name),
                    "exch": "Equities - KRX",
                    "flag": "🇰🇷",
                }
            )
    except Exception:
        kr_stocks = [
            {"ticker": "005930", "name": "삼성전자", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
            {"ticker": "000660", "name": "SK하이닉스", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
            {"ticker": "005380", "name": "현대차", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
            {"ticker": "035420", "name": "NAVER", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
            {"ticker": "035720", "name": "카카오", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
        ]
        kr_stocks = [
            {**row, "aliases": aliases_for(row["ticker"], row["name"])}
            for row in kr_stocks
        ]

    return us_stocks + kr_stocks


def get_stock_data(code):
    """Load the correct fundamental source for KR numeric codes or US tickers."""
    supabase_data = None
    us_company_data = None

    if supabase:
        try:
            if str(code).isdigit():
                res = (
                    supabase.table("Fundamental")
                    .select("*")
                    .eq("stock_code", code)
                    .execute()
                )
                if res.data:
                    supabase_data = res.data[0]
            else:
                ticker_code = str(code).upper()
                us_res = (
                    supabase.table("US_Fundamental")
                    .select("*")
                    .eq("ticker", ticker_code)
                    .execute()
                )
                if us_res.data:
                    supabase_data = us_res.data[0]

                company_res = (
                    supabase.table("US_Companies")
                    .select(
                        "ticker,cik,company_name,sector_common,sector_common_ko,"
                        "company_type,scoring_profile"
                    )
                    .eq("ticker", ticker_code)
                    .execute()
                )
                if company_res.data:
                    us_company_data = company_res.data[0]
        except Exception:
            pass

    ticker_symbol = f"{code}.KS" if str(code).isdigit() else str(code).upper()
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        hist = ticker.history(period="1y")
    except Exception:
        info, hist = {}, pd.DataFrame()

    result = {
        "stock_name": (
            (us_company_data or {}).get("company_name")
            if not str(code).isdigit()
            else (supabase_data or {}).get("stock_name")
        ) or (supabase_data or {}).get("company_name") or info.get("shortName", code),
        "stock_price": (supabase_data or {}).get("stock_price") or info.get("currentPrice", 0),
        "hist": hist,
        "info": info,
        "supabase_data": supabase_data,
        "us_company_data": us_company_data,
    }
    return result


def render_us_fundamental_report(code, data):
    """Render the US SEC scoring report."""
    us_data = data.get("supabase_data") or {}
    company = data.get("us_company_data") or {}
    profile = company.get("scoring_profile") or "standard"
    profile_label = PROFILE_LABELS.get(profile, profile.title())
    profile_desc = PROFILE_DESCRIPTIONS.get(profile, "미국 기업용 펀더멘탈 모델")
    period_scores = us_data.get("period_scores") or {}

    col_logo, col_quote, col_login = st.columns([1.0, 6.8, 1.0])

    with col_logo:
        st.markdown("<div class='logo-box'>📈 Fundamental</div>", unsafe_allow_html=True)

    with col_quote:
        render_quote_box()

    with col_login:
        render_theme_toggle("theme_toggle_us_report")
        if st.button("⬅️ 메인으로", use_container_width=True, key="us_report_home"):
            current_theme = THEME_MODE
            st.query_params.clear()
            if current_theme == "dark":
                st.query_params["theme"] = "dark"
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    left_ad, main_content, right_ad = st.columns([0.6, 6.8, 0.6])

    with left_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

    with main_content:
        company_name = us_data.get("company_name") or data.get("stock_name") or code
        sector = (
            company.get("sector_common_ko")
            or company.get("sector_common")
            or us_data.get("sector")
            or "미분류"
        )
        company_type = company.get("company_type") or "standard"

        st.markdown(f"## 🇺🇸 [{company_name}] 미국 펀더멘탈 방어력 분석")
        st.caption(f"SEC 공시 기반 · {profile_label} · {profile_desc}")
        render_home_stock_news(company_name, code, limit=6)

        if not period_scores:
            st.warning(
                "⚠️ 아직 이 종목의 미국 펀더멘탈 스코어 데이터가 없습니다. "
                "SEC 수집기가 데이터를 완료하면 이 화면에 자동 반영됩니다."
            )
        else:
            latest = period_scores.get("1y") or next(iter(period_scores.values()))
            latest_scores = latest.get("avg") or latest.get("worst") or {}
            total_score = latest_scores.get("total_score")
            grade = latest_scores.get("grade", "N/A")
            coverage_pct = latest_scores.get("coverage_pct")
            missing_count = latest_scores.get("missing_metric_count")
            cap = latest_scores.get("score_cap")
            confidence_level = latest_scores.get("confidence_level")
            reliability = us_data.get("data_reliability")

            if coverage_pct is None:
                metric_scores_for_coverage = latest_scores.get("metric_scores") or {}
                total_weight = sum(
                    float(entry.get("weight", 0) or 0)
                    for entry in metric_scores_for_coverage.values()
                )
                available_weight = sum(
                    float(entry.get("weight", 0) or 0)
                    for entry in metric_scores_for_coverage.values()
                    if entry.get("value") is not None
                )
                if total_weight:
                    coverage_pct = round(available_weight / total_weight * 100.0, 1)
                if confidence_level is None and coverage_pct is not None:
                    confidence_level = (
                        "high" if coverage_pct >= 90
                        else "medium" if coverage_pct >= 75
                        else "low" if coverage_pct >= 60
                        else "insufficient"
                    )
            if cap is None and coverage_pct is not None:
                cap = (
                    100.0 if coverage_pct >= 90
                    else 92.0 if coverage_pct >= 75
                    else 82.0 if coverage_pct >= 60
                    else 70.0
                )

            badge_text = (
                f"**🏷️ 모델:** {profile_label} · "
                f"**🏭 유형:** {company_type} · "
                f"**📚 업종:** {sector}"
            )
            if coverage_pct is not None:
                badge_text += f" · **📐 데이터 완성도:** {coverage_pct:.1f}%"
            if confidence_level:
                confidence_label = {
                    "high": "높음", "medium": "보통",
                    "low": "낮음", "insufficient": "부족",
                }.get(confidence_level, confidence_level)
                badge_text += f" · **📋 점수 완성도:** {confidence_label}"
            if missing_count is not None:
                badge_text += f" · **🧩 결측:** {missing_count}개"
            if reliability:
                badge_text += f" · **📋 신뢰도:** {reliability}"

            st.markdown(f"### {total_score if total_score is not None else 'N/A'} / 100  ·  {grade}")
            st.markdown(badge_text)

            if confidence_level in {"low", "insufficient"}:
                st.warning(
                    "⚠️ 이 점수는 일부 핵심 지표가 결측되어 있습니다. "
                    "결측을 0점으로 넣지 않고 가용 지표만 재정규화했으며, 커버리지 상한을 적용했습니다."
                )

            review_sector = company.get("sector_common")
            if profile == "standard" and review_sector in {"financials", "real_estate", "other"}:
                st.info(
                    "ℹ️ 자동 분류상 Standard 프로필이지만 업종 대분류가 "
                    f"'{review_sector}'입니다. SIC 기반 분류의 경계 사례이므로 업종/프로필 검토 대상으로 표시합니다."
                )

            if cap is not None and cap < 100:
                st.caption(f"ℹ️ 데이터 커버리지 때문에 해당 기간 점수 상한이 {cap:.0f}점으로 적용됐습니다.")

            snapshot = us_data.get("snapshot") or {}
            flows = snapshot.get("flows") or {}

            st.markdown("#### 📋 최신 SEC 재무 스냅샷")
            snap_cols = st.columns(6)
            snap_items = [
                ("회계기간", snapshot.get("fiscal_end") or us_data.get("snapshot_fiscal_end") or "N/A"),
                ("공시형태", snapshot.get("form") or us_data.get("snapshot_form") or "N/A"),
                ("공시일", snapshot.get("filed") or us_data.get("snapshot_filed") or "N/A"),
                ("매출", (flows.get("revenue") or {}).get("reported", {}).get("value")),
                ("영업이익", (flows.get("operating_income") or {}).get("reported", {}).get("value")),
                ("순이익", (flows.get("net_income") or {}).get("reported", {}).get("value")),
            ]

            for idx, (label, value) in enumerate(snap_items):
                with snap_cols[idx]:
                    if isinstance(value, (int, float)):
                        st.metric(label, f"USD {float(value):,.0f}")
                    else:
                        st.metric(label, str(value))

            period_keys = [p for p in ("1y", "3y", "5y", "10y") if p in period_scores]
            period_labels = {"1y": "📅 1년", "3y": "📆 3년", "5y": "🗓️ 5년", "10y": "📈 10년"}
            tabs = st.tabs([period_labels[p] for p in period_keys])

            metric_meta = {
                "revenue_growth": ("매출 성장률", "Revenue Growth", "%"),
                "eps_growth": ("EPS 성장률", "EPS Growth", "%"),
                "opm": ("영업이익률", "OPM", "%"),
                "roic": ("투하자본이익률", "ROIC", "%"),
                "roa": ("총자산이익률", "ROA", "%"),
                "debt_rate": ("부채비율", "Debt Rate", "%"),
                "quick_ratio": ("당좌비율", "Quick Ratio", "배"),
                "interest_coverage": ("이자보상배율", "Interest Coverage", "배"),
                "ocf_ratio": ("영업현금흐름 비율", "OCF Ratio", "배"),
                "sga_ratio": ("판관비 비율", "SG&A Ratio", "%"),
                "downturn_defense": ("하락장 방어력", "Downturn Defense", "%p"),
                "debt_capital": ("부채·자본 구조", "Debt / Capital", "%"),
                "ocf_debt": ("영업현금흐름 / 부채", "OCF / Debt", "%"),
                "fcf_debt": ("잉여현금흐름 / 부채", "FCF / Debt", "%"),
                "dividend_coverage": ("배당커버리지", "Dividend Coverage", "배"),
                "dividend_payout": ("배당성향", "Dividend Payout", "%"),
            }

            for pkey, tab in zip(period_keys, tabs):
                with tab:
                    pdata = period_scores[pkey]
                    score_view = st.radio(
                        "채점 기준",
                        options=["avg", "worst"],
                        format_func=lambda v: "📊 평균 기준 (꾸준함)" if v == "avg" else "🛡️ 최악 기준 (위기 대응력)",
                        horizontal=True,
                        key=f"us_view_mode_{pkey}",
                    )
                    scored = pdata.get(score_view) or {}
                    st.caption(
                        f"사용 기간: {pdata.get('years_used') or '-'} · "
                        f"성장률은 CAGR, 비율 지표는 기간 내 {'평균값' if score_view == 'avg' else '최악값'}"
                    )

                    sub_scores = scored.get("sub_scores") or {}
                    if sub_scores:
                        sub_text = "  ".join(
                            f"**{name}:** {value:.1f}"
                            for name, value in sub_scores.items()
                            if value is not None
                        )
                        if sub_text:
                            st.markdown(sub_text)

                    metric_scores = scored.get("metric_scores") or {}
                    for metric, entry in metric_scores.items():
                        meta = metric_meta.get(metric)
                        if not meta:
                            continue

                        title, english, unit = meta
                        value = entry.get("value")
                        score = entry.get("score")
                        weight = entry.get("weight")
                        contribution = entry.get("weighted_score")
                        excluded = entry.get("excluded_from_total", False)

                        value_display = "N/A" if value is None else f"{float(value):,.2f}{unit}"
                        score_display = "결측 제외" if excluded else (
                            f"{score}/10" if score is not None else "N/A"
                        )
                        contribution_display = "총점 제외" if excluded else (
                            f"{contribution:.1f}/{weight}점"
                            if contribution is not None and weight is not None
                            else "-"
                        )

                        with st.expander(
                            f"{title} | {value_display} | {score_display} | 총점 기여 {contribution_display}"
                        ):
                            st.caption(english)
                            if metric in ("debt_rate", "debt_capital", "sga_ratio", "dividend_payout"):
                                st.write("낮을수록 재무·비용 부담이 작다고 보아 점수가 높아집니다.")
                            elif metric in ("quick_ratio", "interest_coverage", "ocf_ratio", "dividend_coverage"):
                                st.write("배수가 높을수록 단기 재무 여력 또는 현금·커버리지 수준이 높습니다.")
                            elif metric == "downturn_defense":
                                st.write("실제 과거 하락장에서 시장 대비 방어한 정도를 반영합니다.")
                            else:
                                st.write("미국 기업 재무구조에 맞춘 US 전용 점수구간과 가중치로 계산됩니다.")
                            st.caption(
                                f"배점 {weight}점 · 실제 획득 {contribution if contribution is not None else '-'}점"
                            )

                            if entry.get("is_extreme"):
                                st.caption("ℹ️ 극단값으로 표시된 수치입니다. 점수 자체에는 추가 패널티를 주지 않습니다.")

    with right_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def get_chart_history(code, period="1y"):
    """Chart Analysis 전용 - 공부/스윙매매 목적이라 실시간 갱신은 불필요해서 6시간 캐시.
    get_stock_data()와 분리해둔 이유: 저 함수는 매 페이지뷰마다 yfinance를 새로 호출하는
    기존 동작이라 - 여기서 캐시 정책을 바꿔도 메인 펀더멘탈 리포트 페이지는 영향 없음.
    ⚠️ yfinance가 가끔 배당락/액션 처리 과정에서 같은 날짜가 중복되거나 정렬이 흐트러진
    행을 섞어 반환하는 경우가 있어서 - 이러면 차트에서 선이 시간순으로 안 이어지고
    지그재그로 튀어보임(RSI 톱니, 거래량 줄무늬의 흔한 원인) - 방어적으로 정렬+중복제거.
    ⚠️ 코스피(.KS)/코스닥(.KQ) 접미사 문제 - 국내 종목이 어느 시장인지 별도 조회 없이,
    .KS로 먼저 시도해보고 데이터가 비어있으면 .KQ로 한 번 더 시도함 (일부 종목이
    데이터를 못 가져오던 원인 - 코스닥 종목에 .KS를 붙이면 야후 파이낸스가 못 찾음)."""
    try:
        if code.isdigit():
            df = yf.Ticker(f"{code}.KS").history(period=period)
            if df.empty:
                df = yf.Ticker(f"{code}.KQ").history(period=period)
        else:
            df = yf.Ticker(code).history(period=period)
        return df[~df.index.duplicated(keep="last")].sort_index() if not df.empty else df
    except Exception:
        return pd.DataFrame()


HISTORICAL_PATTERN_CACHE_VERSION = "2026-09-20-tech-v2"


@st.cache_data(ttl=1800, show_spinner=False)
def get_cached_pattern_analysis(code, params_items, cache_version=HISTORICAL_PATTERN_CACHE_VERSION):
    """일봉 max history 기반 과거 유사상황 통계.
    분봉/주봉 UI와 무관하게 5/20/60 '거래일' 결과를 유지한다.
    cache_version는 historical_pattern.py 로직 변경 시 이전 결과가 남지 않도록
    의도적으로 캐시 키에 포함한다."""
    try:
        daily_df = get_chart_history(code, period="max")
        if daily_df is None or daily_df.empty or len(daily_df) < 150:
            return {}
        params = dict(params_items)
        daily_indicators = compute_all_indicators(daily_df, params=params)
        return analyze_all_indicator_patterns(daily_df, daily_indicators)
    except Exception:
        return {}



def get_chart_history_intraday(code, yf_interval="30m", period="60d"):
    """분봉 전용. yfinance 자체가 분봉은 최근 구간만 제공하는 제약이 있어서
    (30분봉 기준 최근 60일 정도) 일/주/월봉처럼 전체 기간을 볼 수는 없음 - 그래서
    일봉(get_chart_history)과 별도 함수 + 짧은 캐시(30분)로 분리해둠.
    ⚠️ get_chart_history()와 동일한 이유로 .KS 실패 시 .KQ(코스닥)로 폴백."""
    try:
        if code.isdigit():
            df = yf.Ticker(f"{code}.KS").history(period=period, interval=yf_interval)
            if df.empty:
                df = yf.Ticker(f"{code}.KQ").history(period=period, interval=yf_interval)
        else:
            df = yf.Ticker(code).history(period=period, interval=yf_interval)
        return df[~df.index.duplicated(keep="last")].sort_index() if not df.empty else df
    except Exception:
        return pd.DataFrame()


def resample_ohlcv(df, rule):
    """일봉 데이터를 주/월/분기/년봉으로 묶어줌 - 새로 데이터를 받아올 필요 없이
    이미 캐시된 일봉(get_chart_history)에서 pandas resample만으로 계산 가능."""
    if df.empty or rule is None:
        return df
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    resampled = df.resample(rule).agg(agg).dropna(subset=["Open"])
    return resampled


INTERVAL_RESAMPLE_RULE = {
    "일봉": None, "주봉": "W", "월봉": "MS", "분기봉": "QS", "년봉": "YS",
}


def render_naver_style_chart(hist_df, indicators, visible_map=None, height=None, is_korean_market=True):
    """네이버증권 스타일 차트 - Plotly.js를 components.html로 직접 임베드.
    st.plotly_chart(서버에서 그려서 넘김)로는 줌/팬 시 y축이 안 따라오는 게 기본 동작이라
    (Plotly는 x축만 자동으로 다시 그리고 y축 범위는 그대로 유지함), 이 함수는 브라우저에서
    직접 Plotly.js를 돌려서 'plotly_relayout' 이벤트(사용자가 줌/팬 할 때 발생)를 잡아
    화면에 보이는 구간의 고가/저가/거래량/MACD 범위로 y축을 다시 계산해서 그려줌.

    visible_map: {"ma": bool, "bb": bool, "ichimoku": bool, "vol_ma": bool,
                  "rsi": bool, "stoch": bool, "adx": bool, "atr": bool, "obv": bool, "mfi": bool, "vwap": bool,
                  "williams_r": bool, "cci": bool, "roc": bool, "psar": bool, "cmf": bool, "macd": bool} - 기본 전부 False(꺼짐).
    가격/거래량 위에 얹히는 오버레이 지표(ma/bb/ichimoku/vol_ma)는 트레이스만 숨기고 칸은 유지하지만,
    RSI/스토캐스틱/MACD는 전용 서브플롯 행이 필요한 지표라 - 체크 안 하면 트레이스뿐 아니라
    그 행(축·그리드·기준선) 자체를 아예 안 만들어서, 빈 표가 남아있는 문제를 없앰.
    ⚠️ 기간 탭(분봉/일봉/주봉/월봉/분기봉/년봉)은 여기서 만들지 않음 - iframe 안에서
    window.parent.location.href로 부모 페이지를 이동시키는 게 Streamlit의 iframe 샌드박스
    정책상 막혀서(클릭해도 아무 반응 없던 원인) 호출부에서 진짜 Streamlit 버튼으로 처리함."""

    vm = {
        "ma": False, "bb": False, "ichimoku": False, "vol_ma": False,
        "rsi": False, "stoch": False, "adx": False,
        "atr": False, "obv": False, "mfi": False, "vwap": False,
        "williams_r": False, "cci": False, "roc": False, "psar": False, "cmf": False,
        "macd": False,
    }
    vm.update(visible_map or {})

    def vis(key):
        return True if vm[key] else "legendonly"

    # 전용 행이 필요한 지표만 row_order에 추가하고, VWAP은 가격 패널 위에 오버레이.
    row_order = ["price", "volume"]
    for row_key in ("rsi", "stoch", "adx", "atr", "obv", "mfi", "williams_r", "cci", "roc", "cmf", "macd"):
        if vm[row_key]:
            row_order.append(row_key)

    n_extra = len(row_order) - 2
    gap = 0.025
    total_gap = gap * (len(row_order) - 1)
    if n_extra == 0:
        vol_h = 0.18
        price_h = 1.0 - vol_h - total_gap
        row_heights = {"price": price_h, "volume": vol_h}
    else:
        vol_h = 0.12
        desired_extra_h = 0.14
        price_h = max(0.30, 1.0 - vol_h - desired_extra_h * n_extra - total_gap)
        extra_h = (1.0 - vol_h - price_h - total_gap) / n_extra
        row_heights = {"price": price_h, "volume": vol_h}
        for r in row_order[2:]:
            row_heights[r] = extra_h

    domains = {}
    top = 1.0
    for r in row_order:
        bottom = top - row_heights[r]
        domains[r] = [round(bottom, 4), round(top, 4)]
        top = bottom - gap

    axis_name = {
        r: ("y" if i == 1 else f"y{i}")
        for i, r in enumerate(row_order, start=1)
    }
    if height is None:
        height = 700 + 220 * n_extra

    # 분봉처럼 하루 안에 캔들이 여러 개인 경우, 날짜만으로 카테고리를 만들면 같은 날의
    # 모든 캔들이 한 자리에 겹쳐버림(분봉 캔들 색이 섞여 보이던 원인) - 캔들 간 평균 간격이
    # 하루보다 훨씬 짧으면(분봉) 시:분까지 포함해서 각 캔들이 고유한 자리를 갖게 함
    if len(hist_df.index) >= 2:
        typical_gap = hist_df.index.to_series().diff().median()
        is_intraday = typical_gap < pd.Timedelta(hours=20)
    else:
        is_intraday = False
    date_fmt = "%Y-%m-%d %H:%M" if is_intraday else "%Y-%m-%d"
    tick_date_fmt = "%m/%d %H:%M" if is_intraday else "%b %d"

    dates = [d.strftime(date_fmt) for d in hist_df.index]

    # 카테고리(순번) 축용 눈금 - 거래일만 순서대로 나열하니 Plotly가 날짜를 자동으로
    # 예쁘게 포맷해주지 않아서, 8개 정도로 골라 직접 라벨을 만들어줌
    n_pts = len(dates)
    tick_step = max(1, n_pts // 8)
    tick_indices = list(range(0, n_pts, tick_step))
    tick_vals = [dates[i] for i in tick_indices]
    tick_text = [hist_df.index[i].strftime(tick_date_fmt) for i in tick_indices]
    o, h, l, c = hist_df["Open"].tolist(), hist_df["High"].tolist(), hist_df["Low"].tolist(), hist_df["Close"].tolist()
    volume = hist_df["Volume"].tolist()
    # 색상 컨벤션: 국내 종목은 상승=빨강/하락=파랑, 해외(미국 등) 종목은 상승=초록/하락=빨강(월가 표준) -
    # 네이버증권도 국내/해외를 이 기준으로 다르게 표시함
    chart_bg = THEME["plot_bg"]
    chart_grid = THEME["plot_grid"]
    chart_axis = THEME["plot_axis"]
    chart_text = THEME["plot_text"]
    if is_korean_market:
        up_color = "#F87171" if THEME_MODE == "dark" else "#DC2626"
        down_color = "#60A5FA" if THEME_MODE == "dark" else "#2563EB"
    else:
        up_color = "#4ADE80" if THEME_MODE == "dark" else "#16A34A"
        down_color = "#F87171" if THEME_MODE == "dark" else "#DC2626"
    vol_colors = [up_color if cc >= oo else down_color for oo, cc in zip(o, c)]

    def s(key):
        return indicators[key].tolist()

    # 최고/최저 지점 라벨용 - 지금 불러온 구간 전체 기준 (네이버처럼 확대/축소할 때마다 다시 계산하진 않음)
    high_val = max(h)
    low_val = min(l)
    high_idx = h.index(high_val)
    low_idx = l.index(low_val)
    current_price = c[-1] if c else None
    high_pct = round((current_price - high_val) / high_val * 100, 2) if current_price and high_val else None
    low_pct = round((current_price - low_val) / low_val * 100, 2) if current_price and low_val else None

    payload = {
        "dates": dates, "open": o, "high": h, "low": l, "close": c,
        "volume": volume, "vol_colors": vol_colors,
        "sma5": s("sma5"), "sma20": s("sma20"), "sma60": s("sma60"), "sma120": s("sma120"),
        "bb_upper": s("bb_upper"), "bb_lower": s("bb_lower"),
        "tenkan": s("tenkan"), "kijun": s("kijun"), "senkou_a": s("senkou_a"), "senkou_b": s("senkou_b"),
        "vol_ma20": s("vol_ma20"),
        "rsi14": s("rsi14"),
        "stoch_k": s("stoch_k"), "stoch_d": s("stoch_d"),
        "adx14": s("adx14"), "plus_di14": s("plus_di14"), "minus_di14": s("minus_di14"),
        "atr14": s("atr14"), "obv": s("obv"), "mfi14": s("mfi14"), "rolling_vwap20": s("rolling_vwap20"),
        "williams_r": s("williams_r"), "cci": s("cci"), "roc": s("roc"), "psar": s("psar"), "cmf": s("cmf"),
        "macd_line": s("macd_line"), "macd_signal": s("macd_signal"), "macd_hist": s("macd_hist"),
    }
    data_json = json.dumps(payload)

    # 오버레이 지표(가격/거래량 패널 위) 트레이스 - 항상 정의, visible로만 켜고 끔
    overlay_traces_js = f"""
                visTrace("5일선", D.sma5, "#16A34A", 1.1, {{ yaxis: "y", visible: {json.dumps(vis("ma"))} }}),
                visTrace("20일선", D.sma20, "#DC2626", 1.1, {{ yaxis: "y", visible: {json.dumps(vis("ma"))} }}),
                visTrace("60일선", D.sma60, "#F97316", 1.1, {{ yaxis: "y", visible: {json.dumps(vis("ma"))} }}),
                visTrace("120일선", D.sma120, "#7C3AED", 1.1, {{ yaxis: "y", visible: {json.dumps(vis("ma"))} }}),
                visTrace("볼린저 상단", D.bb_upper, "rgba(217,119,6,0.4)", 1, {{ yaxis: "y", visible: {json.dumps(vis("bb"))}, line: {{ dash: "dot" }} }}),
                visTrace("볼린저 하단", D.bb_lower, "rgba(217,119,6,0.4)", 1, {{ yaxis: "y", visible: {json.dumps(vis("bb"))}, line: {{ dash: "dot" }}, fill: "tonexty", fillcolor: "rgba(244,162,97,0.06)" }}),
                visTrace("전환선", D.tenkan, "#DC2626", 1, {{ yaxis: "y", visible: {json.dumps(vis("ichimoku"))} }}),
                visTrace("기준선", D.kijun, "#2563EB", 1, {{ yaxis: "y", visible: {json.dumps(vis("ichimoku"))} }}),
                visTrace("선행스팬A", D.senkou_a, "rgba(22,163,74,0.5)", 0.8, {{ yaxis: "y", visible: {json.dumps(vis("ichimoku"))} }}),
                visTrace("구름(선행스팬B)", D.senkou_b, "rgba(220,38,38,0.5)", 0.8, {{ yaxis: "y", visible: {json.dumps(vis("ichimoku"))}, fill: "tonexty", fillcolor: "rgba(148,163,184,0.15)" }}),
                {{ type: "bar", x: D.dates, y: D.volume, name: "거래량", yaxis: "y2", marker: {{ color: D.vol_colors, opacity: 0.9, line: {{ width: 0 }} }} }},
                visTrace("거래량 MA20", D.vol_ma20, "#D97706", 1.1, {{ yaxis: "y2", visible: {json.dumps(vis("vol_ma"))} }}),
                visTrace("Rolling VWAP20", D.rolling_vwap20, "#0EA5E9", 1.3, {{ yaxis: "y", visible: {json.dumps(vis("vwap"))}, line: {{ dash: "dash" }} }}),
                visTrace("Parabolic SAR", D.psar, "#EF4444", 1.1, {{ yaxis: "y", visible: {json.dumps(vis("psar"))}, mode: "markers", marker: {{ size: 5 }} }}),
    """

    # 행(row) 기반 지표 - 체크된 것만 트레이스 자체를 생성 (빈 축이 남지 않도록 동일한 row_order를 사용)
    row_traces_js = ""
    if vm["rsi"]:
        row_traces_js += f"""
                visTrace("RSI(14)", D.rsi14, "#7C3AED", 1.3, {{ yaxis: "{axis_name['rsi']}" }}),
        """
    if vm["stoch"]:
        row_traces_js += f"""
                visTrace("%K", D.stoch_k, "#0EA5E9", 1.2, {{ yaxis: "{axis_name['stoch']}" }}),
                visTrace("%D", D.stoch_d, "#F97316", 1.2, {{ yaxis: "{axis_name['stoch']}" }}),
        """
    if vm["adx"]:
        row_traces_js += f"""
                visTrace("ADX(14)", D.adx14, "#7C3AED", 1.3, {{ yaxis: "{axis_name['adx']}" }}),
                visTrace("+DI", D.plus_di14, "#16A34A", 1.0, {{ yaxis: "{axis_name['adx']}" }}),
                visTrace("-DI", D.minus_di14, "#DC2626", 1.0, {{ yaxis: "{axis_name['adx']}" }}),
        """
    if vm["atr"]:
        row_traces_js += f"""
                visTrace("ATR(14)", D.atr14, "#F97316", 1.3, {{ yaxis: "{axis_name['atr']}" }}),
        """
    if vm["obv"]:
        row_traces_js += f"""
                visTrace("OBV", D.obv, "#0EA5E9", 1.3, {{ yaxis: "{axis_name['obv']}" }}),
        """
    if vm["mfi"]:
        row_traces_js += f"""
                visTrace("MFI(14)", D.mfi14, "#A855F7", 1.3, {{ yaxis: "{axis_name['mfi']}" }}),
        """
    if vm["williams_r"]:
        row_traces_js += f"""
                visTrace("Williams %R(14)", D.williams_r, "#14B8A6", 1.3, {{ yaxis: "{axis_name['williams_r']}" }}),
        """
    if vm["cci"]:
        row_traces_js += f"""
                visTrace("CCI(20)", D.cci, "#8B5CF6", 1.3, {{ yaxis: "{axis_name['cci']}" }}),
        """
    if vm["roc"]:
        row_traces_js += f"""
                visTrace("ROC(12)", D.roc, "#06B6D4", 1.3, {{ yaxis: "{axis_name['roc']}" }}),
        """
    if vm["cmf"]:
        row_traces_js += f"""
                visTrace("CMF(20)", D.cmf, "#22C55E", 1.3, {{ yaxis: "{axis_name['cmf']}" }}),
        """
    if vm["macd"]:
        row_traces_js += f"""
                {{ type: "bar", x: D.dates, y: D.macd_hist, name: "MACD 히스토그램", yaxis: "{axis_name['macd']}",
                   marker: {{ color: "rgba(148,163,184,0.6)" }} }},
                visTrace("MACD선", D.macd_line, "#D97706", 1.2, {{ yaxis: "{axis_name['macd']}" }}),
                visTrace("시그널선", D.macd_signal, "#2563EB", 1.2, {{ yaxis: "{axis_name['macd']}" }}),
        """

    # 행 기반 지표의 y축 정의 + 기준선(80/20 또는 ADX 25) - 체크된 것만
    fixed_ranges = {"rsi": "[0, 100]", "stoch": "[0, 100]", "adx": "[0, 100]", "mfi": "[0, 100]", "williams_r": "[-100, 0]", "cmf": "[-1, 1]"}
    axis_titles = {
        "rsi": "RSI", "stoch": "Stoch", "adx": "ADX / DI",
        "atr": "ATR", "obv": "OBV", "mfi": "MFI", "williams_r": "Williams %R",
        "cci": "CCI", "roc": "ROC %", "cmf": "CMF", "macd": "MACD",
    }
    extra_yaxes_js = ""
    extra_shapes_js = ""
    for row_key in row_order[2:]:
        ax = axis_name[row_key]
        axis_id = "yaxis" if ax == "y" else f"yaxis{ax[1:]}"
        range_clause = f", range: {fixed_ranges[row_key]}" if row_key in fixed_ranges else ""
        extra_yaxes_js += (
            f"""{axis_id}: {{ domain: {json.dumps(domains[row_key])}, anchor: "x", """
            f"""side: "right", title: "{axis_titles[row_key]}"{range_clause} }},"""
        )
        if row_key == "rsi":
            extra_shapes_js += f"""
                    {{ type: "line", xref: "paper", yref: "{ax}", x0: 0, x1: 1, y0: 70, y1: 70, line: {{ color: "{THEME['positive']}", width: 1, dash: "dash" }} }},
                    {{ type: "line", xref: "paper", yref: "{ax}", x0: 0, x1: 1, y0: 30, y1: 30, line: {{ color: "{THEME['success']}", width: 1, dash: "dash" }} }},
        """
        elif row_key == "stoch":
            extra_shapes_js += f"""
                    {{ type: "line", xref: "paper", yref: "{ax}", x0: 0, x1: 1, y0: 80, y1: 80, line: {{ color: "{THEME['positive']}", width: 1, dash: "dash" }} }},
                    {{ type: "line", xref: "paper", yref: "{ax}", x0: 0, x1: 1, y0: 20, y1: 20, line: {{ color: "{THEME['success']}", width: 1, dash: "dash" }} }},
        """
        elif row_key == "adx":
            extra_shapes_js += f"""
                    {{ type: "line", xref: "paper", yref: "{ax}", x0: 0, x1: 1, y0: 25, y1: 25, line: {{ color: "{THEME['accent']}", width: 1, dash: "dash" }} }},
        """
        elif row_key == "mfi":
            extra_shapes_js += f"""
                    {{ type: "line", xref: "paper", yref: "{ax}", x0: 0, x1: 1, y0: 80, y1: 80, line: {{ color: "{THEME['positive']}", width: 1, dash: "dash" }} }},
                    {{ type: "line", xref: "paper", yref: "{ax}", x0: 0, x1: 1, y0: 20, y1: 20, line: {{ color: "{THEME['success']}", width: 1, dash: "dash" }} }},
        """
        elif row_key == "williams_r":
            extra_shapes_js += f"""
                    {{ type: "line", xref: "paper", yref: "{ax}", x0: 0, x1: 1, y0: -20, y1: -20, line: {{ color: "{THEME['positive']}", width: 1, dash: "dash" }} }},
                    {{ type: "line", xref: "paper", yref: "{ax}", x0: 0, x1: 1, y0: -80, y1: -80, line: {{ color: "{THEME['success']}", width: 1, dash: "dash" }} }},
        """
        elif row_key == "cci":
            extra_shapes_js += f"""
                    {{ type: "line", xref: "paper", yref: "{ax}", x0: 0, x1: 1, y0: 100, y1: 100, line: {{ color: "{THEME['positive']}", width: 1, dash: "dash" }} }},
                    {{ type: "line", xref: "paper", yref: "{ax}", x0: 0, x1: 1, y0: -100, y1: -100, line: {{ color: "{THEME['success']}", width: 1, dash: "dash" }} }},
        """
        elif row_key == "cmf":
            extra_shapes_js += f"""
                    {{ type: "line", xref: "paper", yref: "{ax}", x0: 0, x1: 1, y0: 0, y1: 0, line: {{ color: "{THEME['accent']}", width: 1, dash: "dash" }} }},
        """

    dynamic_axis_groups = {}
    for row_key, keys in (
        ("atr", ["atr14"]),
        ("obv", ["obv"]),
        ("cci", ["cci"]),
        ("roc", ["roc"]),
        ("macd", ["macd_line", "macd_signal", "macd_hist"]),
    ):
        if vm.get(row_key):
            dynamic_axis_groups[axis_name[row_key]] = keys
    dynamic_axis_groups_js = json.dumps(dynamic_axis_groups)

    def fmt_price(v):
        return f"{v:,.0f}" if is_korean_market else f"{v:,.2f}"

    annotations_js = ""
    if high_pct is not None and low_pct is not None:
        annotations_js = f"""
                    {{ x: D.dates[{high_idx}], y: {high_val}, xref: "x", yref: "y",
                       text: "최고 {fmt_price(high_val)} ({high_pct:+.2f}%)", showarrow: true, arrowhead: 0,
                       ax: 0, ay: -30, font: {{ size: 11, color: "{chart_text}" }} }},
                    {{ x: D.dates[{low_idx}], y: {low_val}, xref: "x", yref: "y",
                       text: "최저 {fmt_price(low_val)} ({low_pct:+.2f}%)", showarrow: true, arrowhead: 0,
                       ax: 0, ay: 30, font: {{ size: 11, color: "{chart_text}" }} }},
        """

    custom_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
        <style>
            body {{ margin: 0; padding: 0; background: {chart_bg}; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
        </style>
    </head>
    <body>
        <div id="naverStyleChart"></div>
        <div id="chartErrorBox"></div>
        <script>
        try {{
            const D = {data_json};
            const DYNAMIC_EXTRA_AXES = {dynamic_axis_groups_js};

            function visTrace(name, y, color, width, extra) {{
                extra = extra || {{}};
                const trace = Object.assign({{
                    type: "scatter", mode: "lines", x: D.dates, y: y, name: name,
                    line: {{ width: width, color: color }},
                }}, extra);
                if (extra.line) {{
                    trace.line = Object.assign({{ width: width, color: color }}, extra.line);
                }}
                return trace;
            }}

            const traces = [
                {{ type: "candlestick", x: D.dates, open: D.open, high: D.high, low: D.low, close: D.close,
                   name: "가격", yaxis: "y", xaxis: "x",
                   increasing: {{ line: {{ color: "{up_color}", width: 1 }}, fillcolor: "{up_color}" }},
                   decreasing: {{ line: {{ color: "{down_color}", width: 1 }}, fillcolor: "{down_color}" }} }},
                {overlay_traces_js}
                {row_traces_js}
            ];

            const layout = {{
                height: {height},
                margin: {{ l: 55, r: 55, t: 10, b: 30 }},
                paper_bgcolor: "{chart_bg}", plot_bgcolor: "{chart_bg}",
                font: {{ color: "{chart_text}", size: 11 }},
                dragmode: "pan",
                showlegend: true,
                legend: {{ orientation: "h", y: 1.03 }},
                xaxis: {{
                    // 카테고리(순번) 축 - 거래일만 순서대로 나열해서 주말이라는 개념 자체가
                    // 축에 없음. "type: date" + rangebreaks 조합은 Plotly에서 막대(bar) 폭
                    // 계산이 깨지는 걸로 알려진 문제가 있어서(거래량 막대가 얇은 선처럼 보이던
                    // 원인) 아예 이 방식으로 바꿈 - 실제 트레이딩뷰 등도 이렇게 처리함.
                    type: "category", anchor: "y", rangeslider: {{ visible: false }},
                    tickvals: {json.dumps(tick_vals)}, ticktext: {json.dumps(tick_text)},
                }},
                yaxis: {{ domain: {json.dumps(domains['price'])}, anchor: "x", side: "right", title: "가격" }},
                yaxis2: {{ domain: {json.dumps(domains['volume'])}, anchor: "x", side: "right", title: "거래량" }},
                {extra_yaxes_js}
                shapes: [
                    {extra_shapes_js}
                ],
                annotations: [
                    {annotations_js}
                ],
            }};

            const config = {{
                scrollZoom: true, displaylogo: false,
                modeBarButtonsToAdd: ["drawline", "drawopenpath", "drawrect", "eraseshape"],
            }};

            const graphDiv = document.getElementById("naverStyleChart");
            Plotly.newPlot(graphDiv, traces, layout, config).catch(function(err) {{
                document.getElementById("chartErrorBox").innerHTML =
                    "<div style='color:#DC2626; background:#FEF2F2; border:1px solid #FCA5A5; " +
                    "border-radius:8px; padding:14px; margin-top:10px; font-family:monospace; font-size:13px;'>" +
                    "⚠️ Plotly 렌더링 오류: " + (err && err.message ? err.message : err) + "</div>";
            }});

            function visibleIndices(x0, x1) {{
                let i0, i1;
                if (typeof x0 === "number" && typeof x1 === "number") {{
                    i0 = x0; i1 = x1;
                }} else {{
                    // 카테고리 문자열(날짜)로 넘어온 경우 - 더블클릭 리셋 등
                    i0 = D.dates.indexOf(x0);
                    i1 = D.dates.indexOf(x1);
                    if (i0 === -1) i0 = 0;
                    if (i1 === -1) i1 = D.dates.length - 1;
                }}
                const lo = Math.max(0, Math.floor(Math.min(i0, i1)));
                const hi = Math.min(D.dates.length - 1, Math.ceil(Math.max(i0, i1)));
                const idxs = [];
                for (let i = lo; i <= hi; i++) idxs.push(i);
                return idxs;
            }}

            function minMax(arr, idxs) {{
                let mn = Infinity, mx = -Infinity;
                for (const i of idxs) {{
                    const v = arr[i];
                    if (v === null || v === undefined || isNaN(v)) continue;
                    if (v < mn) mn = v;
                    if (v > mx) mx = v;
                }}
                return [mn, mx];
            }}

            let isRescaling = false;
            function rescaleYAxes(x0, x1) {{
                const idxs = visibleIndices(x0, x1);
                if (idxs.length === 0) return Promise.resolve();

                const priceLo = minMax(D.low, idxs)[0];
                const priceHi = minMax(D.high, idxs)[1];
                const pad = (priceHi - priceLo) * 0.08 || priceHi * 0.05 || 1;

                const volHi = minMax(D.volume, idxs)[1];

                const update = {{}};
                if (isFinite(priceLo) && isFinite(priceHi)) update["yaxis.range"] = [priceLo - pad, priceHi + pad];
                if (isFinite(volHi)) update["yaxis2.range"] = [0, volHi * 1.15];

                for (const [axisId, seriesKeys] of Object.entries(DYNAMIC_EXTRA_AXES)) {{
                    let mn = Infinity;
                    let mx = -Infinity;
                    for (const key of seriesKeys) {{
                        const arr = D[key] || [];
                        for (const i of idxs) {{
                            const v = arr[i];
                            if (v === null || v === undefined || isNaN(v)) continue;
                            if (v < mn) mn = v;
                            if (v > mx) mx = v;
                        }}
                    }}
                    if (isFinite(mn) && isFinite(mx)) {{
                        const axisPad = (mx - mn) * 0.15 || Math.abs(mx) * 0.05 || 1;
                        update[axisId + ".range"] = [mn - axisPad, mx + axisPad];
                    }}
                }}

                return Plotly.relayout(graphDiv, update);
            }}

            graphDiv.on("plotly_relayout", function(evt) {{
                if (isRescaling) return;
                let newRange = null;
                if (evt["xaxis.range[0]"] !== undefined && evt["xaxis.range[1]"] !== undefined) {{
                    newRange = [evt["xaxis.range[0]"], evt["xaxis.range[1]"]];
                }} else if (Array.isArray(evt["xaxis.range"])) {{
                    newRange = evt["xaxis.range"];
                }} else if (evt["xaxis.autorange"]) {{
                    newRange = [D.dates[0], D.dates[D.dates.length - 1]];
                }}
                if (newRange) {{
                    isRescaling = true;
                    rescaleYAxes(newRange[0], newRange[1])
                        .then(function() {{ isRescaling = false; }})
                        .catch(function() {{ isRescaling = false; }});
                }}
            }});
        }} catch (err) {{
            document.getElementById("chartErrorBox").innerHTML =
                "<div style='color:#DC2626; background:#FEF2F2; border:1px solid #FCA5A5; " +
                "border-radius:8px; padding:14px; margin-top:10px; font-family:monospace; font-size:13px; white-space:pre-wrap;'>" +
                "⚠️ 차트 렌더링 오류: " + (err && err.message ? err.message : err) + "</div>";
        }}
        </script>
    </body>
    </html>
    """
    components.html(custom_html, height=height + 50, scrolling=False)


# ==========================================
# 3. 미국/한국 주식 통합 실시간 검색 컴포넌트
# ==========================================
def render_unified_search_box(stock_db, target_view=None):
    json_db = json.dumps(stock_db, ensure_ascii=False)
    # target_view가 주어지면 검색 결과가 ?code=...&view=<target_view>로 이동함
    # (예: "analysis" -> 차트 분석 페이지). 기존 탭들은 인자를 안 넘기므로 동작 그대로 유지.
    view_query_suffix = f"&view={target_view}" if target_view else ""
    view_query_suffix += f"&theme={THEME_MODE}"

    custom_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            * {{
                box-sizing: border-box;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            }}
            body {{
                margin: 0;
                padding: 0;
                background: transparent;
            }}
            .search-wrapper {{
                position: relative;
                width: 100%;
            }}
            .input-box {{
                width: 100%;
                height: 54px;
                padding: 0 50px 0 20px;
                border: 2px solid {THEME['accent']};
                border-radius: 12px;
                font-size: 16px;
                font-weight: 600;
                outline: none;
                background: {THEME['surface_warm']};
                color: {THEME['text']};
                box-shadow: 0 4px 12px rgba(244, 162, 97, 0.15);
            }}
            .input-box:focus {{
                border-color: {THEME['accent_strong']};
                box-shadow: 0 0 10px rgba(217, 119, 6, 0.25);
            }}
            .search-icon {{
                position: absolute;
                right: 18px;
                top: 15px;
                font-size: 20px;
                color: {THEME['accent_strong']};
                cursor: pointer;
            }}

            .autocomplete-modal {{
                display: none;
                flex-direction: column;
                position: absolute;
                top: 60px;
                left: 0;
                width: 100%;
                background: {THEME['surface']};
                border: 1px solid {THEME['border']};
                border-radius: 12px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.15);
                z-index: 9999;
                overflow: hidden;
            }}

            .modal-content {{
                display: flex;
                min-height: 320px;
            }}

            .left-pane {{
                flex: 65;
                border-right: 1px solid {THEME['surface_muted']};
                padding: 10px 0;
                max-height: 360px;
                overflow-y: auto;
            }}
            .pane-title {{
                font-size: 12px;
                font-weight: 700;
                color: {THEME['text_muted']};
                padding: 6px 16px;
                text-transform: uppercase;
            }}

            .stock-row {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 10px 16px;
                cursor: pointer;
                transition: background 0.15s;
            }}
            .stock-row:hover, .stock-row.active {{
                background-color: #FFF7ED;
            }}
            .stock-info {{
                display: flex;
                align-items: center;
                gap: 10px;
                overflow: hidden;
            }}
            .flag {{ font-size: 16px; }}
            .ticker {{
                font-weight: 700;
                color: {THEME['text']};
                font-size: 14px;
                min-width: 65px;
            }}
            .name {{
                font-size: 13px;
                color: {THEME['text_secondary']};
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .exch {{
                font-size: 11px;
                color: {THEME['text_muted']};
                white-space: nowrap;
            }}
            .highlight {{
                color: {THEME['accent_strong']};
                font-weight: 800;
                background-color: #FEF3C7;
                padding: 0 2px;
                border-radius: 2px;
            }}

            .right-pane {{
                flex: 35;
                background-color: #FAFAFA;
                padding: 12px 16px;
                display: flex;
                flex-direction: column;
                gap: 12px;
            }}
            .section-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                font-size: 12px;
                font-weight: 700;
                color: #334155;
            }}
            .more-link {{
                font-size: 11px;
                color: #2563EB;
                text-decoration: none;
            }}
            .news-item {{
                font-size: 12px;
                color: #334155;
                line-height: 1.4;
                font-weight: 500;
                cursor: pointer;
            }}
            .news-item:hover {{
                text-decoration: underline;
                color: {THEME['accent_strong']};
            }}

            .modal-footer {{
                border-top: 1px solid {THEME['surface_muted']};
                padding: 10px 16px;
                background: #F8FAFC;
                font-size: 13px;
                color: #2563EB;
                font-weight: 600;
                cursor: pointer;
                display: flex;
                align-items: center;
                gap: 6px;
            }}
            .modal-footer:hover {{
                background: {THEME['surface_muted']};
            }}
        </style>
    </head>
    <body>
        <div class="search-wrapper">
            <input 
                type="text" 
                id="unified_search_input" 
                class="input-box" 
                placeholder="🔍 미국/한국 주식명 또는 티커 입력 (예: COST, 코스트코, AAPL, 애플, NAVER, 네이버, 005930)"
                autocomplete="off"
            />
            <span class="search-icon" onclick="triggerSearch()">🔍</span>

            <div id="unified_search_modal" class="autocomplete-modal">
                <div class="modal-content">
                    <div class="left-pane">
                        <div id="unified_search_title" class="pane-title">Matching Instruments (US / KR)</div>
                        <div id="unified_search_list"></div>
                    </div>
                    <div class="right-pane">
                        <div>
                            <div class="section-header">
                                <span>News</span>
                                <a href="#" class="more-link">More</a>
                            </div>
                            <div style="margin-top: 6px;" class="news-item">S&P 500 및 코스피 하락장 대비 방어주 펀더멘탈 분석</div>
                            <div style="margin-top: 8px;" class="news-item">고금리 장기화에 따른 ROIC/부채비율 체력 점검</div>
                        </div>
                        <div>
                            <div class="section-header">
                                <span>Analysis</span>
                                <a href="#" class="more-link">More</a>
                            </div>
                            <div style="margin-top: 6px;" class="news-item">하락장 청산가치 방어력이 우수한 S등급 기업 리스트</div>
                        </div>
                    </div>
                </div>
                <div class="modal-footer" onclick="triggerSearch()">
                    <span>🔍</span> Search for: <span id="unified_search_footer_query" style="font-weight:700;"></span>
                </div>
            </div>
        </div>

        <script>
            const STOCKS = {json_db};
            const inputEl = document.getElementById('unified_search_input');
            const modalEl = document.getElementById('unified_search_modal');
            const listEl = document.getElementById('unified_search_list');
            const footerQueryEl = document.getElementById('unified_search_footer_query');

            function normalizeSearchText(value) {{
                return String(value ?? '')
                    .normalize('NFKC')
                    .toLowerCase()
                    .replace(/\s+/g, '')
                    .replace(/[._\-\/'’(),&]+/g, '');
            }}

            function subsequenceScore(query, text) {{
                if (!query || !text) return 0;
                let qi = 0;
                for (let i = 0; i < text.length && qi < query.length; i++) {{
                    if (text[i] === query[qi]) qi++;
                }}
                return qi === query.length ? (query.length / text.length) : 0;
            }}

            function scoreField(field, query) {{
                const text = normalizeSearchText(field);
                if (!text) return 0;
                if (text === query) return 1000;
                if (text.startsWith(query)) return 820;
                if (text.includes(query)) return 660;
                const subseq = subsequenceScore(query, text);
                if (query.length >= 3 && subseq >= 0.7) return 420 + subseq * 100;
                return 0;
            }}

            function scoreStock(item, query) {{
                const ticker = normalizeSearchText(item.ticker);
                const name = normalizeSearchText(item.name);
                const aliases = Array.isArray(item.aliases) ? item.aliases : [];

                let best = 0;
                if (ticker === query) best = 1200;
                if (name === query) best = Math.max(best, 1150);

                for (const alias of aliases) {{
                    const aliasScore = scoreField(alias, query);
                    if (aliasScore > 0) {{
                        best = Math.max(best, aliasScore + 20);
                    }}
                }}

                const nameScore = scoreField(item.name, query);
                if (nameScore > 0) {{
                    best = Math.max(best, nameScore);
                }}

                const tickerScore = scoreField(item.ticker, query);
                if (tickerScore > 0) {{
                    best = Math.max(best, tickerScore + 10);
                }}
                return best;
            }}

            function searchStocks(query) {{
                const q = normalizeSearchText(query);
                if (!q) return [];
                return STOCKS
                    .map(item => ({{ item: item, score: scoreStock(item, q) }}))
                    .filter(x => x.score > 0)
                    .sort((a, b) => {{
                        if (b.score !== a.score) return b.score - a.score;
                        const an = normalizeSearchText(a.item.name);
                        const bn = normalizeSearchText(b.item.name);
                        return an.localeCompare(bn, 'ko');
                    }});
            }}

            function escapeRegExp(value) {{
                return String(value).replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
            }}

            function highlightMatch(text, query) {{
                if (!query) return text;
                const safe = escapeRegExp(query);
                if (!safe) return text;
                const reg = new RegExp('(' + safe + ')', 'gi');
                return text.replace(reg, '<span class="highlight">$1</span>');
            }}
            function renderList(query) {{
                const q = query.trim();

                if (!q) {{
                    modalEl.style.display = 'none';
                    return;
                }}

                modalEl.style.display = 'flex';
                footerQueryEl.innerText = q;

                const ranked = searchStocks(q);
                if (ranked.length === 0) {{
                    listEl.innerHTML = '<div style="padding:15px; font-size:13px; color:{THEME['text_muted']};">일치하는 종목이 없습니다.</div>';
                    return;
                }}

                let html = '';
                ranked.slice(0, 30).forEach((match, idx) => {{
                    const item = match.item;
                    const highlightTicker = highlightMatch(item.ticker, q);
                    const highlightName = highlightMatch(item.name, q);
                    html +=
                        '<div class="stock-row ' + (idx === 0 ? 'active' : '') + '" onclick="selectStock(' +
                        JSON.stringify(item.ticker) + ')">' +
                            '<div class="stock-info">' +
                                '<span class="flag">' + item.flag + '</span>' +
                                '<span class="ticker">' + highlightTicker + '</span>' +
                                '<span class="name">' + highlightName + '</span>' +
                            '</div>' +
                            '<span class="exch">' + item.exch + '</span>' +
                        '</div>';
                }});
                listEl.innerHTML = html;
            }}

            function selectStock(ticker) {{
                const targetUrl = window.parent.location.origin + window.parent.location.pathname + '?code=' + encodeURIComponent(ticker) + '{view_query_suffix}';

                // Streamlit components.html iframe 안의 window.open은 브라우저/배포 환경에 따라
                // 팝업으로 차단되어 Enter를 눌러도 아무 반응이 없는 경우가 있음.
                // 먼저 새 탭을 시도하고, 차단되면 같은 탭으로 확실하게 이동한다.
                const popup = window.open(targetUrl, '_blank', 'noopener,noreferrer');
                if (!popup || popup.closed || typeof popup.closed === 'undefined') {{
                    window.parent.location.href = targetUrl;
                }}
            }}

            function triggerSearch() {{
                const q = inputEl.value.trim();
                if (!q) return;

                const ranked = searchStocks(q);
                const targetCode = ranked.length > 0 ? ranked[0].item.ticker : q;
                selectStock(targetCode);
            }}

            inputEl.addEventListener('input', (e) => {{
                renderList(e.target.value);
            }});

            inputEl.addEventListener('keypress', (e) => {{
                if (e.key === 'Enter') {{
                    triggerSearch();
                }}
            }});

            document.addEventListener('click', (e) => {{
                if (!e.target.closest('.search-wrapper')) {{
                    modalEl.style.display = 'none';
                }}
            }});
        </script>
    </body>
    </html>
    """
    components.html(custom_html, height=420)


# ==========================================
# 4. 메인 포털 UI & 스케치 기반 상세 분석 리포트
# ==========================================
# /* FUNDAMENTAL_DARK_FINAL_V3 */
if THEME_MODE == "dark":
    final_dark_css = """
<style>
/* Fundamental Dark Final Layer
   Placed immediately before page rendering with deliberately high specificity.
   This is the last parent-document CSS layer, so later Streamlit/custom HTML
   component styles cannot leave legacy light text behind. */

body .stApp,
body .stApp [data-testid="stAppViewContainer"],
body .stApp [data-testid="stMain"],
body .stApp [data-testid="stHeader"] {
    background-color: __PAGE__ !important;
    color: __TEXT__ !important;
    color-scheme: dark !important;
}

/* Native Streamlit text */
body .stApp [data-testid="stMarkdownContainer"] p,
body .stApp [data-testid="stMarkdownContainer"] span,
body .stApp [data-testid="stMarkdownContainer"] div,
body .stApp [data-testid="stMarkdownContainer"] label,
body .stApp [data-testid="stMarkdownContainer"] h1,
body .stApp [data-testid="stMarkdownContainer"] h2,
body .stApp [data-testid="stMarkdownContainer"] h3,
body .stApp [data-testid="stMarkdownContainer"] h4,
body .stApp [data-testid="stMarkdownContainer"] h5,
body .stApp [data-testid="stMarkdownContainer"] h6,
body .stApp [data-testid="stMarkdownContainer"] a,
body .stApp [data-testid="stMarkdownContainer"] b,
body .stApp [data-testid="stMarkdownContainer"] strong,
body .stApp [data-testid="stCaptionContainer"],
body .stApp [data-testid="stCaptionContainer"] * {
    color: __TEXT__ !important;
}

/* Cards with hand-written HTML */
body .stApp [data-testid="stMarkdownContainer"] .sketch-card,
body .stApp [data-testid="stMarkdownContainer"] .sketch-card *,
body .stApp [data-testid="stMarkdownContainer"] .finstat-item,
body .stApp [data-testid="stMarkdownContainer"] .finstat-item *,
body .stApp [data-testid="stMarkdownContainer"] .overview-cell,
body .stApp [data-testid="stMarkdownContainer"] .overview-cell *,
body .stApp [data-testid="stMarkdownContainer"] .grade-hero-box,
body .stApp [data-testid="stMarkdownContainer"] .grade-hero-box *,
body .stApp [data-testid="stMarkdownContainer"] .indicator-card,
body .stApp [data-testid="stMarkdownContainer"] .indicator-card * {
    background-color: __SURFACE__ !important;
    color: __TEXT__ !important;
    border-color: __BORDER__ !important;
}

body .stApp [data-testid="stMarkdownContainer"] .quote-box-v2,
body .stApp [data-testid="stMarkdownContainer"] .quote-box-v2 * {
    color: __TEXT__ !important;
    border-color: __ACCENT__ !important;
}
body .stApp [data-testid="stMarkdownContainer"] .quote-box-v2 {
    background-color: __SURFACE_WARM__ !important;
}
body .stApp [data-testid="stMarkdownContainer"] .quote-divider {
    border-top-color: __ACCENT__ !important;
}

/* Explicit elements shown in the screenshot */
body .stApp [data-testid="stMarkdownContainer"] .sketch-card b,
body .stApp [data-testid="stMarkdownContainer"] .sketch-card a.stock-link,
body .stApp [data-testid="stMarkdownContainer"] .search-count-badge {
    color: __TEXT__ !important;
}
body .stApp [data-testid="stMarkdownContainer"] .sketch-card a.stock-link {
    color: __ACCENT_STRONG__ !important;
}
body .stApp [data-testid="stMarkdownContainer"] .search-count-badge {
    background-color: __SURFACE_MUTED__ !important;
    border: 1px solid __BORDER__ !important;
}
body .stApp [data-testid="stMarkdownContainer"] .card-item-row {
    border-bottom-color: __BORDER__ !important;
}

/* Streamlit buttons / interval tabs */
body .stApp [data-testid="stButton"] button,
body .stApp [data-testid="stButton"] button *,
body .stApp [data-testid="stLinkButton"] a,
body .stApp [data-testid="stLinkButton"] a * {
    background-color: __SURFACE__ !important;
    color: __TEXT__ !important;
    border-color: __BORDER__ !important;
}
body .stApp [data-testid="stButton"] button:hover,
body .stApp [data-testid="stButton"] button:hover *,
body .stApp [data-testid="stLinkButton"] a:hover,
body .stApp [data-testid="stLinkButton"] a:hover * {
    background-color: __SURFACE_WARM__ !important;
    color: __ACCENT_STRONG__ !important;
    border-color: __ACCENT__ !important;
}
body .stApp [data-testid="stButton"] button[data-testid="baseButton-primary"],
body .stApp [data-testid="stButton"] button[data-testid="baseButton-primary"] * {
    background-color: __ACCENT_STRONG__ !important;
    color: #FFFFFF !important;
    border-color: __ACCENT__ !important;
}

/* Checkboxes, radios, toggle, number inputs and selectboxes */
body .stApp [data-testid="stCheckbox"] *,
body .stApp [data-testid="stRadio"] *,
body .stApp [data-testid="stToggle"] *,
body .stApp [data-testid="stNumberInput"] *,
body .stApp [data-testid="stSelectbox"] *,
body .stApp [data-testid="stWidgetLabel"] * {
    color: __TEXT__ !important;
}
body .stApp input,
body .stApp textarea,
body .stApp [data-baseweb="select"] > div {
    background-color: __SURFACE__ !important;
    color: __TEXT__ !important;
    border-color: __BORDER__ !important;
}
body .stApp [role="switch"][aria-checked="true"] {
    background-color: __ACCENT__ !important;
    border-color: __ACCENT__ !important;
}

/* Expanders and their Markdown contents */
body .stApp [data-testid="stExpander"] details,
body .stApp [data-testid="stExpander"] summary {
    background-color: __SURFACE__ !important;
    color: __TEXT__ !important;
    border-color: __BORDER__ !important;
}
body .stApp [data-testid="stExpander"] summary *,
body .stApp [data-testid="stExpander"] [data-testid="stMarkdownContainer"] * {
    color: __TEXT__ !important;
}
body .stApp [data-testid="stExpander"] [data-testid="stCaptionContainer"] * {
    color: __TEXT_MUTED__ !important;
}

/* Main tabs and tab labels */
body .stApp [data-testid="stTabs"] [data-baseweb="tab"],
body .stApp [data-testid="stTabs"] [data-baseweb="tab"] *,
body .stApp [data-testid="stTabs"] [role="tab"],
body .stApp [data-testid="stTabs"] [role="tab"] * {
    color: __TEXT__ !important;
}
body .stApp [data-testid="stTabs"] [aria-selected="true"],
body .stApp [data-testid="stTabs"] [aria-selected="true"] * {
    color: __ACCENT_STRONG__ !important;
}
body .stApp [data-testid="stTabs"] [aria-selected="true"] {
    border-bottom-color: __ACCENT__ !important;
}

/* Alert / info / warning boxes */
body .stApp [data-testid="stAlert"],
body .stApp [data-testid="stAlert"] * {
    color: __TEXT__ !important;
}
body .stApp [data-testid="stAlert"] {
    background-color: __SURFACE_MUTED__ !important;
    border-color: __BORDER__ !important;
}

/* Legacy inline colors */
body .stApp [style*="#1A1A1A"],
body .stApp [style*="#111827"],
body .stApp [style*="#0F172A"],
body .stApp [style*="#4B5563"] {
    color: __TEXT__ !important;
}
body .stApp [style*="#64748B"],
body .stApp [style*="#6B7280"],
body .stApp [style*="#475569"],
body .stApp [style*="#334155"],
body .stApp [style*="#94A3B8"],
body .stApp [style*="#888888"] {
    color: __TEXT_MUTED__ !important;
}
body .stApp [style*="#D97706"] {
    color: __ACCENT_STRONG__ !important;
}
body .stApp [style*="#92400E"],
body .stApp [style*="#9A3412"] {
    color: __WARNING_TEXT__ !important;
}
body .stApp [style*="#DC2626"],
body .stApp [style*="#D93025"] {
    color: __POSITIVE__ !important;
}
body .stApp [style*="#2563EB"] {
    color: __NEGATIVE__ !important;
}
body .stApp [style*="#16A34A"],
body .stApp [style*="#047857"] {
    color: __SUCCESS__ !important;
}
body .stApp [style*="#FFFFFF"],
body .stApp [style*="#ffffff"],
body .stApp [style*="#FFFDF9"],
body .stApp [style*="#FAFAFA"],
body .stApp [style*="#F8FAFC"],
body .stApp [style*="#F1F5F9"] {
    background-color: __SURFACE__ !important;
}
</style>
"""
    replacements = {
        "__PAGE__": THEME["page"],
        "__SURFACE__": THEME["surface"],
        "__SURFACE_WARM__": THEME["surface_warm"],
        "__SURFACE_MUTED__": THEME["surface_muted"],
        "__TEXT__": THEME["text"],
        "__TEXT_MUTED__": THEME["text_muted"],
        "__BORDER__": THEME["border"],
        "__ACCENT__": THEME["accent"],
        "__ACCENT_STRONG__": THEME["accent_strong"],
        "__POSITIVE__": THEME["positive"],
        "__NEGATIVE__": THEME["negative"],
        "__SUCCESS__": THEME["success"],
        "__WARNING_BG__": THEME["warning_bg"],
        "__WARNING_TEXT__": THEME["warning_text"],
    }
    for placeholder, value in replacements.items():
        final_dark_css = final_dark_css.replace(placeholder, value)

    st.markdown(final_dark_css, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# +알파: 홈 화면 Live News / Earnings Calendar preview
# ---------------------------------------------------------------------------
def _format_news_time(value):
    if not value:
        return ""
    try:
        ts = pd.to_datetime(value)
        if pd.isna(ts):
            return ""
        return ts.strftime("%Y.%m.%d %H:%M")
    except Exception:
        return str(value)[:16]


def _escape_html(value):
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


@st.cache_data(ttl=300, show_spinner=False)
def _get_home_macro_news(display=8, cache_version="naver-hub-v2"):
    # cache_version으로 NAVER API HUB 전환 전 빈 캐시를 강제로 무효화한다.
    return fetch_macro_news(display=display)


@st.cache_data(ttl=300, show_spinner=False)
def _get_home_earnings_events(days_back=30):
    disclosures = fetch_dart_disclosures(
        start_date=date.today() - timedelta(days=days_back),
        end_date=date.today(),
        page_count=100,
        max_pages=20,
    )
    return build_earnings_events(disclosures)


@st.cache_data(ttl=300, show_spinner=False)
def _get_home_market_indices(market):
    specs = (
        [("S&P 500", "^GSPC"), ("Nasdaq", "^IXIC"), ("Dow Jones", "^DJI")]
        if market == "US"
        else [("KOSPI", "^KS11"), ("KOSDAQ", "^KQ11")]
    )
    result = []
    for label, ticker in specs:
        try:
            hist = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
            if hist is None or hist.empty or "Close" not in hist.columns:
                continue
            close = hist["Close"].dropna()
            if close.empty:
                continue
            latest = float(close.iloc[-1])
            previous = float(close.iloc[-2]) if len(close) >= 2 else latest
            change_pct = ((latest / previous) - 1.0) * 100.0 if previous else 0.0
            result.append({"label": label, "value": latest, "change_pct": change_pct})
        except Exception:
            continue
    return result


def _render_news_cards(items, limit=6, title="📰 Live News", subtitle=""):
    st.markdown(
        f"""
        <div class="live-news-section">
          <div class="live-news-section-title">{title}</div>
          <div class="live-news-section-subtitle">{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not items:
        st.markdown(
            '<div class="news-empty-state">현재 표시할 뉴스가 없습니다. API 키 설정 또는 잠시 후 다시 시도해 주세요.</div>',
            unsafe_allow_html=True,
        )
        return

    cards = []
    for item in list(items)[:limit]:
        title_text = item.title if hasattr(item, "title") else item.get("title", "")
        desc_text = item.description if hasattr(item, "description") else item.get("description", "")
        article_url = item.link if hasattr(item, "link") else item.get("article_url", "")
        original_url = item.original_link if hasattr(item, "original_link") else item.get("original_url", "")
        pub_date = item.pub_date if hasattr(item, "pub_date") else item.get("published_at", "")
        query = item.query if hasattr(item, "query") else ""
        cards.append(
            f"""
            <article class="live-news-card">
              <div class="live-news-meta">
                <span class="live-news-category">{_escape_html(query) if query else "시장 뉴스"}</span>
              </div>
              <a class="live-news-title" href="{_escape_html(article_url or original_url or '#')}" target="_blank" rel="noopener noreferrer">{_escape_html(title_text)}</a>
              <div class="live-news-desc">{_escape_html(desc_text)}</div>
              <div class="live-news-footer">{_format_news_time(pub_date)} · 원문 보기 ↗</div>
            </article>
            """
        )
    st.markdown('<div class="live-news-grid">' + "".join(cards) + "</div>", unsafe_allow_html=True)


def render_home_live_news(limit=6):
    """메인 Live News: 시장 영향도가 큰 거시·금융 질의를 NAVER 검색 API로 실시간 조회."""
    try:
        items = _get_home_macro_news(display=max(limit, 8))
    except Exception as exc:
        items = []
        st.warning(f"Live News를 불러오지 못했습니다: {exc}")

    _render_news_cards(
        items,
        limit=limit,
        title="📰 Live News",
        subtitle="금리·환율·미국 증시·국내 증시·정책 등 시장 전반의 주요 뉴스를 원문과 함께 보여드립니다.",
    )


def render_home_stock_news(stock_name, stock_code, limit=6):
    """종목 상세 페이지의 종목별 뉴스."""
    try:
        items = fetch_stock_news(stock_name, stock_code, display=max(limit, 8))
    except Exception:
        items = []
    if not items:
        return
    _render_news_cards(
        items,
        limit=limit,
        title=f"📰 {stock_name} 관련 뉴스",
        subtitle="해당 종목명을 기준으로 조회한 최신 뉴스 검색 결과입니다.",
    )



@st.cache_data(ttl=1800, show_spinner=False)
def _get_us_upcoming_earnings(days_forward=14, limit=24):
    """Yahoo Finance 기반 미국 향후 예정 실적. 예정일 데이터가 있는 종목만 표시."""
    try:
        start = date.today()
        end = start + timedelta(days=days_forward)
        calendar = yf.Calendars(start=start, end=end)
        df = calendar.get_earnings_calendar(
            filter_most_active=True,
            limit=min(limit, 100),
        )
        if df is None or df.empty:
            return []
        rows = []
        for _, row in df.reset_index().iterrows():
            reported = row.get("Reported EPS")
            if pd.notna(reported):
                continue
            event_dt = row.get("Event Start Date")
            if pd.isna(event_dt):
                continue
            rows.append({
                "market": "US",
                "symbol": str(row.get("Symbol", "")),
                "company": str(row.get("Company", row.get("Company Name", ""))),
                "date": pd.to_datetime(event_dt).date(),
                "timing": str(row.get("Timing", "")),
                "eps_estimate": row.get("EPS Estimate"),
                "actual": None,
                "surprise": None,
                "status": "upcoming",
            })
        return rows[:limit]
    except Exception:
        return []


@st.cache_data(ttl=1800, show_spinner=False)
def _get_us_earnings_window(days_back=45, days_forward=90, limit=100):
    """월간 캘린더용 미국 실적: 발표 완료 + 예정 일정을 함께 조회."""
    try:
        start = date.today() - timedelta(days=days_back)
        end = date.today() + timedelta(days=days_forward)
        calendar = yf.Calendars(start=start, end=end)
        df = calendar.get_earnings_calendar(
            filter_most_active=True,
            limit=min(limit, 100),
        )
        if df is None or df.empty:
            return []
        rows = []
        for _, row in df.reset_index().iterrows():
            event_dt = row.get("Event Start Date")
            if pd.isna(event_dt):
                continue
            event_date = pd.to_datetime(event_dt).date()
            reported = row.get("Reported EPS")
            rows.append({
                "market": "US",
                "symbol": str(row.get("Symbol", "")),
                "company": str(row.get("Company", row.get("Company Name", ""))),
                "date": event_date,
                "timing": str(row.get("Timing", "")),
                "eps_estimate": row.get("EPS Estimate"),
                "actual": reported if pd.notna(reported) else None,
                "surprise": row.get("Surprise(%)") if pd.notna(row.get("Surprise(%)")) else None,
                "status": "reported" if pd.notna(reported) else "upcoming",
            })
        rows.sort(key=lambda x: x["date"])
        return rows
    except Exception:
        return []


# 초기 한국 미래 어닝 일정은 Yahoo Finance의 종목별 Earnings Date를 사용한다.
# 전 종목을 한 번에 조회하면 페이지 로딩이 과도해질 수 있어, 우선 국내 대표/활발 종목을
# 대상으로 월간 캘린더를 구성하고 추후 종목 범위를 별도 ingestion으로 확장할 수 있게 분리한다.
KR_EARNINGS_WATCHLIST = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("005380", "현대차"),
    ("000270", "기아"),
    ("035420", "NAVER"),
    ("035720", "카카오"),
    ("051910", "LG화학"),
    ("006400", "삼성SDI"),
    ("105560", "KB금융"),
    ("055550", "신한지주"),
    ("000810", "삼성화재"),
    ("012330", "현대모비스"),
    ("028260", "삼성물산"),
    ("034730", "SK"),
    ("003550", "LG"),
    ("096770", "SK이노베이션"),
    ("009150", "삼성전기"),
    ("066570", "LG전자"),
    ("068270", "셀트리온"),
    ("012450", "한화에어로스페이스"),
    ("042700", "한미반도체"),
    ("086520", "에코프로"),
    ("247540", "에코프로비엠"),
    ("352820", "하이브"),
    ("259960", "크래프톤"),
]


@st.cache_data(ttl=1800, show_spinner=False)
def _get_kr_upcoming_earnings(days_forward=120):
    """한국 대표 종목의 향후 실적 예정일. Yahoo Finance 종목별 calendar 기반."""
    try:
        today = date.today()
        end_date = today + timedelta(days=days_forward)
        rows = []

        for code, name in KR_EARNINGS_WATCHLIST:
            calendar_data = {}
            try:
                calendar_data = yf.Ticker(f"{code}.KS").calendar or {}
            except Exception:
                calendar_data = {}

            earnings_dates = calendar_data.get("Earnings Date") or []
            if not earnings_dates:
                continue

            estimate = calendar_data.get("Earnings Average")
            for raw_date in earnings_dates:
                try:
                    event_date = pd.to_datetime(raw_date).date()
                except Exception:
                    continue
                if not (today <= event_date <= end_date):
                    continue

                rows.append({
                    "market": "KR",
                    "symbol": code,
                    "company": name,
                    "date": event_date,
                    "timing": "예정",
                    "eps_estimate": estimate,
                    "actual": None,
                    "surprise": None,
                    "status": "upcoming",
                })
        rows.sort(key=lambda x: (x["date"], x["company"]))
        return rows
    except Exception:
        return []


@st.cache_data(ttl=3600, show_spinner=False)
def _get_yahoo_earnings_history(stock_code):
    """DART 종목의 Yahoo Finance EPS 컨센서스/실적 보조 데이터."""
    code = str(stock_code or "").strip()
    if not code or not code.isdigit():
        return []
    try:
        df = yf.Ticker(f"{code}.KS").get_earnings_dates(limit=12)
        if df is None or df.empty:
            return []
        result = []
        for idx, row in df.iterrows():
            dt = pd.to_datetime(idx)
            result.append({
                "date": dt.date(),
                "estimate": row.get("EPS Estimate"),
                "actual": row.get("Reported EPS"),
                "surprise": row.get("Surprise(%)"),
            })
        return result
    except Exception:
        return []


def _match_earnings_consensus(event):
    try:
        target = date.fromisoformat(str(event.event_date))
    except Exception:
        return None
    history = _get_yahoo_earnings_history(event.stock_code)
    if not history:
        return None
    candidates = []
    for row in history:
        if row.get("actual") is None or row.get("estimate") is None:
            continue
        if pd.isna(row.get("actual")) or pd.isna(row.get("estimate")):
            continue
        delta = abs((row["date"] - target).days)
        if delta <= 7:
            candidates.append((delta, row))
    if not candidates:
        return None
    return min(candidates, key=lambda x: x[0])[1]


def _escape_calendar_text(value):
    return _escape_html(str(value or ""))


def _format_eps(value):
    try:
        if value is None or pd.isna(value):
            return ""
        return f"{float(value):,.2f}"
    except Exception:
        return ""


def _earnings_calendar_events():
    """월간 캘린더에 필요한 이벤트를 한 번에 구성."""
    events = []

    # 최근 DART 실적 공시는 실제 발표일 기준으로 포함한다.
    try:
        dart_events = _get_home_earnings_events(days_back=60)
    except Exception:
        dart_events = []
    for event in dart_events:
        events.append({
            "market": "KR",
            "symbol": str(event.stock_code),
            "company": str(event.corp_name),
            "date": date.fromisoformat(str(event.event_date)),
            "timing": "발표",
            "eps_estimate": None,
            "actual": None,
            "surprise": None,
            "status": "reported",
            "report_name": str(event.report_name or ""),
            "source_url": str(event.source_url or ""),
        })

    events.extend(_get_us_earnings_window(days_back=60, days_forward=120, limit=100))
    events.extend(_get_kr_upcoming_earnings(days_forward=120))

    # 동일 기업/날짜가 여러 소스에서 중복되면 미래 일정 우선.
    deduped = {}
    for row in events:
        key = (row["market"], row["symbol"], row["date"])
        if key not in deduped or row["status"] == "upcoming":
            deduped[key] = row

    return sorted(deduped.values(), key=lambda x: (x["date"], x["market"], x["company"]))


def _render_earnings_calendar_grid(month_start, events, market_filter="전체", selected_date=None):
    """7열 월간 캘린더. 날짜 셀을 클릭하면 선택 날짜를 반환한다."""
    filtered = [
        row for row in events
        if market_filter == "전체" or row["market"] == ("US" if market_filter == "🇺🇸 미국" else "KR")
    ]
    by_date = {}
    for row in filtered:
        by_date.setdefault(row["date"], []).append(row)

    st.markdown("<div class='earnings-calendar-shell'>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='earnings-calendar-head'><div>"
        f"<div class='earnings-calendar-month'>{month_start.year}년 {month_start.month}월</div>"
        f"<div class='earnings-calendar-sub'>실적 발표일 기준 · 예정 일정은 변경될 수 있습니다.</div>"
        f"</div></div>",
        unsafe_allow_html=True,
    )

    header_cols = st.columns(7)
    for col, name in zip(header_cols, ["월", "화", "수", "목", "금", "토", "일"]):
        with col:
            st.markdown(f"<div class='earnings-calendar-week'>{name}</div>", unsafe_allow_html=True)

    weeks = pycalendar.monthcalendar(month_start.year, month_start.month)
    today = date.today()

    for week in weeks:
        cols = st.columns(7)
        for col, day_num in zip(cols, week):
            with col:
                if day_num == 0:
                    st.markdown("<div style='min-height:122px;'></div>", unsafe_allow_html=True)
                    continue

                cell_date = date(month_start.year, month_start.month, day_num)
                items = by_date.get(cell_date, [])
                today_class = " is-today" if cell_date == today else ""
                selected_class = " selected" if selected_date == cell_date else ""

                st.markdown(f"<div class='earnings-calendar-cell{today_class}{selected_class}'>", unsafe_allow_html=True)

                if st.button(
                    f"{day_num}일",
                    key=f"earnings_day_{month_start.isoformat()}_{market_filter}_{cell_date.isoformat()}",
                    use_container_width=True,
                ):
                    st.session_state["earnings_calendar_selected_date"] = cell_date
                    st.rerun()

                for item in items[:3]:
                    market_class = "us" if item["market"] == "US" else "kr"
                    market_tag = "US" if item["market"] == "US" else "KR"
                    name = item["company"]
                    if len(name) > 14:
                        name = name[:13] + "…"
                    status_mark = "예정" if item["status"] == "upcoming" else "실적"
                    st.markdown(
                        f"<div class='earnings-calendar-event {market_class}' title='{_escape_calendar_text(item['company'])} · {status_mark}'>"
                        f"{market_tag} · {_escape_calendar_text(name)}</div>",
                        unsafe_allow_html=True,
                    )
                if len(items) > 3:
                    st.markdown(f"<div class='earnings-calendar-more'>+ {len(items)-3}개 더보기</div>", unsafe_allow_html=True)
                elif not items:
                    st.markdown("<div class='earnings-calendar-empty'>-</div>", unsafe_allow_html=True)

                st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_earnings_detail(selected_date, events, market_filter="전체"):
    filtered = [
        row for row in events
        if row["date"] == selected_date
        and (market_filter == "전체" or row["market"] == ("US" if market_filter == "🇺🇸 미국" else "KR"))
    ]
    st.markdown(
        f"<div class='earnings-upcoming-title'>{selected_date.strftime('%Y년 %m월 %d일')} 실적 일정</div>",
        unsafe_allow_html=True,
    )

    if not filtered:
        st.markdown(
            "<div class='news-empty-state'>선택한 날짜에는 현재 표시할 실적 일정이 없습니다.</div>",
            unsafe_allow_html=True,
        )
        return

    for row in filtered:
        market_class = "us" if row["market"] == "US" else "kr"
        market_label = "🇺🇸 미국" if row["market"] == "US" else "🇰🇷 한국"
        meta_parts = []
        if row.get("timing") and row["timing"] != "nan":
            meta_parts.append(row["timing"])
        if row["status"] == "upcoming":
            meta_parts.append("향후 예정")
        else:
            meta_parts.append("발표 완료")

        eps_est = _format_eps(row.get("eps_estimate"))
        actual = _format_eps(row.get("actual"))
        surprise = _format_eps(row.get("surprise"))

        if row["market"] == "KR" and row["status"] == "reported" and not actual:
            # DART 공시 자체에는 Yahoo 컨센서스가 없는 경우가 있어, 상세 카드에서만
            # best-effort로 실제 EPS/컨센서스를 보강한다.
            try:
                dart_event = type("E", (), {
                    "event_date": row["date"].isoformat(),
                    "stock_code": row["symbol"],
                })()
                consensus = _match_earnings_consensus(dart_event)
                if consensus:
                    eps_est = _format_eps(consensus.get("estimate"))
                    actual = _format_eps(consensus.get("actual"))
                    surprise = _format_eps(consensus.get("surprise"))
            except Exception:
                pass

        compare = ""
        if actual or eps_est:
            if actual and eps_est:
                surprise_text = f" · 서프라이즈 <strong>{surprise}%</strong>" if surprise else ""
                compare = (
                    f"<div class='earnings-detail-compare'>"
                    f"실제 EPS <strong>{actual}</strong> · 컨센서스 <strong>{eps_est}</strong>{surprise_text}"
                    f"</div>"
                )
            elif eps_est:
                compare = (
                    f"<div class='earnings-detail-compare'>예상 EPS <strong>{eps_est}</strong></div>"
                )

        source_link = ""
        if row.get("source_url"):
            source_link = (
                f"<a href='{_escape_calendar_text(row['source_url'])}' target='_blank' "
                f"rel='noopener noreferrer' style='color:#D97706;text-decoration:none;font-weight:800;'>공시 보기 ↗</a>"
            )

        st.markdown(
            f"""
            <div class='earnings-detail-card'>
              <div class='earnings-detail-top'>
                <div>
                  <div class='earnings-detail-name'>{_escape_calendar_text(row['company'])} <span style='color:#6B7280 !important;font-size:11px;font-weight:800;'>({row['symbol']})</span></div>
                  <div class='earnings-detail-meta'>{_escape_calendar_text(" · ".join(meta_parts))} {source_link}</div>
                </div>
                <span class='earnings-detail-market {market_class}'>{market_label}</span>
              </div>
              {compare}
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_home_earnings_calendar(limit=12):
    """메인 홈에는 요약만 보여주고, 전체 월간 캘린더는 별도 탭/창으로 연다."""
    st.markdown(
        "<div class='live-news-section'><div class='live-news-section-title'>📅 Earnings Calendar</div>"
        "<div class='live-news-section-subtitle'>최근 발표 실적과 향후 예정 실적을 간단히 확인하고, 전체 월간 캘린더에서 날짜별로 볼 수 있습니다.</div></div>",
        unsafe_allow_html=True,
    )

    try:
        recent_events = _get_home_earnings_events(days_back=30)
    except Exception:
        recent_events = []

    st.markdown("<div class='earnings-upcoming-title'>🇰🇷 최근 발표 실적</div>", unsafe_allow_html=True)
    if recent_events:
        for event in recent_events[:min(limit, 5)]:
            label = "잠정실적" if event.event_type == "preliminary_earnings" else "정기보고서"
            consensus = _match_earnings_consensus(event) if event.event_type == "preliminary_earnings" else None
            compare = ""
            if consensus:
                actual = _format_eps(consensus.get("actual"))
                estimate = _format_eps(consensus.get("estimate"))
                surprise = _format_eps(consensus.get("surprise"))
                if actual and estimate:
                    compare = (
                        f"<div class='earnings-compare'>실제 EPS <strong>{actual}</strong> · "
                        f"컨센서스 <strong>{estimate}</strong> · 서프라이즈 <strong>{surprise}%</strong></div>"
                    )
            st.markdown(
                f"""
                <div class="earnings-row">
                  <div>
                    <div class="earnings-name">{_escape_html(event.corp_name)} <span class="earnings-primary">{label}</span></div>
                    <div class="earnings-report">{_escape_html(event.report_name)}</div>
                    {compare}
                  </div>
                  <div class="earnings-date">{_escape_html(event.event_date)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.markdown('<div class="news-empty-state">최근 실적 공시가 없습니다.</div>', unsafe_allow_html=True)

    st.markdown("<div class='earnings-upcoming-title'>🇺🇸🇰🇷 향후 예정 실적 · 다음 14일</div>", unsafe_allow_html=True)
    upcoming = _get_us_upcoming_earnings(days_forward=14, limit=10) + _get_kr_upcoming_earnings(days_forward=14)
    upcoming.sort(key=lambda x: (x["date"], x["market"], x["company"]))
    if upcoming:
        st.markdown("<div class='earnings-note'>예정일은 변경될 수 있습니다.</div>", unsafe_allow_html=True)
        for row in upcoming[:min(limit, 10)]:
            eps = _format_eps(row.get("eps_estimate"))
            eps_text = f"컨센서스 EPS {eps}" if eps else "컨센서스 데이터 없음"
            market_text = "미국" if row["market"] == "US" else "한국"
            timing = row.get("timing") if row.get("timing") not in (None, "", "nan") else "예정"
            st.markdown(
                f"""
                <div class="earnings-row">
                  <div style="flex:1 1 auto;min-width:180px;">
                    <div class="earnings-name">{_escape_html(row['company'])} <span class="earnings-primary">{_escape_html(row['symbol'])}</span></div>
                    <div class="earnings-report">{market_text} 예정 실적 · {timing}</div>
                  </div>
                  <div class="earnings-date-wrap">
                    <div class="earnings-date">{row['date'].isoformat()}</div>
                    <div class="earnings-consensus-chip">컨센서스 <strong>{eps if eps else '—'}</strong></div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.markdown('<div class="earnings-note">현재 향후 예정 실적 데이터를 불러오지 못했습니다.</div>', unsafe_allow_html=True)

    st.markdown("<div style='display:flex;justify-content:center;margin:16px 0 4px;'>", unsafe_allow_html=True)
    st.link_button(
        "📅 전체 월간 어닝 캘린더 열기",
        f"?view=earnings_calendar&theme={THEME_MODE}",
        use_container_width=False,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def render_home_market_overview(market):
    title = "🇺🇸 US Market Overview" if market == "US" else "🇰🇷 Korea Market Overview"
    subtitle = "주요 지수의 최신 일봉 기준 시세 흐름입니다." if market == "US" else "국내 주요 지수의 최신 일봉 기준 시세 흐름입니다."
    st.markdown(
        f"<div class='live-news-section'><div class='live-news-section-title'>{title}</div>"
        f"<div class='live-news-section-subtitle'>{subtitle}</div></div>",
        unsafe_allow_html=True,
    )
    rows = _get_home_market_indices(market)
    if not rows:
        st.info("시장 지수 데이터를 불러오지 못했습니다.")
        return
    cols = st.columns(len(rows))
    for col, row in zip(cols, rows):
        with col:
            st.metric(row["label"], f"{row['value']:,.2f}", f"{row['change_pct']:+.2f}%")
    st.caption("시장 데이터: yfinance · 최신 확인 가능 일봉 기준")





query_params = st.query_params
selected_code = query_params.get("code", None)
view_mode_param = query_params.get("view", None)

if selected_code and view_mode_param == "chart":
    # ==========================================
    # [5-0] 차트 확대 페이지 - 새 탭에서 열림 (애드센스 페이지뷰 확보 목적)
    # ==========================================
    col_logo, col_quote, col_login = st.columns([1.0, 6.8, 1.0])

    with col_logo:
        st.markdown("<div class='logo-box'>📈 Fundamental</div>", unsafe_allow_html=True)

    with col_quote:
        render_quote_box()

    with col_login:
        render_theme_toggle("theme_toggle_chart")
        st.link_button("📊 리포트로 돌아가기", f"?code={selected_code}&theme={THEME_MODE}", use_container_width=True)

    st.markdown("<br>", unsafe_allow_html=True)

    left_ad, chart_main, right_ad = st.columns([0.6, 6.8, 0.6])

    with left_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

    with chart_main:
        stock_name_for_chart = query_params.get("name", selected_code)
        st.markdown(f"### 📈 {stock_name_for_chart} ({selected_code}) 상세 차트")

        tv_symbol = f"KRX:{selected_code}" if selected_code.isdigit() else selected_code
        components.html(
            f"""
            <div class="tradingview-widget-container" style="height:600px;">
              <div id="tradingview_chart" style="height:100%;"></div>
              <script src="https://s3.tradingview.com/tv.js"></script>
              <script>
              new TradingView.widget({{
                "width": "100%",
                "height": 600,
                "symbol": "{tv_symbol}",
                "interval": "D",
                "timezone": "Asia/Seoul",
                "theme": "{THEME_MODE}",
                "style": "1",
                "locale": "kr",
                "toolbar_bg": "{THEME['toolbar_bg']}",
                "enable_publishing": false,
                "allow_symbol_change": true,
                "container_id": "tradingview_chart"
              }});
              </script>
            </div>
            """,
            height=620,
        )
        st.caption("차트 제공: TradingView")

        # --- 기업 기본 재무제표 스냅샷 (Supabase Fundamental 테이블 기반) ---
        # 기존 팝업엔 차트만 있었고 재무 데이터가 없었어서 새로 추가하는 부분.
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### 📋 기본 재무제표 스냅샷")

        chart_page_data = get_stock_data(selected_code)
        chart_supabase_data = chart_page_data.get("supabase_data")

        if not chart_supabase_data:
            st.info(
                "⚠️ 아직 이 종목의 재무제표 데이터가 없습니다. "
                "collector.py로 수집되면 매출/영업이익/순이익 등 재무 정보가 여기에 표시됩니다. "
                "(국내(KR) 종목만 DART 기반 데이터를 지원합니다)"
            )
        else:
            def _fmt_won(v):
                if v is None:
                    return "N/A"
                return f"{v:,.0f}원"

            fin_revenue = chart_supabase_data.get("revenue")
            fin_op_income = chart_supabase_data.get("operating_income")
            fin_net_income = chart_supabase_data.get("net_income")
            fin_total_liab = chart_supabase_data.get("total_liabilities")
            fin_total_equity = chart_supabase_data.get("total_equity")
            fin_debt_rate = (
                round(fin_total_liab / fin_total_equity * 100, 1)
                if (fin_total_liab is not None and fin_total_equity)
                else None
            )
            fin_per = chart_supabase_data.get("per")
            fin_pbr = chart_supabase_data.get("pbr")
            fin_price = chart_supabase_data.get("stock_price")
            fin_base_year = chart_supabase_data.get("base_year")
            fin_wics = chart_supabase_data.get("wics_sector")

            finstat_items = [
                ("현재가(기준일 종가)", _fmt_won(fin_price)),
                ("기준 회계연도", str(fin_base_year) if fin_base_year else "N/A"),
                ("업종(WICS)", fin_wics or "N/A"),
                ("매출액", _fmt_won(fin_revenue)),
                ("영업이익", _fmt_won(fin_op_income)),
                ("순이익", _fmt_won(fin_net_income)),
                ("총부채", _fmt_won(fin_total_liab)),
                ("총자본", _fmt_won(fin_total_equity)),
                ("부채비율", f"{fin_debt_rate}%" if fin_debt_rate is not None else "N/A"),
                ("PER", f"{fin_per}" if fin_per is not None else "N/A"),
                ("PBR", f"{fin_pbr}" if fin_pbr is not None else "N/A"),
            ]

            finstat_items_html = "".join(
                f'<div class="finstat-item"><div class="finstat-label">{label}</div>'
                f'<div class="finstat-value">{value}</div></div>'
                for label, value in finstat_items
            )
            st.markdown(f'<div class="finstat-grid">{finstat_items_html}</div>', unsafe_allow_html=True)
            st.caption("ℹ️ 위 재무 수치는 DART 공시 기준 최신 확정 연간 사업보고서(기준 회계연도) 데이터입니다.")

    with right_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

elif view_mode_param == "analysis_search":
    # ==========================================
    # [5-0] 차트 분석 전용 검색 화면 - Chart Analysis 탭에서 새 창으로 열림
    # ==========================================
    col_logo, col_quote, col_login = st.columns([1.0, 6.8, 1.0])

    with col_logo:
        st.markdown("<div class='logo-box'>📈 Fundamental</div>", unsafe_allow_html=True)

    with col_quote:
        render_quote_box()

    with col_login:
        render_theme_toggle("theme_toggle_analysis_search")

    st.markdown("<br>", unsafe_allow_html=True)

    left_ad, search_main, right_ad = st.columns([0.6, 6.8, 0.6])

    with left_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

    with search_main:
        st.markdown("### 📊 차트 분석 (Chart Analysis)")
        st.info(
            "종목을 검색하면 캔들스틱 차트에 이동평균선·볼린저밴드·RSI·스토캐스틱·일목균형표·MACD·ADX/DMI·"
            "ATR·OBV·MFI·Rolling VWAP·거래량 지표를 얹어서, 지금 이 종목 기준 쉬운 설명과 함께 보여드려요."
        )
        analysis_search_stocks_db = get_combined_stock_db()
        render_unified_search_box(stock_db=analysis_search_stocks_db, target_view="analysis")

    with right_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

elif selected_code and view_mode_param == "analysis":
    # ==========================================
    # [5-1] 차트 분석(기술적 지표) 페이지 - 전용 검색 화면에서 검색해 새 창으로 열림
    # ⚠️ 네이버증권처럼 로고/명언/광고 없이 차트+지표에만 집중하는 미니멀 레이아웃 -
    # 다른 페이지(메인/리포트)와 달리 이 페이지만 별도로 이렇게 구성함 (요청사항)
    # ==========================================
    analysis_top_left, analysis_top_right = st.columns([8.5, 1.5])
    with analysis_top_right:
        render_theme_toggle("theme_toggle_analysis")
    with analysis_top_left:
        st.link_button("📊 리포트로 돌아가기", f"?code={selected_code}&theme={THEME_MODE}")

    with st.container():
        analysis_name = query_params.get("name", selected_code)
        st.markdown(f"### 📊 {analysis_name} ({selected_code}) 차트 분석 (Chart Analysis)")

        # ⚠️ 기간 탭은 차트 iframe 안 JS 버튼 대신 진짜 Streamlit 버튼으로 처리함 -
        # components.html iframe 안에서 window.parent.location으로 부모 페이지를 이동시키는 건
        # Streamlit의 iframe 샌드박스 정책상 막혀서 클릭해도 아무 반응이 없었음 (버그 아니라 브라우저 보안 제약).
        interval_choice = query_params.get("interval", "일봉")
        if interval_choice not in INTERVAL_RESAMPLE_RULE and interval_choice != "분봉":
            interval_choice = "일봉"

        INTERVAL_TABS = ["분봉", "일봉", "주봉", "월봉", "분기봉", "년봉"]
        tab_cols = st.columns(len(INTERVAL_TABS))
        for i, ivl in enumerate(INTERVAL_TABS):
            with tab_cols[i]:
                if st.button(
                    ivl, key=f"ivl_btn_{ivl}", use_container_width=True,
                    type="primary" if ivl == interval_choice else "secondary",
                ):
                    st.query_params["interval"] = ivl
                    st.rerun()

        if interval_choice == "분봉":
            hist_df = get_chart_history_intraday(selected_code, yf_interval="30m", period="60d")
            st.caption("⏱️ 분봉은 무료 데이터 소스 제약상 최근 60일치만 제공돼요 (30분봉 기준).")
        else:
            base_hist_df = get_chart_history(selected_code, period="max")
            hist_df = resample_ohlcv(base_hist_df, INTERVAL_RESAMPLE_RULE[interval_choice])

        with st.expander("📊 표시할 지표 선택 - 기본은 전부 꺼져 있어요, 원하는 것만 골라서 켜보세요"):
            vis_col1, vis_col2 = st.columns(2)
            with vis_col1:
                show_ma = st.checkbox("이동평균선 (5·20·60·120)", value=False, key="an_show_ma")
                show_bb = st.checkbox("볼린저 밴드", value=False, key="an_show_bb")
                show_ichimoku = st.checkbox("일목균형표", value=False, key="an_show_ichimoku")
                show_vol_ma = st.checkbox("거래량 이동평균", value=False, key="an_show_vol_ma")
            with vis_col2:
                show_rsi = st.checkbox("RSI (전용 패널)", value=False, key="an_show_rsi")
                show_stoch = st.checkbox("스토캐스틱 (전용 패널)", value=False, key="an_show_stoch")
                show_adx = st.checkbox("ADX / DMI (전용 패널)", value=False, key="an_show_adx")
                show_atr = st.checkbox("ATR (전용 패널)", value=False, key="an_show_atr")
                show_obv = st.checkbox("OBV (전용 패널)", value=False, key="an_show_obv")
                show_macd = st.checkbox("MACD (전용 패널)", value=False, key="an_show_macd")
                show_mfi = st.checkbox("MFI (전용 패널)", value=False, key="an_show_mfi")
                show_vwap = st.checkbox("Rolling VWAP (가격 위 오버레이)", value=False, key="an_show_vwap")
                show_williams_r = st.checkbox("Williams %R (전용 패널)", value=False, key="an_show_williams_r")
                show_cci = st.checkbox("CCI (전용 패널)", value=False, key="an_show_cci")
                show_roc = st.checkbox("ROC (전용 패널)", value=False, key="an_show_roc")
                show_psar = st.checkbox("Parabolic SAR (가격 패널)", value=False, key="an_show_psar")
                show_cmf = st.checkbox("CMF (전용 패널)", value=False, key="an_show_cmf")

        visible_map = {
            "ma": show_ma, "bb": show_bb, "ichimoku": show_ichimoku, "vol_ma": show_vol_ma,
            "rsi": show_rsi, "stoch": show_stoch, "adx": show_adx,
            "atr": show_atr, "obv": show_obv, "mfi": show_mfi, "vwap": show_vwap,
            "williams_r": show_williams_r, "cci": show_cci, "roc": show_roc, "psar": show_psar, "cmf": show_cmf,
            "macd": show_macd,
        }

        # 숫자 바꿀 때마다 매번 다시 계산하지 않도록 st.form으로 묶어서 "적용하기" 눌러야 반영되게 함
        with st.expander("⚙️ 지표 설정 (고급) - 기간을 직접 바꿔볼 수 있어요"):
            with st.form("indicator_settings_form"):
                set_col1, set_col2, set_col3 = st.columns(3)
                with set_col1:
                    sma_tiny = st.number_input("초단기 이동평균(일)", 2, 20, 5, key="an_sma_tiny")
                    sma_short = st.number_input("단기 이동평균(일)", 5, 60, 20, key="an_sma_short")
                    sma_mid = st.number_input("중기 이동평균(일)", 10, 120, 60, key="an_sma_mid")
                    sma_long = st.number_input("장기 이동평균(일)", 20, 300, 120, key="an_sma_long")
                with set_col2:
                    bb_window = st.number_input("볼린저 기간(일)", 5, 60, 20, key="an_bb_window")
                    bb_std = st.number_input("볼린저 표준편차 배수", 1.0, 4.0, 2.0, step=0.5, key="an_bb_std")
                    rsi_window = st.number_input("RSI 기간(일)", 5, 30, 14, key="an_rsi_window")
                    adx_window = st.number_input("ADX 기간(일)", 5, 30, 14, key="an_adx_window")
                    atr_window = st.number_input("ATR 기간(일)", 5, 30, 14, key="an_atr_window")
                    williams_r_window = st.number_input("Williams %R 기간(일)", 5, 30, 14, key="an_williams_r_window")
                    cci_window = st.number_input("CCI 기간(일)", 5, 60, 20, key="an_cci_window")
                with set_col3:
                    macd_fast = st.number_input("MACD 단기", 5, 30, 12, key="an_macd_fast")
                    macd_slow = st.number_input("MACD 장기", 15, 60, 26, key="an_macd_slow")
                    macd_signal = st.number_input("MACD 시그널", 3, 20, 9, key="an_macd_signal")
                    mfi_window = st.number_input("MFI 기간(일)", 5, 30, 14, key="an_mfi_window")
                    vwap_window = st.number_input("Rolling VWAP 기간(봉)", 5, 60, 20, key="an_vwap_window")
                    roc_window = st.number_input("ROC 기간(일)", 5, 60, 12, key="an_roc_window")
                    psar_step = st.number_input("Parabolic SAR 가속계수", 0.01, 0.10, 0.02, step=0.01, format="%.2f", key="an_psar_step")
                    psar_max_step = st.number_input("Parabolic SAR 최대 가속계수", 0.05, 0.50, 0.20, step=0.05, format="%.2f", key="an_psar_max_step")
                    cmf_window = st.number_input("CMF 기간(일)", 5, 60, 20, key="an_cmf_window")
                st.form_submit_button("✅ 적용하기")

        custom_params = {
            "sma_tiny": sma_tiny, "sma_short": sma_short, "sma_mid": sma_mid, "sma_long": sma_long,
            "bb_window": bb_window, "bb_std": bb_std,
            "rsi_window": rsi_window, "adx_window": adx_window,
            "atr_window": atr_window, "mfi_window": mfi_window, "vwap_window": vwap_window,
            "williams_r_window": williams_r_window, "cci_window": cci_window, "roc_window": roc_window,
            "psar_step": psar_step, "psar_max_step": psar_max_step, "cmf_window": cmf_window,
            "macd_fast": macd_fast, "macd_slow": macd_slow, "macd_signal": macd_signal,
        }

        # 과거 유사상황 통계는 선택한 차트 주기와 무관하게 '일봉' 기준으로 계산.
        # 같은 설정값을 사용하되 현재 분석 UI가 분봉이어도 5/20/60 거래일로 해석한다.
        pattern_results = get_cached_pattern_analysis(
            selected_code,
            tuple(sorted(custom_params.items())),
            HISTORICAL_PATTERN_CACHE_VERSION,
        )
        market_condition = (
            pattern_results.get("_market_condition", {})
            if pattern_results
            else {}
        )

        if not hist_df.empty and len(hist_df) >= 20:
            indicators = compute_all_indicators(hist_df, params=custom_params)
            p = indicators["params"]

            st.caption(
                "💡 마우스 휠로 확대/축소, 드래그로 좌우 이동할 수 있어요 - 이동/확대할 때마다 y축(가격·거래량 및 "
                "동적 스케일 지표)가 보이는 구간에 맞춰 자동으로 다시 그려져요 (더블클릭하면 전체 구간으로 리셋). "
                "위쪽 버튼으로 분봉/일봉/주봉/월봉/분기봉/년봉을 바꿀 수 있고, 지표는 '표시할 지표 선택'에서 켜보세요."
            )
            render_naver_style_chart(
                hist_df, indicators, visible_map=visible_map,
                is_korean_market=selected_code.isdigit(),
            )

            st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
            st.markdown("##### 🎓 지표별 강의 (지금 이 종목 기준) - 눌러서 펼쳐보세요")

            close = hist_df["Close"]
            indicator_explainers = [
                ("📏 이동평균선 (MA)", "ma",
                 generate_ma_commentary(close, indicators["sma20"], indicators["sma60"], indicators["sma120"])),
                ("📐 볼린저 밴드", "bollinger",
                 generate_bollinger_commentary(close, indicators["bb_upper"], indicators["bb_mid"], indicators["bb_lower"])),
                ("⚡ RSI (상대강도지수)", "rsi",
                 generate_rsi_commentary(indicators["rsi14"])),
                ("🌀 스토캐스틱", "stochastic",
                 generate_stochastic_commentary(indicators["stoch_k"], indicators["stoch_d"])),
                ("📈 ADX / DMI (추세 강도)", "adx",
                 generate_adx_commentary(indicators["adx14"], indicators["plus_di14"], indicators["minus_di14"])),
                ("📏 ATR (변동성)", "atr",
                 generate_atr_commentary(indicators["atr14"], close)),
                ("📦 OBV (거래량 흐름)", "obv",
                 generate_obv_commentary(indicators["obv"])),
                ("💰 MFI (자금 흐름)", "mfi",
                 generate_mfi_commentary(indicators["mfi14"])),
                ("⚖️ Rolling VWAP (거래량가중 평균가)", "vwap",
                 generate_vwap_commentary(close, indicators["rolling_vwap20"])),
                ("☁️ 일목균형표", "ichimoku",
                 generate_ichimoku_commentary(close, indicators["tenkan"], indicators["kijun"],
                                               indicators["senkou_a"], indicators["senkou_b"])),
                ("🔀 MACD", "macd",
                 generate_macd_commentary(indicators["macd_line"], indicators["macd_signal"], indicators["macd_hist"])),
                ("📊 거래량", "volume",
                 generate_volume_commentary(hist_df["Volume"], indicators["vol_ma20"])),
                ("📉 Williams %R", "williams_r",
                 generate_williams_r_commentary(indicators["williams_r"])),
                ("📏 CCI", "cci",
                 generate_cci_commentary(indicators["cci"])),
                ("🚀 ROC", "roc",
                 generate_roc_commentary(indicators["roc"])),
                ("📍 Parabolic SAR", "psar",
                 generate_psar_commentary(close, indicators["psar"])),
                ("💰 CMF", "cmf",
                 generate_cmf_commentary(indicators["cmf"])),
            ]

            for title, lesson_key, commentary in indicator_explainers:
                lesson = INDICATOR_LESSONS[lesson_key]
                with st.expander(title):
                    st.markdown("**📖 개념**")
                    st.markdown(f"<div class='indicator-card-desc'>{lesson['concept']}</div>", unsafe_allow_html=True)
                    st.markdown("**🧮 계산 방법**")
                    st.markdown(f"<div class='indicator-card-desc'>{lesson['calculation']}</div>", unsafe_allow_html=True)
                    st.markdown("**🎯 실전 활용**")
                    st.markdown(f"<div class='indicator-card-desc'>{lesson['how_to_use']}</div>", unsafe_allow_html=True)
                    st.markdown("**⚠️ 초보자가 흔히 하는 실수**")
                    st.markdown(f"<div class='indicator-card-desc'>{lesson['common_mistakes']}</div>", unsafe_allow_html=True)
                    st.markdown("**🔎 지금 이 종목 기준**")
                    st.markdown(f"<div class='indicator-card-desc'>{commentary}</div>", unsafe_allow_html=True)

                    # ------------------------------------------------------
                    # 과거 유사상황 통계
                    # 현재 상태와 비슷한 과거 일봉을 찾아 그 이후 실제 결과를
                    # 5/20/60 거래일 단위로 집계한다. 미래 예측값이 아니다.
                    # ------------------------------------------------------
                    pattern = pattern_results.get(lesson_key) if pattern_results else None
                    st.markdown("**📈 과거 유사 상황 통계**")

                    # 현재 기술적 상태에 따라 '먼저 보여줄' 역사적 결과 방향을 바꾼다.
                    # 과매수 → 하락 사례 비율 우선, 과매도 → 상승 사례 비율 우선.
                    # 이는 표시 순서만 바꾸며 역사적 표본/계산 방식 자체는 변경하지 않는다.
                    state = market_condition.get("state", "neutral")
                    context = market_condition.get("context", "중립/혼조")
                    primary_direction = market_condition.get("primary_direction", "up")

                    if state.startswith("overbought"):
                        signal_text = ", ".join(market_condition.get("signals", [])[:4])
                        reason_text = (
                            f"현재 기술적 상태는 <b>{context}</b>로 분류됩니다. "
                            f"과매수 신호 {market_condition.get('overbought_count', 0)}개"
                            f"{f' ({signal_text})' if signal_text else ''}. "
                            "따라서 아래에서는 과거 <b>하락 사례 비율</b>을 먼저 보여줍니다."
                        )
                        st.markdown(
                            f"<div class='indicator-card-desc'>{reason_text}</div>",
                            unsafe_allow_html=True,
                        )
                    elif state.startswith("oversold"):
                        signal_text = ", ".join(market_condition.get("signals", [])[:4])
                        reason_text = (
                            f"현재 기술적 상태는 <b>{context}</b>로 분류됩니다. "
                            f"과매도 신호 {market_condition.get('oversold_count', 0)}개"
                            f"{f' ({signal_text})' if signal_text else ''}. "
                            "따라서 아래에서는 과거 <b>상승 사례 비율</b>을 먼저 보여줍니다."
                        )
                        st.markdown(
                            f"<div class='indicator-card-desc'>{reason_text}</div>",
                            unsafe_allow_html=True,
                        )
                    elif context in ("상승추세", "하락추세"):
                        st.caption(
                            f"현재 기술적 문맥: {context}. "
                            "과매수/과매도 전환 조건에는 해당하지 않아 기본 상승 사례 비율을 먼저 보여줍니다."
                        )

                    if not pattern:
                        st.caption("과거 일봉 데이터를 불러오지 못해 유사상황 통계를 계산할 수 없습니다.")
                    elif pattern.get("status") == "insufficient_data":
                        st.caption(
                            pattern.get(
                                "message",
                                f"과거 일봉 데이터가 부족합니다 ({pattern.get('data_bars', 0)}봉 / "
                                f"최소 {pattern.get('required_bars', 120)}봉).",
                            )
                        )
                    elif pattern.get("status") == "insufficient_matches":
                        st.caption(
                            pattern.get(
                                "message",
                                f"과거 데이터는 충분하지만 현재 조건과 유사한 사례가 "
                                f"{pattern.get('matches', 0)}회로 최소 {pattern.get('min_required', 12)}회에 미달합니다.",
                            )
                        )
                    elif pattern.get("status") == "no_outcomes":
                        st.caption("유사한 과거 사례는 찾았지만 5/20/60 거래일 후 실제 결과를 확인할 수 있는 사례가 없습니다.")
                    elif pattern.get("status") != "ok":
                        st.caption(pattern.get("message", "과거 유사상황 통계를 계산할 수 없습니다."))
                    else:
                        lookback_years = pattern.get("lookback_years", 10)
                        min_similarity = pattern.get("min_similarity", 65.0)
                        st.caption(
                            f"최근 {lookback_years}년 안에서 유사도 {min_similarity:.0f}/100 이상인 "
                            f"후보 {pattern.get('candidate_matches', pattern.get('matches', 0))}건 중 "
                            f"5거래일 이상 간격을 둔 유사 조건 {pattern.get('matches', 0)}건을 사용합니다. "
                            f"각 기간별 숫자는 그중 실제 주가 결과가 존재하는 사례만 집계합니다."
                        )
                        horizon_cols = st.columns(3)
                        horizon_labels = {"5": "5거래일 후", "20": "20거래일 후", "60": "60거래일 후"}
                        for col, horizon in zip(horizon_cols, ("5", "20", "60")):
                            stats = pattern.get("horizons", {}).get(horizon)
                            with col:
                                st.markdown(f"**{horizon_labels[horizon]}**")
                                if not stats:
                                    st.caption("실제 결과 사례 없음")
                                else:
                                    if primary_direction == "down":
                                        primary_label = "과거 하락 사례 비율"
                                        primary_value = stats.get("down_probability")
                                        primary_ci_low = stats.get("down_probability_ci_low")
                                        primary_ci_high = stats.get("down_probability_ci_high")
                                        primary_help = (
                                            "예측 확률이 아니라, 현재 과매수/유사 조건과 비슷했던 과거 날짜들 중 "
                                            "해당 기간 후 실제 종가가 하락한 비율입니다."
                                        )
                                        secondary_label = "과거 상승 사례 비율"
                                        secondary_value = stats.get("up_probability")
                                    else:
                                        primary_label = "과거 상승 사례 비율"
                                        primary_value = stats.get("up_probability")
                                        primary_ci_low = stats.get("up_probability_ci_low")
                                        primary_ci_high = stats.get("up_probability_ci_high")
                                        primary_help = (
                                            "예측 확률이 아니라, 조건이 유사했던 과거 날짜들 중 "
                                            "해당 기간 후 실제 종가가 상승한 비율입니다."
                                        )
                                        secondary_label = "과거 하락 사례 비율"
                                        secondary_value = stats.get("down_probability")

                                    stat_cols = st.columns(2)
                                    with stat_cols[0]:
                                        if primary_value is not None:
                                            st.metric(
                                                primary_label,
                                                f"{primary_value:.1f}%",
                                                help=primary_help,
                                            )
                                    with stat_cols[1]:
                                        if secondary_value is not None:
                                            st.metric(
                                                secondary_label,
                                                f"{secondary_value:.1f}%",
                                                help=(
                                                    "상승/하락은 실제 종가 방향 기준입니다. "
                                                    "보합 사례가 있으면 두 수치의 합이 100%가 되지 않을 수 있습니다."
                                                ),
                                            )

                                    st.caption(
                                        f"중앙값 {stats['median_return']:+.1f}% · "
                                        f"평균 {stats['mean_return']:+.1f}% · "
                                        f"실제 결과 사례 {stats['samples']}건"
                                    )
                                    if primary_ci_low is not None and primary_ci_high is not None:
                                        st.caption(
                                            f"{'하락' if primary_direction == 'down' else '상승'}비율 95% 구간: "
                                            f"{primary_ci_low:.1f}% ~ {primary_ci_high:.1f}%"
                                        )
                        if pattern.get("avg_similarity") is not None:
                            st.caption(
                                f"유사도 평균: {pattern['avg_similarity']:.1f}/100 · "
                                f"최근 {lookback_years}년 내 실제 사례 기준"
                            )
                        st.caption(
                            "※ 과거 유사 조건의 실제 결과를 집계한 참고 통계입니다. "
                            "현재 상태의 과매수/과매도 판정은 표시 방향을 정하기 위한 분류일 뿐 미래 가격을 예측하지 않습니다. "
                            "표본 수가 작거나 95% 구간이 넓으면 숫자의 불확실성이 큽니다."
                        )
        elif not hist_df.empty:
            st.info(f"차트 데이터가 20개 캔들({interval_choice} 기준) 미만이라 지표를 계산하기엔 아직 부족해요. 기본 가격 흐름만 보여드릴게요.")
            st.line_chart(hist_df["Close"])
        else:
            st.info("실시간 차트 데이터를 불러올 수 없습니다.")


elif view_mode_param == "earnings_calendar":
    # ==========================================
    # [5-0] Earnings Calendar 전용 전체 화면
    # 메인 홈의 로고/명언/광고 구조만 유지하고, 캘린더를 독립 화면처럼 보여준다.
    # st.link_button은 새 탭을 열기 때문에 메인 화면과 캘린더를 동시에 유지할 수 있다.
    # ==========================================
    col_logo, col_quote, col_login = st.columns([1.0, 6.8, 1.0])

    with col_logo:
        st.markdown("<div class='logo-box'>📈 Fundamental</div>", unsafe_allow_html=True)

    with col_quote:
        render_quote_box()

    with col_login:
        render_theme_toggle("theme_toggle_earnings_calendar")
        st.markdown(
            f"<a href='?theme={THEME_MODE}' target='_self' "
            "style='display:inline-flex;align-items:center;justify-content:center;"
            "min-height:38px;padding:0 13px;border:1.5px solid #D1D5DB;border-radius:10px;"
            "background:#FFFFFF;color:#1A1A1A;text-decoration:none;font-size:13px;font-weight:850;'>"
            "메인으로</a>",
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    left_ad, calendar_main, right_ad = st.columns([0.6, 6.8, 0.6])

    with left_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

    with calendar_main:
        st.markdown("<div class='earnings-page-title'>📅 Earnings Calendar</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='earnings-page-subtitle'>미국·한국 시장의 최근 발표 및 향후 예정 실적을 월간 캘린더로 확인하세요. "
            "향후 일정은 변경될 수 있으며, 실제 발표일과 차이가 날 수 있습니다.</div>",
            unsafe_allow_html=True,
        )

        market_filter = st.radio(
            "시장",
            ["전체", "🇺🇸 미국", "🇰🇷 한국"],
            horizontal=True,
            label_visibility="collapsed",
            key="earnings_calendar_market_filter",
        )

        if "earnings_calendar_month" not in st.session_state:
            st.session_state["earnings_calendar_month"] = date.today().replace(day=1)

        month_start = st.session_state["earnings_calendar_month"]

        nav_prev, nav_title, nav_next = st.columns([1.1, 5.8, 1.1])
        with nav_prev:
            if st.button("‹ 이전", key="earnings_calendar_prev", use_container_width=True):
                year = month_start.year
                month = month_start.month - 1
                if month == 0:
                    year -= 1
                    month = 12
                st.session_state["earnings_calendar_month"] = date(year, month, 1)
                st.rerun()
        with nav_title:
            st.markdown(
                f"<div style='text-align:center;padding:7px 0;color:#111827;font-size:20px;font-weight:900;'>"
                f"{month_start.year}년 {month_start.month}월</div>",
                unsafe_allow_html=True,
            )
        with nav_next:
            if st.button("다음 ›", key="earnings_calendar_next", use_container_width=True):
                year = month_start.year
                month = month_start.month + 1
                if month == 13:
                    year += 1
                    month = 1
                st.session_state["earnings_calendar_month"] = date(year, month, 1)
                st.rerun()

        all_events = _earnings_calendar_events()
        visible_events = [
            row for row in all_events
            if market_filter == "전체" or row["market"] == ("US" if market_filter == "🇺🇸 미국" else "KR")
        ]

        month_events = [row for row in visible_events if row["date"].year == month_start.year and row["date"].month == month_start.month]
        total_count = len(month_events)
        us_count = sum(1 for row in month_events if row["market"] == "US")
        kr_count = sum(1 for row in month_events if row["market"] == "KR")
        consensus_count = sum(
            1 for row in month_events
            if row.get("eps_estimate") is not None and not pd.isna(row.get("eps_estimate"))
        )

        st.markdown(
            f"""
            <div class='earnings-summary-grid'>
              <div class='earnings-summary-card'><div class='earnings-summary-label'>이번 달 일정</div><div class='earnings-summary-value'>{total_count}</div></div>
              <div class='earnings-summary-card'><div class='earnings-summary-label'>🇺🇸 미국</div><div class='earnings-summary-value'>{us_count}</div></div>
              <div class='earnings-summary-card'><div class='earnings-summary-label'>🇰🇷 한국</div><div class='earnings-summary-value'>{kr_count}</div></div>
              <div class='earnings-summary-card'><div class='earnings-summary-label'>컨센서스 제공</div><div class='earnings-summary-value'>{consensus_count}</div></div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        available_dates = sorted({row["date"] for row in month_events})
        current_selected = st.session_state.get("earnings_calendar_selected_date")
        if current_selected not in available_dates:
            current_selected = None

        _render_earnings_calendar_grid(
            month_start,
            all_events,
            market_filter,
            selected_date=current_selected,
        )

        if current_selected:
            _render_earnings_detail(current_selected, all_events, market_filter)
        else:
            st.markdown(
                "<div class='news-empty-state'>위 캘린더에서 날짜를 클릭하면 해당 날짜의 실적 일정이 바로 아래에 표시됩니다.</div>",
                unsafe_allow_html=True,
            )

        st.caption(
            "※ 미국 예정 일정은 Yahoo Finance 제공 일정, 한국 예정 일정도 Yahoo Finance 종목별 Earnings Date를 "
            "기반으로 한 참고 일정입니다. 확정 공시가 나오면 DART 발표 실적이 별도로 표시됩니다."
        )

    with right_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

elif not selected_code:
    col_logo, col_quote, col_login = st.columns([1.0, 6.8, 1.0])

    with col_logo:
        st.markdown(
            "<div class='logo-box'>📈 Fundamental</div>",
            unsafe_allow_html=True,
        )

    with col_quote:
        render_quote_box()

    with col_login:
        render_theme_toggle("theme_toggle_home")
        if st.button("Log in", use_container_width=True):
            st.toast("로그인 기능 준비 중입니다!")

    st.markdown("<br>", unsafe_allow_html=True)

    left_ad, main_content, right_ad = st.columns([0.6, 6.8, 0.6])

    with left_ad:
        st.markdown(
            "<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True
        )

    with main_content:
        # 기존 5개 메뉴의 위치는 그대로 유지하되, 검색창 아래 콘텐츠를 실제로 전환할 수 있도록
        # 가로형 radio를 탭처럼 스타일링한다. (native st.tabs는 선택 상태를 외부에서 읽기 어려움)
        home_nav = st.radio(
            "홈 메뉴",
            [
                "US Market Overview",
                "Korea Market Overview",
                "Live News",
                "Chart Analysis",
                "Earnings Calendar",
            ],
            index=2,
            horizontal=True,
            label_visibility="collapsed",
            key="home_navigation",
        )

        combined_stocks_db = get_combined_stock_db()

        # 검색창은 5개 상단 메뉴 바로 아래에 한 번만 둔다.
        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
        render_unified_search_box(stock_db=combined_stocks_db)

        # 메인 Live News는 검색창 바로 아래가 기본 화면이다.
        if home_nav == "Live News":
            render_home_live_news(limit=6)
        elif home_nav == "US Market Overview":
            render_home_market_overview("US")
        elif home_nav == "Korea Market Overview":
            render_home_market_overview("KR")
        elif home_nav == "Chart Analysis":
            st.markdown(
                "<div class='live-news-section'><div class='live-news-section-title'>📊 Chart Analysis</div>"
                "<div class='live-news-section-subtitle'>기존 차트 분석 기능은 그대로 유지됩니다. 종목을 선택하면 상세 기술적 분석 화면으로 이동합니다.</div></div>",
                unsafe_allow_html=True,
            )
            if st.button("📈 Chart Analysis 열기", use_container_width=False, key="home_open_chart_analysis"):
                st.query_params["view"] = "analysis_search"
                st.query_params.pop("code", None)
                st.rerun()
        elif home_nav == "Earnings Calendar":
            render_home_earnings_calendar(limit=12)


        # 뉴스 아래로 탐색용 3개 카드를 더 내려 배치한다.
        st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='bottom-cards-wrapper'>", unsafe_allow_html=True
        )
        col_6, col_7, col_8 = st.columns(3)

        with col_6:
            st.markdown(
                f"""
                <div class='sketch-card'>
                    <b style='color: {THEME["text"]}; font-size: 15px;'>🔥 Most Searched Stocks</b>
                    <div style='margin-top: 12px;'>
                        <div class='card-item-row'>
                            <a href='/?code=005930&theme={THEME_MODE}' target='_blank' class='stock-link'>1. 삼성전자 (005930)</a>
                            <span class='search-count-badge'>18,420회</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=NVDA&theme={THEME_MODE}' target='_blank' class='stock-link'>2. NVIDIA (NVDA)</a>
                            <span class='search-count-badge'>15,810회</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=000660&theme={THEME_MODE}' target='_blank' class='stock-link'>3. SK하이닉스 (000660)</a>
                            <span class='search-count-badge'>12,340회</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=AAPL&theme={THEME_MODE}' target='_blank' class='stock-link'>4. Apple (AAPL)</a>
                            <span class='search-count-badge'>9,580회</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=TSLA&theme={THEME_MODE}' target='_blank' class='stock-link'>5. Tesla (TSLA)</a>
                            <span class='search-count-badge'>8,210회</span>
                        </div>
                    </div>
                </div>
            """,
                unsafe_allow_html=True,
            )

        with col_7:
            st.markdown(
                f"""
                <div class='sketch-card'>
                    <b style='color: {THEME["text"]}; font-size: 15px;'>🇺🇸 Trending Searches (US)</b>
                    <div style='margin-top: 12px;'>
                        <div class='card-item-row'>
                            <a href='/?code=NVDA&theme={THEME_MODE}' target='_blank' class='stock-link'>1. NVIDIA (NVDA)</a>
                            <span style='font-size: 11px; color: {THEME["success"]}; font-weight: 700;'>▲ HOT</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=AAPL&theme={THEME_MODE}' target='_blank' class='stock-link'>2. Apple (AAPL)</a>
                            <span style='font-size: 11px; color: {THEME["success"]}; font-weight: 700;'>▲ 2</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=TSLA&theme={THEME_MODE}' target='_blank' class='stock-link'>3. Tesla (TSLA)</a>
                            <span style='font-size: 11px; color: {THEME["positive"]}; font-weight: 700;'>▼ 1</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=PLTR&theme={THEME_MODE}' target='_blank' class='stock-link'>4. Palantir (PLTR)</a>
                            <span style='font-size: 11px; color: {THEME["success"]}; font-weight: 700;'>▲ NEW</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=MSFT&theme={THEME_MODE}' target='_blank' class='stock-link'>5. Microsoft (MSFT)</a>
                            <span style='font-size: 11px; color: {THEME["text_muted"]}; font-weight: 700;'>-</span>
                        </div>
                    </div>
                </div>
            """,
                unsafe_allow_html=True,
            )

        with col_8:
            st.markdown(
                f"""
                <div class='sketch-card'>
                    <b style='color: {THEME["text"]}; font-size: 15px;'>🇰🇷 Trending Searches (KOR)</b>
                    <div style='margin-top: 12px;'>
                        <div class='card-item-row'>
                            <a href='/?code=005930&theme={THEME_MODE}' target='_blank' class='stock-link'>1. 삼성전자 (005930)</a>
                            <span style='font-size: 11px; color: {THEME["success"]}; font-weight: 700;'>▲ HOT</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=000660&theme={THEME_MODE}' target='_blank' class='stock-link'>2. SK하이닉스 (000660)</a>
                            <span style='font-size: 11px; color: {THEME["success"]}; font-weight: 700;'>▲ 1</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=005380&theme={THEME_MODE}' target='_blank' class='stock-link'>3. 현대차 (005380)</a>
                            <span style='font-size: 11px; color: {THEME["text_muted"]}; font-weight: 700;'>-</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=035420&theme={THEME_MODE}' target='_blank' class='stock-link'>4. NAVER (035420)</a>
                            <span style='font-size: 11px; color: {THEME["success"]}; font-weight: 700;'>▲ 3</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=035720&theme={THEME_MODE}' target='_blank' class='stock-link'>5. 카카오 (035720)</a>
                            <span style='font-size: 11px; color: {THEME["positive"]}; font-weight: 700;'>▼ 2</span>
                        </div>
                    </div>
                </div>
            """,
                unsafe_allow_html=True,
            )
        st.markdown("</div>", unsafe_allow_html=True)

    with right_ad:
        st.markdown(
            "<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True
        )

else:
    # ==========================================
    # [스케치 기반] 펀더멘탈 상세 분석 리포트 페이지
    # ==========================================
    data = get_stock_data(selected_code)

    if selected_code and not str(selected_code).isdigit():
        render_us_fundamental_report(selected_code, data)
        st.stop()

    col_logo, col_quote, col_login = st.columns([1.0, 6.8, 1.0])

    with col_logo:
        st.markdown(
            "<div class='logo-box'>📈 Fundamental</div>",
            unsafe_allow_html=True,
        )

    with col_quote:
        render_quote_box()

    with col_login:
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
        if st.button("⬅️ 메인으로", use_container_width=True):
            st.query_params.clear()
            st.rerun()

    st.markdown("<br>", unsafe_allow_html=True)

    left_ad, main_content, right_ad = st.columns([0.6, 6.8, 0.6])

    with left_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

    with main_content:
        st.markdown(f"## 📊 [{data.get('stock_name', selected_code)}] 펀더멘탈 방어력 분석")
        render_home_stock_news(data.get("stock_name", selected_code), selected_code, limit=6)

        st.markdown("#### 📈 시세 스냅샷")
        st.caption(
            "📌 아래 가격은 체결 틱이 아니라 **최신으로 확인 가능한 일봉** 기준입니다. 가격 기준일·출처와 재무 기준일은 서로 분리해서 표시합니다."
        )

        @st.cache_data(ttl=300, show_spinner=False)
        def _get_recent_ohlcv_for_overview(code):
            """시세 스냅샷용 일봉 OHLCV.
            
            미국은 yfinance 원가격(auto_adjust=False)을 사용하고, 국내는
            FinanceDataReader를 사용한다. 반환값의 Date 컬럼이 가격 기준일이다.
            이는 '실시간 틱'이 아니라 최신으로 확인 가능한 일봉이라는 점을 명확히 한다.
            """
            is_kr = str(code).isdigit()
            try:
                if is_kr:
                    df = fdr.DataReader(code)
                    source = "FinanceDataReader"
                else:
                    df = yf.Ticker(code).history(
                        period="1y",
                        interval="1d",
                        auto_adjust=False,
                    )
                    source = "yfinance"
                if df is None or df.empty:
                    return pd.DataFrame()
                df = df.tail(400).reset_index()
                if "Date" not in df.columns and "Datetime" in df.columns:
                    df = df.rename(columns={"Datetime": "Date"})
                df.attrs["market_data_source"] = source
                df.attrs["bar_type"] = "1d"
                return df
            except Exception:
                return pd.DataFrame()

        @st.cache_data(ttl=6 * 3600, show_spinner=False)
        def _get_us_sec_share_snapshot(cik):
            """SEC DEI의 최신 보통주 발행주식수 관측치를 반환한다."""
            if not cik:
                return {}
            try:
                cik10 = str(cik).strip().zfill(10)
                url = "https://data.sec.gov/api/xbrl/companyfacts/CIK" + cik10 + ".json"
                response = requests.get(
                    url,
                    headers={"User-Agent": "Fundamental Korea research contact@example.com"},
                    timeout=20,
                )
                response.raise_for_status()
                facts = response.json().get("facts", {}).get("dei", {})
                fact = facts.get("EntityCommonStockSharesOutstanding") or {}
                rows = []
                for unit, values in (fact.get("units") or {}).items():
                    if unit.lower() not in {"shares", "share"}:
                        continue
                    for row in values or []:
                        value = row.get("val")
                        end = row.get("end")
                        filed = row.get("filed")
                        if value is None or not end:
                            continue
                        try:
                            value = float(value)
                        except (TypeError, ValueError):
                            continue
                        rows.append({
                            "value": value,
                            "asof": str(end),
                            "filed": str(filed or ""),
                            "form": row.get("form") or "",
                        })
                if not rows:
                    return {}
                rows.sort(key=lambda x: (x["asof"], x["filed"], x["form"]))
                return rows[-1]
            except Exception:
                return {}

        def _format_usd_compact(value):
            if value is None:
                return "N/A"
            value = float(value)
            sign = "-" if value < 0 else ""
            value = abs(value)
            if value >= 1_000_000_000_000:
                return f"{sign}${value/1_000_000_000_000:.2f}T"
            if value >= 1_000_000_000:
                return f"{sign}${value/1_000_000_000:.2f}B"
            if value >= 1_000_000:
                return f"{sign}${value/1_000_000:.2f}M"
            return f"{sign}${value:,.0f}"

        def _tone(value, ref):
            if value is None or ref is None:
                return "neutral"
            if value > ref:
                return "up"
            if value < ref:
                return "down"
            return "neutral"

        def _format_krw_compact(value):
            """1억 미만은 원 단위 그대로, 그 이상은 조/억 단위로 축약 표시 (예: 2조 5,875억원)."""
            if value is None:
                return "N/A"
            sign = "-" if value < 0 else ""
            v = abs(value)
            JO = 1_0000_0000_0000   # 1조
            EOK = 1_0000_0000       # 1억
            if v >= JO:
                jo_part = int(v // JO)
                eok_part = int((v % JO) // EOK)
                return f"{sign}{jo_part:,}조 {eok_part:,}억원" if eok_part else f"{sign}{jo_part:,}조원"
            elif v >= EOK:
                eok_part = int(v // EOK)
                return f"{sign}{eok_part:,}억원"
            return f"{sign}{v:,.0f}원"

        ohlcv_overview_df = _get_recent_ohlcv_for_overview(selected_code)
        is_kr_stock = str(selected_code).isdigit()
        won = "원" if is_kr_stock else "$"

        overview = {}  # label -> (value_str, tone)
        live_price = None
        price_asof = None
        price_age_days = None
        price_source = None
        price_bar_type = None

        if not ohlcv_overview_df.empty and len(ohlcv_overview_df) >= 2:
            last_row = ohlcv_overview_df.iloc[-1]
            prev_row = ohlcv_overview_df.iloc[-2]
            recent_52w = ohlcv_overview_df.tail(252)
            if "Date" in ohlcv_overview_df.columns:
                parsed_price_date = pd.to_datetime(last_row["Date"], errors="coerce")
                if not pd.isna(parsed_price_date):
                    price_asof = parsed_price_date.date()
                    try:
                        market_tz = "Asia/Seoul" if is_kr_stock else "America/New_York"
                        price_age_days = (pd.Timestamp.now(tz=market_tz).date() - price_asof).days
                    except Exception:
                        price_age_days = None
            price_source = ohlcv_overview_df.attrs.get(
                "market_data_source",
                "FinanceDataReader" if is_kr_stock else "yfinance",
            )
            price_bar_type = ohlcv_overview_df.attrs.get("bar_type", "1d")
            prev_close = float(prev_row["Close"])
            live_price = float(last_row["Close"])
            today_volume = float(last_row["Volume"])

            overview["전일"] = (f"{prev_close:,.0f}{won}", "neutral")
            overview["시가"] = (f"{last_row['Open']:,.0f}{won}", _tone(last_row["Open"], prev_close))
            overview["고가"] = (f"{last_row['High']:,.0f}{won}", _tone(last_row["High"], prev_close))
            overview["저가"] = (f"{last_row['Low']:,.0f}{won}", _tone(last_row["Low"], prev_close))

            # 거래량 옆에 '평소보다 많은 거래인지'를 보여주기 위해 최근 20거래일(오늘 제외)
            # 평균 거래량 대비 비율을 함께 표시.
            volume_value_html = f"{today_volume:,.0f}"
            prior_20d = ohlcv_overview_df["Volume"].iloc[:-1].tail(20)
            if len(prior_20d) >= 5:  # 데이터가 너무 적으면(신규상장 등) 비교 자체를 생략
                avg_volume_20d = float(prior_20d.mean())
                if avg_volume_20d > 0:
                    vol_ratio = today_volume / avg_volume_20d * 100
                    vol_badge_cls = "vol-high" if vol_ratio >= 100 else "vol-low"
                    vol_desc = "평소보다 많음" if vol_ratio >= 100 else "평소보다 적음"
                    volume_value_html += (
                        f"<div class='overview-subvalue {vol_badge_cls}'>"
                        f"20일 평균 대비 {vol_ratio:.0f}% · {vol_desc}</div>"
                    )
            overview["거래량"] = (volume_value_html, "neutral")
            w52_high = float(recent_52w["High"].max())
            w52_low = float(recent_52w["Low"].min())
            overview["52주 최고"] = (f"{w52_high:,.0f}{won}", "neutral")
            overview["52주 최저"] = (f"{w52_low:,.0f}{won}", "neutral")

            # '하락장 방어력'이라는 사이트 성격에 맞게, 오늘 등락률 대신 "52주 고점 대비
            # 지금 얼마나 빠져있는지"를 기본 정보로 보여줌 (0% 이상이면 52주 신고가 갱신).
            if w52_high > 0:
                pct_from_high = (live_price - w52_high) / w52_high * 100
                overview["52주 고점 대비"] = (
                    f"{pct_from_high:+.1f}%",
                    "up" if pct_from_high >= 0 else "down",
                )

        # collector.py가 이미 DART 공시 기준 EPS/BPS/주당배당금을 원시값으로 저장해두고 있어서
        # (PER/PBR을 거꾸로 나눠서 추정할 필요 없이) 그 원시값을 그대로 쓰고, PER/PBR/배당수익률은
        # '오늘 주가 ÷ 원시값'으로 매일 갱신되는 라이브 값을 계산한다. 아직 CFS 재수집 전이라
        # eps/bps 원시값이 없는 종목만 예전 방식(저장된 per/pbr에서 역산)으로 폴백한다.
        overview_supabase_data = data.get("supabase_data") or {}
        ov_per_stored = overview_supabase_data.get("per")
        ov_pbr_stored = overview_supabase_data.get("pbr")
        ov_eps = overview_supabase_data.get("eps")
        ov_bps = overview_supabase_data.get("bps")
        ov_dps = overview_supabase_data.get("dividend_per_share")
        ov_dividend_yield_stored = overview_supabase_data.get("dividend_yield")
        ov_net_income = overview_supabase_data.get("net_income")

        if live_price is None:
            live_price = overview_supabase_data.get("stock_price")
            if live_price is not None:
                price_source = price_source or "Supabase 저장 시세"
                stored_market_date = overview_supabase_data.get("market_snapshot_date")
                if stored_market_date:
                    try:
                        parsed_stored_date = pd.to_datetime(
                            stored_market_date, errors="coerce"
                        )
                        if not pd.isna(parsed_stored_date):
                            price_asof = parsed_stored_date.date()
                            try:
                                market_tz = "Asia/Seoul" if is_kr_stock else "America/New_York"
                                price_age_days = (
                                    pd.Timestamp.now(tz=market_tz).date() - price_asof
                                ).days
                            except Exception:
                                price_age_days = None
                    except Exception:
                        pass

        # 시가총액: 국내는 Fundamental에 저장된 직접값을 우선 사용하고,
        # 미국은 SEC DEI의 보통주 발행주식수 × 최신 가격으로 계산한다.
        if is_kr_stock:
            kr_market_cap = overview_supabase_data.get("market_cap")
            if kr_market_cap is not None:
                overview["시가총액"] = (_format_krw_compact(kr_market_cap), "neutral")
        else:
            sec_shares = _get_us_sec_share_snapshot((data.get("us_company_data") or {}).get("cik"))
            if sec_shares.get("value") and live_price:
                market_cap_usd = float(sec_shares["value"]) * float(live_price)
                overview["시가총액"] = (_format_usd_compact(market_cap_usd), "neutral")
                shares_basis = sec_shares.get("asof")
                if shares_basis:
                    overview["시가총액 기준"] = (
                        f"SEC 주식수 {float(sec_shares['value']):,.0f}주 · {shares_basis}",
                        "neutral",
                    )
            elif (data.get("info") or {}).get("sharesOutstanding") and live_price:
                shares = float((data.get("info") or {}).get("sharesOutstanding"))
                overview["시가총액(보조추정)"] = (
                    _format_usd_compact(shares * float(live_price)),
                    "neutral",
                )

        if ov_eps is not None:
            overview["EPS"] = (f"{ov_eps:,.0f}{won}", "neutral")
            if live_price and ov_eps != 0:
                overview["PER"] = (f"{live_price / ov_eps:.2f}", "neutral")
        elif ov_per_stored is not None and ov_per_stored > 0 and live_price:
            # 폴백: 아직 재수집 전이라 eps 원시값이 없는 종목만 예전처럼 역산 + '(추정)' 라벨
            eps_est = live_price / ov_per_stored
            overview["EPS(추정)"] = (f"{eps_est:,.0f}{won}", "neutral")
            overview["PER"] = (f"{ov_per_stored}", "neutral")

        if ov_bps is not None:
            overview["BPS"] = (f"{ov_bps:,.0f}{won}", "neutral")
            if live_price and ov_bps > 0:
                overview["PBR"] = (f"{live_price / ov_bps:.2f}", "neutral")
        elif ov_pbr_stored is not None and ov_pbr_stored > 0 and live_price:
            bps_est = live_price / ov_pbr_stored
            overview["BPS(추정)"] = (f"{bps_est:,.0f}{won}", "neutral")
            overview["PBR"] = (f"{ov_pbr_stored}", "neutral")

        if ov_dps is not None:
            overview["주당배당금"] = (f"{ov_dps:,.0f}{won}", "neutral")
            if live_price:
                overview["배당수익률"] = (f"{ov_dps / live_price * 100:.2f}%", "neutral")
        elif ov_dividend_yield_stored is not None:
            overview["배당수익률"] = (f"{ov_dividend_yield_stored}%", "neutral")
            if live_price:
                dps_est = ov_dividend_yield_stored / 100 * live_price
                overview["주당배당금(추정)"] = (f"{dps_est:,.0f}{won}", "neutral")

        # 아직 소스가 없는 항목은 값 대신 "준비 중"으로 명시 (없는 척 숨기지 않고 투명하게 표시)
        overview["외인소진율"] = ("준비 중", "neutral")
        overview["추정PER / 추정EPS"] = ("준비 중", "neutral")

        if overview:
            tone_class_map = {"up": "value-up", "down": "value-down", "neutral": ""}
            overview_cells_html = "".join(
                f"<div class='overview-cell'><div class='overview-label'>{label}</div>"
                f"<div class='overview-value {tone_class_map.get(tone, '')}'>{value}</div></div>"
                for label, (value, tone) in overview.items()
            )
            st.markdown(f"<div class='overview-grid'>{overview_cells_html}</div>", unsafe_allow_html=True)

            data_basis_label = overview_supabase_data.get("data_basis_label")
            if data_basis_label:
                st.caption(f"📅 재무 수치 기준: **{data_basis_label}** (최신 공시가 나오면 자동 갱신됩니다)")

            price_meta = []
            if price_asof:
                price_meta.append(f"가격 기준일 {price_asof}")
            if price_age_days is not None:
                price_meta.append(f"기준일로부터 {max(price_age_days, 0)}일")
            if price_source:
                price_meta.append(f"출처 {price_source}")
            if price_bar_type:
                price_meta.append(f"봉 {price_bar_type}")
            if price_meta:
                st.caption("📈 시장 데이터: " + " · ".join(price_meta))
            if price_age_days is not None and price_age_days > 3:
                st.warning(
                    "⚠️ 현재 표시된 시장가격이 시장 기준일보다 3일 이상 경과했습니다. "
                    "주말·휴장일 또는 외부 시세 제공 지연일 수 있으므로 가격 기반 지표를 확인할 때 기준일을 함께 보세요."
                )
        else:
            st.info("시세 스냅샷 데이터를 불러올 수 없습니다.")

        st.caption(
            "ℹ️ 가격·거래량은 최신 일봉, PER/PBR/배당은 화면에 표시된 가격과 최근 확정 재무 데이터를 조합해 계산합니다. "
            "미국 시가총액은 가능한 경우 SEC 보통주 발행주식수와 최신 가격으로 계산하며, 주식수 기준일과 가격 기준일이 다를 수 있습니다. "
            "컨센서스 PER/EPS와 외인소진율은 별도 소스를 연결하기 전까지 표시하지 않습니다."
        )

        st.markdown("<br>", unsafe_allow_html=True)

        # 10개 지표별 표시용 메타데이터 (scoring.py의 METRIC_KEYS와 정확히 일치)
        # 초보자 친화적 구조: title(한글 우선) / english / summary(한 줄 요약, 쉬운 말) /
        # why(왜 중요한지, 비유 포함) / rule_of_thumb(이 정도면 좋다는 감 잡는 기준선)
        METRIC_DISPLAY = {
            "revenue_growth": {
                "title": "1. 매출 성장률",
                "english": "Revenue Growth",
                "summary": "회사가 파는 물건·서비스가 작년보다 얼마나 더 팔렸는지 보여줘요.",
                "why": "매출이 꾸준히 늘어난다는 건 소비자들이 이 회사 제품·서비스를 계속 더 많이 찾는다는 뜻이에요. 특히 경기가 안 좋을 때도 매출을 지켜내는 회사는 그만큼 시장에서 입지가 탄탄하다고 볼 수 있어요.",
                "rule_of_thumb": "연 8% 이상이면 준수, 25% 이상이면 매우 우수한 성장세예요.",
            },
            "eps_growth": {
                "title": "2. 순이익 성장률 (EPS)",
                "english": "EPS Growth",
                "summary": "주식 1주당 회사가 벌어들인 돈이 작년보다 얼마나 늘었는지 보여줘요.",
                "why": "매출이 늘어도 비용이 더 늘면 실속이 없겠죠. 이 지표는 '진짜로 주주 몫이 얼마나 커졌는지'를 보여주는 핵심 숫자예요. 결국 주가는 이 순이익 성장을 뒤따라가는 경향이 있어요.",
                "rule_of_thumb": "연 6% 이상이면 양호, 20% 이상이면 매우 우수해요.",
            },
            "opm": {
                "title": "3. 영업이익률",
                "english": "OPM (Operating Profit Margin)",
                "summary": "물건을 팔아서 남긴 매출 중, 본업으로 실제 남긴 이익이 몇 %인지 보여줘요.",
                "why": "매출이 크더라도 남는 게 없으면 소용없죠. 영업이익률이 높다는 건 회사가 원가·비용을 잘 통제하며 돈을 벌고 있다는 뜻이에요. 금리가 오르거나 원자재값이 뛰어도 버틸 체력이 있다는 신호이기도 해요.",
                "rule_of_thumb": "10% 이상이면 양호, 20% 이상이면 매우 우수한 수익성이에요.",
            },
            "roic": {
                "title": "4. 투하자본이익률",
                "english": "ROIC (Return on Invested Capital)",
                "summary": "회사가 사업에 투입한 돈 대비 얼마나 효율적으로 이익을 냈는지 보여줘요.",
                "why": "빚을 잔뜩 내서 이익을 낸 회사와, 자기 돈으로 효율적으로 이익을 낸 회사는 질이 달라요. ROIC는 '빌린 돈 효과'를 걷어내고 진짜 사업 실력만 보여주는 지표라, 장기투자자들이 특히 중요하게 보는 숫자예요.",
                "rule_of_thumb": "7% 이상이면 양호, 15% 이상이면 매우 우수해요.",
            },
            "debt_rate": {
                "title": "5. 부채비율",
                "english": "Debt Rate",
                "summary": "회사가 자기 돈(자본) 대비 빚(부채)을 얼마나 지고 있는지 보여줘요.",
                "why": "빚이 너무 많으면 경기가 나빠지거나 금리가 오를 때 이자 갚기도 벅차서 회사가 휘청일 수 있어요. 하락장에서 살아남는 회사와 무너지는 회사를 가르는 대표적인 지표예요.",
                "rule_of_thumb": "낮을수록 좋아요. 100% 이하면 안전한 편, 40% 이하면 매우 우수해요.",
            },
            "quick_ratio": {
                "title": "6. 당좌비율",
                "english": "Quick Ratio",
                "summary": "당장 팔기 어려운 재고를 빼고도, 단기 빚을 갚을 현금성 자산이 충분한지 보여줘요.",
                "why": "재고자산은 급하게 현금화하기 어려울 수 있어요. 이 지표가 높을수록 갑자기 돈이 필요한 위기 상황에서도 회사가 버틸 체력이 있다는 뜻이에요.",
                "rule_of_thumb": "100% 이상이면 안전, 150% 이상이면 매우 우수해요.",
            },
            "interest_coverage": {
                "title": "7. 이자보상배율",
                "english": "Interest Coverage",
                "summary": "회사가 벌어들인 영업이익으로 이자를 몇 배나 감당할 수 있는지 보여줘요.",
                "why": "이 숫자가 1보다 작으면 번 돈으로 이자도 못 갚는다는 뜻이라 위험 신호예요. 숫자가 클수록 빚 부담에서 여유롭고 안전하다는 의미예요.",
                "rule_of_thumb": "5배 이상이면 양호, 15배 이상이면 매우 안전한 수준이에요.",
            },
            "ocf_ratio": {
                "title": "8. 영업현금흐름 비율",
                "english": "OCF Ratio",
                "summary": "장부상 이익이 아니라, 실제로 통장에 들어온 현금이 순이익 대비 얼마나 되는지 보여줘요.",
                "why": "회계상 이익은 있는데 실제 현금은 잘 안 들어오는 '이익의 질'이 낮은 회사들이 있어요. 이 비율이 100% 이상이면 장부상 이익만큼(또는 그 이상) 실제 현금도 잘 들어오고 있다는 뜻이라 신뢰도가 높아요.",
                "rule_of_thumb": "1.0(100%) 이상이면 양호, 1.3 이상이면 매우 우수해요.",
            },
            "sga_ratio": {
                "title": "9. 판관비율",
                "english": "SG&A Ratio",
                "summary": "매출 대비 광고비·인건비 등 판매관리비를 얼마나 쓰고 있는지 보여줘요.",
                "why": "비용을 효율적으로 관리하는 회사는 같은 매출로도 더 많은 이익을 남길 수 있어요. 이 비율이 낮을수록 비용 통제를 잘하고 있다는 뜻이에요.",
                "rule_of_thumb": "낮을수록 좋아요. 20% 이하면 양호, 12% 이하면 매우 우수해요.",
            },
            "downturn_defense": {
                "title": "10. 하락장 방어력",
                "english": "Downturn Defense",
                "summary": "코로나 폭락, 2022년 긴축장 같은 실제 하락장에서 이 종목이 코스피보다 덜 떨어졌는지 실측으로 보여줘요.",
                "why": "재무제표 숫자와 별개로 '진짜 위기 때 이 주식이 얼마나 안 흔들렸는지'를 과거 데이터로 직접 확인하는 지표예요. 하락장 방어라는 이 앱의 핵심 컨셉과 가장 직결된 지표예요.",
                "rule_of_thumb": "0%p 이상이면 코스피보다 덜 빠진 것(양호), 10%p 이상이면 매우 방어적이에요.",
            },
            "roa": {
                "title": "4-B. 총자산이익률 (금융업 전용)",
                "english": "ROA",
                "summary": "은행·보험 등 금융회사가 가진 전체 자산 대비 얼마나 효율적으로 이익을 냈는지 보여줘요.",
                "why": "금융회사는 예금·대출 구조가 일반 기업과 달라서, 이 앱은 ROIC 대신 이 지표로 금융업의 수익성을 평가해요.",
                "rule_of_thumb": "0.6% 이상이면 양호, 1.2% 이상이면 매우 우수해요.",
            },
        }
        METRIC_ORDER = list(METRIC_DISPLAY.keys())

        supabase_data = data.get("supabase_data")
        period_scores = (supabase_data or {}).get("period_scores") or {}

        if not period_scores:
            st.warning(
                "⚠️ 아직 이 종목의 펀더멘탈 스코어 데이터가 없습니다. "
                "collector.py로 이 종목을 먼저 수집해야 점수가 표시됩니다. "
                "(국내(KR) 종목만 DART 기반 스코어링을 지원합니다)"
            )
        else:
            # --- 종목 레벨(row) 상태 배지: 데이터 신뢰도 / 자본잠식 / 결측 지표 수 ---
            row_reliability = supabase_data.get("data_reliability")
            row_capital_impairment = supabase_data.get("capital_impairment")
            row_missing_count = supabase_data.get("missing_metric_count")
            row_wics_sector = supabase_data.get("wics_sector")
            # sector_percentile은 8개(기간x기준) 조합 전체가 아니라 "1년-평균" 기준으로만
            # 대표값 1개가 계산되는 설계(rescore_final_grades.py 참고)이므로,
            # 기간 탭 안이 아니라 여기 종목 레벨 배지 줄에서 한 번만 보여준다.
            row_sector_percentile = (
                (period_scores.get("1y") or {}).get("avg") or {}
            ).get("sector_percentile")
            row_per = supabase_data.get("per")
            row_pbr = supabase_data.get("pbr")
            row_per_tier = supabase_data.get("per_tier")
            row_pbr_tier = supabase_data.get("pbr_tier")

            status_pills_html = ""
            if row_reliability:
                rel_cls = {
                    "높음": "reliability-good", "양호": "reliability-good",
                    "보통": "reliability-mid",
                    "낮음": "reliability-low", "주의": "reliability-low",
                }.get(row_reliability, "reliability-mid")
                status_pills_html += f'<span class="status-pill {rel_cls}">📋 데이터 신뢰도: {row_reliability}</span>'
            if row_capital_impairment:
                status_pills_html += '<span class="status-pill impairment-warn">⚠️ 자본잠식 상태</span>'
            if row_wics_sector:
                status_pills_html += f'<span class="status-pill neutral">🏷️ 업종(WICS): {row_wics_sector}</span>'
            if row_sector_percentile is not None:
                status_pills_html += (
                    f'<span class="status-pill neutral">📊 업종 내 상위 '
                    f'{100 - row_sector_percentile:.1f}% (1년 평균 기준)</span>'
                )
            # PER/PBR 업종 내 상대적 저평가(A)/적정(B)/고평가(C) 배지 - rescore_valuation_tiers.py가 계산.
            # 저평가=A라고 해서 매수 신호는 아님(밸류 트랩 가능성 등) - 어디까지나 업종 내 상대적 위치일 뿐.
            # 음수(적자) PER은 크기 비교가 직관과 반대로 움직여서(적자가 클수록 PER 절댓값이
            # 작아짐) 업종 순위 계산에서 아예 제외됨 - 대신 실측값 그대로 "적자"로 표시.
            tier_cls_map = {"A": "tier-a", "B": "tier-b", "C": "tier-c"}
            tier_label_map = {"A": "저평가", "B": "적정", "C": "고평가"}
            per_is_negative = row_per is not None and row_per < 0
            pbr_is_negative = row_pbr is not None and row_pbr < 0

            if row_per is not None:
                if per_is_negative:
                    status_pills_html += (
                        f'<span class="status-pill tier-c" '
                        f'title="적자 상태입니다. PER은 업종 순위 비교에서 제외됩니다.">'
                        f'💰 PER {row_per} (적자)</span>'
                    )
                elif row_per_tier:
                    status_pills_html += (
                        f'<span class="status-pill {tier_cls_map.get(row_per_tier, "neutral")}">'
                        f'💰 PER {row_per} · 업종 내 {tier_label_map.get(row_per_tier, row_per_tier)}(Tier {row_per_tier})</span>'
                    )
                else:
                    status_pills_html += f'<span class="status-pill neutral">💰 PER {row_per}</span>'

            if row_pbr is not None:
                if pbr_is_negative:
                    status_pills_html += (
                        f'<span class="status-pill tier-c" '
                        f'title="자본잠식 상태입니다. PBR은 업종 순위 비교에서 제외됩니다.">'
                        f'🏦 PBR {row_pbr} (자본잠식)</span>'
                    )
                elif row_pbr_tier:
                    status_pills_html += (
                        f'<span class="status-pill {tier_cls_map.get(row_pbr_tier, "neutral")}">'
                        f'🏦 PBR {row_pbr} · 업종 내 {tier_label_map.get(row_pbr_tier, row_pbr_tier)}(Tier {row_pbr_tier})</span>'
                    )
                else:
                    status_pills_html += f'<span class="status-pill neutral">🏦 PBR {row_pbr}</span>'

            row_dividend_yield = supabase_data.get("dividend_yield")
            row_dividend_payout = supabase_data.get("dividend_payout_ratio")
            if row_dividend_yield is not None:
                payout_part = f" · 배당성향 {row_dividend_payout}%" if row_dividend_payout is not None else ""
                status_pills_html += (
                    f'<span class="status-pill neutral" '
                    f'title="하락장에서는 배당이 꾸준한 기업이 상대적으로 방어적인 경향이 있습니다.">'
                    f'💵 배당수익률 {row_dividend_yield}%{payout_part}</span>'
                )

            if row_missing_count is not None:
                status_pills_html += f'<span class="status-pill neutral">🧩 결측 지표: {row_missing_count}개</span>'

            if status_pills_html:
                st.markdown(f'<div class="row-status-bar">{status_pills_html}</div>', unsafe_allow_html=True)
                if row_per_tier or row_pbr_tier:
                    st.caption(
                        "ℹ️ PER/PBR Tier는 같은 업종(WICS) 내 상대적 위치일 뿐이며, "
                        "저평가(A)가 반드시 좋은 투자를 의미하지 않습니다 (실적 악화로 인한 "
                        "'밸류 트랩'일 수도 있음). 참고 정보로만 활용해주세요."
                    )
                if per_is_negative or pbr_is_negative:
                    st.caption(
                        "ℹ️ 적자/자본잠식 상태에서는 PER·PBR 값이 커도 작아도 크기 비교가 "
                        "직관과 반대로 움직여서(적자가 클수록 오히려 절댓값이 작아짐), "
                        "업종 내 순위(Tier) 계산에서 제외했습니다. 실측값 자체는 참고용으로 표시합니다."
                    )

            # 1/3/5/10년 기간 탭
            available_periods = [p for p in ["1y", "3y", "5y", "10y"] if p in period_scores]
            period_labels = {"1y": "📅 1년 (단기)", "3y": "📆 3년 (중기)", "5y": "🗓️ 5년 (중장기)", "10y": "📈 10년 (장기)"}

            period_tabs = st.tabs([period_labels[p] for p in available_periods])

            for period_key, tab in zip(available_periods, period_tabs):
                with tab:
                    pdata = period_scores[period_key]
                    years_used = pdata.get("years_used", [])
                    if len(years_used) == 1:
                        st.caption(f"기준 데이터: {years_used[0]}")
                    elif years_used:
                        st.caption(f"사용된 회계연도: {years_used[0]} ~ {years_used[-1]}")

                    view_mode = st.radio(
                        "채점 기준",
                        options=["avg", "worst"],
                        format_func=lambda v: "📊 평균 기준 (꾸준함)" if v == "avg" else "🛡️ 최악 기준 (위기 대응력)",
                        horizontal=True,
                        key=f"view_mode_{period_key}",
                        help=(
                            "**평균 기준**: 선택한 기간(예: 3년) 동안 각 지표의 연도별 값을 평균 내서 "
                            "채점합니다 - 꾸준한 실적을 잘 반영합니다.\n\n"
                            "**최악 기준**: 같은 기간 동안 각 지표가 가장 나빴던 해의 값으로 채점합니다 - "
                            "위기 상황에서 얼마나 잘 버티는지(하방 방어력)를 보여줍니다. 평균보다 항상 "
                            "같거나 낮은 점수가 나옵니다."
                        ),
                    )

                    view_data = pdata.get(view_mode) or {}
                    total_score = view_data.get("total_score")
                    grade = view_data.get("grade", "N/A")
                    metric_scores = view_data.get("metric_scores", {})
                    sub_scores = view_data.get("sub_scores") or {}
                    financial_adjusted = view_data.get("financial_adjusted")
                    period_missing_count = view_data.get("missing_metric_count")

                    sub_badges = ""
                    if sub_scores:
                        growth_v = sub_scores.get("growth")
                        defense_v = sub_scores.get("defense")
                        if growth_v is not None:
                            growth_tip = (
                                "매출액 성장률 + EPS 성장률, 두 지표의 가중점수 합산 "
                                "(전체 100점 중 성장성에 배정된 배점)"
                            )
                            sub_badges += (
                                f'<span class="mini-stat-badge" title="{growth_tip}">'
                                f'🌱 성장 서브스코어 {growth_v}</span>'
                            )
                        if defense_v is not None:
                            defense_tip = (
                                "성장성 2개 지표를 제외한 나머지 8개 지표(수익성/재무건전성/현금흐름/"
                                "하락장 방어력)의 가중점수 합산"
                            )
                            sub_badges += (
                                f'<span class="mini-stat-badge" title="{defense_tip}">'
                                f'🛡️ 방어 서브스코어 {defense_v}</span>'
                            )

                    if financial_adjusted:
                        sub_badges += (
                            '<span class="mini-stat-badge" title="금융업(은행/보험/증권)은 매출액/영업이익 '
                            '개념이 일반기업과 달라 OPM/ROIC/SG&A비율 3개 지표를 제외하고, 대신 ROA(총자산이익률)로 '
                            '대체 채점한 뒤 100점 만점으로 환산했습니다.">🏦 금융업 보정 적용</span>'
                        )

                    growth_excluded_keys = [
                        k for k in ("revenue_growth", "eps_growth")
                        if (metric_scores.get(k) or {}).get("excluded_from_total")
                    ]

                    if growth_excluded_keys:
                        if len(growth_excluded_keys) == 2:
                            growth_note = (
                                "매출·EPS 성장률 수치가 급변하여 전년 대비 비교가 불가능해 두 지표를 "
                                "제외하고, 나머지 8개 지표 기준으로 재환산한 점수입니다."
                            )
                        else:
                            label = "매출" if growth_excluded_keys[0] == "revenue_growth" else "EPS"
                            growth_note = (
                                f"{label} 성장률 수치가 급변하여 전년 대비 비교가 불가능해 이 지표를 "
                                f"제외하고, 나머지 9개 지표 기준으로 재환산한 점수입니다."
                            )
                        sub_badges += (
                            f'<span class="mini-stat-badge" title="{growth_note}">⚡ 급변 보정 적용</span>'
                        )

                    if period_missing_count is not None:
                        sub_badges += (
                            f'<span class="mini-stat-badge" title="DART 공시 데이터에서 값을 찾지 못해 '
                            f'0점 처리된 지표 수입니다.">🧩 결측 {period_missing_count}개</span>'
                        )

                    st.markdown(
                        f"""
                        <div class="grade-hero-box">
                            <div>
                                <div style="font-size:14px; color:#92400E; font-weight:700;">
                                    {period_labels[period_key]} · {'평균' if view_mode == 'avg' else '최악(위기)'} 기준 종합 점수
                                </div>
                                <div class="grade-hero-score">{total_score if total_score is not None else 'N/A'} / 100</div>
                            </div>
                            <div class="grade-hero-badge">{grade}</div>
                            <div class="grade-hero-sub">{sub_badges}</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        "※ 지표마다 총점에서 차지하는 배점이 달라요 — 하락장 방어력 20점, "
                        "매출·EPS 성장률 각 5점, 나머지 7개 지표는 각 10점. 아래 각 지표의 "
                        "'총점 기여'가 그 지표의 실제 배점 대비 획득 점수예요."
                    )

                    # 지표별 표시 단위 (차트 y축 라벨용)
                    METRIC_UNITS = {
                        "interest_coverage": "배", "ocf_ratio": "배", "downturn_defense": "%p",
                    }

                    # 분기 라벨('2025 3분기보고서' 등)에서 (연도, 분기순번)과 'N분기' 표기를 추출.
                    # jsonb는 딕셔너리 키 삽입 순서를 보장 안 하므로, 저장 순서에 의존하지 않고
                    # 여기서 직접 정렬한다. 사업보고서(연간)는 4분기로 통일 표기.
                    _QUARTER_RANK = {"1분기": 1, "반기": 2, "3분기": 3, "사업보고서": 4}

                    def _parse_quarter_label(label):
                        parts = label.split(" ", 1)
                        if len(parts) != 2:
                            return (0, 0), label
                        year_str, report_part = parts
                        try:
                            year = int(year_str)
                        except ValueError:
                            return (0, 0), label
                        for key, rank in _QUARTER_RANK.items():
                            if key in report_part:
                                return (year, rank), f"{year} {rank}분기"
                        return (year, 0), label

                    def _metric_within_period(metric_key_inner):
                        """현재 보고 있는 기간 탭(period_key) 안에서 이 지표의 세부 추이를 반환.
                        3/5/10년 탭 -> 연도별(예: 2023,2024,2025), 1년 탭 -> 최근 4분기별.
                        jsonb 키 순서가 보장 안 되므로 여기서 명시적으로 시간순 정렬한다."""
                        breakdown = (period_scores.get(period_key, {}) or {}).get("yearly_breakdown", {}) or {}
                        metric_breakdown = breakdown.get(metric_key_inner, {})
                        items = [(label, value) for label, value in metric_breakdown.items() if value is not None]

                        if period_key == "1y":
                            parsed = [(_parse_quarter_label(label), value) for label, value in items]
                            parsed.sort(key=lambda x: x[0][0])
                            return [(x[1], value) for x, value in parsed]
                        else:
                            # 3/5/10y는 연도 문자열 키 -> 숫자로 정렬
                            items.sort(key=lambda x: int(x[0]))
                            return items

                    # collector.py의 leverage_exempt 판정(금융/지주회사/유틸리티는 부채비율 등
                    # 3개 지표 자동 만점)을 저장된 필드로 재구성 - app.py는 DART/WICS 원본 로직에
                    # 접근 못 하므로 supabase에 저장된 값 기준으로 근사
                    leverage_exempt = (
                        row_wics_sector == "금융"
                        or bool(supabase_data.get("holding_company"))
                        or row_wics_sector == "유틸리티"
                    )


                    for metric_key in METRIC_ORDER:
                        entry = metric_scores.get(metric_key)
                        if entry is None:
                            # roa는 금융업이 아닌 경우 metric_scores에 아예 없으므로 스킵
                            continue

                        meta = METRIC_DISPLAY[metric_key]
                        title = meta["title"]
                        value = entry.get("value")
                        score = entry.get("score")
                        excluded = entry.get("excluded_from_total", False)

                        # 값 포맷팅 조정 (성장률이나 비율 지표는 뒤에 % 또는 %p 추가)
                        if value is not None:
                            if metric_key in ["revenue_growth", "eps_growth", "opm", "roic", "roa", "debt_rate", "quick_ratio", "sga_ratio"]:
                                value_display = f"{value}%"
                            elif metric_key == "downturn_defense":
                                value_display = f"{value}%p"
                            else:
                                value_display = f"{value}"
                        elif metric_key in ("revenue_growth", "eps_growth") and entry.get("raw_value") is not None:
                            # 채점용 value는 가드에 걸려 None이지만, 실제 계산된 원본값(raw_value)은
                            # 항상 보여준다 - "N/A"로 감추지 않는 게 최우선 요구사항
                            raw_v = entry["raw_value"]
                            value_display = f"{raw_v:+.2f}% (실측)"
                        else:
                            value_display = "N/A"

                        # revenue_growth/eps_growth가 raw_value로 표시된 경우, 왜 점수 계산에선
                        # 제외됐는지 안내 (구버전 데이터 - sanitize_growth가 값을 null 처리하던
                        # 시절의 잔여 케이스). 새로 재수집된 종목은 이제 값이 null 처리되지 않고
                        # 그대로 채점되며, 대신 아래 is_extreme 플래그로 "이례적 수치" 안내만 붙는다.
                        growth_guard_note = ""
                        if metric_key in ("revenue_growth", "eps_growth") and value is None and entry.get("raw_value") is not None:
                            growth_guard_note = (
                                "<br><span style='font-size:12px; color:#92400E;'>"
                                "ℹ️ 위 실측값은 전년 동기 대비 실제 계산된 증가율입니다. 다만 전년 "
                                "기저값이 너무 작아(또는 흑자전환 등) 왜곡 가능성이 높아 점수 계산에는 "
                                "반영하지 않았습니다 (점수 0점 처리).</span>"
                            )
                        elif value is None and metric_key in ("revenue_growth", "eps_growth"):
                            growth_guard_note = (
                                "<br><span style='font-size:12px; color:#92400E;'>"
                                "ℹ️ 전년 동기 데이터 자체가 없어 증가율을 계산할 수 없습니다."
                                "</span>"
                            )
                        elif metric_key in ("revenue_growth", "eps_growth") and entry.get("is_extreme"):
                            # 500% 초과 등 이례적으로 큰(혹은 작은) 수치 - 점수 자체는 정상적으로
                            # 반영됨(구간표가 이미 상/하한을 캡 처리), 참고용 안내만 표시
                            growth_guard_note = (
                                "<br><span style='font-size:12px; color:#92400E;'>"
                                "ℹ️ 전년 동기 대비 변동폭이 매우 커서(기저효과 등) 수치가 이례적으로 "
                                "크게 나왔습니다. 점수에는 정상 반영되었습니다.</span>"
                            )
                        # 이자비용을 못 찾아 금융비용(포괄 비용)으로 근사 계산된 경우 안내
                        if metric_key == "interest_coverage" and entry.get("is_approximate"):
                            growth_guard_note = (
                                "<br><span style='font-size:12px; color:#92400E;'>"
                                "ℹ️ 순수 이자비용 계정을 찾지 못해 금융비용(환차손 등 포함) 기준 "
                                "근사치로 계산된 값입니다. 실제보다 다소 보수적으로 잡혔을 수 있습니다."
                                "</span>"
                            )
                        elif metric_key == "quick_ratio" and entry.get("is_extreme"):
                            growth_guard_note = (
                                "<br><span style='font-size:12px; color:#92400E;'>"
                                "ℹ️ Quick Ratio가 20배 이상인 경우, 유동부채가 극히 작거나 0에 가까운 "
                                "기업에서는 실제 계산값 자체가 매우 커질 수 있습니다. "
                                "분모 규모를 함께 확인해 해석하세요."
                                "</span>"
                            )

                        if excluded:
                            if metric_key in ("revenue_growth", "eps_growth"):
                                 score_display = "기저효과로 제외"
                            else:
                                 score_display = "업종 특성상 제외"
                            score_emoji = "⚪"
                        elif score is not None:
                            score_display = f"{score}/10"
                            score_emoji = "🟢" if score >= 8 else ("🟡" if score >= 5 else "🔴")
                        else:
                            score_display = "N/A"
                            score_emoji = "⚪"

                        # 지표별 배점(가중치)이 서로 달라서(하락장 방어력 20점, 성장률 2개 각 5점,
                        # 나머지 각 10점) 원점수(X/10)만 보면 총점 기여도를 오해하기 쉬움 - 그래서
                        # "총점 기여 N.N/배점" 형태로 실제 100점 만점 중 얼마를 받았는지 같이 표시
                        metric_weight = ROA_WEIGHT if metric_key == "roa" else METRIC_WEIGHTS.get(metric_key, 10)
                        weighted_score_val = entry.get("weighted_score")
                        if excluded:
                            contribution_display = "총점 제외"
                        elif weighted_score_val is not None:
                            contribution_display = f"총점 기여 {weighted_score_val:.1f}/{metric_weight}점"
                        else:
                            contribution_display = f"총점 기여 -/{metric_weight}점"

                        expander_label = f"{title}   |   실측값 {value_display}   |   {score_emoji} {score_display}   |   {contribution_display}"

                        with st.expander(expander_label):
                            st.caption(meta["english"])
                            st.markdown(f"**{meta['summary']}**")
                            st.markdown(
                                f"💡 **왜 중요할까요?**<br>{meta['why']}{growth_guard_note}",
                                unsafe_allow_html=True,
                            )
                            st.markdown(f"📊 **기준선**: {meta['rule_of_thumb']}")
                            st.markdown("---")


                            # 정확한 채점 구간표는 비공개(경쟁 우위 보호) - 대신 업종 내 상대적
                            # 우위 백분위만 표시. rescore_metric_percentiles.py가 미리 계산해둔
                            # metric_scores[key]["sector_percentile"] (1년 평균 기준 대표값)을 사용.
                            metric_sector_entry = (
                                (period_scores.get("1y", {}).get("avg") or {})
                                .get("metric_scores", {})
                                .get(metric_key, {})
                            )
                            metric_sector_pct = metric_sector_entry.get("sector_percentile")
                            if metric_sector_pct is not None:
                                st.markdown(
                                    f"**업종 내 상대적 위치**: 이 지표에서 같은 업종({row_wics_sector or '미상'}) "
                                    f"내 상위 **{100 - metric_sector_pct:.1f}%** 입니다. (1년 평균 기준)"
                                )
                                st.progress(metric_sector_pct / 100.0)
                                if metric_sector_entry.get("sector_percentile_basis") == "raw_value":
                                    st.caption(
                                        "ℹ️ 점수 계산에는 제외된 실측값(위 안내 참고) 기준으로 "
                                        "순위만 참고용으로 매긴 것입니다."
                                    )
                            else:
                                st.caption("업종 내 비교 데이터가 아직 계산되지 않았습니다.")
                            if leverage_exempt and metric_key in ("debt_rate", "quick_ratio", "interest_coverage"):
                                st.caption("ℹ️ 이 종목은 레버리지 예외 업종이라 이 지표는 자동 만점(10점) 처리됩니다.")

                            history = _metric_within_period(metric_key)
                            if len(history) >= 2:
                                period_chart_title = {
                                    "1y": "**최근 4분기 추이**", "3y": "**연도별 추이 (3년)**",
                                    "5y": "**연도별 추이 (5년)**", "10y": "**연도별 추이 (10년)**",
                                }.get(period_key, "**기간별 추이**")
                                st.markdown(period_chart_title)
                                unit_label = METRIC_UNITS.get(metric_key, "%")
                                x_labels = [h[0] for h in history]
                                trend_df = pd.DataFrame({"기간": x_labels, "실측값": [h[1] for h in history]})
                                chart_type = st.radio(
                                    "차트 유형",
                                    ["선", "막대"],
                                    horizontal=True,
                                    key=f"charttype_{period_key}_{view_mode}_{metric_key}",
                                    label_visibility="collapsed",
                                )
                                base = alt.Chart(trend_df).encode(
                                    x=alt.X("기간:N", sort=x_labels, title="기간",
                                            axis=alt.Axis(labelAngle=0)),
                                    y=alt.Y(
                                        "실측값:Q", title=f"실측값 ({unit_label})",
                                        scale=alt.Scale(nice=True, zero=True),  # 0을 항상 눈금에 포함
                                        axis=alt.Axis(titleAngle=0, titleAlign="left", titleY=-10, titleX=0),
                                    ),
                                    tooltip=["기간", "실측값"],
                                )
                                # 막대그래프는 양수/음수 색을 다르게 (양수=주황, 음수=빨강)
                                sign_color = alt.condition(
                                    alt.datum.실측값 >= 0, alt.value(THEME["positive"]), alt.value(THEME["negative"])
                                )
                                if chart_type == "선":
                                    chart = base.mark_line(point=True, color=THEME["accent_strong"])
                                else:
                                    chart = base.mark_bar().encode(color=sign_color)
                                chart = chart.properties(height=150)
                                st.altair_chart(chart, use_container_width=True)
                            else:
                                st.caption("추이를 그리기엔 사용 가능한 기간 데이터가 부족합니다.")

                            # 급변 감지: 1y 점수가 3y(없으면 5y/10y) 평균 점수 대비 3점 이상 벌어지면 플래그
                            st.markdown("**급변 감지**")
                            one_y_entry = (period_scores.get("1y", {}).get(view_mode) or {}).get("metric_scores", {}).get(metric_key)
                            baseline_period = next((p for p in ("3y", "5y", "10y") if period_scores.get(p)), None)
                            baseline_entry = None
                            if baseline_period:
                                baseline_entry = (period_scores.get(baseline_period, {}).get(view_mode) or {}).get("metric_scores", {}).get(metric_key)

                            if one_y_entry and baseline_entry and one_y_entry.get("score") is not None and baseline_entry.get("score") is not None:
                                score_gap = one_y_entry["score"] - baseline_entry["score"]
                                if abs(score_gap) >= 3:
                                    direction = "개선" if score_gap > 0 else "악화"
                                    st.warning(
                                        f"⚡ 최근 1년 점수({one_y_entry['score']}/10)가 {baseline_period} 평균"
                                        f"({baseline_entry['score']}/10) 대비 급격히 {direction}됐습니다 "
                                        f"(점수차 {abs(score_gap)}점). 일시적 요인인지 추세 전환인지 다른 지표와 "
                                        f"함께 확인해보세요."
                                    )
                                else:
                                    st.caption(f"✅ {baseline_period} 평균 대비 특이 변동 없음 (점수차 {abs(score_gap)}점)")
                            else:
                                st.caption("비교할 기준 기간 데이터가 부족해 급변 여부를 판단할 수 없습니다.")

    with right_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)
