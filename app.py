import random
import json
import pandas as pd
import FinanceDataReader as fdr
import streamlit as st
import streamlit.components.v1 as components
from supabase import create_client
import yfinance as yf

# ==========================================
# 1. 페이지 및 커스텀 디자인 설정
# ==========================================
st.set_page_config(
    page_title="Fundamental Analyzer - 하락장 방어 플랫폼",
    page_icon="🛡️",
    layout="wide",
)

st.markdown(
    """
    <style>
    /* 전체 배경 */
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

    /* 상단 헤더 */
    .logo-box {
        border: 2px solid #F4A261;
        border-radius: 14px;
        background-color: #FFFDF9;
        color: #D97706 !important;
        font-weight: bold;
        font-size: 18px;
        height: 130px !important;
        min-height: 130px !important;
        display: flex;
        align-items: center;
        justify-content: center;
        text-align: center;
        box-shadow: 0 3px 10px rgba(0,0,0,0.03);
    }

    .quote-box {
        background-color: #FAFAFA;
        border: 1.5px solid #E5E5E5;
        border-radius: 14px;
        height: 130px !important;
        min-height: 130px !important;
        display: flex;
        flex-direction: row;
        align-items: center;
        justify-content: center;
        gap: 20px;
        padding: 0 30px;
        box-shadow: 0 3px 10px rgba(0,0,0,0.02);
    }

    .quote-text {
        font-size: 18px;
        font-weight: 600;
        color: #333333 !important;
    }

    /* 좌우 Ads */
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

    /* ========================================================= */
    /* 💥 탭 강력 스타일 재정의 (간격 60px+, 폰트 22px, Bold 900) */
    /* ========================================================= */
    div[data-baseweb="tab-list"] {
        display: flex !important;
        justify-content: space-around !important;
        align-items: center !important;
        gap: 50px !important;
        width: 100% !important;
        margin-bottom: 25px !important;
        border-bottom: 2px solid #E5E5E5 !important;
        padding-bottom: 10px !important;
    }

    div[data-baseweb="tab"] {
        height: 55px !important;
        padding: 0 20px !important;
        margin: 0 10px !important;
        background-color: transparent !important;
        border: none !important;
        outline: none !important;
        cursor: pointer !important;
    }

    div[data-baseweb="tab"] p {
        font-size: 22px !important; /* 글자 크기 훨씬 더 크게 */
        font-weight: 900 !important; /* 엄청 굵게 */
        color: #555555 !important;
        letter-spacing: -0.5px !important;
        white-space: nowrap !important;
    }

    /* 선택된 탭 스타일 */
    div[data-baseweb="tab"][aria-selected="true"] {
        border-bottom: 4px solid #F4A261 !important;
    }

    div[data-baseweb="tab"][aria-selected="true"] p {
        color: #D97706 !important;
        font-weight: 900 !important;
        transform: scale(1.05); /* 선택된 탭 살짝 확대 */
        transition: transform 0.2s ease;
    }

    /* 하단 트렌드 카드 */
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

    div.stButton > button {
        background-color: #F4A261 !important;
        color: #FFFFFF !important;
        border: none !important;
        font-weight: bold !important;
        border-radius: 10px !important;
        height: 48px !important;
    }
    </style>
""",
    unsafe_allow_html=True,
)

# ==========================================
# 2. 데이터 및 유틸리티 설정
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


