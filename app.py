import streamlit as st
import FinanceDataReader as fdr
from supabase import create_client
import pandas as pd
import altair as alt

# ==========================================
# 1. 페이지 및 커스텀 디자인 설정 (피드백 반영 버전)
# ==========================================
st.set_page_config(
    page_title="Fundamental Analyzer - 하락장 방어 플랫폼", 
    page_icon="🛡️", 
    layout="wide"
)

# 커스텀 CSS (광고창 슬림화, 탭 간격 확보, Quote 높이 확대, 검색창 화이트+주황테두리)
st.markdown("""
    <style>
    /* 전체 배경: 화이트 + 4모서리 파스텔 주황 원형 글로우 */
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
    
    /* 일반 카드 박스 */
    .custom-card {
        background-color: #FAFAFA;
        border: 1px solid #E5E5E5;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 15px;
        color: #1A1A1A;
        box-shadow: 0 4px 12px rgba(0,0,0,0.03);
    }

    /* Investor Quote 칸 (세로로 확장하여 이목을 끌도록 디자인) */
    .quote-box {
        background-color: #FAFAFA;
        border: 1px solid #E5E5E5;
        border-radius: 10px;
        padding: 24px 20px;
        color: #333333;
        box-shadow: 0 4px 10px rgba(0,0,0,0.02);
        display: flex;
        align-items: center;
        height: 100%;
    }

    /* 1, 2, 3, 4번 상단 탭 사이 간격(Gap) 넓히기 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 30px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: #FAFAFA;
        border-radius: 8px;
        border: 1px solid #E5E5E5;
        padding: 0 20px;
    }

    /* 파스텔 주황색 포인트 버튼 */
    .pastel-orange-btn {
        background-color: #F4A261;
        color: white;
        padding: 10px 20px;
        border-radius: 8px;
        font-weight: bold;
        text-align: center;
        display: inline-block;
        text-decoration: none;
        box-shadow: 0 2px 6px rgba(244, 162, 97, 0.3);
    }
    .pastel-orange-btn:hover {
        background-color: #E79150;
        color: white;
    }

    /* 세로로 길쭉하게 바뀐 광고 영역 박스 */
    .ad-box {
        background-color: #F8F9FA;
        border: 2px dashed #D0D0D0;
        border-radius: 10px;
        text-align: center;
        color: #888888;
        padding: 160px 10px; /* 세로 길이를 늘림 */
        font-weight: bold;
    }
    </style>
""", unsafe_allow_html=True)

SUPABASE_URL = st.secrets.get("SUPABASE_URL", "https://cnweggechipghcivruie.supabase.co")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNud2VnZ2VjaGlwZ2hjaXZydWllIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcwODU5ODksImV4cCI6MjEwMjY2MTk4OX0.mYi7QB0ekkC0Jg49M18tqrMdCZBQgRHEK2J1EdIBZhc")

@st.cache_resource
def init_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()
query_params = st.query_params
selected_code = query_params.get("code", None)

# ==========================================
# 2. 공통 데이터 및 함수 (기존과 동일)
# ==========================================
@st.cache_data
def get_all_krx_stocks():
    try:
        df = fdr.StockListing("KRX")
        return {f"{name} ({code})": code for name, code in zip(df["Name"], df["Code"])}
    except Exception:
        return {"삼성전자 (005930)": "005930", "SK하이닉스 (000660)": "000660"}

def get_stock_data(stock_code):
    res = supabase.table("Fundamental").select("*").eq("stock_code", stock_code).execute()
    return res.data[0] if res.data else None

