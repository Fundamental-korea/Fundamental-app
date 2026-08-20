import streamlit as st
import FinanceDataReader as fdr
from supabase import create_client
import pandas as pd
import random
import yfinance as yf

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
    /* 1. 전체 배경 */
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

    /* 2. 상단 헤더 컴포넌트 크기 고정 */
    .logo-box {
        border: 2px solid #F4A261;
        border-radius: 12px;
        background-color: #FFFDF9;
        color: #D97706 !important;
        font-weight: bold;
        font-size: 16px;
        height: 100px !important;
        min-height: 100px !important;
        display: flex;
        align-items: center;
        justify-content: center;
        text-align: center;
        box-shadow: 0 2px 8px rgba(0,0,0,0.03);
    }

    .quote-box {
        background-color: #FAFAFA;
        border: 1.5px solid #E5E5E5;
        border-radius: 12px;
        height: 100px !important;
        min-height: 100px !important;
        display: flex;
        flex-direction: row;
        align-items: center;
        justify-content: center;
        gap: 20px;
        padding: 0 20px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.02);
    }

    .quote-text {
        font-size: 16px;
        font-weight: 600;
        color: #333333 !important;
    }

    /* 3. 좌우 Ads (가로 폭 줄임) */
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

    /* 4. 1, 2, 3, 4번 탭 간격 및 배치 스케치 맞춤 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 24px !important;
        justify-content: center !important;
        margin-bottom: 25px !important;
    }
    .stTabs [data-baseweb="tab"] {
        height: 48px !important;
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

    /* 5. 🔥 [스케치 5번] 주황색 테두리 검색창 단일화 */
    div[data-testid="stTextInput"] {
        margin-top: 10px;
        margin-bottom: 20px;
    }
    div[data-baseweb="input"] {
        border: 2px solid #F4A261 !important;
        border-radius: 12px !important;
        background-color: #FFFDF9 !important;
        height: 65px !important;
        padding: 0 12px !important;
        box-shadow: 0 4px 12px rgba(244, 162, 97, 0.08) !important;
    }
    div[data-baseweb="input"] input {
        color: #1A1A1A !important;
        background-color: #FFFDF9 !important;
        -webkit-text-fill-color: #1A1A1A !important;
        font-size: 17px !important;
        font-weight: 600 !important;
    }
    div[data-baseweb="select"] > div {
        background-color: #FFFDF9 !important;
        color: #1A1A1A !important;
        border: 2px solid #F4A261 !important;
        border-radius: 12px !important;
        height: 65px !important;
    }

    /* 6. 스케치 6, 7, 8번 하단 카드 */
    .bottom-cards-wrapper {
        margin-top: 35px;
    }
    .sketch-card {
        background-color: #FAFAFA;
        border: 1.5px solid #E5E5E5;
        border-radius: 12px;
        padding: 24px;
        height: 220px;
        box-shadow: 0 4px 10px rgba(0,0,0,0.02);
    }

    /* 버튼 스타일 */
    div.stButton > button {
        background-color: #F4A261 !important;
        color: #FFFFFF !important;
        border: none !important;
        font-weight: bold !important;
        border-radius: 8px !important;
        height: 48px !important;
        font-size: 15px !important;
    }
    div.stButton > button:hover {
        background-color: #E79150 !important;
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
# 3. 메인 포털 UI
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
                <div style='font-size: 32px;'>👨‍💼</div> 
                <div class='quote-text'>"{selected_quote}"</div>
            </div>
        """, unsafe_allow_html=True)
        
    with col_login:
        st.markdown("<div style='height: 25px;'></div>", unsafe_allow_html=True)
        if st.button("Log in", use_container_width=True):
            st.toast("로그인 기능 준비 중입니다!")

    st.markdown("<br>", unsafe_allow_html=True)

    # --- [본문 레이아웃]: Ads (Left) | Center Main | Ads (Right) ---
    left_ad, main_content, right_ad = st.columns([0.6, 6.8, 0.6])

    with left_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

    with main_content:
        # [스케치 1, 2, 3, 4] 탭
        tab1, tab2, tab3, tab4 = st.tabs(["1. Us stock", "2. Korea stock", "3. Live news", "4. Gem"])
        
        with tab1:
            us_ticker = st.text_input(
                label="미국주식검색",
                value="",
                placeholder="🔍 Searching Tab: 미국 주식 Ticker 입력 (예: AAPL, NVDA, TSLA)",
                label_visibility="collapsed",
                key="us_input"
            ).upper().strip()
            
            if st.button("🚀 미국 주식 펀더멘탈 분석 리포트 열기", use_container_width=True):
                if us_ticker:
                    st.markdown(f"<script>window.location.href='/?code={us_ticker}';</script>", unsafe_allow_html=True)
                else:
                    st.toast("Ticker를 입력해주세요.")
                
        with tab2:
            krx_stocks = get_krx_stocks()
            stock_options = list(krx_stocks.keys())
            selected_option = st.selectbox(
                label="한국주식선택",
                options=stock_options,
                label_visibility="collapsed",
                key="kr_select"
            )
            target_code = krx_stocks[selected_option]["code"]
            
            if st.button(f"🚀 {selected_option} 펀더멘탈 분석 리포트 열기", use_container_width=True):
                st.markdown(f"<script>window.location.href='/?code={target_code}';</script>", unsafe_allow_html=True)
            
        with tab3:
            st.info("📰 실시간 증시 속보 및 주요 뉴스 모니터링 준비 중입니다.")
            
        with tab4:
            st.info("💎 하락장 우수 저평가 종목(Gem) 스크리너 준비 중입니다.")

        # [스케치 6, 7, 8] 하단 트렌드 카드
        st.markdown("<div class='bottom-cards-wrapper'>", unsafe_allow_html=True)
        col_6, col_7, col_8 = st.columns(3)
        
        with col_6:
            st.markdown("""
                <div class='sketch-card'>
                    <b style='color: #1A1A1A;'>Most searched Stocks</b><br><br>
                    • <a href='/?code=005930' style='color: #D97706; text-decoration: none; font-weight: 600;'>삼성전자 (005930)</a><br><br>
                    • <a href='/?code=000660' style='color: #D97706; text-decoration: none; font-weight: 600;'>SK하이닉스 (000660)</a>
                </div>
            """, unsafe_allow_html=True)
            
        with col_7:
            st.markdown("""
                <div class='sketch-card'>
                    <b style='color: #1A1A1A;'>Trending Searches (US)</b><br><br>
                    • <a href='/?code=AAPL' style='color: #D97706; text-decoration: none; font-weight: 600;'>Apple (AAPL)</a><br><br>
                    • <a href='/?code=NVDA' style='color: #D97706; text-decoration: none; font-weight: 600;'>NVIDIA (NVDA)</a><br><br>
                    • <a href='/?code=TSLA' style='color: #D97706; text-decoration: none; font-weight: 600;'>Tesla (TSLA)</a>
                </div>
            """, unsafe_allow_html=True)
            
        with col_8:
            st.markdown("""
                <div class='sketch-card'>
                    <b style='color: #1A1A1A;'>Trending Searches (KOR)</b><br><br>
                    • <a href='/?code=005380' style='color: #D97706; text-decoration: none; font-weight: 600;'>현대차 (005380)</a><br><br>
                    • <a href='/?code=000270' style='color: #D97706; text-decoration: none; font-weight: 600;'>기아 (000270)</a>
                </div>
            """, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with right_ad:
        st.markdown("<div class='ad-box-tall'>Ads</div>", unsafe_allow_html=True)

else:
    # --- [상세 분석 리포트 페이지] ---
    if st.button("⬅️ 메인 포털로 돌아가기"):
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
