import os
from datetime import datetime, timedelta
import math
import time

import FinanceDataReader as fdr
import requests
try:
    from OpenDartReader import OpenDartReader  # pip install OpenDartReader (requirements.txt 기준)
except ImportError:
    from opendartreader import OpenDartReader  # 일부 환경(예: 기존 Colab 세션)의 소문자 모듈명 호환용
from supabase import create_client

from scoring import calculate_fundamental_score, worst_value

# 1. 환경변수 설정
SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
DART_API_KEY = os.environ.get("DART_API_KEY", "")

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    dart = OpenDartReader(DART_API_KEY)
except Exception as e:
    print(f"❌ 초기 설정 에러: {e}")

# fdr.StockListing('KRX')에는 업종 정보가 없음. 'KRX-DESC'의 'Sector' 컬럼은 코스닥 시장구분(벤처기업부 등)일 뿐이라
# 실제 업종 분류가 아니므로 'Industry' 컬럼(KSIC 기준, 예: "기타 금융업", "통신 및 방송 장비 제조업")을 사용.
# 이건 상세 업종 표시용으로 유지하고, 동종업계 비교/그룹핑에는 아래 WICS 대분류를 사용.
FINANCIAL_SECTOR_KEYWORDS = ["금융", "보험", "은행", "캐피탈", "카드", "증권", "저축", "리스"]

# 지주회사는 WICS로 안 잡힘(계열사 업종 따라 산업재/IT 등으로 분류되므로) - 별도 텍스트 키워드로 판별
HOLDING_COMPANY_KEYWORDS = ["지주", "홀딩스"]

# SK/LG처럼 사명 자체에 "지주"/"홀딩스"가 없어 키워드 매칭이 원천적으로 안 되는 주요 지주회사는
# 직접 목록으로 관리. (확실한 것 위주로만 넣었고, 배치 결과 보면서 계속 보강 필요 - 완전하지 않을 수 있음)
KNOWN_HOLDING_COMPANY_CODES = {
    "034730": "SK",
    "003550": "LG",
    "000150": "두산",
    "078930": "GS",
    "001040": "CJ",
    "002020": "코오롱",
}

# 레버리지/유동성 구조가 일반 기업과 달라 부채비율·당좌비율·이자보상배율을 예외 처리하는 WICS 대분류
# (금융은 별도 로직으로 판별하고, 여기엔 그 외 업종만 추가)
LEVERAGE_EXEMPT_WICS_SECTORS = {"유틸리티"}

# WICS(FnGuide Wise Industry Classification Standard) 10개 대분류 - 네이버/다음 증권이 쓰는 것과 동일한 기준
WICS_SECTOR_CODES = {
    "G10": "에너지",
    "G15": "소재",
    "G20": "산업재",
    "G25": "경기소비재",
    "G30": "필수소비재",
    "G35": "건강관리",
    "G40": "금융",
    "G45": "IT",
    "G50": "통신서비스",
    "G55": "유틸리티",
}

# 평균/최악 집계 대상 비율 지표 (성장률 2개는 CAGR로 별도 계산하므로 제외)
# roa는 금융섹터 전용 대체지표 (roic 자리) - 비금융기업은 계산은 되지만 scoring.py에서 안 씀
RATIO_METRICS = [
    "opm", "roic", "debt_rate", "quick_ratio",
    "interest_coverage", "ocf_ratio", "sga_ratio", "roa",
]

DEFAULT_PERIODS = (1, 3, 5, 10)  # 단기/중기/중장기/장기

CORP_TAX_RATE = 0.22  # ROIC의 NOPAT 계산용 근사 법인세율

# '하락장 실제 방어력' 지표 계산에 쓰는 과거 주요 하락장 구간 (코스피 대비 종목의 실제 낙폭 비교)
DOWNTURN_WINDOWS = [
    ("코로나 폭락", "2020-01-20", "2020-03-19"),
    ("2022년 긴축 하락장", "2021-12-01", "2022-09-30"),
]

# --------------------------------------------------------------------------
# B그룹 버그 수정 (최종 재채점 계획: 버그1/버그2/버그6):
#   - 버그1: 1년 growth(revenue_growth/eps_growth) base-effect/계정오류로 인한 폭주
#   - 버그2: interest_coverage가 이자비용 추출 실패 시 무조건 만점(25) 처리되던 문제
#   - 버그6: "자본총계" 계정명 매칭 실패율이 유독 높아 재무건전성 지표가 통째로 왜곡되던 문제
# --------------------------------------------------------------------------
GROWTH_SANITY_THRESHOLD = 500  # % 이 이상이면 base-effect/계정오류로 간주해 결측(None) 처리


def sanitize_growth(value):
    """분모(전년/기준연도) 값이 작아 생기는 base-effect나 계정 매칭 오류로 인한
    비정상적 growth% 폭주를 걸러냄. 임계값 초과 시 결측(None) 처리 (버그1 수정).
    결측 처리된 값은 scoring.py에서 0점 처리되므로, 최소한 "터무니없는 값으로 만점/폭주 점수"를
    받는 것보다는 안전한 방향(과소평가) - 완전한 해결은 아니고 안전장치 수준."""
    if value is None:
        return None
    if abs(value) > GROWTH_SANITY_THRESHOLD:
        return None
    return value


def get_total_equity(df, detail_df=None):
    """
    '자본총계' 추출 강화판 (버그6 수정).
    표준 13계정(df)과 전체 재무제표(detail_df) 양쪽에서, 정확매칭 -> 부분매칭 ->
    (지배기업소유주지분 + 비지배지분) 합산 순으로 폴백. 연결재무제표(CFS)에서 '자본총계'라는
    합계 행 자체가 없고 지배/비지배 지분으로만 쪼개져 표시되는 경우를 구제하기 위함.
    (실측 결과: 기존 로직으로는 전체 종목의 9.8%가 자본총계=0으로 잘못 잡혔음 - 이 중 대부분이
    진짜 자본잠식이 아니라 이 매칭 실패였음)
    """
    def try_direct(source_df):
        row = source_df[source_df["account_nm"] == "자본총계"]
        if row.empty:
            row = source_df[source_df["account_nm"].str.contains("자본총계", na=False, regex=False)]
        if not row.empty:
            val_str = str(row.iloc[0]["thstrm_amount"]).replace(",", "")
            if val_str and val_str != "-":
                return float(val_str)
        return None

    def try_split_sum(source_df):
        controlling_kw = ["지배기업의 소유주에게 귀속되는 자본", "지배기업소유주지분", "지배기업의 소유지분"]
        minority_kw = ["비지배지분"]

        def find_first(keywords):
            for kw in keywords:
                r = source_df[source_df["account_nm"].str.contains(kw, na=False, regex=False)]
                if not r.empty:
                    val_str = str(r.iloc[0]["thstrm_amount"]).replace(",", "")
                    if val_str and val_str != "-":
                        return float(val_str)
            return None

        controlling = find_first(controlling_kw)
        if controlling is None:
            return None
        minority = find_first(minority_kw) or 0.0  # 비지배지분 없으면 0 (지배주주만 있는 회사면 정상)
        return controlling + minority

    for source_df in [df, detail_df]:
        if source_df is None:
            continue
        val = try_direct(source_df)
        if val is not None:
            return val
        val = try_split_sum(source_df)
        if val is not None:
            return val

    return 0.0  # 그래도 못 찾으면 기존처럼 0 (발생 빈도는 크게 줄어들 것으로 예상됨)


