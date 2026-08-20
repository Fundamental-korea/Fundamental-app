import streamlit as st
import FinanceDataReader as fdr
from supabase import create_client
import pandas as pd
import random

# ==========================================
# 1. 페이지 및 커스텀 디자인 설정 (스케치 반영 버전)
# ==========================================
st.set_page_config(
    page_title="Fundamental Analyzer - 하락장 방어 플랫폼", 
    page_icon="🛡️", 
    layout="wide"
)

st.markdown("""
    <style>
    /* 전체 배경: 화이트 + 부드러운 모서리 파스텔 주황 글로우 */
    .stApp {
        background-color: #FFFFFF;
        color: #1A1A1A;
        background-image: 
            radial-gradient(circle at 0% 0%, rgba(248, 190, 140, 0.35) 0%, transparent 45%),
            radial-gradient(circle at 100% 0%, rgba(248, 190, 140, 0.35) 0%, transparent 45%),
            radial-gradient(circle at 0% 100%, rgba(248, 190, 140, 0.35) 0%, transparent 45%),
            radial-gradient(circle at 100% 100%, rgba(248, 190, 140, 0.35) 0%, transparent 45%);
        background-repeat: no-repeat;
        background-attachment: fixed;
    }
    
    /* 상단 로고 박스 */
    .logo-box {
        border: 2px solid #F4A261;
        border-radius: 8px;
        padding: 12px 10px;
        text-align: center;
        font-weight: bold;
        color: #D97706;
        background-color: #FFFDF9;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
    }

    /* Investor Quote 칸: 픽셀 단위로 높이를 크게 고정 */
    .quote-box {
        background-color: #FAFAFA;
        border: 1px solid #E5E5E5;
        border-radius: 10px;
        padding: 30px; /* 내부 여백 확보 */
        color: #333333;
        box-shadow: 0 4px 10px rgba(0,0,0,0.02);
        
        /* 이 부분이 핵심! */
        min-height: 250px; 
        height: 250px;
        
        display: flex;
        flex-direction: column; /* 세로 정렬 */
        align-items: center;
        justify-content: center;
        text-align: center;
    
    }

    /* 좌우 광고 영역 (완벽한 대칭 사이즈) */
    .ad-box {
        background-color: #F8F9FA;
        border: 2px dashed #D0D0D0;
        border-radius: 10px;
        text-align: center;
        color: #888888;
        padding: 180px 10px;
        font-weight: bold;
        height: 100%;
    }

    /* 1, 2, 3, 4번 상단 탭 간격 확보 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 20px;
        justify-content: center;
    }
    .stTabs [data-baseweb="tab"] {
        height: 45px;
        background-color: #FAFAFA;
        border-radius: 8px;
        border: 1px solid #E5E5E5;
        padding: 0 20px;
    }

    /* 5번 검색창 영역 (흰색 바탕 + 주황색 테두리) */
    .search-container {
        background-color: #FFFFFF;
        border: 2px solid #F4A261;
        border-radius: 12px;
        padding: 25px;
        box-shadow: 0 4px 15px rgba(244,162,97,0.1);
    }

    /* 6, 7, 8번 하단 카드 디자인 */
    .custom-card {
        background-color: #FAFAFA;
        border: 1px solid #E5E5E5;
        border-radius: 12px;
        padding: 20px;
        height: 180px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.03);
    }

    /* 파스텔 주황색 포인트 버튼 */
    .pastel-orange-btn {
        background-color: #F4A261;
        color: white;
        padding: 10px 20px;
        border-radius: 8px;
        font-weight: bold;
        text-align: center;
        display: block;
        text-decoration: none;
        box-shadow: 0 2px 6px rgba(244, 162, 97, 0.3);
    }
    .pastel-orange-btn:hover {
        background-color: #E79150;
        color: white;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. Supabase 및 데이터 연동 설정
# ==========================================
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "YOUR_SUPABASE_URL")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "YOUR_SUPABASE_KEY")

@st.cache_resource
def init_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()

@st.cache_data
def get_krx_stocks():
    try:
        df = fdr.StockListing("KRX")
        return {f"{name} ({code})": code for name, code in zip(df["Name"], df["Code"])}
    except Exception:
        return {"삼성전자 (005930)": "005930", "SK하이닉스 (000660)": "000660"}

# ==========================================
# 3. 화면 분기 (메인 포털 vs 상세 분석 리포트)
# ==========================================
query_params = st.query_params
selected_code = query_params.get("code", None)

if not selected_code:
    # --- [상단 영역]: Logo | Investor Quote | Log in ---
    col_logo, col_quote, col_login = st.columns([1.2, 6.8, 1])
    
    with col_logo:
        st.markdown("<div class='logo-box'>📈 Logo</div>", unsafe_allow_html=True)
        
    with col_quote:
    query_params = st.query_params
selected_code = query_params.get("code", None)

if not selected_code:
    # --- [상단 영역]: Logo | Investor Quote | Log in ---
    col_logo, col_quote, col_login = st.columns([1.2, 6.8, 1])
    
    with col_logo:
        st.markdown("<div class='logo-box'>📈 Logo</div>", unsafe_allow_html=True)
        
    with col_quote:
        quotes = [
            "하락장은 우량한 기업을 헐값에 살 수 있는 가장 위대한 기회다.",
            "시장이 공포에 질려 있을 때가 탐욕을 부릴 최적의 시기다.",
            "투자는 지능이 아니라 인내심의 게임이다."
        ]
        selected_quote = random.choice(quotes)
        
        st.markdown(f"""
            <div class='quote-box'>
                <div style='font-size: 40px;'>👨‍💼</div> 
                <div style='font-size: 16px; font-weight: bold; margin-top: 8px;'>
                    {selected_quote}
                </div>
            </div>
        """, unsafe_allow_html=True)
        
    with col_login:
        if st.button("Log in", use_container_width=True):
            st.toast("로그인 기능 준비 중입니다!")

    st.markdown("<br>", unsafe_allow_html=True)
    with col_login:
        if st.button("Log in", use_container_width=True):
            st.toast("로그인 기능 준비 중입니다!")

    st.markdown("<br>", unsafe_allow_html=True)

    # --- [본문 레이아웃]: 좌우 광고창 사이즈를 완벽히 대칭으로 맞춤 ([1, 5, 1]) ---
    left_ad, main_content, right_ad = st.columns([1, 5, 1])

    with left_ad:
        st.markdown("<div class='ad-box'>Ads</div>", unsafe_allow_html=True)

    with main_content:
        # [1, 2, 3, 4] 상단 탭 영역
        tab1, tab2, tab3, tab4 = st.tabs(["1. US stock", "2. Korea stock", "3. Live news", "4. Game"])
        
        with tab1: st.caption("미국 주식 펀더멘탈 분석 기능")
        with tab2: st.caption("한국 주식 재무제표 방어력 분석 기능")
        with tab3: st.caption("실시간 증시 속보 및 마켓 인사이트")
        with tab4: st.caption("AI 기반 젬(Gem) 종목 스크리닝")

        st.markdown("<br>", unsafe_allow_html=True)

        # [5] 중앙 검색 탭 영역 (흰색 바탕 + 주황색 테두리)
        st.markdown("<div class='search-container'>", unsafe_allow_html=True)
        st.markdown("### 🔍 5. Searching Tab (종목 통합 검색)")
        
        krx_stocks = get_krx_stocks()
        stock_options = list(krx_stocks.keys())
        selected_option = st.selectbox("분석할 한국 주식을 선택하세요", stock_options)
        target_code = krx_stocks[selected_option]

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div style="text-align: center;">
                <a href="/?code={target_code}" target="_blank" class="pastel-orange-btn">
                    🚀 상세 분석 리포트 열기
                </a>
            </div>
            """,
            unsafe_allow_html=True
        )
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # [6, 7, 8] 하단 트렌드 카드 3개 배치
        col_t1, col_t2, col_t3 = st.columns(3)
        
        with col_t1:
            st.markdown("""
                <div class='custom-card'>
                    <b>6. Most searched Stocks</b><br><br>
                    • <a href='/?code=005930' target='_blank' style='color: #D97706; text-decoration: none;'>삼성전자 (005930)</a><br>
                    • <a href='/?code=000660' target='_blank' style='color: #D97706; text-decoration: none;'>SK하이닉스 (000660)</a>
                </div>
            """, unsafe_allow_html=True)
            
        with col_t2:
            st.markdown("""
                <div class='custom-card'>
                    <b>7. Trending Searches (US)</b><br><br>
                    • Apple (AAPL)<br>
                    • Tesla (TSLA)<br>
                    • Microsoft (MSFT)
                </div>
            """, unsafe_allow_html=True)
            
        with col_t3:
            st.markdown("""
                <div class='custom-card'>
                    <b>8. Trending Searches (KOR)</b><br><br>
                    • <a href='/?code=005380' target='_blank' style='color: #D97706; text-decoration: none;'>현대차 (005380)</a><br>
                    • <a href='/?code=000270' target='_blank' style='color: #D97706; text-decoration: none;'>기아 (000270)</a>
                </div>
            """, unsafe_allow_html=True)

    with right_ad:
        st.markdown("<div class='ad-box'>Ads</div>", unsafe_allow_html=True)