@st.cache_data
def get_combined_stock_db():
    us_stocks = [
        {
            "ticker": "AAPL",
            "name": "Apple Inc.",
            "exch": "Equities - NASDAQ",
            "flag": "🇺🇸",
        },
        {
            "ticker": "NVDA",
            "name": "NVIDIA Corporation",
            "exch": "Equities - NASDAQ",
            "flag": "🇺🇸",
        },
        {
            "ticker": "TSLA",
            "name": "Tesla Inc.",
            "exch": "Equities - NASDAQ",
            "flag": "🇺🇸",
        },
        {
            "ticker": "MSFT",
            "name": "Microsoft Corp.",
            "exch": "Equities - NASDAQ",
            "flag": "🇺🇸",
        },
        {
            "ticker": "AMZN",
            "name": "Amazon.com Inc.",
            "exch": "Equities - NASDAQ",
            "flag": "🇺🇸",
        },
        {
            "ticker": "GOOGL",
            "name": "Alphabet Inc.",
            "exch": "Equities - NASDAQ",
            "flag": "🇺🇸",
        },
        {
            "ticker": "META",
            "name": "Meta Platforms Inc.",
            "exch": "Equities - NASDAQ",
            "flag": "🇺🇸",
        },
        {
            "ticker": "PLTR",
            "name": "Palantir Technologies",
            "exch": "Equities - NYSE",
            "flag": "🇺🇸",
        },
        {
            "ticker": "P",
            "name": "Pure Storage Inc",
            "exch": "Equities - NYSE",
            "flag": "🇺🇸",
        },
    ]

    kr_stocks = []
    try:
        df = fdr.StockListing("KRX")
        for _, row in df.iterrows():
            market = row.get("Market", "KOSPI")
            kr_stocks.append(
                {
                    "ticker": str(row["Code"]),
                    "name": str(row["Name"]),
                    "exch": f"Equities - {market}",
                    "flag": "🇰🇷",
                }
            )
    except Exception:
        kr_stocks = [
            {
                "ticker": "005930",
                "name": "삼성전자",
                "exch": "Equities - KOSPI",
                "flag": "🇰🇷",
            },
            {
                "ticker": "000660",
                "name": "SK하이닉스",
                "exch": "Equities - KOSPI",
                "flag": "🇰🇷",
            },
            {
                "ticker": "005380",
                "name": "현대차",
                "exch": "Equities - KOSPI",
                "flag": "🇰🇷",
            },
            {
                "ticker": "035420",
                "name": "NAVER",
                "exch": "Equities - KOSPI",
                "flag": "🇰🇷",
            },
            {
                "ticker": "035720",
                "name": "카카오",
                "exch": "Equities - KOSPI",
                "flag": "🇰🇷",
            },
        ]

    return us_stocks + kr_stocks


def get_stock_data(code):
    if supabase:
        try:
            res = (
                supabase.table("Fundamental")
                .select("*")
                .eq("stock_code", code)
                .execute()
            )
            if res.data and len(res.data) > 0:
                return res.data[0]
        except Exception:
            pass

    ticker_symbol = f"{code}.KS" if code.isdigit() else code
    try:
        info = yf.Ticker(ticker_symbol).info
        return {
            "stock_name": info.get("shortName", code),
            "stock_price": info.get("currentPrice", 0),
            "eps": info.get("trailingEps", 0),
            "bps": info.get("bookValue", 0),
            "roe": (
                round(info.get("returnOnEquity", 0) * 100, 2)
                if info.get("returnOnEquity")
                else 0.0
            ),
            "per": info.get("trailingPE", 0.0),
            "pbr": info.get("priceToBook", 0.0),
        }
    except Exception:
        return {
            "stock_name": f"종목({code})",
            "stock_price": 0,
            "eps": 0,
            "bps": 0,
            "roe": 0.0,
            "per": 0.0,
            "pbr": 0.0,
        }


def calculate_defense_score(data):
    score = 50
    reasons = []

    roe = data.get("roe", 0) or 0
    per = data.get("per", 0) or 0
    pbr = data.get("pbr", 0) or 0

    if roe >= 15:
        score += 20
        reasons.append("• ROE가 15% 이상으로 높은 수익성을 유지하고 있습니다.")
    elif roe >= 8:
        score += 10
        reasons.append("• ROE가 준수한 수준을 유지하고 있습니다.")
    else:
        reasons.append("• ROE 수익성 개선이 필요합니다.")

    if 0 < per <= 12:
        score += 15
        reasons.append("• PER이 낮아 밸류에이션 저평가 구간입니다.")
    elif per > 30:
        score -= 10
        reasons.append(
            "• PER이 높아 시장 기대감이 과도하게 반영되었을 수 있습니다."
        )

    if 0 < pbr <= 1.2:
        score += 15
        reasons.append(
            "• PBR 1.2배 이하로 하락장 청산가치 방어력이 우수합니다."
        )

    score = max(0, min(100, score))

    if score >= 85:
        grade = "S"
    elif score >= 70:
        grade = "A"
    elif score >= 55:
        grade = "B"
    elif score >= 40:
        grade = "C"
    else:
        grade = "D"

    return score, grade, reasons


