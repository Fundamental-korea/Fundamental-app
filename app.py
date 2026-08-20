import streamlit as st
import FinanceDataReader as fdr
from supabase import create_client
import pandas as pd
import altair as alt

# ==========================================
# 1. 페이지 및 Supabase 설정
# ==========================================
st.set_page_config(
    page_title="Fundamental Analyzer - 하락장 방어 플랫폼", 
    page_icon="🛡️", 
    layout="wide"
)

SUPABASE_URL = st.secrets.get("SUPABASE_URL", "https://cnweggechipghcivruie.supabase.co")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNud2VnZ2VjaGlwZ2hjaXZydWllIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcwODU5ODksImV4cCI6MjEwMjY2MTk4OX0.mYi7QB0ekkC0Jg49M18tqrMdCZBQgRHEK2J1EdIBZhc")

@st.cache_resource
def init_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()

# ==========================================
# 2. 데이터 불러오기 및 검색 함수
# ==========================================
@st.cache_data
def get_all_krx_stocks():
    """KRX 전체 종목 리스트를 가져와서 검색용 딕셔너리로 반환"""
    try:
        df = fdr.StockListing("KRX")
        return {f"{name} ({code})": code for name, code in zip(df["Name"], df["Code"])}
    except Exception:
        return {"삼성전자 (005930)": "005930", "SK하이닉스 (000660)": "000660"}

def get_stock_data(stock_code):
    res = supabase.table("Fundamental").select("*").eq("stock_code", stock_code).execute()
    return res.data[0] if res.data else None

# ==========================================
# 3. 10대 지표 하락장 방어력 스코어링 엔진 (0~100점 및 S~D 등급)
# ==========================================
def calculate_defense_score(data):
    scores = {}
    reasons = []

    # ① 유동비율
    cr = data.get("current_ratio") or 120 
    s1 = (10 if cr >= 250 else 9 if cr >= 200 else 8 if cr >= 170 
          else 7 if cr >= 150 else 6 if cr >= 130 else 5 if cr >= 110 
          else 4 if cr >= 100 else 3 if cr >= 85 else 2 if cr >= 70 
          else 1 if cr >= 50 else 0)
    scores['score_current_ratio'] = s1
    reasons.append(f"• **유동비율 ({cr}%)**: 단기 채무 지급 능력 평가 (**{s1}/10점**)")

    # ② 부채비율
    de = data.get("debt_ratio") or 100
    s2 = (10 if de <= 30 else 9 if de <= 50 else 8 if de <= 75 
          else 7 if de <= 100 else 6 if de <= 125 else 5 if de <= 150 
          else 4 if de <= 175 else 3 if de <= 200 else 2 if de <= 250 
          else 1 if de <= 300 else 0)
    scores['score_debt_to_equity'] = s2
    reasons.append(f"• **부채비율 ({de}%)**: 재무 레버리지 및 안전성 (**{s2}/10점**)")

    # ③ ROE
    roe = data.get("roe") or 0
    s3 = (10 if roe >= 25 else 9 if roe >= 20 else 8 if roe >= 16 
          else 7 if roe >= 13 else 6 if roe >= 10 else 5 if roe >= 7 
          else 4 if roe >= 5 else 3 if roe >= 3 else 2 if roe >= 1 
          else 1 if roe > 0 else 0)
    scores['score_roe'] = s3
    reasons.append(f"• **ROE ({roe}%)**: 자기자본 이익률 및 효율성 (**{s3}/10점**)")

    # ④ PBR
    pbr = data.get("pbr") or 1.0
    s4 = (10 if pbr < 0.60 else 9 if pbr < 0.80 else 8 if pbr < 1.00 
          else 7 if pbr < 1.30 else 6 if pbr < 1.70 else 5 if pbr < 2.20 
          else 4 if pbr < 3.00 else 3 if pbr < 4.00 else 2 if pbr < 6.00 
          else 1 if pbr < 10.00 else 0)
    scores['score_pbr'] = s4
    reasons.append(f"• **PBR ({pbr}배)**: 자산 가치 대비 저평가 수준 (**{s4}/10점**)")

    # ⑤ 영업이익률
    op_m = data.get("op_margin") or 0
    s5 = (10 if op_m >= 20 else 8 if op_m >= 15 else 6 if op_m >= 10 
          else 4 if op_m >= 5 else 2 if op_m > 0 else 0)
    scores['score_op_margin'] = s5
    reasons.append(f"• **영업이익률 ({op_m}%)**: 본업 마진 및 가격 결정력 (**{s5}/10점**)")

    # ⑥~⑩ 나머지 항목들
    for key, name in [('eps_growth', 'EPS 성장률'), ('fcf_margin', 'FCF 마진'), 
                       ('ocf_to_net_income', '현금흐름 질'), ('per_discount', 'PER 할인율'), 
                       ('net_income_trend_code', '순이익 트렌드')]:
        val = data.get(key)
        score_val = data.get(f"score_{key}", 5) if val is not None else 5
        scores[key] = score_val
        reasons.append(f"• **{name}**: 세부 정밀 평가 (**{score_val}/10점**)")

    total_score = sum(scores.values())
    
    if total_score >= 85: grade = 'S'
    elif total_score >= 70: grade = 'A'
    elif total_score >= 55: grade = 'B'
    elif total_score >= 40: grade = 'C'
    else: grade = 'D'

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