else:
    # --- [상세 분석 페이지]: 종목 카드가 선택되어 새 탭으로 열릴 때 ---
    st.title(f"📊 상세 펀더멘탈 분석 리포트 (종목 코드: {selected_code})")
    st.write("선택하신 종목의 재무 건전성 및 하락장 방어력 지표를 분석하는 공간입니다.")
    
    if st.button("⬅️ 메인 포털로 돌아가기"):
        st.query_params.clear()
        st.rerun()
    # 아래 상세 분석 창(새 탭 열릴 때 실행되는 로직)은 기존 코드 그대로 유지하면 됩니다!
    # ----------------------------------------------------
    # [뷰 B] 새 탭으로 열리는 상세 재무 분석 창 (완전판)
    # ----------------------------------------------------
    st.markdown("### 🛡️ 펀더멘탈 방어력 상세 분석 리포트 (새 탭 전용)")
    st.markdown("<hr style='border: 1px solid #F4A261;'>", unsafe_allow_html=True)
    
    data = get_stock_data(selected_code)

    if data:
        stock_name = data.get('stock_name', '종목')
        try:
            df_price = fdr.DataReader(selected_code)
            live_price = int(df_price['Close'].iloc[-1]) if not df_price.empty else data.get('stock_price', 0)
        except Exception:
            live_price = data.get('stock_price', 0)

        db_eps = data.get('eps')
        live_per = round(live_price / db_eps, 2) if db_eps and db_eps > 0 else data.get('per')

        db_bps = data.get('bps')
        live_pbr = round(live_price / db_bps, 2) if db_bps and db_bps > 0 else data.get('pbr')

        st.subheader(f"📊 [{stock_name}] ({selected_code}) 실시간 지표 분석")
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("현재 실시간 주가", f"{live_price:,} 원")
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
            st.markdown("### 📋 10대 핵심 지표 평가 내역")
            for r in reasons: 
                st.write(r)

        st.divider()

        st.subheader(f"📊 [{stock_name}] 재무제표 기간별 심층 분석")
        tab_5y, tab_3y, tab_q = st.tabs(["📅 5년 장기 흐름", "🕒 3년 핵심 집중", "⚡ 최근 4분기 단기 실적"])

        history_data = supabase.table("Fundamental_History").select("*").eq("stock_code", selected_code).order("year").execute()

        if history_data.data and len(history_data.data) > 0:
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
                    df_5['순이익_조원'] = df_5['net_income'] / 1_000_000_000_000
                    
                    chart_5y = alt.Chart(df_5).mark_bar(color="#F4A261", size=30).encode(
                        x=alt.X('year:O', title='연도', axis=alt.Axis(labelAngle=0)),
                        y=alt.Y('순이익_조원:Q', title='당기순이익 (조 원)'),
                        tooltip=['year', '순이익_조원']
                    ).properties(height=320)
                    st.altair_chart(chart_5y, use_container_width=True)
                    st.dataframe(format_yearly_dataframe(df_5, stock_name), use_container_width=True, hide_index=True)

                with tab_3y:
                    st.markdown(f"#### 🕒 [{stock_name}] 최근 3개년 집중 분석")
                    df_3 = df_hist.tail(3).copy()
                    df_3['year'] = df_3['year'].astype(int)
                    df_3['순이익_조원'] = df_3['net_income'] / 1_000_000_000_000
                    
                    chart_3y = alt.Chart(df_3).mark_bar(color="#F4A261", size=30).encode(
                        x=alt.X('year:O', title='연도', axis=alt.Axis(labelAngle=0)),
                        y=alt.Y('순이익_조원:Q', title='당기순이익 (조 원)'),
                        tooltip=['year', '순이익_조원']
                    ).properties(height=320)
                    st.altair_chart(chart_3y, use_container_width=True)
                    st.dataframe(format_yearly_dataframe(df_3, stock_name), use_container_width=True, hide_index=True)
        else:
            with tab_5y: st.info("저장된 5개년 역사적 재무 데이터가 없습니다.")
            with tab_3y: st.info("저장된 3개년 역사적 재무 데이터가 없습니다.")

        with tab_q:
            st.markdown(f"#### ⚡ [{stock_name}] 최근 4개 분기 실적 추이")
            try:
                import yfinance as yf
                ticker_symbol = f"{selected_code}.KS" if selected_code.isdigit() and len(selected_code)==6 else selected_code
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
                    df_chart['순이익_조원'] = df_chart['_raw_net'] / 1_000_000_000_000
                    
                    chart = alt.Chart(df_chart).mark_bar(color="#F4A261", size=30).encode(
                        x=alt.X('분기 기준일:N', title='분기 기준일', sort=None, axis=alt.Axis(labelAngle=0)),
                        y=alt.Y('순이익_조원:Q', title='당기순이익 (조 원)'),
                        tooltip=['분기 기준일', '순이익_조원']
                    ).properties(height=320)
                    st.altair_chart(chart, use_container_width=True)

                    df_table = df_quarterly_raw.sort_values("분기 기준일", ascending=False).copy()
                    df_table["매출액"] = df_table["매출액"].apply(format_korean_currency)
                    df_table["영업이익"] = df_table["영업이익"].apply(format_korean_currency)
                    df_table["당기순이익"] = df_table["당기순이익"].apply(format_korean_currency)
                    df_table["영업이익률"] = df_table["영업이익률"].apply(lambda x: f"{x:,.2f}%" if pd.notna(x) else "-")
                    df_table["순이익률"] = df_table["순이익률"].apply(lambda x: f"{x:,.2f}%" if pd.notna(x) else "-")
                    
                    st.dataframe(df_table.drop(columns=["_raw_net"]), use_container_width=True, hide_index=True)
                else:
                    st.warning("해당 종목의 분기 실적 데이터를 불러올 수 없습니다.")
            except Exception as e:
                st.error(f"분기 데이터 로딩 중 오류 발생: {e}")
    else:
        st.error("선택한 종목의 데이터가 Supabase DB에 존재하지 않습니다.")