# ==========================================
# 3. 미국/한국 주식 통합 실시간 검색 컴포넌트
# ==========================================
def render_unified_search_box(stock_db):
    json_db = json.dumps(stock_db, ensure_ascii=False)

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
                border: 2px solid #F4A261;
                border-radius: 12px;
                font-size: 16px;
                font-weight: 600;
                outline: none;
                background: #FFFDF9;
                color: #1A1A1A;
                box-shadow: 0 4px 12px rgba(244, 162, 97, 0.15);
            }}
            .input-box:focus {{
                border-color: #D97706;
                box-shadow: 0 0 10px rgba(217, 119, 6, 0.25);
            }}
            .search-icon {{
                position: absolute;
                right: 18px;
                top: 15px;
                font-size: 20px;
                color: #D97706;
                cursor: pointer;
            }}

            .autocomplete-modal {{
                display: none;
                flex-direction: column;
                position: absolute;
                top: 60px;
                left: 0;
                width: 100%;
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
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
                border-right: 1px solid #F1F5F9;
                padding: 10px 0;
                max-height: 360px;
                overflow-y: auto;
            }}
            .pane-title {{
                font-size: 12px;
                font-weight: 700;
                color: #64748B;
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
                color: #0F172A;
                font-size: 14px;
                min-width: 65px;
            }}
            .name {{
                font-size: 13px;
                color: #475569;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .exch {{
                font-size: 11px;
                color: #94A3B8;
                white-space: nowrap;
            }}
            .highlight {{
                color: #D97706;
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
                color: #D97706;
            }}

            .modal-footer {{
                border-top: 1px solid #F1F5F9;
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
                background: #F1F5F9;
            }}
        </style>
    </head>
    <body>
        <div class="search-wrapper">
            <input 
                type="text" 
                id="unified_search_input" 
                class="input-box" 
                placeholder="🔍 미국/한국 주식 종목명 또는 티커 입력 (예: NVDA, AAPL, 삼성전자, 005930)"
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
                            <div style="margin-top: 8px;" class="news-item">고금리 장기화에 따른 ROE/PBR 체력 점검</div>
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

            function renderList(query) {{
                const q = query.trim().toLowerCase();
                
                if (!q) {{
                    modalEl.style.display = 'none';
                    return;
                }}

                modalEl.style.display = 'flex';
                footerQueryEl.innerText = q;

                const filtered = STOCKS.filter(s => 
                    s.ticker.toLowerCase().includes(q) || 
                    s.name.toLowerCase().includes(q)
                );

                if (filtered.length === 0) {{
                    listEl.innerHTML = '<div style="padding:15px; font-size:13px; color:#94A3B8;">일치하는 종목이 없습니다.</div>';
                    return;
                }}

                let html = '';
                filtered.slice(0, 30).forEach((item, idx) => {{
                    const highlightTicker = highlightMatch(item.ticker, q);
                    const highlightName = highlightMatch(item.name, q);
                    html += `
                        <div class="stock-row ${{idx === 0 ? 'active' : ''}}" onclick="selectStock('${{item.ticker}}')">
                            <div class="stock-info">
                                <span class="flag">${{item.flag}}</span>
                                <span class="ticker">${{highlightTicker}}</span>
                                <span class="name">${{highlightName}}</span>
                            </div>
                            <span class="exch">${{item.exch}}</span>
                        </div>
                    `;
                }});
                listEl.innerHTML = html;
            }}

            function highlightMatch(text, query) {{
                if (!query) return text;
                const reg = new RegExp(`(${{query}})`, 'gi');
                return text.replace(reg, '<span class="highlight">$1</span>');
            }}

            function selectStock(ticker) {{
                const targetUrl = window.parent.location.origin + window.parent.location.pathname + '?code=' + encodeURIComponent(ticker);
                window.open(targetUrl, '_blank');
            }}

            function triggerSearch() {{
                const q = inputEl.value.trim();
                if (!q) return;

                const filtered = STOCKS.filter(s => 
                    s.ticker.toLowerCase().includes(q.toLowerCase()) || 
                    s.name.toLowerCase().includes(q.toLowerCase())
                );
                const targetCode = filtered.length > 0 ? filtered[0].ticker : q;
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
# 4. 메인 포털 UI
# ==========================================
query_params = st.query_params
selected_code = query_params.get("code", None)

if not selected_code:
    # --- [상단 헤더]: Logo | Investor Quote | Log in ---
    col_logo, col_quote, col_login = st.columns([1.0, 6.8, 1.0])

    with col_logo:
        st.markdown(
            "<div class='logo-box'>📈 Fundamental</div>",
            unsafe_allow_html=True,
        )

    with col_quote:
        quotes = [
            "하락장은 우량한 기업을 헐값에 살 수 있는 가장 위대한 기회다.",
            "시장이 공포에 질려 있을 때가 탐욕을 부릴 최적의 시기다.",
            "투자는 지능이 아니라 인내심의 게임이다.",
            "가격은 내가 지불하는 것이고, 가치는 내가 얻는 것이다.",
        ]
        selected_quote = random.choice(quotes)

        st.markdown(
            f"""
            <div class='quote-box'>
                <div style='font-size: 36px;'>👨‍💼</div> 
                <div class='quote-text'>"{selected_quote}"</div>
            </div>
        """,
            unsafe_allow_html=True,
        )

    with col_login:
        st.markdown(
            "<div style='height: 40px;'></div>", unsafe_allow_html=True
        )
        if st.button("Log in", use_container_width=True):
            st.toast("로그인 기능 준비 중입니다!")

    st.markdown("<br>", unsafe_allow_html=True)

    # --- [본문 레이아웃]: Ads (Left) | Center Main | Ads (Right) ---
    left_ad, main_content, right_ad = st.columns([0.6, 6.8, 0.6])

    with left_ad:
        st.markdown(
            "<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True
        )

    with main_content:
        # 1. 상단 탭 (숫자 없이, 크게, 볼드로 넓게 떨어진 배치)
        tab1, tab2, tab3, tab4 = st.tabs(
            [
                "US Market Overview",
                "Korea Market Overview",
                "Live News",
                "Gem Screener",
            ]
        )

        # 2. 통합 검색창
        combined_stocks_db = get_combined_stock_db()

        with tab1:
            render_unified_search_box(stock_db=combined_stocks_db)
            st.info(
                "🇺🇸 **US Stock Market Overview**: S&P 500, 나스닥 지수 흐름, 섹터별 펀더멘탈 현황 및 매크로 지표 정보 공간입니다."
            )

        with tab2:
            render_unified_search_box(stock_db=combined_stocks_db)
            st.info(
                "🇰🇷 **Korea Stock Market Overview**: 코스피, 코스닥 지수 동향, 외국인/기관 수급 및 국채 금리 현황 정보 공간입니다."
            )

        with tab3:
            render_unified_search_box(stock_db=combined_stocks_db)
            st.info(
                "📰 **Live News**: 글로벌 증시 속보 및 하락장 리스크 관리 뉴스를 실시간으로 모니터링하는 공간입니다."
            )

        with tab4:
            render_unified_search_box(stock_db=combined_stocks_db)
            st.info(
                "💎 **Gem Screener**: ROE/PER/PBR 펀더멘탈 요건을 모두 충족한 하락장 우수 방어주(Gem) 스크리닝 리스트입니다."
            )

        # --- [하단 트렌드 추천 카드] ---
        st.markdown(
            "<div class='bottom-cards-wrapper'>", unsafe_allow_html=True
        )
        col_6, col_7, col_8 = st.columns(3)

        with col_6:
            st.markdown(
                """
                <div class='sketch-card'>
                    <b style='color: #1A1A1A; font-size: 15px;'>🔥 Most Searched Stocks</b>
                    <div style='margin-top: 12px;'>
                        <div class='card-item-row'>
                            <a href='/?code=005930' target='_blank' class='stock-link'>1. 삼성전자 (005930)</a>
                            <span class='search-count-badge'>18,420회</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=NVDA' target='_blank' class='stock-link'>2. NVIDIA (NVDA)</a>
                            <span class='search-count-badge'>15,810회</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=000660' target='_blank' class='stock-link'>3. SK하이닉스 (000660)</a>
                            <span class='search-count-badge'>12,340회</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=AAPL' target='_blank' class='stock-link'>4. Apple (AAPL)</a>
                            <span class='search-count-badge'>9,580회</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=TSLA' target='_blank' class='stock-link'>5. Tesla (TSLA)</a>
                            <span class='search-count-badge'>8,210회</span>
                        </div>
                    </div>
                </div>
            """,
                unsafe_allow_html=True,
            )

        with col_7:
            st.markdown(
                """
                <div class='sketch-card'>
                    <b style='color: #1A1A1A; font-size: 15px;'>🇺🇸 Trending Searches (US)</b>
                    <div style='margin-top: 12px;'>
                        <div class='card-item-row'>
                            <a href='/?code=NVDA' target='_blank' class='stock-link'>1. NVIDIA (NVDA)</a>
                            <span style='font-size: 11px; color: #16A34A; font-weight: 700;'>▲ HOT</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=AAPL' target='_blank' class='stock-link'>2. Apple (AAPL)</a>
                            <span style='font-size: 11px; color: #16A34A; font-weight: 700;'>▲ 2</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=TSLA' target='_blank' class='stock-link'>3. Tesla (TSLA)</a>
                            <span style='font-size: 11px; color: #DC2626; font-weight: 700;'>▼ 1</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=PLTR' target='_blank' class='stock-link'>4. Palantir (PLTR)</a>
                            <span style='font-size: 11px; color: #16A34A; font-weight: 700;'>▲ NEW</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=MSFT' target='_blank' class='stock-link'>5. Microsoft (MSFT)</a>
                            <span style='font-size: 11px; color: #64748B; font-weight: 700;'>-</span>
                        </div>
                    </div>
                </div>
            """,
                unsafe_allow_html=True,
            )

        with col_8:
            st.markdown(
                """
                <div class='sketch-card'>
                    <b style='color: #1A1A1A; font-size: 15px;'>🇰🇷 Trending Searches (KOR)</b>
                    <div style='margin-top: 12px;'>
                        <div class='card-item-row'>
                            <a href='/?code=005930' target='_blank' class='stock-link'>1. 삼성전자 (005930)</a>
                            <span style='font-size: 11px; color: #16A34A; font-weight: 700;'>▲ HOT</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=000660' target='_blank' class='stock-link'>2. SK하이닉스 (000660)</a>
                            <span style='font-size: 11px; color: #16A34A; font-weight: 700;'>▲ 1</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=005380' target='_blank' class='stock-link'>3. 현대차 (005380)</a>
                            <span style='font-size: 11px; color: #64748B; font-weight: 700;'>-</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=035420' target='_blank' class='stock-link'>4. NAVER (035420)</a>
                            <span style='font-size: 11px; color: #16A34A; font-weight: 700;'>▲ 3</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=035720' target='_blank' class='stock-link'>5. 카카오 (035720)</a>
                            <span style='font-size: 11px; color: #DC2626; font-weight: 700;'>▼ 2</span>
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
    # --- [상세 분석 리포트 페이지] ---
    if st.button("⬅️ 창 닫기 / 메인으로 돌아가기"):
        st.query_params.clear()
        st.rerun()

    st.markdown("### 🛡️ 펀더멘탈 방어력 상세 분석 리포트")
    st.markdown(
        "<hr style='border: 1px solid #F4A261;'>", unsafe_allow_html=True
    )

    data = get_stock_data(selected_code)

    if data:
        stock_name = data.get("stock_name", selected_code)
        is_krx = selected_code.isdigit() and len(selected_code) == 6

        if is_krx:
            krx_stocks = get_combined_stock_db()
            market_type = "KOSPI"
            for v in krx_stocks:
                if v["ticker"] == selected_code:
                    market_type = v["exch"].replace("Equities - ", "")
                    break
            ticker_symbol = (
                f"{selected_code}.KQ"
                if market_type == "KOSDAQ"
                else f"{selected_code}.KS"
            )
        else:
            ticker_symbol = selected_code.upper()

        try:
            yticker = yf.Ticker(ticker_symbol)
            fast_info = yticker.fast_info
            live_price = (
                int(fast_info.last_price)
                if fast_info
                and hasattr(fast_info, "last_price")
                and fast_info.last_price
                else data.get("stock_price", 0)
            )
        except Exception:
            live_price = data.get("stock_price", 0)

        db_eps = data.get("eps")
        live_per = (
            round(live_price / db_eps, 2)
            if db_eps and db_eps > 0
            else data.get("per")
        )

        db_bps = data.get("bps")
        live_pbr = (
            round(live_price / db_bps, 2)
            if db_bps and db_bps > 0
            else data.get("pbr")
        )

        currency_unit = "원" if is_krx else "$"
        st.subheader(
            f"📊 [{stock_name}] ({selected_code}) 실시간 지표 분석"
        )

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("현재 실시간 주가", f"{live_price:,} {currency_unit}")
        col2.metric("PER", f"{live_per} 배" if live_per else "N/A")
        col3.metric("PBR", f"{live_pbr} 배" if live_pbr else "N/A")
        col4.metric(
            "ROE", f"{data.get('roe')}%" if data.get("roe") else "N/A"
        )

        st.divider()

        eval_data = data.copy()
        eval_data["per"] = live_per
        eval_data["pbr"] = live_pbr

        total_score, grade, reasons = calculate_defense_score(eval_data)

        col_score, col_detail = st.columns([1, 2])
        with col_score:
            st.markdown("### 🛡️ 하락장 방어 종합 등급")
            st.title(f"**{total_score}**점 / [{grade}] 등급")
            if grade == "S":
                st.success("🟢 **S등급 (요새형 최우수 방어주)**")
            elif grade == "A":
                st.success("🟢 **A등급 (우수한 재무 체력)**")
            elif grade == "B":
                st.info("🟡 **B등급 (평균적 방어력)**")
            elif grade == "C":
                st.warning("🟠 **C등급 (하락장 주의 필요)**")
            else:
                st.warning("🔴 **D등급 (하락장 취약 위험주)**")

        with col_detail:
            st.markdown("### 📋 핵심 지표 평가 내역")
            for r in reasons:
                st.write(r)
    else:
        st.error("선택한 종목의 데이터를 찾을 수 없습니다.")