def resolve_interest_coverage(op_profit, interest_exp, debt_rate):
    """
    interest_coverage 기본값(25점 만점) 오남용 방지 (버그2 수정).
    이자비용이 0으로 잡히면 '진짜 무차입'인지 '계정 추출 실패'인지 debt_rate로 교차검증.
    - debt_rate가 낮으면(<=30%) 무차입 가능성이 높으므로 기존처럼 만점 수준(25.0) 부여
    - debt_rate가 높은데도 interest_exp가 0으로 잡히면 추출 실패로 간주해 결측(None) 처리
      (실측 결과: 아시아나항공 부채비율 2714%인데도 interest_coverage가 25로 찍히는 등
      전체의 56%가 이 기본값을 받고 있었음 - 그 상당수가 실제로는 이런 오탐이었을 것으로 추정)
    """
    if interest_exp and interest_exp > 0:
        return round(op_profit / interest_exp, 2)
    if debt_rate is not None and debt_rate <= 30:
        return 25.0
    return None


def _normalize_account_name(name):
    """계정명 비교용 정규화: 모든 공백 제거. 한국 재무제표는 같은 계정을
    '영업활동현금흐름'/'영업활동 현금흐름'처럼 공백 유무만 다르게 표기하는 경우가 흔해서,
    단순 str.contains 매칭이 공백 하나 때문에 통째로 실패하는 걸 방지 (버그7 수정)."""
    if name is None:
        return ""
    return "".join(str(name).split())


def _find_account_value(source_df, keywords, field="thstrm_amount"):
    """계정명 공백 차이를 무시하고 정확매칭 -> 부분매칭 순으로 값을 찾는다.
    get_value()들의 공통 로직 - 공백 정규화가 반영된 버전. 못 찾으면 None
    (0.0 fallback 여부는 호출부가 결정하도록 여기선 값 유무만 알려줌)."""
    if source_df is None or source_df.empty:
        return None
    norm_names = source_df["account_nm"].map(_normalize_account_name)

    for kw in keywords:
        nkw = _normalize_account_name(kw)
        mask = norm_names == nkw
        if mask.any():
            val_str = str(source_df[mask].iloc[0][field]).replace(",", "")
            if val_str and val_str != "-":
                return float(val_str)

    for kw in keywords:
        nkw = _normalize_account_name(kw)
        mask = norm_names.str.contains(nkw, na=False, regex=False)
        if mask.any():
            val_str = str(source_df[mask].iloc[0][field]).replace(",", "")
            if val_str and val_str != "-":
                return float(val_str)

    return None


def get_interest_expense(detail_df, field="thstrm_amount"):
    """
    이자비용 추출 다단계 폴백 (버그8 수정 - '부채 지급비용'이라는 원래 취지에 가장 가까운
    값을 우선순위대로 시도):
      1순위 '이자비용'/'금융원가' - 손익계산서상 순수 이자비용 계정 (있으면 가장 정확)
      2순위 '이자의 지급' - 현금흐름표 주석의 실제 이자 지급액(현금주의). 발생주의는 아니지만
             환차손 등 노이즈가 안 섞여 있어 순수 이자부담에 가장 가까운 근사치
      3순위 '금융비용' - 이자비용 외에 환차손/파생상품평가손실 등이 섞인 포괄 비용.
             실제 이자부담보다 부풀려질 수 있는 최후의 수단이라 근사치로 플래그를 남김
    반환값: (interest_exp, is_approximate) 튜플. 3순위를 쓴 경우만 근사치(True)로 표시.
    """
    val = _find_account_value(detail_df, ["이자비용", "금융원가"], field=field)
    if val is not None:
        return abs(val), False

    val = _find_account_value(detail_df, ["이자의 지급"], field=field)
    if val is not None:
        return abs(val), False

    val = _find_account_value(detail_df, ["금융비용"], field=field)
    if val is not None:
        return abs(val), True

    return 0.0, False


def get_sector_map():
    """{종목코드: 업종(Industry) 문자열} 딕셔너리 반환 (상세 업종, 표시용). KRX-DESC 조회 실패 시 빈 dict."""
    try:
        df_desc = fdr.StockListing("KRX-DESC")
        return dict(zip(df_desc["Code"], df_desc["Industry"]))
    except Exception as e:
        print(f"⚠️ KRX-DESC(Industry) 조회 실패, 업종 판별 없이 진행: {e}")
        return {}


def get_wics_sector_map(target_date=None, max_lookback_days=10):
    """
    WiseIndex(FnGuide)의 비공식 API로 WICS 10개 대분류 기준 {종목코드: 섹터명} 매핑을 가져옴.
    네이버/다음 증권이 쓰는 것과 동일한 업종 분류 - 동종업계 비교/그룹핑용.
    target_date가 None이면 오늘부터 거슬러 올라가며 데이터가 있는 가장 최근 영업일을 찾음(주말/공휴일 대응).
    """
    if target_date is None:
        target_date = datetime.now()

    for i in range(max_lookback_days):
        dt_str = (target_date - timedelta(days=i)).strftime("%Y%m%d")
        sector_map = {}
        any_data = False

        for sec_cd, sec_name in WICS_SECTOR_CODES.items():
            url = "https://www.wiseindex.com/Index/GetIndexComponets"
            try:
                res = requests.get(url, params={"ceil_yn": 0, "dt": dt_str, "sec_cd": sec_cd}, timeout=15)
                data = res.json()
                items = data.get("list", [])
            except Exception as e:
                print(f"  ⚠️ WICS {sec_name}({sec_cd}) 조회 실패({dt_str}): {e}")
                continue

            if items:
                any_data = True
            for item in items:
                # 필드명이 바뀌었을 가능성 대비 몇 가지 후보를 시도
                code = item.get("CMP_CD") or item.get("cmp_cd") or item.get("code")
                if code:
                    sector_map[code] = sec_name

        if any_data and sector_map:
            print(f"✅ WICS 업종 매핑 확보 (기준일: {dt_str}, {len(sector_map)}개 종목)")
            return sector_map

    print("⚠️ WICS 업종 매핑을 가져오지 못했습니다 (최근 영업일 탐색 실패). 빈 매핑으로 진행합니다.")
    return {}


def _max_drawdown(price_series):
    """가격 시계열에서 최대낙폭(MDD, %)을 계산. 결과는 0 또는 음수."""
    if price_series is None or len(price_series) < 2:
        return None
    roll_max = price_series.cummax()
    drawdown = (price_series - roll_max) / roll_max * 100
    return float(drawdown.min())


def get_kospi_mdd_cache():
    """코스피 지수의 하락장 구간별 MDD를 미리 계산해 캐싱 (전 종목 공통이라 배치당 한 번만 계산)"""
    cache = {}
    for label, start, end in DOWNTURN_WINDOWS:
        try:
            df = fdr.DataReader("KS11", start, end)
            cache[label] = _max_drawdown(df["Close"])
        except Exception as e:
            print(f"⚠️ 코스피 [{label}] 구간 MDD 계산 실패: {e}")
            cache[label] = None
    print(f"📉 코스피 하락장 구간 MDD 기준값: {cache}")
    return cache


def calculate_downturn_defense(stock_code, kospi_mdd_cache):
    """
    과거 주요 하락장 구간에서 이 종목이 코스피 대비 얼마나 덜/더 빠졌는지 계산.
    반환값(%p) = 평균(종목 MDD - 코스피 MDD). 양수=코스피보다 덜 빠짐(방어적), 음수=더 빠짐(취약).
    해당 구간에 상장 전이었던 등 데이터 없는 구간은 자동 제외. 전부 없으면 None.
    """
    relative_defenses = []
    for label, start, end in DOWNTURN_WINDOWS:
        kospi_mdd = kospi_mdd_cache.get(label)
        if kospi_mdd is None:
            continue
        try:
            df = fdr.DataReader(stock_code, start, end)
            if df is None or df.empty or len(df) < 5:
                continue
            stock_mdd = _max_drawdown(df["Close"])
            if stock_mdd is None:
                continue
            relative_defenses.append(stock_mdd - kospi_mdd)
        except Exception:
            continue

    if not relative_defenses:
        return None
    return round(sum(relative_defenses) / len(relative_defenses), 2)


