import json
import random
import re
import FinanceDataReader as fdr
import pandas as pd
import altair as alt
import streamlit as st
from supabase import create_client
import yfinance as yf
import base64
import calendar as pycalendar
from datetime import datetime, date, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from urllib.parse import quote, urlencode, urlparse
from zoneinfo import ZoneInfo
import hashlib

from search_aliases import aliases_for

from scoring import METRIC_WEIGHTS, ROA_WEIGHT  # 지표별 가중치 - "총점 기여도" 표시에 사용 (scoring.py가 단일 소스)
from us_scoring import PROFILE_DESCRIPTIONS, PROFILE_LABELS
from historical_pattern import analyze_all_indicator_patterns
from market_overview import fetch_market_overview, load_market_overview
from news_earnings import (
    NaverNewsItem,
    fetch_dart_disclosures,
    fetch_macro_news,
    fetch_stock_news,
    build_earnings_events,
    _sort_news_latest_first,
)
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
# 원문 대표 이미지가 없을 때 기사별 고해상도 AI 편집 일러스트를 생성한다.
# 정적 저해상도 fallback은 사용하지 않는다.

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
        gap: 14px;
        margin-bottom: 16px;
    }
    .live-news-card-link {
        display: block;
        color: inherit !important;
        text-decoration: none !important;
    }
    .live-news-card-link:hover,
    .live-news-card-link:focus,
    .live-news-card-link:visited {
        color: inherit !important;
        text-decoration: none !important;
    }
    .live-news-ai-badge {
        position: absolute;
        top: 8px;
        right: 8px;
        z-index: 2;
        padding: 3px 7px;
        border-radius: 999px;
        background: rgba(17, 24, 39, 0.78);
        color: #FFFFFF !important;
        font-size: 9px;
        font-weight: 800;
        letter-spacing: .1px;
        backdrop-filter: blur(3px);
    }
    .news-reader-wrap {
        max-width: 980px;
        margin: 0 auto;
    }
    .news-reader-kicker {
        font-size: 11px;
        font-weight: 800;
        letter-spacing: .2px;
        margin-bottom: 8px;
    }
    .news-reader-title {
        font-size: 32px;
        line-height: 1.3;
        font-weight: 900;
        margin: 0 0 12px;
    }
    .news-reader-meta {
        font-size: 12px;
        line-height: 1.5;
        margin-bottom: 18px;
    }
    .news-reader-image {
        width: 100%;
        max-height: 520px;
        object-fit: cover;
        border-radius: 16px;
        display: block;
        margin: 0 0 18px;
        border: 1px solid #E5E7EB;
    }
    .news-reader-body {
        font-size: 15px;
        line-height: 1.85;
        white-space: pre-wrap;
        margin: 0 0 18px;
    }
    .news-reader-body + .news-reader-body {
        margin-top: -6px;
    }
    .news-reader-ai-article {
        font-size: 16px;
        line-height: 1.95;
        white-space: pre-wrap;
        margin: 26px 0 0;
    }
    .news-reader-ai-article::first-line {
        font-weight: 500;
    }
    .news-reader-disclosure {
        font-size: 11px;
        line-height: 1.65;
        margin: 28px 0 8px;
    }
    .news-reader-facts {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
        margin: 18px 0;
    }
    .news-reader-fact {
        padding: 12px 14px;
        border: 1px solid #E5E7EB;
        border-radius: 12px;
        background: #FFFFFF;
    }
    .news-reader-fact-label {
        font-size: 10px;
        font-weight: 800;
        margin-bottom: 5px;
    }
    .news-reader-fact-value {
        font-size: 13px;
        line-height: 1.55;
        font-weight: 700;
    }
    @media (max-width: 800px) {
        .news-reader-facts {
            grid-template-columns: 1fr;
        }
    }
    .news-reader-source-link {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        font-size: 12px;
        font-weight: 800;
        text-decoration: none !important;
        border-bottom: 1px solid currentColor;
        padding-bottom: 2px;
    }
    .news-reader-source-link:hover {
        opacity: .78;
    }
    .live-news-card {
        background: #FFFFFF;
        border: 1px solid #E5E7EB;
        border-radius: 12px;
        overflow: hidden;
        min-width: 0;
        min-height: 0;
        box-shadow: 0 2px 8px rgba(15, 23, 42, 0.045);
        transition: border-color .15s ease, box-shadow .15s ease, transform .15s ease;
        box-sizing: border-box;
    }
    .live-news-card:hover {
        border-color: #D1D5DB;
        box-shadow: 0 7px 18px rgba(15, 23, 42, 0.08);
        transform: translateY(-2px);
    }
    .live-news-image-wrap {
        position: relative;
        width: 100%;
        height: 148px;
        overflow: hidden;
        background: #F3F4F6;
    }
    .live-news-image {
        display: block;
        width: 100%;
        height: 100%;
        object-fit: cover;
    }
    .live-news-image-fallback {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 5px;
        background: linear-gradient(135deg, #FFF7ED 0%, #F8FAFC 100%);
        color: #D97706 !important;
    }
    .live-news-image-fallback span {
        font-size: 30px;
        line-height: 1;
    }
    .live-news-image-fallback small {
        color: #6B7280 !important;
        font-size: 10px;
        font-weight: 700;
        max-width: 85%;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .live-news-image-wrap.image-failed {
        display: flex;
        align-items: center;
        justify-content: center;
        background: #F8FAFC;
    }
    .live-news-image-wrap.image-failed::after {
        content: "📰";
        font-size: 28px;
    }
    .live-news-image-wrap.image-failed img {
        display: none;
    }
    .live-news-card-body {
        padding: 12px 14px 13px;
    }
    .live-news-meta {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        margin-bottom: 7px;
        color: #6B7280 !important;
        font-size: 10px;
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
    @media (max-width: 1200px) {
        .live-news-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 700px) {
        .live-news-grid { grid-template-columns: 1fr; }
        .live-news-image-wrap { height: 170px; }
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
    .earnings-company-ko {
        color: #6B7280 !important;
        font-size: 0.82em;
        font-weight: 650;
        margin-left: 4px;
        white-space: nowrap;
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
    [class*="st-key-earnings_cell_"] button {
        width: 100% !important;
        min-height: 28px !important;
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
    [class*="st-key-earnings_cell_"] button:hover {
        background: #FFF7ED !important;
        color: #D97706 !important;
    }
    [class*="st-key-earnings_cell_selected_"] {
        border: 2px solid #F4A261 !important;
        background: #FFFDF9 !important;
    }
    .earnings-calendar-empty {
        color: #D1D5DB !important;
        font-size: 12px;
    }
    .earnings-calendar-company-ko {
        font-size: 0.86em;
        font-weight: 650;
        color: #6B7280 !important;
        margin-left: 2px;
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
    .earnings-detail-compare {        margin-top: 9px;
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
        .indicator-card,
        .live-news-card,
        .news-empty-state,
        .earnings-row,
        .earnings-calendar-shell,
        .earnings-calendar-cell,
        .earnings-detail-card,
        .earnings-summary-card {
            background-color: __THEME_SURFACE__ !important;
            color: __THEME_TEXT__ !important;
            border-color: __THEME_BORDER__ !important;
        }

        .live-news-card *,
        .news-empty-state *,
        .earnings-row *,
        .earnings-calendar-shell *,
        .earnings-calendar-cell *,
        .earnings-detail-card *,
        .earnings-summary-card * {
            color: __THEME_TEXT__ !important;
        }

        .live-news-meta,
        .live-news-meta *,
        .live-news-desc,
        .live-news-footer,
        .earnings-report,
        .earnings-note,
        .earnings-calendar-sub,
        .earnings-calendar-week,
        .earnings-calendar-more,
        .earnings-detail-meta,
        .earnings-company-ko,
        .earnings-calendar-company-ko,
        .earnings-compare,
        .earnings-detail-compare {
            color: __THEME_TEXT_MUTED__ !important;
        }

        .live-news-title,
        .earnings-name,
        .earnings-date,
        .earnings-upcoming-title,
        .earnings-calendar-month,
        .earnings-calendar-day,
        .earnings-detail-name,
        .earnings-summary-value {
            color: __THEME_TEXT__ !important;
        }

        .earnings-calendar-event,
        .earnings-calendar-event.us {
            background-color: __THEME_SURFACE_MUTED__ !important;
            color: __THEME_TEXT__ !important;
        }

        .earnings-calendar-event.kr,
        .earnings-consensus-chip,
        .earnings-primary {
            background-color: __THEME_WARNING_BG__ !important;
            color: __THEME_WARNING_TEXT__ !important;
            border-color: __THEME_ACCENT__ !important;
        }

        .earnings-secondary {
            background-color: __THEME_SURFACE_MUTED__ !important;
            color: __THEME_TEXT_MUTED__ !important;
            border-color: __THEME_BORDER__ !important;
        }

        [class*="st-key-earnings_cell_"] button {
            color: __THEME_TEXT__ !important;
        }

        [class*="st-key-earnings_cell_"] button:hover {
            background-color: __THEME_SURFACE_WARM__ !important;
            color: __THEME_ACCENT_STRONG__ !important;
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
        /* ===== Live News / Earnings dark-mode v2 =====
           새 기능 영역의 텍스트/배경/링크/버튼을 명시적으로 분리해
           Streamlit 기본 테마와 전역 CSS의 충돌을 막는다. */
        .live-news-section-title,
        .live-news-section-subtitle,
        .live-news-source,
        .live-news-category,
        .live-news-title,
        .live-news-desc,
        .live-news-footer,
        .news-empty-state,
        .earnings-name,
        .earnings-company-ko,
        .earnings-report,
        .earnings-date,
        .earnings-compare,
        .earnings-upcoming-title,
        .earnings-note,
        .earnings-calendar-month,
        .earnings-calendar-sub,
        .earnings-calendar-week,
        .earnings-calendar-day,
        .earnings-calendar-empty,
        .earnings-calendar-more,
        .earnings-calendar-company-ko,
        .earnings-detail-name,
        .earnings-detail-meta,
        .earnings-summary-label,
        .earnings-summary-value,
        .earnings-page-title,
        .earnings-page-subtitle,
        .earnings-detail-compare {
            color: __THEME_TEXT__ !important;
            -webkit-text-fill-color: __THEME_TEXT__ !important;
        }

        .live-news-section-subtitle,
        .live-news-source,
        .live-news-category,
        .live-news-desc,
        .live-news-footer,
        .earnings-company-ko,
        .earnings-report,
        .earnings-compare,
        .earnings-note,
        .earnings-calendar-sub,
        .earnings-calendar-week,
        .earnings-calendar-empty,
        .earnings-calendar-more,
        .earnings-detail-meta,
        .earnings-summary-label,
        .earnings-page-subtitle,
        .earnings-detail-compare {
            color: __THEME_TEXT_MUTED__ !important;
            -webkit-text-fill-color: __THEME_TEXT_MUTED__ !important;
        }

        .live-news-card,
        .news-empty-state,
        .earnings-row,
        .earnings-calendar-shell,
        .earnings-calendar-cell,
        .earnings-detail-card,
        .earnings-summary-card {
            background: __THEME_SURFACE__ !important;
            color: __THEME_TEXT__ !important;
            border-color: __THEME_BORDER__ !important;
        }

        .live-news-card:hover {
            background: __THEME_SURFACE__ !important;
            border-color: __THEME_BORDER__ !important;
        }

        .live-news-title,
        .live-news-title:visited,
        .live-news-title:hover,
        .live-news-title:active {
            color: __THEME_TEXT__ !important;
            -webkit-text-fill-color: __THEME_TEXT__ !important;
        }

        .live-news-footer,
        .earnings-report,
        .earnings-note,
        .earnings-calendar-sub,
        .earnings-detail-meta {
            background: transparent !important;
        }

        .earnings-calendar-event,
        .earnings-calendar-event.us {
            background: __THEME_SURFACE_MUTED__ !important;
            border-left-color: __THEME_BORDER__ !important;
            color: __THEME_TEXT__ !important;
            -webkit-text-fill-color: __THEME_TEXT__ !important;
        }

        .earnings-calendar-event.kr,
        .earnings-consensus-chip,
        .earnings-primary {
            background: __THEME_WARNING_BG__ !important;
            border-color: __THEME_ACCENT__ !important;
            color: __THEME_WARNING_TEXT__ !important;
            -webkit-text-fill-color: __THEME_WARNING_TEXT__ !important;
        }

        .earnings-consensus-chip strong,
        .earnings-primary,
        .earnings-secondary {
            color: __THEME_WARNING_TEXT__ !important;
            -webkit-text-fill-color: __THEME_WARNING_TEXT__ !important;
        }

        .earnings-secondary {
            background: __THEME_SURFACE_MUTED__ !important;
            border-color: __THEME_BORDER__ !important;
            color: __THEME_TEXT_MUTED__ !important;
            -webkit-text-fill-color: __THEME_TEXT_MUTED__ !important;
        }

        .earnings-open-calendar {
            background: __THEME_SURFACE_WARM__ !important;
            border-color: __THEME_ACCENT__ !important;
            color: __THEME_ACCENT_STRONG__ !important;
            -webkit-text-fill-color: __THEME_ACCENT_STRONG__ !important;
        }

        .earnings-open-calendar:hover {
            background: __THEME_ACCENT__ !important;
            color: #FFFFFF !important;
            -webkit-text-fill-color: #FFFFFF !important;
        }

        .earnings-detail-market.us {
            background: __THEME_SURFACE_MUTED__ !important;
            color: __THEME_TEXT_MUTED__ !important;
            -webkit-text-fill-color: __THEME_TEXT_MUTED__ !important;
        }

        .earnings-detail-market.kr {
            background: __THEME_WARNING_BG__ !important;
            color: __THEME_WARNING_TEXT__ !important;
            -webkit-text-fill-color: __THEME_WARNING_TEXT__ !important;
        }

        .earnings-date a,
        .earnings-date a:visited,
        .earnings-date a:hover,
        .earnings-date a:active {
            color: __THEME_ACCENT_STRONG__ !important;
            -webkit-text-fill-color: __THEME_ACCENT_STRONG__ !important;
        }

        [class*="st-key-earnings_cell_"] button,
        [class*="st-key-earnings_cell_"] button p,
        [class*="st-key-earnings_cell_"] button span {
            background: transparent !important;
            color: __THEME_TEXT__ !important;
            -webkit-text-fill-color: __THEME_TEXT__ !important;
        }

        [class*="st-key-earnings_cell_"] button:hover,
        [class*="st-key-earnings_cell_"] button:focus {
            background: __THEME_SURFACE_WARM__ !important;
            color: __THEME_ACCENT_STRONG__ !important;
            -webkit-text-fill-color: __THEME_ACCENT_STRONG__ !important;
        }

        [class*="st-key-earnings_cell_selected_"] {
            background: __THEME_SURFACE_WARM__ !important;
            border-color: __THEME_ACCENT__ !important;
        }

        /* Streamlit의 전역 button CSS가 HTML 카드 내부의 명시적 스타일보다
           우선되는 경우를 막는다. */
        .earnings-calendar-shell a,
        .earnings-calendar-shell span,
        .earnings-calendar-shell strong,
        .earnings-calendar-shell div,
        .earnings-detail-card a,
        .earnings-detail-card span,
        .earnings-detail-card strong,
        .earnings-detail-card div,
        .earnings-row a,
        .earnings-row span,
        .earnings-row strong,
        .earnings-row div,
        .live-news-card a,
        .live-news-card span,
        .live-news-card strong,
        .live-news-card div {
            -webkit-text-fill-color: currentColor;
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
    except Exception:        us_stocks = [
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
        # 뉴스는 점수 로직 최하단으로 이동

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

    render_home_stock_news(company_name, code, limit=3)

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
    st.iframe(custom_html, height=height + 50)


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
                transition: background 0.15s;                text-decoration: none !important;
                color: inherit !important;
            }}
            .stock-row:hover, .stock-row.active {{
                background-color: {THEME['surface_warm']};
            }}
            .stock-info {{
                display: flex;
                align-items: center;
                gap: 10px;
                overflow: hidden;
            }}
            .flag {{ font-size: 16px; }}
            .ticker {{
                font-weight: 800;
                color: {THEME['text']} !important;
                -webkit-text-fill-color: {THEME['text']} !important;
                font-size: 14px;
                min-width: 65px;
            }}
            .name {{
                font-size: 13px;
                color: {THEME['text_secondary']} !important;
                -webkit-text-fill-color: {THEME['text_secondary']} !important;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .exch {{
                font-size: 11px;
                color: {THEME['text_muted']} !important;
                -webkit-text-fill-color: {THEME['text_muted']} !important;
                white-space: nowrap;
            }}
            .highlight {{
                color: {THEME['accent_strong']} !important;
                -webkit-text-fill-color: {THEME['accent_strong']} !important;
                font-weight: 800;
                background-color: {THEME['warning_bg']};
                padding: 0 2px;
                border-radius: 2px;
            }}

            .right-pane {{
                flex: 35;
                background-color: {THEME['surface_muted']};
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
            const STOCKS = Array.isArray({json_db}) ? {json_db} : [];
            const inputEl = document.getElementById('unified_search_input');
            const modalEl = document.getElementById('unified_search_modal');
            const listEl = document.getElementById('unified_search_list');
            const footerQueryEl = document.getElementById('unified_search_footer_query');

            function normalizeSearchText(value) {{
                return String(value ?? '')
                    .normalize('NFKC')
                    .toLowerCase()
                    .replace(/\\s+/g, '')
                    .replace(/[._\/'’(),&-]+/g, '');
            }}

            // normalizeSearchText 정의 이후에 검색 인덱스를 생성해야 합니다.
            // JS의 TDZ(Temporal Dead Zone)로 인해 초기화 전에 호출되면 검색 전체가 중단됩니다.
            const SEARCH_INDEX = STOCKS.map(item => ({{
                item: item,
                ticker: normalizeSearchText(item && item.ticker),
                name: normalizeSearchText(item && item.name),
                aliases: (Array.isArray(item && item.aliases) ? item.aliases : []).map(normalizeSearchText)
            }}));

            function subsequenceScore(query, text) {{
                if (!query || !text) return 0;
                let qi = 0;
                for (let i = 0; i < text.length && qi < query.length; i++) {{
                    if (text[i] === query[qi]) qi++;
                }}
                return qi === query.length ? (query.length / text.length) : 0;
            }}

            function scoreFieldNormalized(text, query) {{
                if (!text) return 0;
                if (text === query) return 1000;
                if (text.startsWith(query)) return 820;
                if (text.includes(query)) return 660;
                const subseq = subsequenceScore(query, text);
                if (query.length >= 3 && subseq >= 0.7) return 420 + subseq * 100;
                return 0;
            }}

            function scoreStock(searchItem, query) {{
                let best = 0;
                if (searchItem.ticker === query) best = 1200;
                if (searchItem.name === query) best = Math.max(best, 1150);

                for (const alias of searchItem.aliases) {{
                    const aliasScore = scoreFieldNormalized(alias, query);
                    if (aliasScore > 0) best = Math.max(best, aliasScore + 20);
                }}

                const nameScore = scoreFieldNormalized(searchItem.name, query);
                if (nameScore > 0) best = Math.max(best, nameScore);

                const tickerScore = scoreFieldNormalized(searchItem.ticker, query);
                if (tickerScore > 0) best = Math.max(best, tickerScore + 10);
                return best;
            }}

            function searchStocks(query) {{
                const q = normalizeSearchText(query);
                if (!q) return [];
                return SEARCH_INDEX
                    .map(searchItem => ({{ item: searchItem.item, score: scoreStock(searchItem, q) }}))
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
                    const targetUrl = buildTargetUrl(item.ticker);
                    html +=
                        '<a class="stock-row ' + (idx === 0 ? 'active' : '') + '" href="' + targetUrl + '" target="_blank" rel="noopener noreferrer">' +
                            '<div class="stock-info">' +
                                '<span class="flag">' + item.flag + '</span>' +
                                '<span class="ticker">' + highlightTicker + '</span>' +
                                '<span class="name">' + highlightName + '</span>' +
                            '</div>' +
                            '<span class="exch">' + item.exch + '</span>' +
                        '</a>';
                }});
                listEl.innerHTML = html;
            }}

            function buildTargetUrl(ticker) {{
                return window.parent.location.origin + window.parent.location.pathname +
                    '?code=' + encodeURIComponent(ticker) + '{view_query_suffix}';
            }}

            function selectStock(ticker) {{
                const targetUrl = buildTargetUrl(ticker);
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
    st.iframe(custom_html, height=420)


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
    """뉴스 발행시각을 한국시간(KST)으로 통일해 표시한다."""
    if not value:
        return ""
    try:
        ts = pd.to_datetime(value, utc=True)
        if pd.isna(ts):
            return ""
        ts = ts.tz_convert("Asia/Seoul")
        return f"{ts.month}/{ts.day} {ts.strftime('%H:%M')}"
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


@st.cache_data(ttl=900, show_spinner=False)
def _get_home_macro_news_direct_fallback(
    display=20,
    day_key="",
    cache_version="live-news-fetch-v13",
):
    """Live News provider fallback. Cache version is bumped with feed logic changes."""
    return fetch_macro_news(display=min(max(display, 1), 20))


@st.cache_data(ttl=60, show_spinner=False)
def _get_home_macro_news(display=20, cache_version="supabase-live-news-v13"):
    """자동 수집 DB를 우선하고, 부족하면 실시간 공급원으로 즉시 20개까지 보충한다."""
    target = min(max(display, 1), 20)
    db_rows: list[NaverNewsItem] = []

    if supabase is not None:
        try:
            # collector가 유지하는 rolling feed(최대 20개)를 그대로 읽고,
            # 실제 발행시각을 기준으로 최신 기사가 항상 앞에 오도록 정렬한다.
            result = (
                supabase.table("news_items")
                .select("source,source_id,title,description,article_url,original_url,published_at,collected_at,metadata")
                .eq("is_macro", True)
                .in_("source", ["MARKETAUX", "NAVER", "RSS"])
                .order("published_at", desc=True)
                .limit(target)
                .execute()
            )
            for row in result.data or []:
                meta = row.get("metadata") or {}
                db_rows.append(NaverNewsItem(
                    title=str(row.get("title") or ""),
                    description=str(row.get("description") or ""),
                    link=str(row.get("article_url") or row.get("original_url") or ""),
                    original_link=str(row.get("original_url") or row.get("article_url") or ""),
                    pub_date=str(row.get("published_at") or row.get("collected_at") or ""),
                    query=str(meta.get("query") or "시장 뉴스"),
                    source=str(meta.get("source_label") or row.get("source") or "News"),
                    image_url=str(meta.get("image_url") or ""),
                    snippet=str(meta.get("snippet") or ""),
                    keywords=str(meta.get("keywords") or ""),
                    entities=str(meta.get("entities") or ""),
                ))
            print(f"[HOME LIVE NEWS] Supabase today rows={len(db_rows)}/{target}")
        except Exception as exc:
            print(f"[HOME LIVE NEWS] Supabase read failed: {type(exc).__name__}: {exc}")

    # 수집 시각이 최근이어도 발행시각이 24시간 이상 오래된 스냅샷이면 즉시 공급원을 재조회한다.
    latest_published_ts = 0.0
    for item in db_rows:
        try:
            latest_published_ts = max(latest_published_ts, float(pd.to_datetime(item.pub_date, utc=True).timestamp()))
        except Exception:
            continue
    fresh_cutoff_ts = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
    if len(db_rows) >= target and latest_published_ts >= fresh_cutoff_ts:
        return _sort_news_latest_first(db_rows)[:target]

    day_key = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    fallback = _get_home_macro_news_direct_fallback(
        display=target, day_key=day_key, cache_version="live-news-fetch-v12"
    )
    merged = []
    seen = set()
    for item in _sort_news_latest_first(db_rows + list(fallback or [])):
        key = _canonical_news_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(item)
        if len(merged) >= target:
            break
    print(f"[HOME LIVE NEWS] merged rows={len(merged)}/{target} db={len(db_rows)} fallback={len(fallback or [])}")
    return merged

def _get_earnings_events_db(days_back=90, days_forward=120):
    """Earnings UI는 외부 API를 직접 호출하지 않고 수집된 DB snapshot만 읽는다."""
    if supabase is None:
        return []
    try:
        start_date = (date.today() - timedelta(days=days_back)).isoformat()
        end_date = (date.today() + timedelta(days=days_forward)).isoformat()
        result = (
            supabase.table("earnings_events")
            .select("market,stock_code,stock_name,event_date,event_type,report_name,source_url,metadata,is_primary_event")
            .gte("event_date", start_date)
            .lte("event_date", end_date)
            .order("event_date")
            .order("stock_name")
            .limit(2000)
            .execute()
        )
        rows = []
        today = date.today()
        for row in result.data or []:
            meta = row.get("metadata") or {}
            event_date = date.fromisoformat(str(row["event_date"]))
            rows.append({
                "market": row.get("market"),
                "symbol": str(row.get("stock_code") or ""),
                "company": row.get("stock_name") or "",
                "date": event_date,
                "timing": meta.get("timing") or ("발표" if event_date < today else "예정"),
                "eps_estimate": meta.get("eps_estimate"),
                "actual": meta.get("actual"),
                "surprise": meta.get("surprise"),
                "status": meta.get("status") or ("upcoming" if event_date >= today and row.get("event_type") == "earnings_calendar" else "reported"),
                "event_type": row.get("event_type") or "",
                "report_name": row.get("report_name") or "",
                "source_url": row.get("source_url") or "",
            })
        return rows
    except Exception:
        return []



@st.cache_data(ttl=300, show_spinner=False)
def _get_home_market_overview(market):
    """DB snapshot first; direct source fallback keeps the home page resilient."""
    target = str(market).upper()
    try:
        persisted = load_market_overview(target)
        if persisted:
            return persisted
    except Exception:
        pass
    try:
        return fetch_market_overview(target)
    except Exception:
        return []


def _news_source_label(url: str, fallback: str = "뉴스") -> str:
    """원문 URL의 도메인에서 사람이 읽기 쉬운 뉴스 출처명을 만든다."""
    raw = str(url or "").strip()
    if not raw:
        return fallback
    try:
        host = urlparse(raw).netloc.lower().split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        known = {
            "reuters.com": "Reuters",
            "bloomberg.com": "Bloomberg",
            "wsj.com": "The Wall Street Journal",
            "ft.com": "Financial Times",
            "cnbc.com": "CNBC",
            "marketwatch.com": "MarketWatch",
            "seekingalpha.com": "Seeking Alpha",
            "investing.com": "Investing.com",
            "yna.co.kr": "연합뉴스",
            "newsis.com": "뉴시스",
            "sedaily.com": "서울경제",
            "mk.co.kr": "매일경제",
            "hankyung.com": "한국경제",
        }
        if host in known:
            return known[host]
        return host or fallback
    except Exception:
        return fallback


@st.cache_data(ttl=86400, show_spinner=False)
def _translate_news_cards(
    items: tuple[tuple[str, str], ...],
    cache_version: str = "live-news-korean-v4",
) -> dict:
    """Translate the visible Live News cards with one plain Gemini request."""
    clean_items = tuple(
        (
            str(title or "").replace("\n", " ").strip(),
            str(description or "").replace("\n", " ").strip(),
        )
        for title, description in items
    )
    if not clean_items:
        return {}

    api_key = str(st.secrets.get("GEMINI_API_KEY", "")).strip()
    if not api_key:
        print("[Gemini Live News] GEMINI_API_KEY is not configured.")
        return {}

    model = str(st.secrets.get("GEMINI_NEWS_MODEL", "gemini-3.5-flash-lite")).strip()
    source_lines = "\n".join(
        f"{idx}. 원문 제목: {title}\n원문 설명: {description}"
        for idx, (title, description) in enumerate(clean_items, start=1)
    )
    prompt = f"""
너는 한국의 금융 뉴스 편집자다.
아래 Live News 카드의 영문 제목과 설명을 한국어로 정확하게 현지화하라.

{source_lines}

규칙:
- 영어 제목은 자연스러운 한국어 금융 뉴스 제목으로 번역한다.
- 영어 설명도 자연스러운 한국어로 번역한다.
- 이미 한국어이면 의미를 바꾸지 않는다.
- 원문에 없는 사실, 숫자, 인용, 전망, 투자 의견은 추가하지 않는다.
- 반드시 입력된 번호를 모두 유지한다.
- 한 뉴스는 반드시 한 줄로 출력한다.
- 각 줄은 반드시 "번호|||한국어 제목|||한국어 설명" 형식으로 출력한다.
- 설명 안에 "|" 문자가 필요하면 하나의 "|"만 사용할 수 있지만 "|||"는 절대 사용하지 않는다.
- 마크다운, 코드블록, 추가 설명은 출력하지 않는다.
"""

    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "maxOutputTokens": 2000,
                    "temperature": 0.1,
                },
            },
            timeout=30,
        )
        if not response.ok:
            print(
                f"[Gemini Live News] HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
            return {}

        payload = response.json()
        parts = (
            payload.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        result = "\n".join(
            str(part.get("text", "")).strip()
            for part in parts
            if part.get("text")
        ).strip()
        if not result:
            print("[Gemini Live News] Empty Gemini response.")
            return {}

        localized = {}
        for raw_line in result.splitlines():
            line = raw_line.strip().strip("`")
            if not line or "|||" not in line:
                continue
            fields = [field.strip() for field in line.split("|||")]
            if len(fields) < 3:
                continue
            match = re.search(r"\d+", fields[0])
            if not match:
                continue
            idx = int(match.group(0))
            translated_title = fields[1].strip()
            translated_description = fields[2].strip()
            if 1 <= idx <= len(clean_items) and translated_title:
                localized[idx - 1] = {
                    "title": translated_title,
                    "description": translated_description,
                }

        if not localized:
            print(
                "[Gemini Live News] Could not parse translation response. "
                f"Raw response: {result[:800]}"
            )
        return localized
    except Exception as exc:
        print(f"[Gemini Live News] Request failed: {type(exc).__name__}: {exc}")
        return {}

@st.cache_data(ttl=86400, show_spinner=False)
def _generate_ai_news_article(
    title: str,
    description: str = "",
    snippet: str = "",
    keywords: str = "",
    entities: str = "",
    source: str = "",
) -> dict:
    """Translate the source headline into Korean and generate a Korean financial brief."""
    api_key = str(st.secrets.get("GEMINI_API_KEY", "")).strip()
    if not api_key:
        return {}

    model = str(st.secrets.get("GEMINI_NEWS_MODEL", "gemini-3.5-flash-lite")).strip()
    source_material = "\n".join([
        f"원문 제목: {title}",
        f"출처: {source}",
        f"설명: {description}",
        f"짧은 본문 문맥: {snippet}",
        f"핵심 키워드: {keywords}",
        f"관련 기업·자산: {entities}",
    ])

    prompt = f"""
너는 미국·한국 금융시장 전문 뉴스 에디터다.
아래 자료는 실제 뉴스 공급원이 제공한 메타데이터와 짧은 문맥이다.

{source_material}

위 자료만 근거로 한국어 금융뉴스를 작성하라.
원문 제목이 영어라면 의미를 정확히 보존한 자연스러운 한국어 금융 제목으로 먼저 번역하고,
그 한국어 주제를 중심으로 본문을 작성하라.

작성 규칙:
1. 원문 문장을 그대로 복사하지 말고 완전히 다른 표현으로 재구성한다.
2. 자료에 없는 사실, 숫자, 인용, 발언, 일정, 전망을 절대로 만들어내지 않는다.
3. 자료만으로 확인할 수 없는 내용은 추측하지 않는다.
4. 본문은 7~9개 문단, 총 1200~1800자 정도로 작성한다.
5. 첫 문단은 무슨 일이 있었는지를 바로 설명한다.
6. 이어서 배경, 핵심 사실, 시장 영향, 향후 체크포인트 순서로 설명한다.
7. 시장 영향은 자료에서 합리적으로 연결되는 범위에서만 설명한다.
8. 투자 추천이나 매수·매도 지시는 하지 않는다.
9. 기업명·자산명·시장명·수치가 제공된 경우 정확하게 유지한다.
10. AI 안내문이나 출처 표시는 출력하지 않는다.
11. 아래 형식을 정확히 지킨다.

제목:
<한국어 제목>

본문:
<본문>
"""
    try:
        response = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": 2200},
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        parts = (
            payload.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [])
        )
        result = "\n".join(
            str(p.get("text", "")).strip()
            for p in parts
            if p.get("text")
        ).strip()

        ai_title = ""
        ai_body = result
        if "제목:" in result:
            after_title = result.split("제목:", 1)[1].strip()
            if "본문:" in after_title:
                ai_title, ai_body = after_title.split("본문:", 1)
                ai_title = ai_title.strip()
                ai_body = ai_body.strip()

        return {"title": ai_title, "body": ai_body}
    except Exception:
        return {}

def _get_ai_news_image_url(
    title: str,
    description: str = "",
    query: str = "",
    article_url: str = "",
) -> str:
    """대표 이미지가 없을 때 기사별 고해상도 AI 금융 일러스트 URL을 만든다.

    기사마다 seed와 시각 스타일을 달리해 같은 이미지가 반복되지 않도록 한다.
    Pollinations의 현재 image endpoint에서 16:9 고해상도 FLUX 렌더링을 요청한다.
    """
    key = f"{article_url}|{title}|{description}|{query}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    seed = int(digest[:8], 16)

    text = f"{title} {description} {query}".lower()
    if any(term in text for term in (
        "fed", "federal reserve", "interest rate", "inflation", "cpi",
        "pce", "treasury", "bond", "yield", "central bank", "연준",
        "금리", "물가", "채권",
    )):
        visual_theme = (
            "a modern central-bank and Treasury-market editorial scene, "
            "government financial district architecture, bond yield curves, "
            "subtle economic data displays"
        )
    elif any(term in text for term in (
        "ai", "artificial intelligence", "semiconductor", "chip", "nvidia",
        "data center", "software", "technology", "인공지능", "반도체", "테크",
    )):
        visual_theme = (
            "a premium technology finance editorial scene, advanced semiconductor "
            "wafer, data-center racks, luminous network connections and market charts"
        )
    elif any(term in text for term in (
        "oil", "crude", "brent", "energy", "opec", "gas", "원유", "석유", "에너지",
    )):
        visual_theme = (
            "a sophisticated energy-market editorial scene, oil refinery and storage "
            "tanks at dusk, commodity price visualization, restrained newsroom aesthetic"
        )
    elif any(term in text for term in (
        "gold", "silver", "copper", "commodity", "금값", "금", "은", "구리", "원자재",
    )):
        visual_theme = (
            "a premium commodities-market editorial scene, realistic gold bars and "
            "metal textures with a subtle trading-floor background and price charts"
        )
    elif any(term in text for term in (
        "tariff", "trade", "export", "import", "shipping", "port", "manufacturing",
        "관세", "무역", "수출", "수입", "제조", "물류",
    )):
        visual_theme = (
            "a global trade and manufacturing editorial scene, container port, cargo "
            "ships and industrial facilities, subtle financial market overlays"
        )
    else:
        style_variants = (
            "a global trading floor with multiple market monitors and an editorial news-desk atmosphere",
            "a polished financial-district cityscape with market charts reflected in glass architecture",
            "a sophisticated newsroom scene with analysts, screens and abstract market data, no identifiable people",
            "a global markets visualization with index charts, currency symbols and a premium business-news aesthetic",
        )
        visual_theme = style_variants[seed % len(style_variants)]

    topic = (description or title or query or "global financial markets").strip()[:420]
    prompt = (
        "High-end editorial illustration for a professional financial-news website. "        "Landscape 16:9, photorealistic but polished newsroom aesthetic, crisp details, "
        "natural lighting, realistic materials, depth and clean composition. "
        "No readable text, no headlines, no logos, no brand marks, no watermarks, "
        "no recognizable real people, no duplicated objects. "
        f"Visual theme: {visual_theme}. "
        f"News context: {topic}."
    )
    return (
        "https://image.pollinations.ai/prompt/"
        + quote(prompt, safe="")
        + f"?model=flux&width=1536&height=864&seed={seed}&nologo=true"
    )


def _build_news_reader_url(
    *,
    title: str,
    description: str,
    article_url: str,
    original_url: str,
    pub_date: str,
    image_url: str,
    source: str,
    category: str,
    back_url: str = "",
    snippet: str = "",
    keywords: str = "",
    entities: str = "",
) -> str:
    payload = {
        "news_view": "reader",
        "news_title": title,
        "news_desc": description,
        "news_url": article_url,
        "news_original_url": original_url,
        "news_time": _format_news_time(pub_date),
        "news_image": image_url,
        "news_source": source,
        "news_category": category or "시장 뉴스",
        "news_snippet": snippet,
        "news_keywords": keywords,
        "news_entities": entities,
        "news_back": back_url,
        "theme": THEME_MODE,
    }
    payload = {k: v for k, v in payload.items() if v}
    return "?" + urlencode(payload)


def render_news_reader():
    """외부 기사 HTML을 그대로 열지 않고, 우리 사이트의 내부 뉴스 리더 화면으로 표시한다."""
    qp = st.query_params
    title = str(qp.get("news_title", "")).strip()
    description = str(qp.get("news_desc", "")).strip()
    article_url = str(qp.get("news_url", "")).strip()
    original_url = str(qp.get("news_original_url", "")).strip()
    news_time = str(qp.get("news_time", "")).strip()
    source = str(qp.get("news_source", "")).strip() or _news_source_label(original_url or article_url)
    category = str(qp.get("news_category", "")).strip() or "시장 뉴스"
    snippet = str(qp.get("news_snippet", "")).strip()
    keywords = str(qp.get("news_keywords", "")).strip()
    entities = str(qp.get("news_entities", "")).strip()
    image_url = str(qp.get("news_image", "")).strip()
    ai_result = _generate_ai_news_article(
        title=title,
        description=description,
        snippet=snippet,
        keywords=keywords,
        entities=entities,
        source=source,
    )
    ai_title = str(ai_result.get("title") or "").strip()
    ai_body = str(ai_result.get("body") or "").strip()
    reader_title = ai_title or title
    back_url = str(qp.get("news_back", "")).strip() or f"?theme={THEME_MODE}"

    if not title:
        st.info("표시할 뉴스가 없습니다.")
        st.stop()

    if not image_url:
        image_url = _get_news_image_url(original_url or article_url)

    col_logo, col_quote, col_login = st.columns([1.0, 6.8, 1.0])
    with col_logo:
        st.markdown("<div class='logo-box'>📈 Fundamental</div>", unsafe_allow_html=True)
    with col_quote:
        render_quote_box()
    with col_login:
        render_theme_toggle("theme_toggle_news_reader")
        st.link_button("← 이전 화면", back_url, use_container_width=True)

    st.markdown("<br>", unsafe_allow_html=True)

    left_ad, article_main, right_ad = st.columns([0.6, 6.8, 0.6])
    with left_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

    with article_main:
        image_html = (
            f'<img class="news-reader-image" src="{_escape_html(image_url)}" alt="{_escape_html(title)}" '
            f'loading="eager" onerror="this.style.display=\'none\';">'
            if image_url else ""
        )
        st.html(
            f"""
            <div class="news-reader-wrap">
              <div class="news-reader-kicker" style="color:{THEME['accent_strong']};">
                {_escape_html(category)} · {_escape_html(source)}
              </div>
              <h1 class="news-reader-title" style="color:{THEME['text']};">
                {_escape_html(reader_title)}
              </h1>
              <div class="news-reader-meta" style="color:{THEME['text_muted']};">
                {_escape_html(news_time)}
              </div>
              {image_html}
              <div class="news-reader-ai-article" style="color:{THEME['text']};">
                {_escape_html(ai_body or description or snippet or "기사 내용을 불러오지 못했습니다.")}
              </div>
            </div>
            """
        )
        st.html(
            f'<div class="news-reader-disclosure" style="color:{THEME["text_muted"]};">'
            '※ AI에 의해 작성된 기사입니다. 원출처의 정보를 바탕으로 재구성했으며, 원문을 그대로 복제하지 않습니다.'
            '</div>'
        )
        source_url = original_url or article_url
        if source_url:
            st.html(
                f'<div style="text-align:right; margin-top:8px;">'
                f'<a class="news-reader-source-link" href="{_escape_html(source_url)}" target="_blank" rel="noopener noreferrer" '
                f'style="color:{THEME["accent_strong"]};">{_escape_html(source)} ↗</a>'
                f'</div>'
            )

    with right_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)


@st.cache_data(ttl=1800, show_spinner=False)
def _news_image_dimensions(image_url: str):
    """이미지 헤더만 확인해 대표 이미지의 대략적인 픽셀 크기를 반환한다."""
    url = str(image_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return (0, 0)
    try:
        response = requests.get(
            url,
            timeout=4,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; FundamentalNews/1.0)",
                "Range": "bytes=0-131071",
            },
            allow_redirects=True,
        )
        response.raise_for_status()
        data = response.content
        content_type = str(response.headers.get("Content-Type", "")).lower()

        # PNG: IHDR width/height
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            return (int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))

        # WEBP: VP8X / VP8 / VP8L
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            if data[12:16] == b"VP8X" and len(data) >= 30:
                width = 1 + int.from_bytes(data[24:27], "little")
                height = 1 + int.from_bytes(data[27:30], "little")
                return (width, height)
            if data[12:16] == b"VP8L" and len(data) >= 25:
                if data[20] == 0x2F:
                    b0, b1, b2, b3 = data[21:25]
                    width = 1 + (b0 | ((b1 & 0x3F) << 8))
                    height = 1 + (((b1 >> 6) | (b2 << 2) | ((b3 & 0x0F) << 10)))
                    return (width, height)

        # JPEG: SOF marker까지 스캔하여 width/height 확인
        if data.startswith(b"\xff\xd8"):
            i = 2
            sof_markers = {
                0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
            }
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                while i < len(data) and data[i] == 0xFF:
                    i += 1
                if i >= len(data):
                    break
                marker = data[i]
                i += 1
                if marker in (0xD8, 0xD9):
                    continue
                if i + 2 > len(data):
                    break
                segment_len = int.from_bytes(data[i:i + 2], "big")
                if marker in sof_markers and i + 7 < len(data):
                    height = int.from_bytes(data[i + 3:i + 5], "big")
                    width = int.from_bytes(data[i + 5:i + 7], "big")
                    return (width, height)
                if segment_len < 2:
                    break
                i += segment_len
    except Exception:
        pass
    return (0, 0)


def _news_image_quality_ok(image_url: str) -> bool:
    """뉴스 카드에서 명백한 검색 썸네일/저해상도 이미지만 차단한다."""
    url = str(image_url or "").strip().lower()
    if not url:
        return False

    # Bing News 검색 썸네일은 기사 원본이 아니므로 제외한다.
    try:
        parsed_url = urlparse(url)
        if (
            "bing.com" in parsed_url.netloc
            and ("th=" in parsed_url.query or parsed_url.path.rstrip("/").endswith("/th"))
        ):
            return False
    except Exception:
        pass

    # 실제 원문 CDN 경로에 'small/thumb' 같은 단어가 포함되는 경우가 있어
    # 의미가 확실한 픽셀 크기 힌트만 차단한다.
    lowres_hints = (
        "150x", "180x", "200x", "240x", "300x", "320x", "400x",
        "width=150", "width=180", "width=200", "width=240",
        "width=300", "width=320", "width=400",
        "w_150", "w_180", "w_200", "w_240", "w_300", "w_320", "w_400",
    )
    if any(hint in url for hint in lowres_hints):
        return False

    width, height = _news_image_dimensions(image_url)
    if width and height:
        # 카드가 약 148px 높이이므로 480x270부터는 실제 원문 이미지로 허용한다.
        return width >= 480 and height >= 270

    # 치수 확인이 안 되는 정상 URL은 허용한다.
    return True

@st.cache_data(ttl=1800, show_spinner=False)
def _get_news_image_url(article_url: str) -> str:
    """기사 원문에서 고해상도 대표 이미지 후보를 추출한다."""
    url = str(article_url or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return ""
    try:
        response = requests.get(
            url,
            timeout=5,
            headers={"User-Agent": "Mozilla/5.0 (compatible; FundamentalNews/1.0)"},
            allow_redirects=True,
        )
        response.raise_for_status()
        html = response.text[:800_000]
        patterns = (
            r"<meta[^>]+property=[\"']og:image:secure_url[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image:secure_url[\"']",
            r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image[\"']",
            r"<meta[^>]+name=[\"']twitter:image:src[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+name=[\"']twitter:image:src[\"']",
            r"<meta[^>]+name=[\"']twitter:image[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+name=[\"']twitter:image[\"']",
            r"<meta[^>]+itemprop=[\"']image[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+itemprop=[\"']image[\"']",
            r"<link[^>]+rel=[\"'][^\"']*image_src[^\"']*[\"'][^>]+href=[\"']([^\"']+)",
        )
        candidates = []
        seen = set()
        from urllib.parse import urljoin

        def add_candidate(raw_url):
            image_url = unescape(str(raw_url or "")).strip()
            if image_url.startswith("//"):
                image_url = "https:" + image_url
            elif image_url.startswith("/"):
                image_url = urljoin(response.url or url, image_url)
            if not image_url.startswith(("http://", "https://")):
                return
            key = image_url.strip().lower()
            if key in seen:
                return
            seen.add(key)
            candidates.append(image_url)

        # 1) srcset / data-srcset: 브라우저가 선택하는 가장 큰 원본 후보를 우선한다.
        srcset_values = re.findall(
            r"""(?:srcset|data-srcset)=["']([^"']+)["']""",
            html,
            flags=re.IGNORECASE,
        )
        srcset_candidates = []
        for srcset in srcset_values:
            for entry in re.split(r"\s*,\s*", srcset):
                parts = entry.strip().split()
                if not parts:
                    continue
                raw_url = parts[0]
                width = 0
                if len(parts) > 1:
                    match_width = re.match(r"(\d+)w$", parts[1])
                    if match_width:
                        width = int(match_width.group(1))
                srcset_candidates.append((width, raw_url))
        for _, raw_url in sorted(srcset_candidates, key=lambda x: x[0], reverse=True)[:12]:
            add_candidate(raw_url)

        # 2) JSON-LD 구조화 데이터의 image / primaryImageOfPage 후보.
        for match in re.finditer(
            r'"(?:image|contentUrl|thumbnailUrl)"\s*:\s*"([^"]+)"',
            html,
            flags=re.IGNORECASE,
        ):
            add_candidate(match.group(1))
            if len(candidates) >= 24:
                break

        # 3) 주요 메타 태그.
        patterns = (
            r"<meta[^>]+property=[\"']og:image:secure_url[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image:secure_url[\"']",
            r"<meta[^>]+property=[\"']og:image[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+property=[\"']og:image[\"']",
            r"<meta[^>]+name=[\"']twitter:image:src[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+name=[\"']twitter:image:src[\"']",
            r"<meta[^>]+name=[\"']twitter:image[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+name=[\"']twitter:image[\"']",
            r"<meta[^>]+itemprop=[\"']image[\"'][^>]+content=[\"']([^\"']+)",
            r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+itemprop=[\"']image[\"']",
            r"<link[^>]+rel=[\"'][^\"']*image_src[^\"']*[\"'][^>]+href=[\"']([^\"']+)",
        )
        for pattern in patterns:
            for match in re.finditer(pattern, html, flags=re.IGNORECASE):
                add_candidate(match.group(1))
                if len(candidates) >= 24:
                    break
            if len(candidates) >= 24:
                break

        # 4) 흔한 lazy-load 원본 속성.
        for attr in ("data-original", "data-src", "data-lazy-src"):
            for match in re.finditer(
                rf"""{attr}=["']([^"']+)["']""",
                html,
                flags=re.IGNORECASE,
            ):
                add_candidate(match.group(1))
                if len(candidates) >= 24:
                    break
            if len(candidates) >= 24:
                break

        # 정상 크기 후보를 우선 반환한다. 대표 이미지 후보가 하나뿐이고
        # 치수 확인이 안 되는 경우에도 URL은 유지한다.
        for image_url in candidates:
            if _news_image_quality_ok(image_url):
                return image_url
    except Exception:
        pass
    return ""

def _get_news_images(urls):
    urls = [str(u or "") for u in urls]
    if not urls:
        return []
    # 현재 화면에 표시할 카드 전체에 대해 원문 대표 이미지를 확인한다.
    # 병렬 조회로 로딩 시간을 관리하고, 실패 시 공급원 썸네일을 사용한다.
    results = [""] * len(urls)
    lookup_count = len(urls)
    lookup_urls = [(idx, urls[idx]) for idx in range(lookup_count) if urls[idx]]
    if not lookup_urls:
        return results
    with ThreadPoolExecutor(max_workers=min(6, len(lookup_urls))) as executor:
        fetched = executor.map(lambda pair: (pair[0], _get_news_image_url(pair[1])), lookup_urls)
        for idx, image_url in fetched:
            results[idx] = image_url
    return results


def _render_news_cards(items, limit=9, title="📰 Live News", subtitle="", back_url=""):
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

    # Defensive dedupe: provider/fallback results can occasionally repeat the same
    # article under slightly different metadata. Keep one card per canonical URL/title.
    unique_items = []
    seen_news_keys = set()
    for item in list(items):
        item_url = (
            item.original_link if hasattr(item, "original_link")
            else item.get("original_url", "")
        ) or (
            item.link if hasattr(item, "link")
            else item.get("article_url", "")
        )
        item_title = item.title if hasattr(item, "title") else item.get("title", "")
        news_key = str(item_url or item_title or "").strip().lower()
        if news_key and news_key in seen_news_keys:
            continue
        if news_key:
            seen_news_keys.add(news_key)
        unique_items.append(item)
        if len(unique_items) >= limit:
            break
    selected_items = unique_items
    article_urls = []
    for item in selected_items:
        original_url = item.original_link if hasattr(item, "original_link") else item.get("original_url", "")
        article_url = item.link if hasattr(item, "link") else item.get("article_url", "")
        article_urls.append(original_url or article_url)
    image_urls = _get_news_images(article_urls)

    localized_cards = {}
    translation_input = tuple(
        (
            item.title if hasattr(item, "title") else item.get("title", ""),
            item.description if hasattr(item, "description") else item.get("description", ""),
        )
        for item in selected_items
    )
    needs_localization = any(
        bool(
            re.search(
                r"[A-Za-z]",
                str(title_value or "") + " " + str(desc_value or ""),
            )
        )
        for title_value, desc_value in translation_input
    )
    if needs_localization:
        localized_cards = _translate_news_cards(
            translation_input,
            cache_version="live-news-korean-v6",
        )

    cards = []
    for idx, item in enumerate(selected_items):
        title_text = item.title if hasattr(item, "title") else item.get("title", "")
        desc_text = item.description if hasattr(item, "description") else item.get("description", "")
        localized = localized_cards.get(idx, {})
        display_title = str(localized.get("title") or title_text).strip()
        display_desc = str(localized.get("description") or desc_text).strip()
        article_url = item.link if hasattr(item, "link") else item.get("article_url", "")
        original_url = item.original_link if hasattr(item, "original_link") else item.get("original_url", "")
        pub_date = item.pub_date if hasattr(item, "pub_date") else item.get("published_at", "")
        query = item.query if hasattr(item, "query") else ""
        source_hint = item.source if hasattr(item, "source") else item.get("source", "")
        snippet_text = getattr(item, "snippet", "")
        keywords_text = getattr(item, "keywords", "")
        entities_text = getattr(item, "entities", "")
        provided_image_url = getattr(item, "image_url", "")

        # 이미지 우선순위: 검증된 원문 대표 이미지 → 검증된 공급원 이미지 → 기사별 AI 이미지.
        # AI fallback은 실제 원문 이미지가 없는 경우에만 생성한다.
        direct_url = original_url or article_url
        # 카드 클릭은 내부 뉴스 리더로 연결하고, 원문 URL을 안전한 fallback으로 사용한다.
        reader_url = original_url or article_url
        image_candidates = [
            image_urls[idx] if idx < len(image_urls) else "",
            provided_image_url,
        ]
        image_url = ""
        for candidate_image in image_candidates:
            if candidate_image and _news_image_quality_ok(candidate_image):
                image_url = candidate_image
                break

        if not image_url:
            image_url = _get_ai_news_image_url(
                title=title_text,
                description=desc_text,
                query=query,
                article_url=direct_url,
            )

        # 메인 카드에는 AI 이미지 여부를 별도 배지로 표시하지 않는다.


        if image_url:
            media_html = (
                f'<div class="live-news-image-wrap">'
                f'<img class="live-news-image" src="{_escape_html(image_url)}" loading="lazy" '
                f'alt="{_escape_html(display_title)}" onerror="this.onerror=null;this.style.display=\'none\';this.parentElement.classList.add(\'live-news-image-broken\');">'
                f'</div>'
            )
        else:
            # 원문 대표 이미지가 없을 때 분류명을 이미지처럼 보여주지 않는다.
            # 실제 이미지가 없다는 사실만 중립적으로 표시해 신뢰도 저하를 방지한다.
            media_html = (
                '<div class="live-news-image-wrap live-news-image-fallback">'
                '<span>📰</span><small>원문 이미지 없음</small>'
                '</div>'
            )

        cards.append(
            f'<a class="live-news-card-link" href="{_escape_html(reader_url)}" target="_self" rel="noopener">'
            f'<article class="live-news-card">'
            f'{media_html}'
            f'<div class="live-news-card-body">'
            f'<div class="live-news-meta">'
            f'<span class="live-news-source">{_escape_html(source_label)}</span>'
            f'<span class="live-news-category">{_escape_html(category_display)}</span>'
            f'</div>'
            f'<div class="live-news-title">{_escape_html(display_title)}</div>'
            f'<div class="live-news-desc">{_escape_html(display_desc)}</div>'
            f'<div class="live-news-footer">{_format_news_time(pub_date)} · 기사 보기 ↗</div>'
            f'</div>'
            f'</article>'
            f'</a>'
        )
    # Render the complete card grid as HTML so the anchor remains part of the
    # card instead of being interpreted by Streamlit's Markdown parser.
    st.html('<div class="live-news-grid">' + ''.join(cards) + '</div>')


def render_home_live_news(limit=20):
    """메인 Live News: 20개를 확보하고 화면에는 10개씩 좌우 화살표로 넘겨 보여준다."""
    try:
        items = _get_home_macro_news(display=max(limit, 20))
    except Exception as exc:
        items = []
        st.warning(f"Live News를 불러오지 못했습니다: {exc}")

    page_size = 10
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    day_key = datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()
    if st.session_state.get("live_news_day_key") != day_key:
        st.session_state["live_news_day_key"] = day_key
        st.session_state["live_news_page"] = 0
    current_page = int(st.session_state.get("live_news_page", 0))
    current_page = max(0, min(current_page, total_pages - 1))
    st.session_state["live_news_page"] = current_page

    start = current_page * page_size
    page_items = list(items)[start:start + page_size]

    _render_news_cards(
        page_items,
        limit=page_size,
        title="📰 Live News",
        subtitle="미국·한국 경제·금융 중심의 주요 뉴스 20개 · 최신 기사부터 표시 · 새로 수집된 기사는 앞쪽에 추가 · 2시간 자동수집 · 10개씩 표시",
        back_url=f"?theme={THEME_MODE}",
    )

    if total_pages > 1:
        nav_left, nav_mid, nav_right = st.columns([1, 6, 1])
        with nav_left:
            if st.button("←", key="live_news_prev", use_container_width=True, disabled=current_page == 0):
                st.session_state["live_news_page"] = max(0, current_page - 1)
                st.rerun()
        with nav_mid:
            st.markdown(
                f"<div style='text-align:center; color:{THEME['text_muted']}; font-size:12px; padding-top:8px;'>"
                f"{current_page + 1} / {total_pages} · 총 {len(items)}개 뉴스"
                f"</div>",
                unsafe_allow_html=True,
            )
        with nav_right:
            if st.button("→", key="live_news_next", use_container_width=True, disabled=current_page >= total_pages - 1):
                st.session_state["live_news_page"] = min(total_pages - 1, current_page + 1)
                st.rerun()


@st.cache_data(ttl=1800, show_spinner=False)
def _get_stock_news_cached(
    stock_name,
    stock_code,
    limit=3,
    cache_version="stock-news-v12",
):
    return fetch_stock_news(
        stock_name,
        stock_code,
        display=min(max(limit, 1), 3),
    )


def render_home_stock_news(stock_name, stock_code, limit=3):
    """종목 상세 페이지의 종목별 뉴스."""
    try:
        items = _get_stock_news_cached(
            stock_name,
            stock_code,
            limit,
            cache_version="stock-news-v11",
        )
    except Exception:
        items = []
    if not items:
        return
    _render_news_cards(
        items,
        limit=limit,
        title=f"📰 {stock_name} 관련 뉴스",
        subtitle="해당 종목명을 기준으로 조회한 최신 뉴스 검색 결과입니다. 카드를 누르면 우리 사이트의 내부 뉴스 리더로 이동합니다.",
        back_url=f"?code={stock_code}&theme={THEME_MODE}",
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
        target = date.fromisoformat(str(event["date"].isoformat()))
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


def _korean_company_alias(symbol, company_name=""):
    """검색 alias에 등록된 한국어 기업명을 실적 UI에서 보조 표기로 사용한다."""
    key = str(symbol or "").strip().upper().replace("-", ".")
    aliases = aliases_for(key, company_name)
    for alias in aliases:
        if any("\uAC00" <= ch <= "\uD7A3" for ch in str(alias)):
            if str(alias).strip().casefold() != str(company_name or "").strip().casefold():
                return str(alias).strip()
    return ""


@st.cache_data(ttl=300, show_spinner=False)
def _earnings_calendar_events():
    """수집된 earnings_events snapshot만 읽어 월간 캘린더를 즉시 구성."""
    events = _get_earnings_events_db(days_back=90, days_forward=120)
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
                is_selected = selected_date == cell_date

                cell_key = (
                    f"earnings_cell_selected_{month_start.isoformat()}_{market_filter}_{cell_date.isoformat()}"
                    if is_selected
                    else f"earnings_cell_{month_start.isoformat()}_{market_filter}_{cell_date.isoformat()}"
                )

                with col.container(height=122, border=True, key=cell_key):
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
                        ko_name = _korean_company_alias(item["symbol"], item["company"]) if item["market"] == "US" else ""
                        status_mark = "예정" if item["status"] == "upcoming" else "실적"
                        title_name = f"{item['company']} ({ko_name})" if ko_name else item["company"]
                        ko_html = (
                            f"<span class='earnings-calendar-company-ko'>({_escape_calendar_text(ko_name)})</span>"
                            if ko_name else ""
                        )
                        st.markdown(
                            f"<div class='earnings-calendar-event {market_class}' title='{_escape_calendar_text(title_name)} · {status_mark}'>"
                            f"{market_tag} · {_escape_calendar_text(name)}{ko_html}</div>",
                            unsafe_allow_html=True,
                        )
                    if len(items) > 3:
                        st.markdown(f"<div class='earnings-calendar-more'>+ {len(items)-3}개 더보기</div>", unsafe_allow_html=True)
                    elif not items:
                        st.markdown("<div class='earnings-calendar-empty'>-</div>", unsafe_allow_html=True)
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
                  <div class='earnings-detail-name'>{_escape_calendar_text(row['company'])}{f"<span class='earnings-company-ko'>({_escape_calendar_text(_korean_company_alias(row['symbol'], row['company']))})</span>" if row['market'] == 'US' and _korean_company_alias(row['symbol'], row['company']) else ''} <span style='color:#6B7280 !important;font-size:11px;font-weight:800;'>({row['symbol']})</span></div>
                  <div class='earnings-detail-meta'>{_escape_calendar_text(" · ".join(meta_parts))} {source_link}</div>
                </div>
                <span class='earnings-detail-market {market_class}'>{market_label}</span>
              </div>
              {compare}
            </div>
            """,
            unsafe_allow_html=True,        )


@st.fragment(run_every="5m")
def render_home_live_news_auto(limit=20):
    """열린 브라우저 세션에서 Live News 영역만 5분마다 재조회한다."""
    render_home_live_news(limit=limit)


def render_home_earnings_calendar(limit=12):
    """메인 홈에는 요약만 보여주고, 전체 월간 캘린더는 별도 탭/창으로 연다."""
    # 제목 옆에 전체 월간 캘린더 버튼을 배치해, 아래 콘텐츠와 분리된
    # 독립적인 액션으로 보이도록 한다.
    title_col, calendar_col = st.columns([5.8, 1.65], vertical_alignment="center")
    with title_col:
        st.markdown(
            "<div class='live-news-section'>"
            "<div class='live-news-section-title'>📅 Earnings Calendar</div>"
            "<div class='live-news-section-subtitle'>최근 발표 실적과 향후 예정 실적을 간단히 확인하고, 전체 월간 캘린더에서 날짜별로 볼 수 있습니다.</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with calendar_col:
        st.link_button(
            "📅 전체 캘린더",
            f"?view=earnings_calendar&theme={THEME_MODE}",
            use_container_width=True,
        )

    recent_events = [
        row for row in _get_earnings_events_db(days_back=30, days_forward=0)
        if row["market"] == "KR" and row["status"] == "reported"
    ]

    st.markdown("<div class='earnings-upcoming-title'>🇰🇷 최근 발표 실적</div>", unsafe_allow_html=True)
    if recent_events:
        for event in recent_events[:min(limit, 5)]:
            label = "잠정실적" if event["event_type"] == "preliminary_earnings" else "정기보고서"
            consensus = {
                "actual": event.get("actual"),
                "estimate": event.get("eps_estimate"),
                "surprise": event.get("surprise"),
            } if any(event.get(k) is not None for k in ("actual", "eps_estimate", "surprise")) else None
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
            source_link = ""
            if event["source_url"]:
                source_link = (
                    f"<a href='{_escape_html(event['source_url'])}' target='_blank' "
                    f"rel='noopener noreferrer' style='color:#D97706;text-decoration:none;font-weight:800;margin-left:8px;'>공시 보기 ↗</a>"
                )

            st.markdown(
                f"""
                <div class="earnings-row">
                  <div>
                    <div class="earnings-name">{_escape_html(event["company"])} <span class="earnings-primary">{label}</span></div>
                    <div class="earnings-report">{_escape_html(event["report_name"])}{source_link}</div>
                    {compare}
                  </div>
                  <div class="earnings-date">{_escape_html(event["date"].isoformat())}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.markdown('<div class="news-empty-state">최근 실적 공시가 없습니다.</div>', unsafe_allow_html=True)

    st.markdown("<div class='earnings-upcoming-title'>🇺🇸🇰🇷 향후 예정 실적 · 다음 14일</div>", unsafe_allow_html=True)
    # Earnings are collected by the background job and served from the
    # Supabase snapshot. Do not call Yahoo/DART from the Streamlit render path.
    upcoming = [
        row for row in _get_earnings_events_db(days_back=0, days_forward=14)
        if row["status"] == "upcoming" and row["event_type"] == "earnings_calendar"
    ]
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
                    <div class="earnings-name">{_escape_html(row['company'])}{f"<span class='earnings-company-ko'>({_escape_html(_korean_company_alias(row['symbol'], row['company']))})</span>" if row['market'] == 'US' and _korean_company_alias(row['symbol'], row['company']) else ''} <span class="earnings-primary">{_escape_html(row['symbol'])}</span></div>
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



def _format_market_overview_value(row):
    label = str(row.get("label") or "")
    value = row.get("value")
    if value is None:
        return "—"
    if label == "US 10Y":
        return f"{float(value):.2f}%"
    if label == "USD/KRW":
        return f"₩{float(value):,.1f}"
    if label in {"Gold", "WTI Oil"}:
        return f"${float(value):,.2f}"
    return f"{float(value):,.2f}"


def _format_market_overview_delta(row):
    label = str(row.get("label") or "")
    change_pct = row.get("change_pct")
    change_abs = row.get("change_abs")
    if change_pct is None:
        return None
    if label == "US 10Y" and change_abs is not None:
        return f"{float(change_abs):+.2f}%p"
    if label == "VIX" and change_abs is not None:
        return f"{float(change_abs):+.2f}"
    return f"{float(change_pct):+.2f}%"


def render_home_market_overview(market):
    market = str(market).upper()
    title = "🇺🇸 US Market Overview" if market == "US" else "🇰🇷 Korea Market Overview"
    subtitle = (
        "주요 지수와 시장 환경 지표의 최신 일봉 기준 시세 흐름입니다."
        if market == "US"
        else "국내 주요 지수와 주요 시장 환경 지표의 최신 일봉 기준 시세 흐름입니다."
    )
    st.markdown(
        f"<div class='live-news-section'><div class='live-news-section-title'>{title}</div>"
        f"<div class='live-news-section-subtitle'>{subtitle}</div></div>",
        unsafe_allow_html=True,
    )

    rows = _get_home_market_overview(market)
    if not rows:
        st.info("시장 스냅샷 데이터를 불러오지 못했습니다.")
        return

    group_titles = {"market": "주요 지수", "conditions": "시장 환경"}
    for group in ("market", "conditions"):
        group_rows = [row for row in rows if row.get("metric_group") == group]
        order = (
            ["S&P 500", "Nasdaq", "Dow Jones", "Russell 2000"]
            if market == "US" and group == "market"
            else ["VIX", "US 10Y", "Gold", "WTI Oil"]
            if market == "US"
            else ["KOSPI", "KOSDAQ"]
            if group == "market"
            else ["USD/KRW", "Gold", "WTI Oil"]
        )
        rank = {label: idx for idx, label in enumerate(order)}
        group_rows.sort(key=lambda row: rank.get(str(row.get("label") or ""), 999))
        if not group_rows:
            continue
        if group == "conditions":
            st.markdown(
                f"<div class='overview-group-title'>{group_titles[group]}</div>",
                unsafe_allow_html=True,
            )
        cols = st.columns(min(4, len(group_rows)))
        for idx, row in enumerate(group_rows):
            with cols[idx % len(cols)]:
                delta = _format_market_overview_delta(row)
                kwargs = {}
                if row.get("label") == "VIX":
                    kwargs["delta_color"] = "inverse"
                st.metric(
                    row.get("label") or row.get("symbol") or "Market",
                    _format_market_overview_value(row),
                    delta,
                    **kwargs,
                )

    latest_dates = sorted({str(row.get("asof_date")) for row in rows if row.get("asof_date")})
    source_names = sorted({str(row.get("source")) for row in rows if row.get("source")})
    asof_text = latest_dates[-1] if latest_dates else "—"
    source_text = " · ".join(source_names) if source_names else "market snapshot"
    st.caption(f"시장 데이터: {source_text} · 최신 확인 가능 일봉 · 최근 기준일 {asof_text}")




query_params = st.query_params
if query_params.get("news_view") == "reader":
    render_news_reader()
    st.stop()

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
        st.iframe(
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

            render_home_stock_news(data.get("stock_name", selected_code), selected_code, limit=3)

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

        # 관련 최신 뉴스는 기간 탭/지표 반복문이 모두 끝난 뒤 한 번만 렌더링한다.
        render_home_stock_news(
            data.get("stock_name", selected_code),
            selected_code,
            limit=3,
        )


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
            render_home_live_news_auto(limit=20)
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