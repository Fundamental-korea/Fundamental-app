import streamlit as st
import FinanceDataReader as fdr
from supabase import create_client

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
    # {'삼성전자 (005930)': '005930', ...} 형태의 딕셔너리 생성
    return {f"{name} ({code})": code for name, code in zip(df["Name"], df["Code"])}

def get_stock_data(stock_code):
    res = supabase.table("Fundamental").select("*").eq("stock_code", stock_code).execute()
    return res.data[0] if res.data else None

# ==========================================
# 3. 하락장 방어력 스코어 계산 함수
# ==========================================
def calculate_defense_score(data):
    score = 50
    reasons = []
    roe = data.get("roe") or 0
    if roe >= 15:
        score += 20
        reasons.append("✅ **ROE 15% 이상**: 높은 자본 효율성 (+20점)")
    elif roe >= 10:
        score += 10
        reasons.append("✅ **ROE 10% 이상**: 양호한 수익성 (+10점)")
    else:
        reasons.append("⚠️ **ROE 10% 미만**: 수익성 개선 필요 (0점)")

    debt = data.get("debt_ratio") or 999
    if debt <= 50:
        score += 20
        reasons.append("✅ **부채비율 50% 이하**: 탄탄한 재무 구조 (+20점)")
    elif debt <= 100:
        score += 10
        reasons.append("✅ **부채비율 100% 이하**: 안정적 부채 수준 (+10점)")
    else:
        reasons.append("⚠️ **부채비율 100% 초과**: 하락장 이자 부담 위험 (0점)")

    op_m = data.get("op_margin") or 0
    if op_m >= 15:
        score += 10
        reasons.append("✅ **영업이익률 15% 이상**: 강력한 마진/가격 결정력 (+10점)")

    return min(score, 100), reasons

# ==========================================
# 4. 웹 UI 화면 구성
# ==========================================
st.title("🛡️ 펀더멘탈(Fundamental) - 하락장 방어 알짜기업 분석")
st.caption("시장이 흔들려도 버티는 재무제표 완벽 분석 플랫폼")

# 사이드바에서 KRX 전체 종목 검색
st.sidebar.header("🔍 한국 주식 검색")
krx_stocks = get_all_krx_stocks()
selected_option = st.sidebar.selectbox("종목 검색 (이름/코드 입력)", list(krx_stocks.keys()))
selected_code = krx_stocks[selected_option]

# DB에서 해당 종목의 데이터 조회
data = get_stock_data(selected_code)

if data:
    st.subheader(f"📊 {data['stock_name']} ({data['stock_code']}) 재무 분석")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("현재 주가", f"{data['stock_price']:,} 원")
    col2.metric("PER", f"{data['per']} 배" if data["per"] else "N/A")
    col3.metric("PBR", f"{data['pbr']} 배" if data["pbr"] else "N/A")
    col4.metric("ROE", f"{data['roe']}%" if data["roe"] else "N/A")

    st.divider()

    score, reasons = calculate_defense_score(data)
    col_score, col_detail = st.columns([1, 2])
    with col_score:
        st.markdown("### 🛡️ 하락장 방어 점수")
        st.title(f"**{score}** / 100 점")
        if score >= 80: st.success("🟢 **최우수 (하락장 최적 방어주)**")
        elif score >= 60: st.info("🟡 **우수 (평균 이상의 체력)**")
        else: st.warning("🔴 **주의 (변동성 장세 주의)**")
    with col_detail:
        st.markdown("### 📋 세부 평가 항목")
        for r in reasons: st.write(r)

    st.divider()
    with st.expander("📄 상세 재무 데이터"):
        st.json({
            "매출액": f"{data['revenue']:,}",
            "영업이익": f"{data['operating_income']:,}",
            "당기순이익": f"{data['net_income']:,}",
            "자산총계": f"{data['total_assets']:,}",
            "부채총계": f"{data['total_liabilities']:,}",
            "자본총계": f"{data['total_equity']:,}",
            "부채비율": f"{data['debt_ratio']}%",
            "영업이익률": f"{data['op_margin']}%",
        })
else:
    st.warning(f"⚠️ **[{selected_option.split('(')[0].strip()}]** 데이터가 DB에 없습니다.")
    st.info("데이터를 보려면 `collector.py` 대상 종목 리스트에 이 종목을 추가하세요!")