def is_financial_sector(sector_str, wics_sector=None):
    """
    금융업 여부 판별. WICS가 '금융'이면 바로 True.
    WICS가 다른 값이어도(예: 지주회사가 계열사 업종으로 분류돼서), KSIC 상세업종 텍스트에
    금융 관련 키워드가 있으면 True로 판정 (WICS 하나만 보고 끝내면 SK처럼 KSIC상 '기타 금융업'인데도
    WICS가 '에너지'로 잡혀서 놓치는 케이스가 생김 - 두 신호를 OR로 결합).
    """
    if wics_sector == "금융":
        return True
    if sector_str and isinstance(sector_str, str) and any(kw in sector_str for kw in FINANCIAL_SECTOR_KEYWORDS):
        return True
    return False


def is_holding_company(stock_code, sector_str, stock_name):
    """
    (비금융) 지주회사 여부 판별. 지주사는 별도재무제표(OFS)가 계열사 지분만 들고 있는 빈 껍데기라
    CFS(연결)를 써야 함 - 금융지주는 is_financial_sector로 이미 걸러지므로 여기선 나머지만 잡음.
    1순위: 알려진 지주회사 종목코드 목록 (SK/LG처럼 사명에 "지주"가 없어 키워드로 못 잡는 케이스 보완)
    2순위: KSIC Industry 텍스트 또는 종목명에 "지주"/"홀딩스" 포함 여부
    """
    if stock_code in KNOWN_HOLDING_COMPANY_CODES:
        return True
    haystack = f"{sector_str or ''} {stock_name or ''}"
    return any(kw in haystack for kw in HOLDING_COMPANY_KEYWORDS)


def _fetch_full_statement_df(stock_code, year, reprt_code="11011", use_ofs_for_manufacturing=True):
    """
    dart.finstate()의 '주요계정'(13개 표준항목)에는 재고자산/판관비/이자비용/영업활동현금흐름이
    아예 없어서, 전체 재무제표(finstate_all)를 별도로 조회해 이 계정들을 찾기 위한 함수.
    finstate_all()은 finstate()와 달리 fs_div(OFS/CFS)를 파라미터로 직접 지정해야 하며,
    결과 df엔 이미 그 구분으로 걸러져 있어 'fs_div' 컬럼 자체가 없음(finstate()와 스키마가 다름).
    """
    fs_div_order = ["OFS", "CFS"] if use_ofs_for_manufacturing else ["CFS", "OFS"]

    for fs_div in fs_div_order:
        try:
            fin_data = dart.finstate_all(stock_code, year, reprt_code=reprt_code, fs_div=fs_div)
        except Exception as e:
            print(f"  ⚠️ 전체 재무제표 조회 실패({year}년 {reprt_code}, {fs_div}): {e}")
            fin_data = None

        if fin_data is not None and not fin_data.empty:
            return fin_data

    return None


def get_latest_annual_year():
    """사업보고서(연간)는 통상 익년 3월 말 공시되므로, 4월 이전엔 전전년도까지만 확정치로 간주"""
    now = datetime.now()
    return now.year - 2 if now.month < 4 else now.year - 1


def get_latest_available_report():
    """
    분기/반기 보고서까지 포함해 '지금 시점 기준 가장 최근에 확정 공시됐을' 보고서의 (연도, reprt_code) 추적.
    DART 실제 제출기한 기준(사업보고서 3/31, 분기·반기 보고서는 결산일 후 45일)으로 일(day) 단위까지 반영.
    1년(단기) 기간의 최신성을 위해 사용 - 연간 사업보고서만 쓰는 3/5/10년과는 별도 로직.
    """
    now = datetime.now()
    md = (now.month, now.day)
    year = now.year
    if md < (4, 1):
        return year - 1, "11014"   # 전년도 3분기보고서 (전년도 사업보고서는 아직 미공시)
    elif md < (5, 15):
        return year - 1, "11011"   # 전년도 사업보고서 (제출기한 3/31)
    elif md < (8, 15):
        return year, "11013"       # 올해 1분기보고서 (제출기한 5/15경)
    elif md < (11, 15):
        return year, "11012"       # 올해 반기보고서 (제출기한 8/14경)
    else:
        return year, "11014"       # 올해 3분기보고서 (제출기한 11/14경)


def _sanitize_json(obj):
    """NaN/Infinity 같은 JSON 비호환 float 값을 None으로 치환 (dict/list 재귀 순회)"""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json(v) for v in obj]
    return obj


def _parse_year_financials(df, df_full=None):
    """단일 연도 재무제표 df에서 주요 계정과 비율 지표를 파싱 (계정명 다중 키워드 매핑).
    df_full(전체 재무제표)이 주어지면 재고자산/판관비/이자비용/영업현금흐름은 거기서 찾음
    (dart.finstate()의 표준 주요계정엔 이 4개가 없어서)."""

    def get_value(keywords, source_df):
        # 공백 유무 차이(예: '영업활동현금흐름' vs '영업활동 현금흐름')를 무시하고 매칭 (버그7 수정)
        val = _find_account_value(source_df, keywords, field="thstrm_amount")
        return val if val is not None else 0.0

    revenue = get_value(["매출액", "수익(매출액)", "영업수익"], df)
    op_profit = get_value(["영업이익", "영업이익(손실)"], df)
    net_income = get_value(["당기순이익", "당기순이익(손실)"], df)
    total_assets = get_value(["자산총계"], df)
    current_assets = get_value(["유동자산"], df)
    current_liab = get_value(["유동부채"], df)
    total_liab = get_value(["부채총계"], df)

    detail_df = df_full if df_full is not None else df

    # 자본총계는 매칭 실패율이 유독 높았던 계정이라 전용 폴백 로직 사용 (버그6 수정)
    total_equity = get_total_equity(df, detail_df)

    inventory = get_value(["재고자산"], detail_df)
    sga_costs = get_value(["판매비와관리비", "판매비와 관리비", "판관비"], detail_df)
    operating_cf = get_value(["영업활동현금흐름", "영업활동으로 인한 현금흐름"], detail_df)
    # 이자비용: 이자비용/금융원가 -> 이자의 지급(현금흐름표) -> 금융비용(포괄, 근사치) 순 폴백 (버그8 수정)
    interest_exp, interest_exp_is_approx = get_interest_expense(detail_df, field="thstrm_amount")

    # 투하자본 근사치: 자산총계 - 유동부채 (이자부채만 정확히 구분하기 어려워 유동부채 전체를 차감하는 간이 추정)
    invested_capital = total_assets - current_liab
    nopat = op_profit * (1 - CORP_TAX_RATE)

    debt_rate = round(total_liab / total_equity * 100, 2) if total_equity > 0 else None

    ratios = {
        "opm": round(op_profit / revenue * 100, 2) if revenue > 0 else None,
        "roic": round(nopat / invested_capital * 100, 2) if invested_capital > 0 else None,
        "roa": round(net_income / total_assets * 100, 2) if total_assets > 0 else None,  # 금융섹터 대체지표
        "debt_rate": debt_rate,
        "quick_ratio": round((current_assets - inventory) / current_liab * 100, 2) if current_liab > 0 else None,
        # interest_exp 추출 실패와 진짜 무차입을 debt_rate로 교차검증 (버그2 수정)
        "interest_coverage": resolve_interest_coverage(op_profit, interest_exp, debt_rate),
        "ocf_ratio": round(operating_cf / net_income, 2) if net_income > 0 else None,
        "sga_ratio": round(sga_costs / revenue * 100, 2) if revenue > 0 else None,
    }

    raw = {
        "revenue": revenue,
        "operating_income": op_profit,
        "net_income": net_income,
        "total_liabilities": total_liab,
        "total_equity": total_equity,
        "interest_exp_is_approx": interest_exp_is_approx,
    }

    return {**ratios, **raw}