def format_yearly_dataframe(df_target, stock_name):
    df_f = df_target.copy()
    if "year" in df_f.columns: 
        df_f["year"] = df_f["year"].astype(int)
    if "net_income" in df_f.columns: df_f["net_income"] = df_f["net_income"].apply(format_korean_currency)
    if "total_equity" in df_f.columns: df_f["total_equity"] = df_f["total_equity"].apply(format_korean_currency)
    if "eps" in df_f.columns: df_f["eps"] = df_f["eps"].apply(lambda x: f"{int(x):,} 원" if pd.notna(x) else "-")
    if "bps" in df_f.columns: df_f["bps"] = df_f["bps"].apply(lambda x: f"{int(x):,} 원" if pd.notna(x) else "-")
    if "roe" in df_f.columns: df_f["roe"] = df_f["roe"].apply(lambda x: f"{x:,.2f}%" if pd.notna(x) else "-")
    if "debt_ratio" in df_f.columns: df_f["debt_ratio"] = df_f["debt_ratio"].apply(lambda x: f"{x:,.2f}%" if pd.notna(x) else "-")
    
    df_f["종목명"] = stock_name
    
    rename_dict = {
        "year": "연도", "종목명": "종목명", "net_income": "당기순이익",
        "total_equity": "자본총계", "eps": "EPS (주당순이익)", "bps": "BPS (주당순자산)",
        "roe": "ROE", "debt_ratio": "부채비율"
    }
    df_f = df_f.rename(columns=rename_dict)
    desired_order = ["종목명", "연도", "당기순이익", "자본총계", "EPS (주당순이익)", "BPS (주당순자산)", "ROE", "부채비율"]
    existing_cols = [c for c in desired_order if c in df_f.columns]
    return df_f[existing_cols]

# ==========================================
# 4. 세션 상태 관리 (메인 화면 vs 상세 분석 창)
# ==========================================
if "selected_code" not in st.session_state:
    st.session_state.selected_code = None

# ==========================================
# 5. 화면 분기 렌더링
# ==========================================
if st.session_state.selected_code is None:
    # ----------------------------------------------------
    # [뷰 A] 메인 화면 (검색 포털 및 UI 스케치 반영)
    # ----------------------------------------------------
    col_logo, col_quote, col_login = st.columns([1, 4, 1])
    with col_logo:
        st.markdown("### 🛡️ Logo")
    with col_quote:
        st.info("💡 *\"하락장은 우량한 기업을 헐값에 살 수 있는 가장 위대한 기회다.\"* — 펀더멘탈 분석")
    with col_login:
        if st.button("Log in"):
            st.toast("로그인 기능은 준비 중입니다!")

    st.markdown("---")

    left_ad, main_content, right_ad = st.columns([1, 4, 1])

    with left_ad:
        st.markdown("---")
        st.markdown("<div style='text-align: center; color: gray;'><b>Ads</b><br><br>Advertisement Space</div>", unsafe_allow_html=True)
        st.markdown("---")

    with main_content:
        tab1, tab2, tab3, tab4 = st.tabs(["🇺🇸 US stock", "🇰🇷 Korea stock", "📰 Live news", "💎 Gem"])

        with tab1:
            st.caption("미국 주식 시장의 펀더멘탈 검색을 지원합니다.")
        with tab2:
            st.caption("한국 주식 시장의 재무제표 검색을 지원합니다.")
        with tab3:
            st.caption("실시간 마켓 뉴스를 제공합니다.")
        with tab4:
            st.caption("AI 젬(Gem) 인사이트 분석 서비스입니다.")

        st.markdown("<br>", unsafe_allow_html=True)

        st.markdown("## 🔍 펀더멘탈 종목 검색")
        
        krx_stocks = get_all_krx_stocks()
        stock_options = list(krx_stocks.keys())
        
        selected_option = st.selectbox("분석하고 싶은 한국 주식 선택 (종목명/코드)", stock_options)
        
        col_btn1, col_btn2 = st.columns([1, 5])
        with col_btn1:
            if st.button("종목 분석", type="primary"):
                st.session_state.selected_code = krx_stocks[selected_option]
                st.rerun()

        st.markdown("<br><br>", unsafe_allow_html=True)

        st.markdown("### 🔥 Market Trends")
        col_t1, col_t2, col_t3 = st.columns(3)
        
        with col_t1:
            st.markdown("##### 6. Most searched Stocks")
            if st.button("삼성전자 (005930) 바로가기"):
                st.session_state.selected_code = "005930"
                st.rerun()
            if st.button("SK하이닉스 (000660) 바로가기"):
                st.session_state.selected_code = "000660"
                st.rerun()
            
        with col_t2:
            st.markdown("##### 7. Trending Searches (US)")
            st.caption("- 애플 (AAPL)\n- 테슬라 (TSLA)\n- 마이크로소프트 (MSFT)")
            
        with col_t3:
            st.markdown("##### 8. Trending Searches (KOR)")
            if st.button("현대차 (005380) 바로가기"):
                st.session_state.selected_code = "005380"
                st.rerun()
            if st.button("기아 (000270) 바로가기"):
                st.session_state.selected_code = "000270"
                st.rerun()

    with right_ad:
        st.markdown("---")
        st.markdown("<div style='text-align: center; color: gray;'><b>Ads</b><br><br>Advertisement Space</div>", unsafe_allow_html=True)
        st.markdown("---")

