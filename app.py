import streamlit as st
import FinanceDataReader as fdr
from supabase import create_client
import pandas as pd
import random
import yfinance as yf
import streamlit.components.v1 as components

# ==========================================
# 1. 페이지 및 커스텀 디자인 설정
# ==========================================
st.set_page_config(
    page_title="Fundamental Analyzer - 하락장 방어 플랫폼", 
    page_icon="🛡️", 
    layout="wide"
)

st.markdown("""
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
        min-height: 520px;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        box-sizing: border-box;
    }

    /* 탭 스타일 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 20px !important;
        justify-content: center !important;
        margin-bottom: 10px !important;
    }
    .stTabs [data-baseweb="tab"] {
        height: 46px !important;
        min-width: 140px !important;
        background-color: #FAFAFA !important;
        border-radius: 10px !important;
        border: 1.5px solid #E5E5E5 !important;
        justify-content: center !important;
    }
    .stTabs [data-baseweb="tab"] p {
        color: #555555 !important;
        font-weight: 600 !important;
        font-size: 15px !important;
    }
    .stTabs [aria-selected="true"] {
        background-color: #FFFDF9 !important;
        border: 2px solid #F4A261 !important;
    }
    .stTabs [aria-selected="true"] p {
        color: #D97706 !important;
        font-weight: bold !important;
    }

    /* 하단 트렌드 카드 */
    .bottom-cards-wrapper {
        margin-top: 10px;
    }
    .sketch-card {
        background-color: #FAFAFA;
        border: 1.5px solid #E5E5E5;
        border-radius: 12px;
        padding: 24px;
        height: 220px;
        box-shadow: 0 4px 10px rgba(0,0,0,0.02);
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
""", unsafe_allow_html=True)

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
def get_krx_stocks():
    try:
        df = fdr.StockListing("KRX")
        stocks = {}
        for _, row in df.iterrows():
            market = row.get("Market", "KOSPI")
            stocks[f"{row['Name']} ({row['Code']})"] = {
                "code": str(row["Code"]),
                "market": market
            }
        return stocks
    except Exception:
        return {
            "삼성전자 (005930)": {"code": "005930", "market": "KOSPI"},
            "SK하이닉스 (000660)": {"code": "000660", "market": "KOSPI"},
            "현대차 (005380)": {"code": "005380", "market": "KOSPI"}
        }

def get_stock_data(code):
    if supabase:
        try:
            res = supabase.table("Fundamental").select("*").eq("stock_code", code).execute()
            if res.data and len(res.data) > 0:
                return res.data[0]
        except Exception:
            pass
    
    ticker_symbol = f"{code}.KS" if code.isdigit() else code
    try:
        info = yf.Ticker(ticker_symbol).info
        return {
            'stock_name': info.get('shortName', code),
            'stock_price': info.get('currentPrice', 0),
            'eps': info.get('trailingEps', 0),
            'bps': info.get('bookValue', 0),
            'roe': round(info.get('returnOnEquity', 0) * 100, 2) if info.get('returnOnEquity') else 0.0,
            'per': info.get('trailingPE', 0.0),
            'pbr': info.get('priceToBook', 0.0)
        }
    except Exception:
        return {'stock_name': f'종목({code})', 'stock_price': 0, 'eps': 0, 'bps': 0, 'roe': 0.0, 'per': 0.0, 'pbr': 0.0}

def calculate_defense_score(data):
    score = 50
    reasons = []
    
    roe = data.get('roe', 0) or 0
    per = data.get('per', 0) or 0
    pbr = data.get('pbr', 0) or 0
    
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
        reasons.append("• PER이 높아 시장 기대감이 과도하게 반영되었을 수 있습니다.")
        
    if 0 < pbr <= 1.2:
        score += 15
        reasons.append("• PBR 1.2배 이하로 하락장 청산가치 방어력이 우수합니다.")
        
    score = max(0, min(100, score))
    
    if score >= 85: grade = 'S'
    elif score >= 70: grade = 'A'
    elif score >= 55: grade = 'B'
    elif score >= 40: grade = 'C'
    else: grade = 'D'
    
    return score, grade, reasons