def _parse_report_financials(df, df_full=None):
    """
    단일 보고서 df에서 주요 계정, 비율 지표, 그리고 당기(thstrm) vs 전년동기(frmtrm) 성장률까지 파싱.
    분기/반기 누적보고서도 frmtrm_amount가 '작년 동기간 누적'이라 그대로 동기간 성장률로 쓸 수 있음.
    df_full(전체 재무제표)이 주어지면 재고자산/판관비/이자비용/영업현금흐름은 거기서 찾음.
    """

    def get_value(keywords, source_df, field="thstrm_amount"):
        # 공백 유무 차이를 무시하고 매칭 (버그7 수정)
        val = _find_account_value(source_df, keywords, field=field)
        return val if val is not None else 0.0

    revenue = get_value(["매출액", "수익(매출액)", "영업수익"], df)
    op_profit = get_value(["영업이익", "영업이익(손실)"], df)
    net_income = get_value(["당기순이익", "당기순이익(손실)"], df)
    total_assets = get_value(["자산총계"], df)
    current_assets = get_value(["유동자산"], df)
    current_liab = get_value(["유동부채"], df)
    total_liab = get_value(["부채총계"], df)

    detail_df = df_full if df_full is not None else df

    total_equity = get_total_equity(df, detail_df)

    inventory = get_value(["재고자산"], detail_df)
    sga_costs = get_value(["판매비와관리비", "판매비와 관리비", "판관비"], detail_df)
    operating_cf = get_value(["영업활동현금흐름", "영업활동으로 인한 현금흐름"], detail_df)
    # 이자비용: 이자비용/금융원가 -> 이자의 지급(현금흐름표) -> 금융비용(포괄, 근사치) 순 폴백 (버그8 수정)
    interest_exp, interest_exp_is_approx = get_interest_expense(detail_df, field="thstrm_amount")

    prev_revenue = get_value(["매출액", "수익(매출액)", "영업수익"], df, field="frmtrm_amount")
    prev_net_income = get_value(["당기순이익", "당기순이익(손실)"], df, field="frmtrm_amount")

    invested_capital = total_assets - current_liab
    nopat = op_profit * (1 - CORP_TAX_RATE)

    debt_rate = round(total_liab / total_equity * 100, 2) if total_equity > 0 else None

    ratios = {
        "opm": round(op_profit / revenue * 100, 2) if revenue > 0 else None,
        "roic": round(nopat / invested_capital * 100, 2) if invested_capital > 0 else None,
        "roa": round(net_income / total_assets * 100, 2) if total_assets > 0 else None,  # 금융섹터 대체지표
        "debt_rate": debt_rate,
        "quick_ratio": round((current_assets - inventory) / current_liab * 100, 2) if current_liab > 0 else None,
        "interest_coverage": resolve_interest_coverage(op_profit, interest_exp, debt_rate),
        "ocf_ratio": round(operating_cf / net_income, 2) if net_income > 0 else None,
        "sga_ratio": round(sga_costs / revenue * 100, 2) if revenue > 0 else None,
    }

    # base-effect/계정오류로 인한 growth% 폭주 방지 (버그1 수정)
    revenue_growth = sanitize_growth(
        round((revenue - prev_revenue) / abs(prev_revenue) * 100, 2)
        if prev_revenue not in (0, None) else None
    )
    # EPS 성장률은 발행주식수가 안정적이라는 가정 하에 순이익 증가율로 근사
    eps_growth = sanitize_growth(
        round((net_income - prev_net_income) / abs(prev_net_income) * 100, 2)
        if prev_net_income not in (0, None) else None
    )

    raw = {
        "revenue": revenue,
        "operating_income": op_profit,
        "net_income": net_income,
        "total_liabilities": total_liab,
        "total_equity": total_equity,
        "interest_exp_is_approx": interest_exp_is_approx,
    }

    return {**ratios, "revenue_growth": revenue_growth, "eps_growth": eps_growth, **raw}


def fetch_latest_report_metrics(stock_code, use_ofs_for_manufacturing=True):
    """
    1년(단기) 기간 전용: 연간 사업보고서가 아니라 '지금 시점 가장 최신' 분기/반기 보고서 기준으로
    지표와 전년동기 대비 성장률을 계산 (최신성 우선).
    """
    year, reprt_code = get_latest_available_report()
    try:
        fin_data = dart.finstate(stock_code, year, reprt_code=reprt_code)
    except Exception as e:
        print(f"  ⚠️ 최신 보고서({year}년 {reprt_code}) 조회 실패: {e}")
        return None

    if fin_data is None or fin_data.empty:
        return None

    cfs = fin_data[fin_data["fs_div"] == "CFS"]
    ofs = fin_data[fin_data["fs_div"] == "OFS"]
    if use_ofs_for_manufacturing and not ofs.empty:
        df = ofs
    else:
        df = cfs if not cfs.empty else ofs
    if df.empty:
        return None

    result = _parse_report_financials(df, df_full=_fetch_full_statement_df(
        stock_code, year, reprt_code=reprt_code, use_ofs_for_manufacturing=use_ofs_for_manufacturing
    ))
    result["_report_year"] = year
    result["_report_code"] = reprt_code
    return result


REPORT_CODE_LABEL = {
    "11011": "사업보고서(연간)",
    "11012": "반기보고서",
    "11013": "1분기보고서",
    "11014": "3분기보고서",
}


def fetch_year_data(stock_code, year, use_ofs_for_manufacturing=True):
    """특정 사업연도(사업보고서 기준) 재무데이터 1건 조회. 실패/결측 시 None."""
    try:
        fin_data = dart.finstate(stock_code, year, reprt_code="11011")
    except Exception as e:
        print(f"  ⚠️ {year}년 조회 실패: {e}")
        return None

    if fin_data is None or fin_data.empty:
        return None

    cfs = fin_data[fin_data["fs_div"] == "CFS"]
    ofs = fin_data[fin_data["fs_div"] == "OFS"]
    if use_ofs_for_manufacturing and not ofs.empty:
        df = ofs
    else:
        df = cfs if not cfs.empty else ofs
    if df.empty:
        return None

    return _parse_year_financials(df, df_full=_fetch_full_statement_df(
        stock_code, year, reprt_code="11011", use_ofs_for_manufacturing=use_ofs_for_manufacturing
    ))