else:
    # ----------------------------------------------------
    # [뷰 B] 선택한 종목 상세 분석 창 (새로운 화면으로 열림)
    # ----------------------------------------------------
    selected_code = st.session_state.selected_code
    
    if st.button("⬅️ 메인 검색 화면으로 돌아가기", type="secondary"):
        st.session_state.selected_code = None
        st.rerun()

    st.markdown("---")
    
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

        st.subheader(f"📊 [{stock_name}] ({selected_code}) 실시간 재무 분석 리포트")
        
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("현재 실시간 주가", f"{live_price:,} 원", f"{live_price - data.get('stock_price', live_price):,} 원" if data.get('stock_price') else None)
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
            if grade == 'S': st.success("🟢 **S등급 (하락장 최적 요새형 방어주)**")
            elif grade == 'A': st.success("🟢 **A등급 (우수한 재무 체력)**")
            elif grade == 'B': st.info("🟡 **B등급 (평균적인 방어력)**")
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
            numeric_cols = ["net_income", "total_equity", "eps", "bps", "roe", "debt_ratio"]
            for col in numeric_cols:
                if col in df_hist.columns:
                    df_hist[col] = pd.to_numeric(df_hist[col], errors='coerce')

            if 'year' in df_hist.columns:
                df_hist = df_hist.sort_values("year")

                with tab_5y:
                    st.markdown(f"#### 📅 [{stock_name}] 최근 5개년 재무 흐름")
                    df_5 = df_hist.tail(5).copy()
                    df_5['year'] = df_5['year'].astype(int)
                    df_5['순이익_조원'] = df_5['net_income'] / 1_000_000_000_000
                    
                    chart_5y = alt.Chart(df_5).mark_bar(color="#ff4b4b", size=30).encode(
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
                    
                    chart_3y = alt.Chart(df_3).mark_bar(color="#ff4b4b", size=30).encode(
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
            st.markdown(f"#### ⚡ [{stock_name}] 가장 최근 4개 분기 실적 및 수익성 심층 분석")
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
                    
                    st.markdown("##### 📈 최근 4개 분기 당기순이익 추이")
                    chart = alt.Chart(df_chart).mark_bar(color="#ffa500", size=30).encode(
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
                    
                    df_table = df_table.drop(columns=["_raw_net"])
                    
                    st.markdown("##### 📄 분기별 재무제표 심층 요약")
                    st.dataframe(df_table, use_container_width=True, hide_index=True)
                else:
                    st.warning("해당 종목의 분기 실적 데이터를 불러올 수 없습니다.")
            except Exception as e:
                st.error(f"분기 데이터를 불러오는 중 오류가 발생했습니다: {e}")
    else:
        st.error("선택한 종목의 데이터가 Supabase DB에 존재하지 않습니다. 먼저 데이터를 수집해 주세요!")
