import streamlit as st
import FinanceDataReader as fdr
from supabase import create_client
import pandas as pd
import random
import altair as alt
import yfinance as yf

# ==========================================
# 1. 페이지 및 커스텀 디자인 설정
# ==========================================
st.set_page_config(
    page_title="Fundamental Analyzer - 하락장 방어 플랫폼", 
    page_icon="🛡️", 
    layout="wide"
)

# Streamlit 기본 테마 및 다크모드 무력화 CSS
st.markdown("""
    <style>
    /* 1. 전체 배경 및 기본 글자색 고정 */
    html, body, [data-testid="stAppViewContainer"], .stApp {
        background-color: #FFFFFF !important;
        color: #1A1A1A !important;
        background-image: 
            radial-gradient(circle at 0% 0%, rgba(248, 190, 140, 0.25) 0%, transparent 45%),
            radial-gradient(circle at 100% 0%, rgba(248, 190, 140, 0.25) 0%, transparent 45%),
            radial-gradient(circle at 0% 100%, rgba(248, 190, 140, 0.25) 0%, transparent 45%),
            radial-gradient(circle at 100% 100%, rgba(248, 190, 140, 0.25) 0%, transparent 45%) !important;
        background-repeat: no-repeat !important;
        background-attachment: fixed !important;
    }

    /* 모든 텍스트 요소를 검은색/어두운색으로 고정 */
    p, span, div, label, h1, h2, h3, h4, h5, h6 {
        color: #1A1A1A !important;
    }
    
    /* 2. Streamlit Input 박스 (검색창) 정상화 */
    div[data-baseweb="input"] {
        background-color: #FFFFFF !important;
        border: 2px solid #F4A261 !important;
        border-radius: 8px !important;
    }
    div[data-baseweb="input"] input {
        color: #1A1A1A !important;
        background-color: #FFFFFF !important;
    }

    /* 3. Streamlit Selectbox (드롭다운) 정상화 */
    div[data-baseweb="select"] > div {
        background-color: #FFFFFF !important;
        color: #1A1A1A !important;
        border: 1px solid #F4A261 !important;
    }

    /* 4. Streamlit 기본 버튼 정상화 */
    div.stButton > button {
        background-color: #F4A261 !important;
        color: #FFFFFF !important;
        border: none !important;
        font-weight: bold !important;
        border-radius: 8px !important;
        padding: 8px 16px !important;
        transition: all 0.3s ease !important;
    }
    div.stButton > button:hover {
        background-color: #E79150 !important;
        color: #FFFFFF !important;
    }
    div.stButton > button p {
        color: #FFFFFF !important; /* 버튼 안 텍스트 흰색 강제 */
    }

    /* 5. 상단 로고 박스 */
    .logo-box {
        border: 2px solid #F4A261;
        border-radius: 8px;
        padding: 12px 10px;
        text-align: center;
        font-weight: bold;
        color: #D97706 !important;
        background-color: #FFFDF9;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
    }

    /* 6. Investor Quote 칸 */
    .quote-box {
        background-color: #FAFAFA;
        border: 1px solid #E5E5E5;
        border-radius: 10px;
        padding: 20px;
        color: #333333 !important;
        box-shadow: 0 4px 10px rgba(0,0,0,0.02);
        min-height: 180px; 
        height: 180px;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        text-align: center;
    }

    /* 7. 좌우 광고 영역 */
    .ad-box {
        background-color: #F8F9FA;
        border: 2px dashed #D0D0D0;
        border-radius: 10px;
        text-align: center;
        color: #888888 !important;
        padding: 220px 5px;
        font-weight: bold;
        font-size: 14px;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        box-sizing: border-box;
    }

    /* 8. 상단 탭 스타일 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 15px;
        justify-content: center;
    }
    .stTabs [data-baseweb="tab"] {
        height: 45px;
        background-color: #FAFAFA !important;
        border-radius: 8px;
        border: 1px solid #E5E5E5;
        padding: 0 20px;
    }
    .stTabs [data-baseweb="tab"] p {
        color: #444444 !important;
        font-weight: 600;
    }
    .stTabs [aria-selected="true"] {
        background-color: #FFFDF9 !important;
        border: 2px solid #F4A261 !important;
    }
    .stTabs [aria-selected="true"] p {
        color: #D97706 !important;
    }

    /* 9. 하단 트렌드 카드 */
    .custom-card {
        background-color: #FAFAFA;
        border: 1px solid #E5E5E5;
        border-radius: 12px;
        padding: 20px;
        height: 180px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.03);
    }

    /* 10. 커스텀 링크 버튼 */
    .pastel-orange-btn {
        background-color: #F4A261 !important;
        color: white !important;
        padding: 12px 20px;
        border-radius: 8px;
        font-weight: bold;
        text-align: center;
        display: block;
        text-decoration: none;
        box-shadow: 0 2px 6px rgba(244, 162, 97, 0.3);
    }
    .pastel-orange-btn:hover {
        background-color: #E79150 !important;
        color: white !important;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Supabase 및 유틸리티 함수 설정
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
            "에코프로비엠 (247540)": {"code": "247540", "market": "KOSDAQ"}
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

def format_korean_currency(val):
    if pd.isna(val) or val is None: return "-"
    if abs(val) >= 1_000_000_000_000:
        return f"{val / 1_000_000_000_000:,.2f} 조 원"
    elif abs(val) >= 100_000_000:
        return f"{val / 100_000_000:,.2f} 억 원"
    return f"{val:,.0f} 원"

def format_yearly_dataframe(df, stock_name):
    df_formatted = df.copy()
    columns_map = {
        'year': '연도',
        'net_income': '당기순이익',
        'total_equity': '자본총계',
        'eps': 'EPS(원)',
        'bps': 'BPS(원)',
        'roe': 'ROE(%)',
        'debt_ratio': '부채비율(%)'
    }
    
    for col in ['net_income', 'total_equity']:
        if col in df_formatted.columns:
            df_formatted[col] = df_formatted[col].apply(format_korean_currency)
            
    for col in ['roe', 'debt_ratio']:
        if col in df_formatted.columns:
            df_formatted[col] = df_formatted[col].apply(lambda x: f"{x:,.2f}%" if pd.notna(x) else "-")
            
    for col in ['eps', 'bps']:
        if col in df_formatted.columns:
            df_formatted[col] = df_formatted[col].apply(lambda x: f"{x:,.0f} 원" if pd.notna(x) else "-")
            
    return df_formatted.rename(columns=columns_map)

# ==========================================
# 3. 화면 분기 (메인 포털 vs 상세 분석 리포트)
# ==========================================
query_params = st.query_params
selected_code = query_params.get("code", None)

if not selected_code:
    # --- [상단 영역]: Logo | Investor Quote | Log in ---
    col_logo, col_quote, col_login = st.columns([1.2, 6.8, 1])
    
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
                <div style='font-size: 15px; font-weight: bold; margin-top: 6px; color: #333333;'>
                    "{selected_quote}"
                </div>
            </div>
        """, unsafe_allow_html=True)
        
    with col_login:
        if st.button("Log in", use_container_width=True):
            st.toast("로그인 기능 준비 중입니다!")

    st.markdown("<br>", unsafe_allow_html=True)

    # --- [본문 레이아웃] ([1, 5, 1]) ---
    left_ad, main_content, right_ad = st.columns([1, 5, 1])

    with left_ad:
        st.markdown("<div class='ad-box'>Ads</div>", unsafe_allow_html=True)

    with main_content:
        tab1, tab2, tab3, tab4 = st.tabs(["1. US stock", "2. Korea stock", "3. Live news", "4. Gem Screener"])
        
        with tab1:
            st.caption("🇺🇸 미국 주식 펀더멘탈 간이 검색 (Ticker 입력)")
            us_ticker = st.text_input("미국 주식 티커를 입력하세요", value="AAPL", key="us_input").upper().strip()
            if st.button("미국 주식 분석", key="us_btn"):
                st.markdown(f"<a href='/?code={us_ticker}' target='_self' class='pastel-orange-btn'>🚀 {us_ticker} 분석 리포트 열기</a>", unsafe_allow_html=True)
                
        with tab2:
            st.caption("🇰🇷 한국 주식 재무제표 방어력 검색")
            krx_stocks = get_krx_stocks()
            stock_options = list(krx_stocks.keys())
            selected_option = st.selectbox("분석할 한국 주식을 선택하세요", stock_options, key="kr_select")
            target_code = krx_stocks[selected_option]["code"]
            st.markdown(f"<br><a href='/?code={target_code}' target='_self' class='pastel-orange-btn'>🚀 {selected_option} 분석 리포트 열기</a>", unsafe_allow_html=True)
            
        with tab3:
            st.caption("📰 실시간 증시 속보 및 마켓 인사이트 (업데이트 예정)")
            
        with tab4:
            st.caption("💎 AI 기반 하락장 방어주 젬(Gem) 스크리닝 (업데이트 예정)")

        st.markdown("<br>", unsafe_allow_html=True)

        # 하단 트렌드 카드 3개
        col_t1, col_t2, col_t3 = st.columns(3)
        
        with col_t1:
            st.markdown("""
                <div class='custom-card'>
                    <b style='color: #1A1A1A;'>🔥 인기 검색 (국내)</b><br><br>
                    • <a href='/?code=005930' style='color: #D97706; text-decoration: none; font-weight: 600;'>삼성전자 (005930)</a><br>
                    • <a href='/?code=000660' style='color: #D97706; text-decoration: none; font-weight: 600;'>SK하이닉스 (000660)</a>
                </div>
            """, unsafe_allow_html=True)
            
        with col_t2:
            st.markdown("""
                <div class='custom-card'>
                    <b style='color: #1A1A1A;'>🇺🇸 Trending Searches (US)</b><br><br>
                    • <a href='/?code=AAPL' style='color: #D97706; text-decoration: none; font-weight: 600;'>Apple (AAPL)</a><br>
                    • <a href='/?code=NVDA' style='color: #D97706; text-decoration: none; font-weight: 600;'>NVIDIA (NVDA)</a><br>
                    • <a href='/?code=TSLA' style='color: #D97706; text-decoration: none; font-weight: 600;'>Tesla (TSLA)</a>
                </div>
            """, unsafe_allow_html=True)
            
        with col_t3:
            st.markdown("""
                <div class='custom-card'>
                    <b style='color: #1A1A1A;'>🚗 주주환원 우수 종목</b><br><br>
                    • <a href='/?code=005380' style='color: #D97706; text-decoration: none; font-weight: 600;'>현대차 (005380)</a><br>
                    • <a href='/?code=000270' style='color: #D97706; text-decoration: none; font-weight: 600;'>기아 (000270)</a>
                </div>
            """, unsafe_allow_html=True)

    with right_ad:
        st.markdown("<div class='ad-box'>Ads</div>", unsafe_allow_html=True)