def fetch_multi_year_metrics(stock_code, periods=DEFAULT_PERIODS, use_ofs_for_manufacturing=True, kospi_mdd_cache=None):
    """
    periods(예: 1/3/5/10년)별로 '평균 기준'과 '최악 기준' 지표 세트를 각각 계산.
    - 비율 지표(opm, roic, debt_rate 등): 기간 내 연도별 값의 평균 / 최악값
    - 성장률 지표(revenue_growth, eps_growth): 기간 시작~종료 연도 CAGR (평균/최악 공통)
    - downturn_defense: 과거 특정 하락장 구간 기준 고정값이라 기간과 무관하게 모든 period에 동일 주입
    각 period는 CAGR 계산을 위해 (period + 1)개 연도 데이터가 필요.
    """
    base_year = get_latest_annual_year()
    max_period = max(periods)
    years_needed = list(range(base_year - max_period, base_year + 1))

    print(f"📅 [{stock_code}] {years_needed[0]}~{years_needed[-1]}년 데이터 수집 중...")
    yearly_data = {}
    for y in years_needed:
        data = fetch_year_data(stock_code, y, use_ofs_for_manufacturing=use_ofs_for_manufacturing)
        yearly_data[y] = data
        print(f"  · {y}년: {'확보' if data else '결측'}")

    if yearly_data.get(base_year) is None:
        print(f"❌ [{stock_code}] 최신 확정연도({base_year}) 데이터가 없어 분석할 수 없습니다.")
        return None

    # 하락장 실제 방어력 - 기간(1/3/5/10년)과 무관한 고정값이므로 한 번만 계산
    downturn_defense = None
    if kospi_mdd_cache:
        downturn_defense = calculate_downturn_defense(stock_code, kospi_mdd_cache)
        print(f"  📉 하락장 실제 방어력(코스피 대비): {downturn_defense}%p" if downturn_defense is not None else "  📉 하락장 방어력: 데이터 부족으로 계산 불가")

    period_results = {}
    for period in periods:
        window_years = [y for y in range(base_year - period, base_year + 1) if yearly_data.get(y)]
        if len(window_years) < 2:
            print(f"  ⚠️ {period}년 기간: 사용 가능한 연도가 부족해 스킵합니다.")
            period_results[period] = None
            continue

        oldest, newest = window_years[0], window_years[-1]
        actual_span = newest - oldest  # 결측 연도가 있으면 요청한 period보다 짧아질 수 있음

        rev_oldest, rev_newest = yearly_data[oldest]["revenue"], yearly_data[newest]["revenue"]
        ni_oldest, ni_newest = yearly_data[oldest]["net_income"], yearly_data[newest]["net_income"]

        # base-effect/계정오류로 인한 CAGR 폭주 방지 (버그1 수정)
        revenue_cagr = sanitize_growth(
            round(((rev_newest / rev_oldest) ** (1 / actual_span) - 1) * 100, 2)
            if (rev_oldest > 0 and rev_newest > 0 and actual_span > 0) else None
        )
        eps_cagr = sanitize_growth(
            round(((ni_newest / ni_oldest) ** (1 / actual_span) - 1) * 100, 2)
            if (ni_oldest > 0 and ni_newest > 0 and actual_span > 0) else None
        )

        # 평균/최악 집계는 경계연도(가장 오래된 해)를 제외한 최근 period개 연도만 사용
        recent_years = window_years[1:] if len(window_years) > 1 else window_years

        avg_metrics = {"revenue_growth": revenue_cagr, "eps_growth": eps_cagr, "downturn_defense": downturn_defense}
        worst_metrics = {"revenue_growth": revenue_cagr, "eps_growth": eps_cagr, "downturn_defense": downturn_defense}

        for metric in RATIO_METRICS:
            series = [yearly_data[y][metric] for y in recent_years if yearly_data[y].get(metric) is not None]
            avg_metrics[metric] = round(sum(series) / len(series), 2) if series else None
            worst_metrics[metric] = worst_value(metric, series)

        # interest_coverage 계산에 쓰인 이자비용이 근사치(금융비용 기준, 버그8)였는지 여부.
        # 최악값이 정확히 어느 해에서 나왔는지까지는 추적하지 않고, "이 기간에 값을 채택한
        # 연도 중 하나라도 근사치를 썼으면 이 기간 전체를 근사치로 표시"하는 보수적 방식.
        ic_approx_years = [
            yearly_data[y].get("interest_exp_is_approx", False)
            for y in recent_years
            if yearly_data[y].get("interest_coverage") is not None
        ]
        interest_coverage_is_approx = any(ic_approx_years) if ic_approx_years else False
        avg_metrics["interest_coverage_is_approx"] = interest_coverage_is_approx
        worst_metrics["interest_coverage_is_approx"] = interest_coverage_is_approx

        period_results[period] = {
            "years_used": window_years,
            "avg_metrics": avg_metrics,
            "worst_metrics": worst_metrics,
        }

    # 1년(단기) 기간은 연간 사업보고서 대신 '지금 시점 가장 최신' 분기/반기 보고서로 대체 (최신성 우선)
    if 1 in periods:
        latest_report = fetch_latest_report_metrics(stock_code, use_ofs_for_manufacturing=use_ofs_for_manufacturing)
        if latest_report:
            report_year = latest_report["_report_year"]
            report_code = latest_report["_report_code"]
            report_label = REPORT_CODE_LABEL.get(report_code, report_code)

            metrics_1y = {
                "revenue_growth": latest_report["revenue_growth"],
                "eps_growth": latest_report["eps_growth"],
                "downturn_defense": downturn_defense,
            }
            for metric in RATIO_METRICS:
                metrics_1y[metric] = latest_report.get(metric)
            metrics_1y["interest_coverage_is_approx"] = latest_report.get("interest_exp_is_approx", False)

            period_results[1] = {
                "years_used": [f"{report_year} {report_label}"],
                "avg_metrics": metrics_1y,
                "worst_metrics": metrics_1y,  # 데이터가 보고서 1개뿐이므로 평균=최악
            }
            print(f"  🕐 1년 기간: {report_year}년 {report_label} 기준으로 최신화")
        else:
            print("  ⚠️ 1년 기간: 최신 분기/반기 보고서를 가져오지 못해 연간 사업보고서 기준으로 대체합니다.")

    return {"base_year": base_year, "yearly_data": yearly_data, "period_results": period_results}


