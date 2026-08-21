import json
import random
import FinanceDataReader as fdr
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from supabase import create_client
import yfinance as yf

# ==========================================
# 1. 페이지 및 커스텀 디자인 설정 (다크/라이트 통합 대응)
# ==========================================
st.set_page_config(
    page_title="Fundamental Analyzer - 하락장 방어 플랫폼",
    page_icon="🛡️",
    layout="wide",
)

st.markdown(
    """
    <style>
    /* 전체 배경 스타일 */
    html, body, [data-testid="stAppViewContainer"], .stApp {
        background-color: #0F172A !important;
        color: #F8FAFC !important;
    }

    p, span, div, label, h1, h2, h3, h4, h5, h6 {
        color: #F8FAFC !important;
    }

    /* 상단 로고 박스 */
    .logo-box {
        border: 2px solid #38BDF8;
        border-radius: 12px;
        background-color: #1E293B;
        color: #38BDF8 !important;
        font-weight: 800;
        font-size: 20px;
        height: 100px !important;
        min-height: 100px !important;
        display: flex;
        align-items: center;
        justify-content: center;
        text-align: center;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    }

    /* 상단 명언 박스 */
    .quote-box {
        background-color: #1E293B;
        border: 1.5px solid #334155;
        border-radius: 12px;
        height: 100px !important;
        min-height: 100px !important;
        display: flex;
        flex-direction: row;
        align-items: center;
        justify-content: center;
        gap: 20px;
        padding: 0 25px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.2);
    }

    .quote-text {
        font-size: 16px;
        font-weight: 600;
        color: #E2E8F0 !important;
    }

    /* 좌우 Ads 영역 */
    .ad-box-tall {
        background-color: #1E293B;
        border: 2px dashed #475569;
        border-radius: 12px;
        text-align: center;
        color: #64748B !important;
        font-weight: bold;
        font-size: 18px;
        min-height: 850px;
        height: 100%;
        display: flex;
        align-items: center;
        justify-content: center;
        box-sizing: border-box;
    }

    /* Streamlit 일반 버튼 스타일 */
    div[data-testid="stButton"] > button, div.stButton > button {
        background-color: #1E293B !important;
        color: #F8FAFC !important;
        border: 1.5px solid #38BDF8 !important;
        border-radius: 10px !important;
        font-size: 15px !important;
        font-weight: 700 !important;
        transition: all 0.2s ease !important;
    }

    div[data-testid="stButton"] > button:hover, div.stButton > button:hover {
        background-color: #38BDF8 !important;
        color: #0F172A !important;
    }

    /* Streamlit 네이티브 st.tabs 디자인 강제 덮어쓰기 */
    div[data-testid="stTabs"] [data-baseweb="tab-list"] {
        gap: 20px !important;
        border-bottom: 2px solid #334155 !important;
        padding-bottom: 2px !important;
        background-color: transparent !important;
    }

    div[data-testid="stTabs"] [data-baseweb="tab"] {
        height: 50px !important;
        background-color: transparent !important;
        border: none !important;
        padding: 0px 8px !important;
    }

    div[data-testid="stTabs"] [data-baseweb="tab"] p,
    div[data-testid="stTabs"] [data-baseweb="tab"] span {
        font-size: 18px !important;
        font-weight: 800 !important;
        color: #94A3B8 !important;
    }

    div[data-testid="stTabs"] [aria-selected="true"] {
        border-bottom: 4px solid #38BDF8 !important;
    }

    div[data-testid="stTabs"] [aria-selected="true"] p,
    div[data-testid="stTabs"] [aria-selected="true"] span {
        color: #38BDF8 !important;
        font-size: 19px !important;
    }

    /* 하단 트렌드 카드 */
    .bottom-cards-wrapper {
        margin-top: 25px;
    }
    .sketch-card {
        background-color: #1E293B;
        border: 1.5px solid #334155;
        border-radius: 12px;
        padding: 18px 20px;
        min-height: 290px;
        box-shadow: 0 4px 10px rgba(0,0,0,0.3);
    }
    .card-item-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 8px 0;
        border-bottom: 1px dashed #334155;
    }
    .card-item-row:last-child {
        border-bottom: none;
    }
    .stock-link {
        color: #38BDF8 !important;
        text-decoration: none !important;
        font-weight: 600;
        font-size: 14px;
    }
    .stock-link:hover {
        text-decoration: underline !important;
    }
    .search-count-badge {
        font-size: 11px;
        color: #94A3B8;
        background-color: #0F172A;
        padding: 2px 8px;
        border-radius: 12px;
        font-weight: 600;
    }

    /* Slide 5 Expander 드롭다운 스타일 */
    div[data-testid="stExpander"] {
        background-color: #1E293B !important;
        border: 1.5px solid #334155 !important;
        border-radius: 10px !important;
        margin-bottom: 12px !important;
    }
    .stExpander p, .stExpander span {
        color: #CBD5E1 !important;
    }
    </style>
""",
    unsafe_allow_html=True,
)