def calculate_defense_score(data):
    scores = {}
    reasons = []
    cr = data.get("current_ratio") or 120 
    s1 = (10 if cr >= 250 else 9 if cr >= 200 else 8 if cr >= 170 
          else 7 if cr >= 150 else 6 if cr >= 130 else 5 if cr >= 110 
          else 4 if cr >= 100 else 3 if cr >= 85 else 2 if cr >= 70 
          else 1 if cr >= 50 else 0)
    scores['score_current_ratio'] = s1
    reasons.append(f"• **유동비율 ({cr}%)**: 단기 채무 지급 능력 평가 (**{s1}/10점**)")

    de = data.get("debt_ratio") or 100
    s2 = (10 if de <= 30 else 9 if de <= 50 else 8 if de <= 75 
          else 7 if de <= 100 else 6 if de <= 125 else 5 if de <= 150 
          else 4 if de <= 175 else 3 if de <= 200 else 2 if de <= 250 
          else 1 if de <= 300 else 0)
    scores['score_debt_to_equity'] = s2
    reasons.append(f"• **부채비율 ({de}%)**: 재무 레버리지 및 안전성 (**{s2}/10점**)")

    roe = data.get("roe") or 0
    s3 = (10 if roe >= 25 else 9 if roe >= 20 else 8 if roe >= 16 
          else 7 if roe >= 13 else 6 if roe >= 10 else 5 if roe >= 7 
          else 4 if roe >= 5 else 3 if roe >= 3 else 2 if roe >= 1 
          else 1 if roe > 0 else 0)
    scores['score_roe'] = s3
    reasons.append(f"• **ROE ({roe}%)**: 자기자본 이익률 및 효율성 (**{s3}/10점**)")

    pbr = data.get("pbr") or 1.0
    s4 = (10 if pbr < 0.60 else 9 if pbr < 0.80 else 8 if pbr < 1.00 
          else 7 if pbr < 1.30 else 6 if pbr < 1.70 else 5 if pbr < 2.20 
          else 4 if pbr < 3.00 else 3 if pbr < 4.00 else 2 if pbr < 6.00 
          else 1 if pbr < 10.00 else 0)
    scores['score_pbr'] = s4
    reasons.append(f"• **PBR ({pbr}배)**: 자산 가치 대비 저평가 수준 (**{s4}/10점**)")

    op_m = data.get("op_margin") or 0
    s5 = (10 if op_m >= 20 else 8 if op_m >= 15 else 6 if op_m >= 10 
          else 4 if op_m >= 5 else 2 if op_m > 0 else 0)
    scores['score_op_margin'] = s5
    reasons.append(f"• **영업이익률 ({op_m}%)**: 본업 마진 및 가격 결정력 (**{s5}/10점**)")

    total_score = sum(scores.values())
    grade = 'S' if total_score >= 85 else ('A' if total_score >= 70 else ('B' if total_score >= 55 else ('C' if total_score >= 40 else 'D')))
    return total_score, grade, reasons

def format_korean_currency(val):
    if pd.isna(val): return "-"
    val = float(val)
    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    if abs_val >= 1_000_000_000_000:
        jo = abs_val // 1_000_000_000_000
        eok = (abs_val % 1_000_000_000_000) // 100_000_000
        return f"{sign}{int(jo):,}조 {int(eok):,}억원" if eok > 0 else f"{sign}{int(jo):,}조 원"
    elif abs_val >= 100_000_000:
        return f"{sign}{int(abs_val // 100_000_000):,}억원"
    else:
        return f"{sign}{int(abs_val):,}원"