def sync_kor_stock_fundamental(stock_code, stock_name, df_krx=None, sector_map=None, wics_sector_map=None, kospi_mdd_cache=None, use_ofs_for_manufacturing=True):
    try:
        print(f"🔄 [{stock_name} ({stock_code})] 데이터 수집 및 분석 시작...")

        if df_krx is None or df_krx.empty:
            df_krx = fdr.StockListing("KRX")
        if sector_map is None:
            sector_map = get_sector_map()
        if wics_sector_map is None:
            wics_sector_map = get_wics_sector_map()
        if kospi_mdd_cache is None:
            kospi_mdd_cache = get_kospi_mdd_cache()

        target_stock = df_krx[df_krx["Code"] == stock_code]
        if target_stock.empty:
            print(f"❌ [{stock_name}] KRX 상장 정보를 찾을 수 없습니다.")
            return

        current_price = int(target_stock.iloc[0]["Close"])
        issued_shares = int(target_stock.iloc[0]["Stocks"])

        sector = sector_map.get(stock_code)
        wics_sector = wics_sector_map.get(stock_code)
        financial_sector = is_financial_sector(sector, wics_sector=wics_sector)
        holding_company = is_holding_company(stock_code, sector, stock_name)
        leverage_exempt = financial_sector or holding_company or (wics_sector in LEVERAGE_EXEMPT_WICS_SECTORS)
        print(
            f"🏷️ [{stock_name}] 업종(상세): {sector or '미상'} / WICS: {wics_sector or '미상'} "
            f"(금융업: {financial_sector} / 지주회사: {holding_company} / 레버리지 예외: {leverage_exempt})"
        )

        # 금융지주/보험/은행/비금융 지주회사는 별도재무제표(OFS)가 사실상 빈 껍데기라, 연결재무제표(CFS)를 써야 실질적인 사업이 보임
        effective_use_ofs = use_ofs_for_manufacturing and not (financial_sector or holding_company)

        multi = fetch_multi_year_metrics(stock_code, use_ofs_for_manufacturing=effective_use_ofs, kospi_mdd_cache=kospi_mdd_cache)
        if multi is None:
            # 데이터를 못 찾은 종목도 최소 기록을 남겨야 resume 시 매번 재조회하지 않음
            # (우선주처럼 DART에 별도 재무제표가 없는 종목 등)
            fail_payload = _sanitize_json({
                "stock_code": stock_code,
                "stock_name": stock_name,
                "market": "KOR",
                "sector": sector if isinstance(sector, str) else None,
                "wics_sector": wics_sector,
                "period_scores": None,
                "data_unavailable": True,
                "b_group_synced_at": datetime.utcnow().isoformat(),
            })
            try:
                supabase.table("Fundamental").upsert(fail_payload, on_conflict="stock_code").execute()
            except Exception as e:
                print(f"  ⚠️ 실패 기록 저장도 안 됨: {e}")
            return

        base_year = multi["base_year"]
        latest = multi["yearly_data"][base_year]

        net_income = latest["net_income"]
        total_equity = latest["total_equity"]
        # 버그6(자본총계 매칭 실패) 수정 이후이므로 이제 이 값을 신뢰할 수 있음 -> 자본잠식 플래그로 활용
        capital_impairment = total_equity < 0
        eps = (net_income / issued_shares) if issued_shares > 0 else None
        bps = (total_equity / issued_shares) if issued_shares > 0 else None
        per = round(current_price / eps, 2) if (eps and eps > 0) else None
        pbr = round(current_price / bps, 2) if (bps and bps > 0) else None

        period_scores = {}
        for period, pdata in multi["period_results"].items():
            if pdata is None:
                continue
            avg_score = calculate_fundamental_score(pdata["avg_metrics"], leverage_exempt=leverage_exempt, is_financial=financial_sector)
            worst_score = calculate_fundamental_score(pdata["worst_metrics"], leverage_exempt=leverage_exempt, is_financial=financial_sector)

            # 이자비용이 근사치(금융비용 기준, 버그8)로 계산된 기간이면 metric_scores에 표시해서
            # app.py가 "이 값은 근사치입니다" 안내를 붙일 수 있게 함
            if pdata["avg_metrics"].get("interest_coverage_is_approx") and "interest_coverage" in avg_score["metric_scores"]:
                avg_score["metric_scores"]["interest_coverage"]["is_approximate"] = True
            if pdata["worst_metrics"].get("interest_coverage_is_approx") and "interest_coverage" in worst_score["metric_scores"]:
                worst_score["metric_scores"]["interest_coverage"]["is_approximate"] = True
            period_scores[f"{period}y"] = {
                "years_used": pdata["years_used"],
                "avg": {
                    "total_score": avg_score["total_score"],
                    "grade": avg_score["grade"],  # 잠정 등급 - 전체 수집 완료 후 재산정 예정
                    "metric_scores": avg_score["metric_scores"],
                    "sub_scores": avg_score["sub_scores"],
                    "financial_adjusted": avg_score["financial_adjusted"],
                    "missing_metric_count": avg_score["missing_metric_count"],
                },
                "worst": {
                    "total_score": worst_score["total_score"],
                    "grade": worst_score["grade"],  # 잠정 등급 - 전체 수집 완료 후 재산정 예정
                    "metric_scores": worst_score["metric_scores"],
                    "sub_scores": worst_score["sub_scores"],
                    "financial_adjusted": worst_score["financial_adjusted"],
                    "missing_metric_count": worst_score["missing_metric_count"],
                },
            }
            print(
                f"  📊 {period}년 기준 -> 평균 {avg_score['total_score']}점({avg_score['grade']}) / "
                f"최악 {worst_score['total_score']}점({worst_score['grade']})"
            )

        payload = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "market": "KOR",
            "sector": sector,
            "wics_sector": wics_sector,
            "holding_company": holding_company,
            "base_year": base_year,
            "stock_price": current_price,
            "per": per,
            "pbr": pbr,
            "revenue": int(latest["revenue"]),
            "operating_income": int(latest["operating_income"]),
            "net_income": int(latest["net_income"]),
            "total_liabilities": int(latest["total_liabilities"]),
            "total_equity": int(latest["total_equity"]),
            "capital_impairment": capital_impairment,
            "period_scores": period_scores,  # Supabase jsonb 컬럼 저장 추천
            "b_group_synced_at": datetime.utcnow().isoformat(),
        }

        payload = _sanitize_json(payload)
        supabase.table("Fundamental").upsert(payload, on_conflict="stock_code").execute()

        print(f"✅ [{stock_name}] 완료! (주가: {current_price:,}원, 기준연도: {base_year})")

    except Exception as e:
        print(f"❌ [{stock_name}] 업데이트 에러: {e}")


def get_already_synced_codes():
    """Supabase Fundamental 테이블에 이미 저장된 stock_code 목록 조회 (최초 수집 재시작 시 중복 방지용).
    PostgREST 기본 응답 제한(1000행)을 페이지네이션으로 처리."""
    try:
        all_codes = set()
        page_size = 1000
        start = 0
        while True:
            res = (
                supabase.table("Fundamental")
                .select("stock_code")
                .range(start, start + page_size - 1)
                .execute()
            )
            rows = res.data
            if not rows:
                break
            all_codes.update(row["stock_code"] for row in rows)
            if len(rows) < page_size:
                break
            start += page_size
        return all_codes
    except Exception as e:
        print(f"⚠️ 기존 저장 목록 조회 실패, 처음부터 진행합니다: {e}")
        return set()


def get_b_group_done_codes():
    """
    B그룹(계정 매칭 버그 수정 후 재수집) 완료된 종목 코드 목록 조회.
    get_already_synced_codes()는 '테이블에 존재하는지'만 봐서 재수집 용도로는 못 씀 -
    이미 예전 버그 버전으로 저장된 종목도 전부 다시 처리해야 하므로 별도 컬럼(b_group_synced_at)으로 추적.
    PostgREST 기본 1000행 제한 페이지네이션 처리.
    """
    try:
        all_codes = set()
        page_size = 1000
        start = 0
        while True:
            res = (
                supabase.table("Fundamental")
                .select("stock_code")
                .not_.is_("b_group_synced_at", "null")
                .range(start, start + page_size - 1)
                .execute()
            )
            rows = res.data
            if not rows:
                break
            all_codes.update(row["stock_code"] for row in rows)
            if len(rows) < page_size:
                break
            start += page_size
        return all_codes
    except Exception as e:
        print(f"⚠️ B그룹 완료 목록 조회 실패, 처음부터 진행합니다: {e}")
        return set()


def sync_all_kor_stocks(limit=None, sleep_sec=0.5, resume=True, use_ofs_for_manufacturing=True):
    """
    KRX 상장 전체 종목을 순회하며 sync_kor_stock_fundamental 실행.
    - limit: 테스트용 개수 제한 (None이면 전체)
    - sleep_sec: DART 서버 부담을 줄이기 위한 종목 간 대기시간(초)
    - resume: True면 Supabase에 이미 저장된 종목은 자동으로 건너뜀 (중간에 끊겨도 이어서 실행 가능)
    실패한 종목은 건너뛰고 계속 진행하며, 마지막에 성공/실패 목록을 출력.

    ⚠️ 최초 전체 수집용 함수. B그룹 버그 수정 후 전체 재수집이 목적이면
    sync_all_kor_stocks_b_group()을 대신 사용할 것 (이미 저장된 종목도 다시 처리해야 하므로).
    """
    df_krx = fdr.StockListing("KRX")
    sector_map = get_sector_map()
    wics_sector_map = get_wics_sector_map()
    kospi_mdd_cache = get_kospi_mdd_cache()

    already_synced = get_already_synced_codes() if resume else set()
    if already_synced:
        print(f"⏭️  이미 처리된 {len(already_synced)}개 종목은 건너뜁니다.")

    targets = df_krx[~df_krx["Code"].isin(already_synced)]
    if limit:
        targets = targets.head(limit)

    total = len(targets)
    est_calls = total * 24  # 종목당 대략 (연간 11회 + 최신 분기/반기 1회) x 2 (주요계정 + 전체재무제표)
    print(f"📋 대상 종목 {total}개 (예상 DART 호출 약 {est_calls:,}건 / 일일 한도 40,000건)")

    succeeded, failed = [], []
    for i, row in enumerate(targets.itertuples(), 1):
        code, name = row.Code, row.Name
        print(f"\n[{i}/{total}] ", end="")
        try:
            sync_kor_stock_fundamental(
                code, name, df_krx=df_krx, sector_map=sector_map, wics_sector_map=wics_sector_map,
                kospi_mdd_cache=kospi_mdd_cache,
                use_ofs_for_manufacturing=use_ofs_for_manufacturing,
            )
            succeeded.append(code)
        except Exception as e:
            print(f"❌ [{name}({code})] 처리 중 예외 발생, 건너뜁니다: {e}")
            failed.append(code)
        time.sleep(sleep_sec)

    print(f"\n\n=== 배치 완료: 성공 {len(succeeded)} / 실패 {len(failed)} / 전체 {total} ===")
    if failed:
        print("실패한 종목코드:", failed)
    return succeeded, failed