# ==========================================
# 3. 인베스팅닷컴 스타일 동적 검색 컴포넌트
# ==========================================
def render_investing_search_box(stock_db, placeholder_text, key_prefix):
    import json
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
                height: 48px;
                padding: 0 45px 0 16px;
                border: 2px solid #F4A261;
                border-radius: 8px;
                font-size: 15px;
                font-weight: 600;
                outline: none;
                background: #FFFDF9;
                color: #1A1A1A;
            }}
            .input-box:focus {{
                border-color: #D97706;
                box-shadow: 0 0 8px rgba(217, 119, 6, 0.2);
            }}
            .search-icon {{
                position: absolute;
                right: 15px;
                top: 13px;
                font-size: 18px;
                color: #D97706;
                cursor: pointer;
            }}

            /* 검색어 입력 시에만 나타나는 팝업 레이어 */
            .autocomplete-modal {{
                display: none; /* 기본 상태: 숨김 */
                flex-direction: column;
                position: absolute;
                top: 54px;
                left: 0;
                width: 100%;
                background: #FFFFFF;
                border: 1px solid #E2E8F0;
                border-radius: 8px;
                box-shadow: 0 10px 25px rgba(0,0,0,0.15);
                z-index: 9999;
                overflow: hidden;
            }}

            .modal-content {{
                display: flex;
                min-height: 300px;
            }}

            /* 좌측: 실시간 연관 검색 목록 (65%) */
            .left-pane {{
                flex: 65;
                border-right: 1px solid #F1F5F9;
                padding: 10px 0;
                max-height: 340px;
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

            /* 우측: 뉴스 및 분석 탭 (35%) */
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

            /* 하단 검색 실행 바 */
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
                id="{key_prefix}_input" 
                class="input-box" 
                placeholder="{placeholder_text}"
                autocomplete="off"
            />
            <span class="search-icon" onclick="triggerSearch()">🔍</span>

            <div id="{key_prefix}_modal" class="autocomplete-modal">
                <div class="modal-content">
                    <div class="left-pane">
                        <div id="{key_prefix}_title" class="pane-title">Matching Instruments</div>
                        <div id="{key_prefix}_list"></div>
                    </div>
                    <div class="right-pane">
                        <div>
                            <div class="section-header">
                                <span>News</span>
                                <a href="#" class="more-link">More</a>
                            </div>
                            <div style="margin-top: 6px;" class="news-item">S&P 500 하락장 대비 안전자산 및 고배당 저평가 펀더멘탈 분석</div>
                            <div style="margin-top: 8px;" class="news-item">금리 변동성에 따른 우량주 ROE/PBR 체력 점검</div>
                        </div>
                        <div>
                            <div class="section-header">
                                <span>Analysis</span>
                                <a href="#" class="more-link">More</a>
                            </div>
                            <div style="margin-top: 6px;" class="news-item">2026 하락장 방어력이 가장 뛰어난 S등급 기업 리스트</div>
                        </div>
                    </div>
                </div>
                <div class="modal-footer" onclick="triggerSearch()">
                    <span>🔍</span> Search website for: <span id="{key_prefix}_footer_query" style="font-weight:700;"></span>
                </div>
            </div>
        </div>

        <script>
            const STOCKS = {json_db};
            const inputEl = document.getElementById('{key_prefix}_input');
            const modalEl = document.getElementById('{key_prefix}_modal');
            const listEl = document.getElementById('{key_prefix}_list');
            const footerQueryEl = document.getElementById('{key_prefix}_footer_query');

            function renderList(query) {{
                const q = query.trim().toLowerCase();
                
                // 입력값이 없으면 레이어 닫기
                if (!q) {{
                    modalEl.style.display = 'none';
                    return;
                }}

                // 입력값이 있을 때만 레이어 표시
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
                filtered.forEach((item, idx) => {{
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

            // 바깥 영역 클릭 시 드롭다운 닫기
            document.addEventListener('click', (e) => {{
                if (!e.target.closest('.search-wrapper')) {{
                    modalEl.style.display = 'none';
                }}
            }});
        </script>
    </body>
    </html>
    """
    components.html(custom_html, height=450)

# ==========================================
# 4. 메인 포털 UI
# ==========================================
query_params = st.query_params
selected_code = query_params.get("code", None)

if not selected_code:
    # --- [상단 헤더]: Logo | Investor Quote | Log in ---
    col_logo, col_quote, col_login = st.columns([1.0, 6.8, 1.0])
    
    with col_logo:
        st.markdown("<div class='logo-box'>📈 Fundamental</div>", unsafe_allow_html=True)
        
    with col_quote:
        quotes = [
            "하락장은 우량한 기업을 헐값에 살 수 있는 가장 위대한 기회다.",
            "시장이 공포에 질려 있을 때가 탐욕을 부릴 최적의 시기다.",
            "투자는 지능이 아니라 인내심의 게임이다.",
            "가격은 내가 지불하는 것이고, 가치는 내가 얻는 것이다."
        ]
        selected_quote = random.choice(quotes)
        
        st.markdown(f"""
            <div class='quote-box'>
                <div style='font-size: 36px;'>👨‍💼</div> 
                <div class='quote-text'>"{selected_quote}"</div>
            </div>
        """, unsafe_allow_html=True)
        
    with col_login:
        st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
        if st.button("Log in", use_container_width=True):
            st.toast("로그인 기능 준비 중입니다!")

    st.markdown("<br>", unsafe_allow_html=True)

    # --- [본문 레이아웃]: Ads (Left) | Center Main | Ads (Right) ---
    left_ad, main_content, right_ad = st.columns([0.6, 6.8, 0.6])

    with left_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

    with main_content:
        tab1, tab2, tab3, tab4 = st.tabs(["1. Us stock", "2. Korea stock", "3. Live news", "4. Gem"])
        
        # 1. 미국 주식 실시간 반응형 검색
        with tab1:
            us_stocks_db = [
                {"ticker": "P", "name": "Pure Storage Inc", "exch": "Equities - NYSE", "flag": "🇺🇸"},
                {"ticker": "AMPH", "name": "Amphastar Pharmaceuticals", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
                {"ticker": "AAPL", "name": "Apple Inc.", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
                {"ticker": "NVDA", "name": "NVIDIA Corporation", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
                {"ticker": "TSLA", "name": "Tesla Inc.", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
                {"ticker": "MSFT", "name": "Microsoft Corp.", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
                {"ticker": "AMZN", "name": "Amazon.com Inc.", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
                {"ticker": "GOOGL", "name": "Alphabet Inc.", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
                {"ticker": "META", "name": "Meta Platforms Inc.", "exch": "Equities - NASDAQ", "flag": "🇺🇸"},
                {"ticker": "PLTR", "name": "Palantir Technologies", "exch": "Equities - NYSE", "flag": "🇺🇸"}
            ]
            render_investing_search_box(
                stock_db=us_stocks_db,
                placeholder_text="🔍 미국 주식 Ticker 또는 종목명 입력 (예: P, AAPL, NVDA)",
                key_prefix="us_investing_search"
            )
            
        # 2. 한국 주식 실시간 반응형 검색 ('삼', '하' 입력 시 즉시 반응)
        with tab2:
            krx_dict = get_krx_stocks()
            kr_stocks_db = []
            if krx_dict:
                for name_code, info in list(krx_dict.items())[:300]:
                    name_only = name_code.split(" (")[0]
                    kr_stocks_db.append({
                        "ticker": info["code"],
                        "name": name_only,
                        "exch": f"Equities - {info['market']}",
                        "flag": "🇰🇷"
                    })
            else:
                kr_stocks_db = [
                    {"ticker": "005930", "name": "삼성전자", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "000660", "name": "SK하이닉스", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "005380", "name": "현대차", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "035420", "name": "NAVER", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "035720", "name": "카카오", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "000270", "name": "기아", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "005490", "name": "POSCO홀딩스", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "006400", "name": "삼성SDI", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "207940", "name": "삼성바이오로직스", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "010140", "name": "삼성중공업", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "086790", "name": "하나금융지주", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "012330", "name": "현대모비스", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "042660", "name": "한화오션", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "009830", "name": "한화솔루션", "exch": "Equities - KOSPI", "flag": "🇰🇷"},
                    {"ticker": "000810", "name": "삼성화재", "exch": "Equities - KOSPI", "flag": "🇰🇷"}
                ]

            render_investing_search_box(
                stock_db=kr_stocks_db,
                placeholder_text="🔍 한국 주식명 또는 6자리 코드 입력 (예: 삼성전자, 005930)",
                key_prefix="kr_investing_search"
            )
            
        with tab3:
            st.info("📰 실시간 증시 속보 및 주요 뉴스 모니터링 준비 중입니다.")
            
        with tab4:
            st.info("💎 하락장 우수 저평가 종목(Gem) 스크리너 준비 중입니다.")

        # 하단 트렌드 카드
        st.markdown("<div class='bottom-cards-wrapper'>", unsafe_allow_html=True)
        col_6, col_7, col_8 = st.columns(3)
        
        with col_6:
            st.markdown("""
                <div class='sketch-card'>
                    <b style='color: #1A1A1A;'>Most searched Stocks</b><br><br>
                    • <a href='/?code=005930' target='_blank' style='color: #D97706; text-decoration: none; font-weight: 600;'>삼성전자 (005930)</a><br><br>
                    • <a href='/?code=000660' target='_blank' style='color: #D97706; text-decoration: none; font-weight: 600;'>SK하이닉스 (000660)</a>
                </div>
            """, unsafe_allow_html=True)
            
        with col_7:
            st.markdown("""
                <div class='sketch-card'>
                    <b style='color: #1A1A1A;'>Trending Searches (US)</b><br><br>
                    • <a href='/?code=AAPL' target='_blank' style='color: #D97706; text-decoration: none; font-weight: 600;'>Apple (AAPL)</a><br><br>
                    • <a href='/?code=NVDA' target='_blank' style='color: #D97706; text-decoration: none; font-weight: 600;'>NVIDIA (NVDA)</a><br><br>
                    • <a href='/?code=TSLA' target='_blank' style='color: #D97706; text-decoration: none; font-weight: 600;'>Tesla (TSLA)</a>
                </div>
            """, unsafe_allow_html=True)
            
        with col_8:
            st.markdown("""
                <div class='sketch-card'>
                    <b style='color: #1A1A1A;'>Trending Searches (KOR)</b><br><br>
                    • <a href='/?code=005380' target='_blank' style='color: #D97706; text-decoration: none; font-weight: 600;'>현대차 (005380)</a><br><br>
                    • <a href='/?code=000270' target='_blank' style='color: #D97706; text-decoration: none; font-weight: 600;'>기아 (000270)</a>
                </div>
            """, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with right_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

else:
    # --- [상세 분석 리포트 페이지] ---
    if st.button("⬅️ 창 닫기 / 메인으로 돌아가기"):
        st.query_params.clear()
        st.rerun()

    st.markdown("### 🛡️ 펀더멘탈 방어력 상세 분석 리포트")
    st.markdown("<hr style='border: 1px solid #F4A261;'>", unsafe_allow_html=True)
    
    data = get_stock_data(selected_code)

    if data:
        stock_name = data.get('stock_name', selected_code)
        is_krx = selected_code.isdigit() and len(selected_code) == 6
        
        if is_krx:
            krx_stocks = get_krx_stocks()
            market_type = "KOSPI"
            for k, v in krx_stocks.items():
                if v["code"] == selected_code:
                    market_type = v["market"]
                    break
            ticker_symbol = f"{selected_code}.KQ" if market_type == "KOSDAQ" else f"{selected_code}.KS"
        else:
            ticker_symbol = selected_code.upper()

        try:
            yticker = yf.Ticker(ticker_symbol)
            fast_info = yticker.fast_info
            live_price = int(fast_info.last_price) if fast_info and hasattr(fast_info, 'last_price') and fast_info.last_price else data.get('stock_price', 0)
        except Exception:
            live_price = data.get('stock_price', 0)

        db_eps = data.get('eps')
        live_per = round(live_price / db_eps, 2) if db_eps and db_eps > 0 else data.get('per')

        db_bps = data.get('bps')
        live_pbr = round(live_price / db_bps, 2) if db_bps and db_bps > 0 else data.get('pbr')

        currency_unit = "원" if is_krx else "$"
        st.subheader(f"📊 [{stock_name}] ({selected_code}) 실시간 지표 분석")
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("현재 실시간 주가", f"{live_price:,} {currency_unit}")
        col2.metric("PER", f"{live_per} 배" if live_per else "N/A")
        col3.metric("PBR", f"{live_pbr} 배" if live_pbr else "N/A")
        col4.metric("ROE", f"{data.get('roe')}%" if data.get("roe") else "N/A")

        st.divider()

        eval_data = data.copy()
        eval_data['per'] = live_per
        eval_data['pbr'] = live_pbr

        total_score, grade, reasons = calculate_defense_score(eval_data)
        
        col_score, col_detail = st.columns([1, 2])
        with col_score:
            st.markdown("### 🛡️ 하락장 방어 종합 등급")
            st.title(f"**{total_score}**점 / [{grade}] 등급")
            if grade == 'S': st.success("🟢 **S등급 (요새형 최우수 방어주)**")
            elif grade == 'A': st.success("🟢 **A등급 (우수한 재무 체력)**")
            elif grade == 'B': st.info("🟡 **B등급 (평균적 방어력)**")
            elif grade == 'C': st.warning("🟠 **C등급 (하락장 주의 필요)**")
            else: st.warning("🔴 **D등급 (하락장 취약 위험주)**")
            
        with col_detail:
            st.markdown("### 📋 핵심 지표 평가 내역")
            for r in reasons: 
                st.write(r)
    else:
        st.error("선택한 종목의 데이터를 찾을 수 없습니다.")