# ==========================================
# 3. 화면 분기 (메인 포털 레이아웃 - 수정 완료 버전)
# ==========================================
if not selected_code:
    # 상단 헤더 영역 (로고 / 커진 Investor Quote / 로그인)
    col_logo, col_quote, col_login = st.columns([1.2, 5.8, 1])
    with col_logo:
        st.markdown("<div style='border: 2px solid #F4A261; border-radius: 8px; padding: 25px 10px; text-align: center; font-weight: bold; color: #D97706; background-color: #FFFDF9; height: 100%; display: flex; align-items: center; justify-content: center;'>🛡️ Logo</div>", unsafe_allow_html=True)
    with col_quote:
        st.markdown("<div class='quote-box'>💡 <b>Investor Image</b> &nbsp;|&nbsp; <i>\"하락장은 우량한 기업을 헐값에 살 수 있는 가장 위대한 기회다.\"</i></div>", unsafe_allow_html=True)
    with col_login:
        if st.button("Log in", use_container_width=True):
            st.toast("로그인 기능 준비 중!")

    st.markdown("<br>", unsafe_allow_html=True)

    # 3단 구조: 광고창 가로폭을 줄이고 세로로 길쭉하게 조정 ([0.8, 5.4, 0.8])
    left_ad, main_content, right_ad = st.columns([0.8, 5.4, 0.8])

    with left_ad:
        st.markdown("<div class='ad-box'><b>Ads</b><br><br>Ad Space</div>", unsafe_allow_html=True)

    with main_content:
        # [1, 2, 3, 4] 상단 탭 영역 (간격 벌어짐 적용됨)
        tab1, tab2, tab3, tab4 = st.tabs(["🇺🇸 1. Us stock", "🇰🇷 2. Korea stock", "📰 3. Live news", "💎 4. Gem"])
        
        with tab1: st.caption("미국 주식 펀더멘탈 분석 기능")
        with tab2: st.caption("한국 주식 재무제표 방어력 분석 기능")
        with tab3: st.caption("실시간 증시 속보 및 마켓 인사이트")
        with tab4: st.caption("AI 기반 젬(Gem) 종목 스크리닝")

        st.markdown("<br>", unsafe_allow_html=True)

        # [5] 중앙 검색 탭 영역 (흰색 바탕 + 주황색 테두리 조합으로 변경)
        st.markdown("<div style='background-color: #FFFFFF; border: 2px solid #F4A261; border-radius: 12px; padding: 25px; box-shadow: 0 4px 15px rgba(244,162,97,0.1);'>", unsafe_allow_html=True)
        st.markdown("### 🔍 5. Searching Tab (종목 통합 검색)")
        
        krx_stocks = get_all_krx_stocks()
        stock_options = list(krx_stocks.keys())
        selected_option = st.selectbox("분석할 한국 주식을 선택하세요", stock_options)
        target_code = krx_stocks[selected_option]

        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            f"""
            <div style="text-align: center;">
                <a href="/?code={target_code}" target="_blank" class="pastel-orange-btn">
                    🚀 새 탭(Popup)으로 상세 재무 분석 리포트 열기
                </a>
            </div>
            """,
            unsafe_allow_html=True
        )
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # [6, 7, 8] 하단 트렌드 포털 카드 3개 배치
        col_t1, col_t2, col_t3 = st.columns(3)
        
        with col_t1:
            st.markdown("""
                <div class='custom-card' style='height: 180px;'>
                    <b>6. Most searched Stocks</b><br><br>
                    • <a href='/?code=005930' target='_blank' style='color: #D97706; text-decoration: none;'>삼성전자 (005930)</a><br>
                    • <a href='/?code=000660' target='_blank' style='color: #D97706; text-decoration: none;'>SK하이닉스 (000660)</a>
                </div>
            """, unsafe_allow_html=True)
            
        with col_t2:
            st.markdown("""
                <div class='custom-card' style='height: 180px;'>
                    <b>7. Trending Searches (US)</b><br><br>
                    • Apple (AAPL)<br>
                    • Tesla (TSLA)<br>
                    • Microsoft (MSFT)
                </div>
            """, unsafe_allow_html=True)
            
        with col_t3:
            st.markdown("""
                <div class='custom-card' style='height: 180px;'>
                    <b>8. Trending Searches (KOR)</b><br><br>
                    • <a href='/?code=005380' target='_blank' style='color: #D97706; text-decoration: none;'>현대차 (005380)</a><br>
                    • <a href='/?code=000270' target='_blank' style='color: #D97706; text-decoration: none;'>기아 (000270)</a>
                </div>
            """, unsafe_allow_html=True)

    with right_ad:
        st.markdown("<div class='ad-box'><b>Ads</b><br><br>Ad Space</div>", unsafe_allow_html=True)

else:
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