def get_bugfix_affected_codes():
    """
    버그7(OCF 공백매칭 실패)/버그8(이자비용 계정 부재) 영향을 받았을 가능성이 있는 종목만 추려낸다.
    판정 기준: 어느 기간(1y/3y/5y/10y)의 avg 또는 worst 어느 쪽에서든
      - ocf_ratio 값이 정확히 0 이거나 (계정 못 찾아 0.0 fallback된 흔적)
      - interest_coverage 값이 None (이자비용 추출 실패로 결측 처리된 흔적)
    인 경우 그 종목을 대상에 포함. 전체 재수집 대신 이 목록만 다시 돌리면 DART 호출을
    크게 줄일 수 있음 (실측: 전체 대비 약 절반 수준).
    """
    all_rows = []
    page_size = 500
    start = 0
    while True:
        res = (
            supabase.table("Fundamental")
            .select("stock_code, period_scores")
            .not_.is_("period_scores", "null")
            .range(start, start + page_size - 1)
            .execute()
        )
        rows = res.data
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        start += page_size

    affected = []
    for row in all_rows:
        ps = row.get("period_scores") or {}
        is_affected = False
        for period in PERIODS_LABELS:
            pdata = ps.get(period)
            if not pdata:
                continue
            for mode in ("avg", "worst"):
                mscores = (pdata.get(mode) or {}).get("metric_scores") or {}
                ocf_entry = mscores.get("ocf_ratio") or {}
                ic_entry = mscores.get("interest_coverage") or {}
                if ocf_entry.get("value") == 0:
                    is_affected = True
                if "interest_coverage" in mscores and ic_entry.get("value") is None:
                    is_affected = True
                if is_affected:
                    break
            if is_affected:
                break
        if is_affected:
            affected.append(row["stock_code"])

    print(f"🔍 버그7/8 영향 추정 종목: {len(affected)}개 / 전체 스코어 보유 {len(all_rows)}개")
    return affected


PERIODS_LABELS = ["1y", "3y", "5y", "10y"]


def resync_bugfix_affected_stocks(limit=None, sleep_sec=0.5, use_ofs_for_manufacturing=True):
    """
    get_bugfix_affected_codes()로 추린 종목만 다시 수집 (전체 재수집 대신 사용).
    sync_all_kor_stocks_b_group()과 달리 b_group_synced_at 기준 resume 로직은 안 씀 -
    이건 일회성 패치 재수집이라, 이 함수를 다시 돌리면 매번 같은 대상 목록을 다시 계산해서
    돈다(이미 고쳐진 종목은 다음 실행 때 판정 기준에 안 걸려 자동으로 목록에서 빠짐).
    """
    df_krx = fdr.StockListing("KRX")
    sector_map = get_sector_map()
    wics_sector_map = get_wics_sector_map()
    kospi_mdd_cache = get_kospi_mdd_cache()

    targets = get_bugfix_affected_codes()
    if limit:
        targets = targets[:limit]

    total = len(targets)
    est_calls = total * 24
    print(f"📋 재수집 대상 {total}개 (예상 DART 호출 약 {est_calls:,}건 / 일일 한도 40,000건)")
    if est_calls > 40000:
        print("   ⚠️ 예상 호출 건수가 일일 한도를 초과합니다 - limit을 나눠서 여러 날에 걸쳐 실행하세요.")

    succeeded, failed = [], []
    for i, code in enumerate(targets, 1):
        name_row = df_krx[df_krx["Code"] == code]
        name = name_row.iloc[0]["Name"] if not name_row.empty else code
        print(f"\n[{i}/{total}] ", end="")
        try:
            sync_kor_stock_fundamental(
                code, name, df_krx=df_krx, sector_map=sector_map, wics_sector_map=wics_sector_map,
                kospi_mdd_cache=kospi_mdd_cache,
                use_ofs_for_manufacturing=use_ofs_for_manufacturing,
            )
            succeeded.append(code)
        except Exception as e:
            print(f"❌ [{name}({code})] 처리 중 예외 발생, 건너뜁니다: {e}")
            failed.append(code)
        time.sleep(sleep_sec)

    print(f"\n\n=== 버그 패치 재수집 완료: 성공 {len(succeeded)} / 실패 {len(failed)} / 전체 {total} ===")
    if failed:
        print("실패한 종목코드:", failed)
    return succeeded, failed

def sync_all_kor_stocks_b_group(limit=None, sleep_sec=0.5, use_ofs_for_manufacturing=True):
    """
    B그룹 버그 수정(자본총계 매칭 강화 / interest_coverage 오탐 방지 / growth 안전장치) 반영 후
    전체 종목 재수집. 이미 저장된 종목도 전부 다시 처리하는 게 핵심이라
    sync_all_kor_stocks()와는 완전히 다른 기준(b_group_synced_at 컬럼)으로 대상 판별.

    ⚠️ DART 일일 호출 한도(4만 건) 때문에 전체(약 2,900개 x 24회 ≈ 69,000건)를
    하루에 다 못 끝낼 가능성이 높음. 한도 초과로 중간에 실패가 늘어나도 걱정 말고,
    다음날 이 함수를 다시 실행하면 b_group_synced_at 기준으로 이어서 진행됨.
    """
    df_krx = fdr.StockListing("KRX")
    sector_map = get_sector_map()
    wics_sector_map = get_wics_sector_map()
    kospi_mdd_cache = get_kospi_mdd_cache()

    already_done = get_b_group_done_codes()
    if already_done:
        print(f"⏭️  B그룹 재수집 이미 완료된 {len(already_done)}개 종목은 건너뜁니다.")

    targets = df_krx[~df_krx["Code"].isin(already_done)]
    if limit:
        targets = targets.head(limit)

    total = len(targets)
    est_calls = total * 24
    print(f"📋 B그룹 재수집 대상 {total}개 (예상 DART 호출 약 {est_calls:,}건 / 일일 한도 40,000건)")
    if est_calls > 40000:
        print("   ⚠️ 예상 호출 건수가 일일 한도를 초과합니다 - 하루에 다 못 끝날 수 있습니다.")
        print("   ⚠️ 도중에 DART 호출 실패가 급증하면 중단하고, 다음날 이 함수를 다시 실행해 이어가세요.")

    succeeded, failed = [], []
    for i, row in enumerate(targets.itertuples(), 1):
        code, name = row.Code, row.Name
        print(f"\n[{i}/{total}] ", end="")
        try:
            sync_kor_stock_fundamental(
                code, name, df_krx=df_krx, sector_map=sector_map, wics_sector_map=wics_sector_map,
                kospi_mdd_cache=kospi_mdd_cache,
                use_ofs_for_manufacturing=use_ofs_for_manufacturing,
            )
            succeeded.append(code)
        except Exception as e:
            print(f"❌ [{name}({code})] 처리 중 예외 발생, 건너뜁니다: {e}")
            failed.append(code)
        time.sleep(sleep_sec)

    print(f"\n\n=== B그룹 재수집 배치 완료: 성공 {len(succeeded)} / 실패 {len(failed)} / 전체 {total} ===")
    if failed:
        print("실패한 종목코드:", failed)
    return succeeded, failed



