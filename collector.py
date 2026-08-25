import os
from datetime import datetime, timedelta
import math
import time

import FinanceDataReader as fdr
import requests
from opendartreader import OpenDartReader
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
RATIO_METRICS = [
    "opm", "roic", "debt_rate", "quick_ratio",
    "interest_coverage", "ocf_ratio", "sga_ratio",
]

DEFAULT_PERIODS = (1, 3, 5, 10)  # 단기/중기/중장기/장기

CORP_TAX_RATE = 0.22  # ROIC의 NOPAT 계산용 근사 법인세율

# '하락장 실제 방어력' 지표 계산에 쓰는 과거 주요 하락장 구간 (코스피 대비 종목의 실제 낙폭 비교)
DOWNTURN_WINDOWS = [
    ("코로나 폭락", "2020-01-20", "2020-03-19"),
    ("2022년 긴축 하락장", "2021-12-01", "2022-09-30"),
]


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
    금융업 여부 판별. WICS 분류가 있으면 그걸 우선 사용(정확), 없으면 KSIC Industry 키워드로 추정(휴리스틱).
    """
    if wics_sector == "금융":
        return True
    if wics_sector is not None and wics_sector != "금융":
        return False
    if not sector_str or not isinstance(sector_str, str):
        return False
    return any(kw in sector_str for kw in FINANCIAL_SECTOR_KEYWORDS)


def _fetch_full_statement_df(stock_code, year, reprt_code="11011", use_ofs_for_manufacturing=True):
    """
    dart.finstate()의 '주요계정'(13개 표준항목)에는 재고자산/판관비/이자비용/영업활동현금흐름이
    아예 없어서, 전체 재무제표(finstate_all)를 별도로 조회해 이 계정들을 찾기 위한 함수.
    """
    try:
        fin_data = dart.finstate_all(stock_code, year, reprt_code=reprt_code)
    except Exception as e:
        print(f"  ⚠️ 전체 재무제표 조회 실패({year}년 {reprt_code}): {e}")
        return None

    if fin_data is None or fin_data.empty:
        return None

    cfs = fin_data[fin_data["fs_div"] == "CFS"]
    ofs = fin_data[fin_data["fs_div"] == "OFS"]
    if use_ofs_for_manufacturing and not ofs.empty:
        return ofs
    return cfs if not cfs.empty else ofs


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
        for kw in keywords:
            row = source_df[source_df["account_nm"].str.contains(kw, na=False, regex=False)]
            if not row.empty:
                val_str = str(row.iloc[0]["thstrm_amount"]).replace(",", "")
                if val_str and val_str != "-":
                    return float(val_str)
        return 0.0

    revenue = get_value(["매출액", "수익(매출액)", "영업수익"], df)
    op_profit = get_value(["영업이익", "영업이익(손실)"], df)
    net_income = get_value(["당기순이익", "당기순이익(손실)"], df)
    total_assets = get_value(["자산총계"], df)
    current_assets = get_value(["유동자산"], df)
    current_liab = get_value(["유동부채"], df)
    total_liab = get_value(["부채총계"], df)
    total_equity = get_value(["자본총계"], df)

    detail_df = df_full if df_full is not None else df
    inventory = get_value(["재고자산"], detail_df)
    sga_costs = get_value(["판매비와관리비", "판매비와 관리비", "판관비"], detail_df)
    operating_cf = get_value(["영업활동현금흐름", "영업활동으로 인한 현금흐름"], detail_df)
    interest_exp = get_value(["이자비용", "금융원가"], detail_df)

    # 투하자본 근사치: 자산총계 - 유동부채 (이자부채만 정확히 구분하기 어려워 유동부채 전체를 차감하는 간이 추정)
    invested_capital = total_assets - current_liab
    nopat = op_profit * (1 - CORP_TAX_RATE)

    ratios = {
        "opm": round(op_profit / revenue * 100, 2) if revenue > 0 else None,
        "roic": round(nopat / invested_capital * 100, 2) if invested_capital > 0 else None,
        "debt_rate": round(total_liab / total_equity * 100, 2) if total_equity > 0 else None,
        "quick_ratio": round((current_assets - inventory) / current_liab * 100, 2) if current_liab > 0 else None,
        # 이자비용이 0이거나 거의 없으면 최고점 수준으로 처리
        "interest_coverage": round(op_profit / interest_exp, 2) if interest_exp > 0 else 25.0,
        "ocf_ratio": round(operating_cf / net_income, 2) if net_income > 0 else None,
        "sga_ratio": round(sga_costs / revenue * 100, 2) if revenue > 0 else None,
    }

    raw = {
        "revenue": revenue,
        "operating_income": op_profit,
        "net_income": net_income,
        "total_liabilities": total_liab,
        "total_equity": total_equity,
    }

    return {**ratios, **raw}


def _parse_report_financials(df, df_full=None):
    """
    단일 보고서 df에서 주요 계정, 비율 지표, 그리고 당기(thstrm) vs 전년동기(frmtrm) 성장률까지 파싱.
    분기/반기 누적보고서도 frmtrm_amount가 '작년 동기간 누적'이라 그대로 동기간 성장률로 쓸 수 있음.
    df_full(전체 재무제표)이 주어지면 재고자산/판관비/이자비용/영업현금흐름은 거기서 찾음.
    """

    def get_value(keywords, source_df, field="thstrm_amount"):
        for kw in keywords:
            row = source_df[source_df["account_nm"].str.contains(kw, na=False, regex=False)]
            if not row.empty:
                val_str = str(row.iloc[0][field]).replace(",", "")
                if val_str and val_str != "-":
                    return float(val_str)
        return 0.0

    revenue = get_value(["매출액", "수익(매출액)", "영업수익"], df)
    op_profit = get_value(["영업이익", "영업이익(손실)"], df)
    net_income = get_value(["당기순이익", "당기순이익(손실)"], df)
    total_assets = get_value(["자산총계"], df)
    current_assets = get_value(["유동자산"], df)
    current_liab = get_value(["유동부채"], df)
    total_liab = get_value(["부채총계"], df)
    total_equity = get_value(["자본총계"], df)

    detail_df = df_full if df_full is not None else df
    inventory = get_value(["재고자산"], detail_df)
    sga_costs = get_value(["판매비와관리비", "판매비와 관리비", "판관비"], detail_df)
    operating_cf = get_value(["영업활동현금흐름", "영업활동으로 인한 현금흐름"], detail_df)
    interest_exp = get_value(["이자비용", "금융원가"], detail_df)

    prev_revenue = get_value(["매출액", "수익(매출액)", "영업수익"], df, field="frmtrm_amount")
    prev_net_income = get_value(["당기순이익", "당기순이익(손실)"], df, field="frmtrm_amount")

    invested_capital = total_assets - current_liab
    nopat = op_profit * (1 - CORP_TAX_RATE)

    ratios = {
        "opm": round(op_profit / revenue * 100, 2) if revenue > 0 else None,
        "roic": round(nopat / invested_capital * 100, 2) if invested_capital > 0 else None,
        "debt_rate": round(total_liab / total_equity * 100, 2) if total_equity > 0 else None,
        "quick_ratio": round((current_assets - inventory) / current_liab * 100, 2) if current_liab > 0 else None,
        "interest_coverage": round(op_profit / interest_exp, 2) if interest_exp > 0 else 25.0,
        "ocf_ratio": round(operating_cf / net_income, 2) if net_income > 0 else None,
        "sga_ratio": round(sga_costs / revenue * 100, 2) if revenue > 0 else None,
    }

    revenue_growth = (
        round((revenue - prev_revenue) / abs(prev_revenue) * 100, 2)
        if prev_revenue not in (0, None) else None
    )
    # EPS 성장률은 발행주식수가 안정적이라는 가정 하에 순이익 증가율로 근사
    eps_growth = (
        round((net_income - prev_net_income) / abs(prev_net_income) * 100, 2)
        if prev_net_income not in (0, None) else None
    )

    raw = {
        "revenue": revenue,
        "operating_income": op_profit,
        "net_income": net_income,
        "total_liabilities": total_liab,
        "total_equity": total_equity,
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

        revenue_cagr = (
            round(((rev_newest / rev_oldest) ** (1 / actual_span) - 1) * 100, 2)
            if (rev_oldest > 0 and rev_newest > 0 and actual_span > 0) else None
        )
        eps_cagr = (
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
        print(f"🏷️ [{stock_name}] 업종(상세): {sector or '미상'} / WICS: {wics_sector or '미상'} (금융업 여부: {financial_sector})")

        # 금융지주/보험/은행 등은 별도재무제표(OFS)가 사실상 빈 껍데기라, 연결재무제표(CFS)를 써야 실질적인 사업이 보임
        effective_use_ofs = use_ofs_for_manufacturing and not financial_sector

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
        eps = (net_income / issued_shares) if issued_shares > 0 else None
        bps = (total_equity / issued_shares) if issued_shares > 0 else None
        per = round(current_price / eps, 2) if (eps and eps > 0) else None
        pbr = round(current_price / bps, 2) if (bps and bps > 0) else None

        period_scores = {}
        for period, pdata in multi["period_results"].items():
            if pdata is None:
                continue
            avg_score = calculate_fundamental_score(pdata["avg_metrics"], is_financial_sector=financial_sector)
            worst_score = calculate_fundamental_score(pdata["worst_metrics"], is_financial_sector=financial_sector)
            period_scores[f"{period}y"] = {
                "years_used": pdata["years_used"],
                "avg": {
                    "total_score": avg_score["total_score"],
                    "grade": avg_score["grade"],
                    "metric_scores": avg_score["metric_scores"],
                },
                "worst": {
                    "total_score": worst_score["total_score"],
                    "grade": worst_score["grade"],
                    "metric_scores": worst_score["metric_scores"],
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
            "base_year": base_year,
            "stock_price": current_price,
            "per": per,
            "pbr": pbr,
            "revenue": int(latest["revenue"]),
            "operating_income": int(latest["operating_income"]),
            "net_income": int(latest["net_income"]),
            "total_liabilities": int(latest["total_liabilities"]),
            "total_equity": int(latest["total_equity"]),
            "period_scores": period_scores,  # Supabase jsonb 컬럼 저장 추천
        }

        payload = _sanitize_json(payload)
        supabase.table("Fundamental").upsert(payload, on_conflict="stock_code").execute()

        print(f"✅ [{stock_name}] 완료! (주가: {current_price:,}원, 기준연도: {base_year})")

    except Exception as e:
        print(f"❌ [{stock_name}] 업데이트 에러: {e}")


def get_already_synced_codes():
    """Supabase Fundamental 테이블에 이미 저장된 stock_code 목록 조회 (재시작 시 중복 방지용)"""
    try:
        res = supabase.table("Fundamental").select("stock_code").execute()
        return {row["stock_code"] for row in res.data}
    except Exception as e:
        print(f"⚠️ 기존 저장 목록 조회 실패, 처음부터 진행합니다: {e}")
        return set()


def sync_all_kor_stocks(limit=None, sleep_sec=0.5, resume=True, use_ofs_for_manufacturing=True):
    """
    KRX 상장 전체 종목을 순회하며 sync_kor_stock_fundamental 실행.
    - limit: 테스트용 개수 제한 (None이면 전체)
    - sleep_sec: DART 서버 부담을 줄이기 위한 종목 간 대기시간(초)
    - resume: True면 Supabase에 이미 저장된 종목은 자동으로 건너뜀 (중간에 끊겨도 이어서 실행 가능)
    실패한 종목은 건너뛰고 계속 진행하며, 마지막에 성공/실패 목록을 출력.
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