else:
    # --- [상세 분석 페이지] ---
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

        st.divider()

        st.subheader(f"📊 [{stock_name}] 재무제표 기간별 심층 분석")
        tab_5y, tab_3y, tab_q = st.tabs(["📅 5년 장기 흐름", "🕒 3년 핵심 집중", "⚡ 최근 4분기 단기 실적"])

        history_data = None
        if supabase:
            try:
                history_data = supabase.table("Fundamental_History").select("*").eq("stock_code", selected_code).order("year").execute()
            except Exception:
                history_data = None

        if history_data and history_data.data and len(history_data.data) > 0:
            df_hist = pd.DataFrame(history_data.data)
            for col in ["net_income", "total_equity", "eps", "bps", "roe", "debt_ratio"]:
                if col in df_hist.columns:
                    df_hist[col] = pd.to_numeric(df_hist[col], errors='coerce')

            if 'year' in df_hist.columns:
                df_hist = df_hist.sort_values("year")

                with tab_5y:
                    st.markdown(f"#### 📅 [{stock_name}] 최근 5개년 재무 흐름")
                    df_5 = df_hist.tail(5).copy()
                    df_5['year'] = df_5['year'].astype(int)
                    
                    unit_divider = 1_000_000_000_000 if is_krx else 1_000_000
                    unit_label = "조 원" if is_krx else "M $"
                    df_5['순이익_표시'] = df_5['net_income'] / unit_divider
                    
                    chart_5y = alt.Chart(df_5).mark_bar(color="#F4A261", size=30).encode(
                        x=alt.X('year:O', title='연도', axis=alt.Axis(labelAngle=0)),
                        y=alt.Y('순이익_표시:Q', title=f'당기순이익 ({unit_label})'),
                        tooltip=['year', '순이익_표시']
                    ).properties(height=320)
                    st.altair_chart(chart_5y, use_container_width=True)
                    st.dataframe(format_yearly_dataframe(df_5.drop(columns=['순이익_표시'], errors='ignore'), stock_name), use_container_width=True, hide_index=True)

                with tab_3y:
                    st.markdown(f"#### 🕒 [{stock_name}] 최근 3개년 집중 분석")
                    df_3 = df_hist.tail(3).copy()
                    df_3['year'] = df_3['year'].astype(int)
                    
                    unit_divider = 1_000_000_000_000 if is_krx else 1_000_000
                    unit_label = "조 원" if is_krx else "M $"
                    df_3['순이익_표시'] = df_3['net_income'] / unit_divider
                    
                    chart_3y = alt.Chart(df_3).mark_bar(color="#F4A261", size=30).encode(
                        x=alt.X('year:O', title='연도', axis=alt.Axis(labelAngle=0)),
                        y=alt.Y('순이익_표시:Q', title=f'당기순이익 ({unit_label})'),
                        tooltip=['year', '순이익_표시']
                    ).properties(height=320)
                    st.altair_chart(chart_3y, use_container_width=True)
                    st.dataframe(format_yearly_dataframe(df_3.drop(columns=['순이익_표시'], errors='ignore'), stock_name), use_container_width=True, hide_index=True)
        else:
            with tab_5y: st.info("저장된 5개년 히스토리 재무 데이터가 없습니다.")
            with tab_3y: st.info("저장된 3개년 히스토리 재무 데이터가 없습니다.")

        with tab_q:
            st.markdown(f"#### ⚡ [{stock_name}] 최근 4개 분기 실적 추이")
            try:
                yticker = yf.Ticker(ticker_symbol)
                q_fin = yticker.quarterly_financials
                
                if q_fin is not None and not q_fin.empty:
                    q_df = q_fin.T.head(4).copy()
                    parsed_rows = []
                    
                    def get_q_value(q_col, keys):
                        for k in keys:
                            if k in q_fin.index:
                                val = q_fin.loc[k, q_col]
                                if pd.notna(val): return val
                        return None

                    for date_idx, row in q_df.iterrows():
                        q_date = str(date_idx).split(" ")[0]
                        revenue = get_q_value(date_idx, ['Total Revenue', 'Revenue'])
                        op_income = get_q_value(date_idx, ['Operating Income', 'Operating Revenue'])
                        net_inc = get_q_value(date_idx, ['Net Income', 'Net Income Common Stockholders', 'Net Income From Continuing Operation'])
                        
                        op_margin = (op_income / revenue * 100) if (revenue and op_income and revenue != 0) else None
                        net_margin = (net_inc / revenue * 100) if (revenue and net_inc and revenue != 0) else None
                        
                        parsed_rows.append({
                            "종목명": stock_name,
                            "분기 기준일": q_date,
                            "매출액": revenue,
                            "영업이익": op_income,
                            "당기순이익": net_inc,
                            "영업이익률": op_margin,
                            "순이익률": net_margin,
                            "_raw_net": net_inc if net_inc is not None else 0
                        })
                    
                    df_quarterly_raw = pd.DataFrame(parsed_rows)
                    df_chart = df_quarterly_raw.sort_values("분기 기준일").copy()
                    
                    unit_div = 1_000_000_000_000 if is_krx else 1_000_000
                    unit_title = "당기순이익 (조 원)" if is_krx else "당기순이익 (M $)"
                    df_chart['순이익_표시'] = df_chart['_raw_net'] / unit_div
                    
                    chart = alt.Chart(df_chart).mark_bar(color="#F4A261", size=30).encode(
                        x=alt.X('분기 기준일:N', title='분기 기준일', sort=None, axis=alt.Axis(labelAngle=0)),
                        y=alt.Y('순이익_표시:Q', title=unit_title),
                        tooltip=['분기 기준일', '순이익_표시']
                    ).properties(height=320)
                    st.altair_chart(chart, use_container_width=True)

                    df_table = df_quarterly_raw.sort_values("분기 기준일", ascending=False).copy()
                    if is_krx:
                        df_table["매출액"] = df_table["매출액"].apply(format_korean_currency)
                        df_table["영업이익"] = df_table["영업이익"].apply(format_korean_currency)
                        df_table["당기순이익"] = df_table["당기순이익"].apply(format_korean_currency)
                    else:
                        df_table["매출액"] = df_table["매출액"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "-")
                        df_table["영업이익"] = df_table["영업이익"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "-")
                        df_table["당기순이익"] = df_table["당기순이익"].apply(lambda x: f"${x:,.0f}" if pd.notna(x) else "-")
                        
                    df_table["영업이익률"] = df_table["영업이익률"].apply(lambda x: f"{x:,.2f}%" if pd.notna(x) else "-")
                    df_table["순이익률"] = df_table["순이익률"].apply(lambda x: f"{x:,.2f}%" if pd.notna(x) else "-")
                    
                    st.dataframe(df_table.drop(columns=["_raw_net"]), use_container_width=True, hide_index=True)
                else:
                    st.warning("해당 종목의 분기 실적 데이터를 불러올 수 없습니다.")
            except Exception as e:
                st.error(f"분기 데이터 로딩 중 오류 발생: {e}")
    else:
        st.error("선택한 종목의 데이터를 찾을 수 없습니다.")
