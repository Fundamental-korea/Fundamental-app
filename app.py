import json
import random
import FinanceDataReader as fdr
import pandas as pd
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

    div[data-testid="stButton"] > button, div.stButton > button {
        background-color: #FFFFFF !important;
        color: #1A1A1A !important;
        border: 1.5px solid #D1D5DB !important;
        border-radius: 10px !important;
        font-size: 16px !important;
        font-weight: 800 !important;
        box-shadow: 0 2px 5px rgba(0,0,0,0.04) !important;
        transition: all 0.2s ease !important;
    }

    div[data-testid="stButton"] > button:hover, div.stButton > button:hover {
        border-color: #F4A261 !important;
        color: #D97706 !important;
        background-color: #FFFDF9 !important;
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
    </style>
""",
    unsafe_allow_html=True,
)

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


@st.cache_data
def get_combined_stock_db():
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
            {"ticker": "005930", "name": "삼성전자", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
            {"ticker": "000660", "name": "SK하이닉스", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
            {"ticker": "005380", "name": "현대차", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
            {"ticker": "035420", "name": "NAVER", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
            {"ticker": "035720", "name": "카카오", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
        ]

    return us_stocks + kr_stocks


def get_stock_data(code):
    """Supabase에 펀더멘탈 스코어 데이터가 있으면 그걸 우선 사용, 없으면 yfinance로 가격 정보만 폴백"""
    supabase_data = None
    if supabase:
        try:
            res = (
                supabase.table("Fundamental")
                .select("*")
                .eq("stock_code", code)
                .execute()
            )
            if res.data and len(res.data) > 0:
                supabase_data = res.data[0]
        except Exception:
            pass

    ticker_symbol = f"{code}.KS" if code.isdigit() else code
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        hist = ticker.history(period="1y")
    except Exception:
        info, hist = {}, pd.DataFrame()

    result = {
        "stock_name": (supabase_data or {}).get("stock_name") or info.get("shortName", code),
        "stock_price": (supabase_data or {}).get("stock_price") or info.get("currentPrice", 0),
        "hist": hist,
        "info": info,
        "supabase_data": supabase_data,
    }
    return result


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
# 4. 메인 포털 UI & 스케치 기반 상세 분석 리포트
# ==========================================
query_params = st.query_params
selected_code = query_params.get("code", None)

if not selected_code:
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

    left_ad, main_content, right_ad = st.columns([0.6, 6.8, 0.6])

    with left_ad:
        st.markdown(
            "<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True
        )

    with main_content:
        tab1, tab2, tab3, tab4 = st.tabs(
            [
                "US Market Overview",
                "Korea Market Overview",
                "Live News",
                "Gem Screener",
            ]
        )

        combined_stocks_db = get_combined_stock_db()

        with tab1:
            st.markdown(
                "<div style='margin-bottom: 15px;'></div>",
                unsafe_allow_html=True,
            )
            render_unified_search_box(stock_db=combined_stocks_db)
            st.info(
                "🇺🇸 **US Stock Market Overview**: S&P 500, 나스닥 지수 흐름, 섹터별 펀더멘탈 현황 및 매크로 지표 정보 공간입니다."
            )

        with tab2:
            st.markdown(
                "<div style='margin-bottom: 15px;'></div>",
                unsafe_allow_html=True,
            )
            render_unified_search_box(stock_db=combined_stocks_db)
            st.info(
                "🇰🇷 **Korea Stock Market Overview**: 코스피, 코스닥 지수 동향, 외국인/기관 수급 및 국채 금리 현황 정보 공간입니다."
            )

        with tab3:
            st.markdown(
                "<div style='margin-bottom: 15px;'></div>",
                unsafe_allow_html=True,
            )
            render_unified_search_box(stock_db=combined_stocks_db)
            st.info(
                "📰 **Live News**: 글로벌 증시 속보 및 하락장 리스크 관리 뉴스를 실시간으로 모니터링하는 공간입니다."
            )

        with tab4:
            st.markdown(
                "<div style='margin-bottom: 15px;'></div>",
                unsafe_allow_html=True,
            )
            render_unified_search_box(stock_db=combined_stocks_db)
            st.info(
                "💎 **Gem Screener**: ROIC/PER/PBR 펀더멘탈 요건을 모두 충족한 하락장 우수 방어주(Gem) 스크리닝 리스트입니다."
            )

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
    # ==========================================
    # [스케치 기반] 펀더멘탈 상세 분석 리포트 페이지
    # ==========================================
    data = get_stock_data(selected_code)

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

        st.markdown("#### 📉 Live Chart")
        hist_df = data.get("hist", pd.DataFrame())
        if not hist_df.empty:
            st.line_chart(hist_df["Close"])
        else:
            st.info("실시간 차트 데이터를 불러올 수 없습니다.")

        st.markdown("<br>", unsafe_allow_html=True)

        # 10개 지표별 표시용 메타데이터 (scoring.py의 METRIC_KEYS와 정확히 일치)
        METRIC_DISPLAY = {
            "revenue_growth": ("1. Revenue Growth", "매출액 성장률: 기업의 외형 성장세와 하락장 속 시장 점유율 유지 능력을 측정합니다."),
            "eps_growth": ("2. EPS Growth", "순이익 성장률: 주주 가치 창출 능력의 핵심 지표로, 순이익의 실질적 증가세를 평가합니다."),
            "opm": ("3. OPM", "영업이익률: 본업에서의 수익 창출 효율성 및 고금리/원가 상승 방어력을 나타냅니다."),
            "roic": ("4. ROIC", "투하자본이익률: 차입금 레버리지를 배제하고 실제 영업투하자본 대비 순수익 창출력을 평가합니다."),
            "debt_rate": ("5. Debt Rate", "부채비율: 하락장 및 고금리 환경에서 기업의 재무적 생존 가능성과 이자 부담 위험을 평가합니다."),
            "quick_ratio": ("6. Quick Ratio", "당좌비율: 재고자산을 제외한 단기 채무 지급 능력을 측정하여 위기 시 유동성 방어력을 평가합니다."),
            "interest_coverage": ("7. Interest Coverage", "이자보상배율: 영업이익으로 이자비용을 감당할 수 있는 수치로, 채무불이행 위험을 방어합니다."),
            "ocf_ratio": ("8. OCF Ratio", "영업활동현금흐름/순이익 비율: 장부상 이익이 아닌 실제 유입되는 현금 체력의 우수성을 나타냅니다."),
            "sga_ratio": ("9. SG&A Ratio", "판관비율: 매출 대비 판매관리비 비중으로, 기업의 비용 통제 및 경영 효율성을 보여줍니다."),
            "downturn_defense": ("10. Downturn Defense", "하락장 실제 방어력: 과거 주요 하락장(코로나, 2022년 긴축 등)에서 코스피 지수 대비 덜 하락한 정도(%p)를 반영합니다."),
            "roa": ("4-B. ROA (금융업 전용)", "총자산이익률: 금융업종은 레버리지 구조상 ROIC 대신 총자산 대비 순이익 창출력으로 대체 평가합니다."),
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
            if row_missing_count is not None:
                status_pills_html += f'<span class="status-pill neutral">🧩 결측 지표: {row_missing_count}개</span>'

            if status_pills_html:
                st.markdown(f'<div class="row-status-bar">{status_pills_html}</div>', unsafe_allow_html=True)

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
                            sub_badges += f'<span class="mini-stat-badge">🌱 성장 서브스코어 {growth_v}</span>'
                        if defense_v is not None:
                            sub_badges += f'<span class="mini-stat-badge">🛡️ 방어 서브스코어 {defense_v}</span>'
                    if financial_adjusted:
                        sub_badges += '<span class="mini-stat-badge">🏦 금융업 보정 적용</span>'
                    if period_missing_count is not None:
                        sub_badges += f'<span class="mini-stat-badge">🧩 결측 {period_missing_count}개</span>'

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

                    for metric_key in METRIC_ORDER:
                        entry = metric_scores.get(metric_key)
                        if entry is None:
                            # roa는 금융업이 아닌 경우 metric_scores에 아예 없으므로 스킵
                            continue

                        title, desc = METRIC_DISPLAY[metric_key]
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
                        else:
                            value_display = "N/A"

                        # revenue_growth/eps_growth가 N/A인 경우, 데이터가 없는 게 아니라
                        # 전년 기저값이 지나치게 작아 증가율이 왜곡되는 걸 막는 안전장치
                        # (sanitize_growth: |증가율| > 500% 시 None 처리)가 작동한 경우가 대부분.
                        growth_guard_note = ""
                        if value is None and metric_key in ("revenue_growth", "eps_growth"):
                            growth_guard_note = (
                                "<br><span style='font-size:12px; color:#92400E;'>"
                                "ℹ️ 전년 동기 기저값이 너무 작아(또는 흑자전환 등) 증가율이 "
                                "왜곡되는 걸 막기 위해 제외된 값입니다. 데이터 누락이 아닙니다."
                                "</span>"
                            )

                        box_cls = "sketch-item-box excluded" if excluded else "sketch-item-box"
                        if excluded:
                            score_display = "업종 특성상 제외"
                            score_cls = "sketch-item-score excluded"
                        else:
                            score_display = f"{score}/10" if score is not None else "N/A"
                            score_cls = "sketch-item-score"

                        st.markdown(
                            f"""
                            <div class="{box_cls}">
                                <div class="sketch-item-title">{title}</div>
                                <div class="sketch-item-desc">{desc}<br><b>실측값: {value_display}</b>{growth_guard_note}</div>
                                <div class="{score_cls}">{score_display}</div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

    with right_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)