# ==========================================
# 2. 데이터 및 세션 상태 초기화 (Supabase & yfinance & FDR)
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
    ticker_symbol = f"{code}.KS" if code.isdigit() else code
    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info
        hist = ticker.history(period="1y")

        return {
            "name": info.get("shortName", code),
            "symbol": code,
            "hist": hist,
            "revenue_growth": info.get("revenueGrowth", 0.08) * 100 if info.get("revenueGrowth") else 8.5,
            "eps_growth": info.get("earningsGrowth", 0.10) * 100 if info.get("earningsGrowth") else 12.0,
            "opm": info.get("operatingMargins", 0.15) * 100 if info.get("operatingMargins") else 15.0,
            "roe": info.get("returnOnEquity", 0.12) * 100 if info.get("returnOnEquity") else 12.0,
            "debt_rate": info.get("debtToEquity", 75.0) if info.get("debtToEquity") else 75.0,
            "current_ratio": info.get("currentRatio", 1.6) if info.get("currentRatio") else 1.6,
            "operating_cf": info.get("operatingCashflow", 50000000) if info.get("operatingCashflow") else 50000000,
            "pbr": info.get("priceToBook", 1.2) if info.get("priceToBook") else 1.2,
            "per": info.get("trailingPE", 14.0) if info.get("trailingPE") else 14.0,
        }
    except Exception:
        df = pd.DataFrame({"Close": [100 + i + (i % 3) * 2 for i in range(100)]})
        return {
            "name": f"Stock ({code})",
            "symbol": code,
            "hist": df,
            "revenue_growth": 8.5,
            "eps_growth": 12.0,
            "opm": 15.2,
            "roe": 14.0,
            "debt_rate": 65.0,
            "current_ratio": 1.8,
            "operating_cf": 50000000,
            "pbr": 1.1,
            "per": 12.5,
        }


