import streamlit as st
import FinanceDataReader as fdr
from supabase import create_client
import pandas as pd

# ==========================================
# 1. 페이지 및 Supabase 설정
# ==========================================
st.set_page_config(
    page_title="펀더멘탈 - 알짜기업 분석기", page_icon="📈", layout="wide"
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
    df = fdr.StockListing("KRX")
    return {f"{name} ({code})": code for name, code in zip(df["Name"], df["Code"])}

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

# ==========================================
# 4. 웹 UI 화면 구성
# ==========================================
st.title("🛡️ 펀더멘탈(Fundamental) - 하락장 방어 알짜기업 분석")
st.caption("시장이 흔들려도 버티는 재무제표 10대 지표 정밀 분석 플랫폼 (실시간 연동)")

# 사이드바에서 KRX 전체 종목 검색
st.sidebar.header("🔍 한국 주식 검색")
krx_stocks = get_all_krx_stocks()
selected_option = st.sidebar.selectbox("종목 검색 (이름/코드 입력)", list(krx_stocks.keys()))
selected_code = krx_stocks[selected_option]

# DB에서 해당 종목의 기본 재무 데이터 조회
data = get_stock_data(selected_code)

if data:
    try:
        df_price = fdr.DataReader(selected_code)
        live_price = int(df_price['Close'].iloc[-1]) if not df_price.empty else data.get('stock_price', 0)
    except Exception:
        live_price = data.get('stock_price', 0)

    # 실시간 주가 변동에 따른 PER/PBR 동적 보정
    db_eps = data.get('eps')
    live_per = round(live_price / db_eps, 2) if db_eps and db_eps > 0 else data.get('per')

    db_bps = data.get('bps')
    live_pbr = round(live_price / db_bps, 2) if db_bps and db_bps > 0 else data.get('pbr')

    st.subheader(f"📊 {data.get('stock_name')} ({data.get('stock_code')}) 실시간 재무 분석")
    
    # 상단 지표 카드
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("현재 실시간 주가", f"{live_price:,} 원", f"{live_price - data.get('stock_price', live_price):,} 원" if data.get('stock_price') else None)
    col2.metric("PER", f"{live_per} 배" if live_per else "N/A")
    col3.metric("PBR", f"{live_pbr} 배" if live_pbr else "N/A")
    col4.metric("ROE", f"{data.get('roe')}%" if data.get("roe") else "N/A")

    st.divider()

    # 방어력 점수 계산 시 실시간 보정 데이터 반영
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
# ==========================================
    # 5. 기간별 탭 버튼 및 심층 분석 (수정된 버전)
    # ==========================================
    st.subheader(f"📊 [{data.get('stock_name')}] 재무제표 기간별 심층 분석")
    tab_5y, tab_3y, tab_q = st.tabs(["📅 5년 장기 흐름", "🕒 3년 핵심 집중", "⚡ 최근 4분기 단기 실적"])

    # 1. 연도별 데이터 처리 함수 (콤마 제거 & 막대 차트 연동)
    def render_yearly_tab(tab_obj, df_hist, years_count, title):
        with tab_obj:
            st.markdown(f"#### 🏢 {title}")
            df_target = df_hist.tail(years_count).copy()
            
            # 연도 콤마 제거 및 정수형 변환
            df_target['year'] = df_target['year'].astype(int)
            
            # 막대 차트용 데이터셋
            df_chart = df_target.copy()
            df_chart['순이익_조원'] = df_chart['net_income'] / 1_000_000_000_000
            
            # 📈 막대 차트 생성
            st.bar_chart(df_chart.set_index("year")[["순이익_조원"]], color="#ff4b4b")
            
            # 📄 표 출력 (종목명 맨 앞, 연도 콤마 제거)
            df_display = df_target.copy()
            # 포맷팅 로직 (위에 정의한 format_yearly_dataframe 활용)
            st.dataframe(format_yearly_dataframe(df_display, stock_name), use_container_width=True, hide_index=True)

    # ... (history_data 호출 후)
    if history_data.data:
        df_hist = pd.DataFrame(history_data.data)
        # 연도 정렬
        df_hist = df_hist.sort_values("year")
        
        render_yearly_tab(tab_5y, df_hist, 5, f"[{stock_name}] 최근 5개년 재무 흐름")
        render_yearly_tab(tab_3y, df_hist, 3, f"[{stock_name}] 최근 3개년 집중 분석")

    # 2. 최근 4분기 (막대 차트 적용)
    with tab_q:
        st.markdown(f"#### ⚡ [{stock_name}] 최근 4분기 실적 분석")
        # ... (yfinance 데이터 로직은 동일)
        
        # 📈 막대 차트 (가로 정렬 완료)
        chart = alt.Chart(df_chart).mark_bar(color="#ffa500").encode(
            x=alt.X('분기 기준일:N', title='분기 기준일', axis=alt.Axis(labelAngle=0)),
            y=alt.Y('순이익_조원:Q', title='당기순이익 (조 원)'),
            tooltip=['분기 기준일', '순이익_조원']
        ).properties(height=320)
        st.altair_chart(chart, use_container_width=True)
        
        # ... (이하 테이블 출력 로직)