# --------------------------------------------------------------------------
# 분기 자동갱신 파이프라인
# 실적발표(분기/반기/사업보고서) 나올 때마다 1년(1y) 지표만 가볍게 갱신 -
# 3y/5y/10y는 연간 사업보고서에만 의존하므로 건드리지 않음. 종목당 DART 호출
# ~24회(전체 재수집) -> ~2회로 줄어들어 전체 종목을 매번 돌려도 일일 한도(4만) 내로 여유있음.
# --------------------------------------------------------------------------

def sync_1y_only(stock_code, stock_name, sector, wics_sector, holding_company,
                  existing_period_scores, kospi_mdd_cache, use_ofs_for_manufacturing=True):
    """
    단일 종목의 1y 지표만 갱신. 3y/5y/10y는 existing_period_scores에서 그대로 유지.
    이미 annual baseline 수집이 끝난 종목 대상 - sector/wics_sector/holding_company는
    이미 저장된 값을 그대로 재사용 (매 분기마다 KRX-DESC/WICS 재조회 안 함 - 그건 연 1회 전체
    재수집 때만 갱신되면 충분한 정보라서).
    """
    try:
        financial_sector = is_financial_sector(sector, wics_sector=wics_sector)
        leverage_exempt = financial_sector or holding_company or (wics_sector in LEVERAGE_EXEMPT_WICS_SECTORS)
        effective_use_ofs = use_ofs_for_manufacturing and not (financial_sector or holding_company)

        latest_report = fetch_latest_report_metrics(stock_code, use_ofs_for_manufacturing=effective_use_ofs)
        if latest_report is None:
            print(f"  ⚠️ [{stock_name}] 최신 보고서를 가져오지 못해 1y 갱신을 건너뜁니다.")
            return False

        downturn_defense = calculate_downturn_defense(stock_code, kospi_mdd_cache)

        metrics_1y = {
            "revenue_growth": latest_report["revenue_growth"],
            "eps_growth": latest_report["eps_growth"],
            "downturn_defense": downturn_defense,
        }
        for metric in RATIO_METRICS:
            metrics_1y[metric] = latest_report.get(metric)
        metrics_1y["interest_coverage_is_approx"] = latest_report.get("interest_exp_is_approx", False)

        score = calculate_fundamental_score(metrics_1y, leverage_exempt=leverage_exempt, is_financial=financial_sector)
        if metrics_1y.get("interest_coverage_is_approx") and "interest_coverage" in score["metric_scores"]:
            score["metric_scores"]["interest_coverage"]["is_approximate"] = True

        report_year = latest_report["_report_year"]
        report_code = latest_report["_report_code"]
        report_label = REPORT_CODE_LABEL.get(report_code, report_code)

        merged_period_scores = dict(existing_period_scores or {})
        merged_period_scores["1y"] = {
            "years_used": [f"{report_year} {report_label}"],
            "avg": {
                "total_score": score["total_score"],
                "grade": score["grade"],  # 잠정 등급 - 등급 재산정 패스에서 최종 반영됨
                "metric_scores": score["metric_scores"],
                "sub_scores": score["sub_scores"],
                "financial_adjusted": score["financial_adjusted"],
                "missing_metric_count": score["missing_metric_count"],
            },
            "worst": {  # 보고서 1개뿐이라 평균=최악
                "total_score": score["total_score"],
                "grade": score["grade"],
                "metric_scores": score["metric_scores"],
                "sub_scores": score["sub_scores"],
                "financial_adjusted": score["financial_adjusted"],
                "missing_metric_count": score["missing_metric_count"],
            },
        }

        capital_impairment = latest_report["total_equity"] < 0

        payload = _sanitize_json({
            "stock_code": stock_code,
            "period_scores": merged_period_scores,
            "capital_impairment": capital_impairment,
            "last_1y_updated_at": datetime.utcnow().isoformat(),
        })
        supabase.table("Fundamental").upsert(payload, on_conflict="stock_code").execute()

        print(f"  ✅ [{stock_name}] 1y 갱신 완료 -> {report_year}년 {report_label} 기준 {score['total_score']}점")
        return True

    except Exception as e:
        print(f"❌ [{stock_name}] 1y 갱신 에러: {e}")
        return False


def get_1y_update_targets():
    """
    1y 갱신 대상 종목 조회 - annual baseline(period_scores)이 이미 있는 종목만 대상
    (data_unavailable=true인 우선주 등은 애초에 재무제표가 없어서 1y 갱신도 무의미).
    PostgREST 1000행 제한 페이지네이션 처리.
    """
    all_rows = []
    page_size = 1000
    start = 0
    while True:
        res = (
            supabase.table("Fundamental")
            .select("stock_code, stock_name, sector, wics_sector, holding_company, period_scores")
            .not_.is_("period_scores", "null")
            .range(start, start + page_size - 1)
            .execute()
        )
        rows = res.data
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        start += page_size
    return all_rows


def sync_all_kor_stocks_1y_only(limit=None, sleep_sec=0.3, use_ofs_for_manufacturing=True):
    """
    전체 종목의 1y 지표를 일괄 갱신 (분기 자동갱신 파이프라인의 메인 진입점).
    - 종목당 DART 호출 약 2회 -> 전체 약 2,600개 기준 5,000~6,000건 -> 일일 한도(4만) 내 여유
    - KRX-DESC/WICS 재조회 없음 (이미 저장된 sector/wics_sector/holding_company 재사용)
    - GitHub Actions 등에서 매일 실행해도 무방 (실제 새 보고서가 없으면 같은 보고서를 다시
      확인만 하고 끝나므로 안전 - 낭비되는 DART 호출은 있지만 데이터가 틀어지진 않음)
    """
    kospi_mdd_cache = get_kospi_mdd_cache()

    targets = get_1y_update_targets()
    if limit:
        targets = targets[:limit]

    total = len(targets)
    est_calls = total * 2
    print(f"📋 1y 갱신 대상 {total}개 (예상 DART 호출 약 {est_calls:,}건 / 일일 한도 40,000건)")

    succeeded, failed = [], []
    for i, row in enumerate(targets, 1):
        code, name = row["stock_code"], row["stock_name"]
        print(f"\n[{i}/{total}] 🔄 [{name} ({code})]", end=" ")
        ok = sync_1y_only(
            code, name,
            sector=row.get("sector"), wics_sector=row.get("wics_sector"),
            holding_company=row.get("holding_company") or False,
            existing_period_scores=row.get("period_scores") or {},
            kospi_mdd_cache=kospi_mdd_cache,
            use_ofs_for_manufacturing=use_ofs_for_manufacturing,
        )
        (succeeded if ok else failed).append(code)
        time.sleep(sleep_sec)

    print(f"\n\n=== 1y 갱신 배치 완료: 성공 {len(succeeded)} / 실패 {len(failed)} / 전체 {total} ===")
    if failed:
        print("실패한 종목코드:", failed)
    return succeeded, failed


if __name__ == "__main__":
    target_stocks = [
        ("005930", "삼성전자"), ("000660", "SK하이닉스"),
        ("005935", "삼성전자우"), ("005380", "현대차"), ("000270", "기아"),
    ]

    print("🌐 KRX 상장 종목 데이터 불러오는 중...")
    df_krx_all = fdr.StockListing("KRX")
    sector_map_all = get_sector_map()

    for code, name in target_stocks:
        sync_kor_stock_fundamental(code, name, df_krx=df_krx_all, sector_map=sector_map_all)
    for code, name in target_stocks:
        sync_kor_stock_fundamental(code, name, df_krx=df_krx_all, sector_map=sector_map_all)