def calculate_10_scores(data):
    """Slide 5 스케치 와이어프레임용 10가지 재무지표 Scoring 엔진"""
    scores = {}

    # 1. Revenue Growth
    rg = data["revenue_growth"]
    scores["1. Revenue Growth"] = {
        "val": f"{rg:.1f}%",
        "score": 10 if rg >= 15 else (7 if rg >= 5 else 4),
        "explanation": "기업의 외형 성장을 보여주는 매출액 증가율입니다.",
        "importance": "High: 하락장에서도 매출이 성장하는 기업은 시장 점유율을 늘리고 있다는 증거입니다.",
    }

    # 2. EPS Growth
    eg = data["eps_growth"]
    scores["2. EPS Growth"] = {
        "val": f"{eg:.1f}%",
        "score": 10 if eg >= 12 else (7 if eg >= 4 else 3),
        "explanation": "주당순이익(EPS)의 성장 속도를 측정합니다.",
        "importance": "High: 주당 이익이 늘어나는 기업은 조정장 이후 주가 회복 속도가 가장 빠릅니다.",
    }

    # 3. OPM
    opm = data["opm"]
    scores["3. OPM"] = {
        "val": f"{opm:.1f}%",
        "score": 10 if opm >= 20 else (7 if opm >= 10 else 4),
        "explanation": "매출액 대비 순수 영업활동으로 창출한 영업이익률입니다.",
        "importance": "Critical: 높은 이익률은 인플레이션 및 원가 상승 압박을 견디는 가격 결정력을 의미합니다.",
    }

    # 4. ROE
    roe = data["roe"]
    scores["4. ROE"] = {
        "val": f"{roe:.1f}%",
        "score": 10 if roe >= 15 else (7 if roe >= 8 else 4),
        "explanation": "자기자본을 얼마나 효율적으로 활용해 이익을 내는지 나타냅니다.",
        "importance": "Critical: 워런 버핏이 가장 강조하는 지표로, 장기 복리 수익의 핵심 구동축입니다.",
    }

    # 5. Debt rate
    dr = data["debt_rate"]
    scores["5. Debt rate"] = {
        "val": f"{dr:.1f}%",
        "score": 10 if dr <= 50 else (7 if dr <= 100 else 3),
        "explanation": "타인자본(부채)과 자기자본의 비율입니다.",
        "importance": "Fatal in Bear Market: 고금리 하락장에서 부채 비율이 높은 기업은 유동성 위기를 맞습니다.",
    }

    # 6. Current ratio
    cr = data["current_ratio"]
    scores["6. Current ratio"] = {
        "val": f"{cr:.2f}배",
        "score": 10 if cr >= 2.0 else (7 if cr >= 1.2 else 3),
        "explanation": "1년 이내 현금화 가능한 유동자산 비율입니다.",
        "importance": "High: 단기 채무 대응 능력을 나타내며 1.5배 이상이어야 신용 위기를 방어합니다.",
    }

    # 7. Interest coverage rate
    icr = max(1.0, opm / 2.0)
    scores["7. Interest coverage rate"] = {
        "val": f"{icr:.1f}배",
        "score": 10 if icr >= 5.0 else (7 if icr >= 2.0 else 2),
        "explanation": "영업이익으로 금융 이자 비용을 감당할 수 있는 이자보상배율입니다.",
        "importance": "Critical: 1배 미만인 한계기업은 금리 상승기 및 경기 후퇴기에 도산 위험에 노출됩니다.",
    }

    # 8. Operating cash flow
    ocf = data["operating_cf"]
    scores["8. Operating cash flow"] = {
        "val": "양수 (+)" if ocf > 0 else "음수 (-)",
        "score": 10 if ocf > 0 else 2,
        "explanation": "장부상 이익이 아닌 실제 통장에 유입된 영업현금흐름입니다.",
        "importance": "Critical: 영업현금흐름이 음수인 기업은 장부상 흑자라도 흑자도산 가능성이 존재합니다.",
    }

    # 9. Retained Earnings Ratio
    rer = 1100.0 if data["pbr"] > 1.0 else 450.0
    scores["9. Retained Earnings Ratio"] = {
        "val": f"{rer:.0f}%",
        "score": 10 if rer >= 1000 else (7 if rer >= 500 else 4),
        "explanation": "기업 내부에 축적된 유보금의 비율입니다.",
        "importance": "Medium: 풍부한 유보금은 약세장에서 무상증자나 자사주 매입/소각의 재원이 됩니다.",
    }

    # 10. SG&A Ratio
    sga = 14.5
    scores["10. SG&A Ratio"] = {
        "val": f"{sga:.1f}%",
        "score": 10 if sga <= 15 else (7 if sga <= 30 else 4),
        "explanation": "매출액 대비 판매비와 관리비(판관비) 비율입니다.",
        "importance": "Medium: 판관비 구조가 가벼운 기업일수록 하락장에서 비용 절감이 유리합니다.",
    }

    return scores


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
                border: 2px solid #38BDF8;
                border-radius: 12px;
                font-size: 16px;
                font-weight: 600;
                outline: none;
                background: #1E293B;
                color: #F8FAFC;
                box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            }}
            .input-box:focus {{
                border-color: #0284C7;
                box-shadow: 0 0 10px rgba(56, 189, 248, 0.4);
            }}
            .search-icon {{
                position: absolute;
                right: 18px;
                top: 15px;
                font-size: 20px;
                color: #38BDF8;
                cursor: pointer;
            }}

            .autocomplete-modal {{
                display: none;
                flex-direction: column;
                position: absolute;
                top: 60px;
                left: 0;
                width: 100%;
                background: #1E293B;
                border: 1px solid #334155;
                border-radius: 12px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.5);
                z-index: 9999;
                overflow: hidden;
            }}

            .modal-content {{
                display: flex;
                min-height: 320px;
            }}

            .left-pane {{
                flex: 65;
                border-right: 1px solid #334155;
                padding: 10px 0;
                max-height: 360px;
                overflow-y: auto;
            }}
            .pane-title {{
                font-size: 12px;
                font-weight: 700;
                color: #94A3B8;
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
                background-color: #0F172A;
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
                color: #F8FAFC;
                font-size: 14px;
                min-width: 65px;
            }}
            .name {{
                font-size: 13px;
                color: #CBD5E1;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }}
            .exch {{
                font-size: 11px;
                color: #64748B;
                white-space: nowrap;
            }}
            .highlight {{
                color: #38BDF8;
                font-weight: 800;
                background-color: #0F172A;
                padding: 0 2px;
            }}

            .right-pane {{
                flex: 35;
                background-color: #0F172A;
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
                color: #94A3B8;
            }}
            .more-link {{
                font-size: 11px;
                color: #38BDF8;
                text-decoration: none;
            }}
            .news-item {{
                font-size: 12px;
                color: #CBD5E1;
                line-height: 1.4;
                font-weight: 500;
                cursor: pointer;
            }}
            .news-item:hover {{
                text-decoration: underline;
                color: #38BDF8;
            }}

            .modal-footer {{
                border-top: 1px solid #334155;
                padding: 10px 16px;
                background: #0F172A;
                font-size: 13px;
                color: #38BDF8;
                font-weight: 600;
                cursor: pointer;
                display: flex;
                align-items: center;
                gap: 6px;
            }}
            .modal-footer:hover {{
                background: #1E293B;
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
                window.open(targetUrl, '_self');
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
# 4. 라우팅 및 화면 전환 제어 (메인 홈 vs Slide 5 검색 화면)
# ==========================================
query_params = st.query_params
selected_code = query_params.get("code", None)

if not selected_code:
    # ------------------------------------------------------------------
    # [화면 1: 메인 포털 홈 화면]
    # ------------------------------------------------------------------
    col_logo, col_quote, col_login = st.columns([1.5, 7, 1.5])

    with col_logo:
        st.markdown("<div class='logo-box'>📈 Fundamental</div>", unsafe_allow_html=True)

    with col_quote:
        quotes = [
            "하락장은 우량한 기업을 헐값에 살 수 있는 가장 위대한 기회다. - 워런 버핏",
            "시장이 공포에 질려 있을 때가 탐욕을 부릴 최적의 시기다. - 벤자민 그레이엄",
            "투자는 지능이 아니라 인내심의 게임이다. - 피터 린치",
            "가격은 내가 지불하는 것이고, 가치는 내가 얻는 것이다. - 워런 버핏",
        ]
        st.markdown(
            f"<div class='quote-box'><div class='quote-text'>"{random.choice(quotes)}"</div></div>",
            unsafe_allow_html=True,
        )

    with col_login:
        st.markdown("<div style='height: 25px;'></div>", unsafe_allow_html=True)
        if st.button("Log in", use_container_width=True):
            st.toast("로그인 기능 준비 중입니다!")

    st.markdown("<br>", unsafe_allow_html=True)

    # 메인 포털 3단 컬럼: Ads (Left) | Main Content | Ads (Right)
    left_ad, main_content, right_ad = st.columns([1.2, 7.6, 1.2])

    with left_ad:
        st.markdown("<div class='ad-box-tall'>ads</div>", unsafe_allow_html=True)

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
            st.markdown("<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True)
            render_unified_search_box(stock_db=combined_stocks_db)
            st.info("🇺🇸 **US Stock Market Overview**: S&P 500, 나스닥 지수 흐름 및 미국 기업 펀더멘탈 검색 공간입니다.")

        with tab2:
            st.markdown("<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True)
            render_unified_search_box(stock_db=combined_stocks_db)
            st.info("🇰🇷 **Korea Stock Market Overview**: 코스피, 코스닥 지수 동향 및 한국 기업 재무 검색 공간입니다.")

        with tab3:
            st.markdown("<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True)
            render_unified_search_box(stock_db=combined_stocks_db)
            st.info("📰 **Live News**: 글로벌 증시 속보 및 하락장 리스크 관리 뉴스 모니터링 공간입니다.")

        with tab4:
            st.markdown("<div style='margin-bottom: 15px;'></div>", unsafe_allow_html=True)
            render_unified_search_box(stock_db=combined_stocks_db)
            st.info("💎 **Gem Screener**: ROE/PER/PBR 요건을 충족한 하락장 우수 방어주(Gem) 스크리닝 공간입니다.")

        # 하단 트렌드 추천 카드 3개
        st.markdown("<div class='bottom-cards-wrapper'>", unsafe_allow_html=True)
        col_6, col_7, col_8 = st.columns(3)

        with col_6:
            st.markdown(
                """
                <div class='sketch-card'>
                    <b style='color: #38BDF8; font-size: 15px;'>🔥 Most Searched Stocks</b>
                    <div style='margin-top: 12px;'>
                        <div class='card-item-row'>
                            <a href='/?code=005930' class='stock-link'>1. 삼성전자 (005930)</a>
                            <span class='search-count-badge'>18,420회</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=NVDA' class='stock-link'>2. NVIDIA (NVDA)</a>
                            <span class='search-count-badge'>15,810회</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=000660' class='stock-link'>3. SK하이닉스 (000660)</a>
                            <span class='search-count-badge'>12,340회</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=AAPL' class='stock-link'>4. Apple (AAPL)</a>
                            <span class='search-count-badge'>9,580회</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=TSLA' class='stock-link'>5. Tesla (TSLA)</a>
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
                    <b style='color: #38BDF8; font-size: 15px;'>🇺🇸 Trending Searches (US)</b>
                    <div style='margin-top: 12px;'>
                        <div class='card-item-row'>
                            <a href='/?code=NVDA' class='stock-link'>1. NVIDIA (NVDA)</a>
                            <span style='font-size: 11px; color: #22C55E; font-weight: 700;'>▲ HOT</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=AAPL' class='stock-link'>2. Apple (AAPL)</a>
                            <span style='font-size: 11px; color: #22C55E; font-weight: 700;'>▲ UP</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=PLTR' class='stock-link'>3. Palantir (PLTR)</a>
                            <span style='font-size: 11px; color: #22C55E; font-weight: 700;'>▲ HOT</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=MSFT' class='stock-link'>4. Microsoft (MSFT)</a>
                            <span style='font-size: 11px; color: #94A3B8; font-weight: 600;'>- STABLE</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=AMZN' class='stock-link'>5. Amazon (AMZN)</a>
                            <span style='font-size: 11px; color: #22C55E; font-weight: 700;'>▲ UP</span>
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
                    <b style='color: #38BDF8; font-size: 15px;'>🇰🇷 Trending Searches (KR)</b>
                    <div style='margin-top: 12px;'>
                        <div class='card-item-row'>
                            <a href='/?code=005930' class='stock-link'>1. 삼성전자 (005930)</a>
                            <span style='font-size: 11px; color: #22C55E; font-weight: 700;'>▲ HOT</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=000660' class='stock-link'>2. SK하이닉스 (000660)</a>
                            <span style='font-size: 11px; color: #22C55E; font-weight: 700;'>▲ HOT</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=005380' class='stock-link'>3. 현대차 (005380)</a>
                            <span style='font-size: 11px; color: #94A3B8; font-weight: 600;'>- STABLE</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=035420' class='stock-link'>4. NAVER (035420)</a>
                            <span style='font-size: 11px; color: #22C55E; font-weight: 700;'>▲ UP</span>
                        </div>
                        <div class='card-item-row'>
                            <a href='/?code=035720' class='stock-link'>5. 카카오 (035720)</a>
                            <span style='font-size: 11px; color: #94A3B8; font-weight: 600;'>- STABLE</span>
                        </div>
                    </div>
                </div>
            """,
                unsafe_allow_html=True,
            )

        st.markdown("</div>", unsafe_allow_html=True)

    with right_ad:
        st.markdown("<div class='ad-box-tall'>ads</div>", unsafe_allow_html=True)

else:
    # ------------------------------------------------------------------
    # [화면 2: 검색 후 Slide 5 스케치 레이아웃 상세 화면]
    # ------------------------------------------------------------------
    stock_data = get_stock_data(selected_code)
    score_dict = calculate_10_scores(stock_data)

    # Top Header Area
    col_logo, col_quote, col_login = st.columns([1.5, 7, 1.5])

    with col_logo:
        if st.button("⬅️ Logo (Home)", use_container_width=True):
            st.query_params.clear()
            st.rerun()

    with col_quote:
        quotes = [
            "Investing quotes (Randomly): 'Rule No.1: Never lose money. Rule No.2: Never forget rule No.1.' - Warren Buffett",
            "Investing quotes (Randomly): 'In the short run, the market is a voting machine, but in the long run, it is a weighing machine.' - Benjamin Graham",
            "Investing quotes (Randomly): 'The time of maximum pessimism is the best time to buy.' - John Templeton",
        ]
        st.markdown(
            f"<div class='quote-box'><div class='quote-text'>{random.choice(quotes)}</div></div>",
            unsafe_allow_html=True,
        )

    with col_login:
        st.markdown("<div style='height: 20px;'></div>", unsafe_allow_html=True)
        st.button("Log in", use_container_width=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # Slide 5 3단 레이아웃: Ads (Left) | Main Section | Ads (Right)
    left_ad, main_sec, right_ad = st.columns([1.2, 7.6, 1.2])

    with left_ad:
        st.markdown("<div class='ad-box-tall'>ads</div>", unsafe_allow_html=True)

    with main_sec:
        st.title(f"🔍 {stock_data['name']} ({stock_data['symbol']}) - Fundamental Report")

        # [0] Live Chart
        st.markdown("### 0. Live Chart")
        if isinstance(stock_data["hist"], pd.DataFrame) and not stock_data["hist"].empty:
            st.line_chart(stock_data["hist"]["Close"], height=280)

        st.markdown("<br>", unsafe_allow_html=True)

        total_score = sum(item["score"] for item in score_dict.values())
        st.markdown(f"### 🛡️ Fundamental Defense Score: **{total_score} / 100**")
        st.markdown("---")

        # [1 ~ 10] 스케치 10가지 항목별 Scoring Cards
        for metric_name, details in score_dict.items():
            expander_title = f"{metric_name}   |   Explanation, importance   |   Score: {details['score']}/10 ⬇"

            with st.expander(expander_title, expanded=False):
                st.markdown(f"**Current Value:** {details['val']}")
                st.markdown(f"**Explanation:** {details['explanation']}")
                st.markdown(f"**Importance:** {details['importance']}")
                st.progress(details["score"] * 10)

    with right_ad:
        st.markdown("<div class='ad-box-tall'>ads</div>", unsafe_allow_html=True)
