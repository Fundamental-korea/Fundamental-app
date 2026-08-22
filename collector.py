import os
from datetime import datetime
import math

import FinanceDataReader as fdr
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

# fdr.StockListing('KRX')에는 Sector가 없고, 'KRX-DESC'에만 있음 (Code 기준으로 별도 조회 후 병합 필요)
FINANCIAL_SECTOR_KEYWORDS = ["금융", "보험", "은행", "캐피탈", "카드", "증권", "저축", "리스"]

# 평균/최악 집계 대상 비율 지표 (성장률 2개는 CAGR로 별도 계산하므로 제외)
RATIO_METRICS = [
    "opm", "roe", "debt_rate", "current_ratio",
    "interest_coverage", "ocf_ratio", "retained_earnings", "sga_ratio",
]

DEFAULT_PERIODS = (1, 3, 5, 10)  # 단기/중기/중장기/장기


def get_sector_map():
    """{종목코드: Sector 문자열} 딕셔너리 반환. KRX-DESC 조회 실패 시 빈 dict."""
    try:
        df_desc = fdr.StockListing("KRX-DESC")
        return dict(zip(df_desc["Code"], df_desc["Sector"]))
    except Exception as e:
        print(f"⚠️ KRX-DESC(Sector) 조회 실패, 업종 판별 없이 진행: {e}")
        return {}


def is_financial_sector(sector_str):
    """Sector 문자열에 금융 관련 키워드가 포함되면 금융업으로 간주 (휴리스틱 - 오분류 가능성 있음)"""
    if not sector_str or not isinstance(sector_str, str):
        return False
    return any(kw in sector_str for kw in FINANCIAL_SECTOR_KEYWORDS)


def get_latest_annual_year():
    """사업보고서(연간)는 통상 익년 3월 말 공시되므로, 4월 이전엔 전전년도까지만 확정치로 간주"""
    now = datetime.now()
    return now.year - 2 if now.month < 4 else now.year - 1


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


def _parse_year_financials(df):
    """단일 연도 재무제표 df에서 주요 계정과 비율 지표를 파싱 (계정명 다중 키워드 매핑)"""

    def get_value(keywords):
        for kw in keywords:
            row = df[df["account_nm"].str.contains(kw, na=False)]
            if not row.empty:
                val_str = str(row.iloc[0]["thstrm_amount"]).replace(",", "")
                if val_str and val_str != "-":
                    return float(val_str)
        return 0.0

    revenue = get_value(["매출액", "수익(매출액)", "영업수익"])
    op_profit = get_value(["영업이익", "영업이익(손실)"])
    net_income = get_value(["당기순이익", "당기순이익(손실)"])
    current_assets = get_value(["유동자산"])
    current_liab = get_value(["유동부채"])
    total_liab = get_value(["부채총계"])
    total_equity = get_value(["자본총계"])
    capital_stock = get_value(["자본금"])
    sga_costs = get_value(["판매비와관리비", "판매비와 관리비", "판관비"])
    operating_cf = get_value(["영업활동현금흐름", "영업활동으로 인한 현금흐름"])
    interest_exp = get_value(["이자비용", "금융원가"])

    ratios = {
        "opm": round(op_profit / revenue * 100, 2) if revenue > 0 else None,
        "roe": round(net_income / total_equity * 100, 2) if total_equity > 0 else None,
        "debt_rate": round(total_liab / total_equity * 100, 2) if total_equity > 0 else None,
        "current_ratio": round(current_assets / current_liab * 100, 2) if current_liab > 0 else None,
        # 이자비용이 0이거나 거의 없으면 최고점 수준으로 처리
        "interest_coverage": round(op_profit / interest_exp, 2) if interest_exp > 0 else 25.0,
        "ocf_ratio": round(operating_cf / net_income, 2) if net_income > 0 else None,
        "retained_earnings": round((total_equity - capital_stock) / capital_stock * 100, 2) if capital_stock > 0 else None,
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

    return _parse_year_financials(df)


def fetch_multi_year_metrics(stock_code, periods=DEFAULT_PERIODS, use_ofs_for_manufacturing=True):
    """
    periods(예: 1/3/5/10년)별로 '평균 기준'과 '최악 기준' 지표 세트를 각각 계산.
    - 비율 지표(opm, roe, debt_rate 등): 기간 내 연도별 값의 평균 / 최악값
    - 성장률 지표(revenue_growth, eps_growth): 기간 시작~종료 연도 CAGR (평균/최악 공통)
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

        avg_metrics = {"revenue_growth": revenue_cagr, "eps_growth": eps_cagr}
        worst_metrics = {"revenue_growth": revenue_cagr, "eps_growth": eps_cagr}

        for metric in RATIO_METRICS:
            series = [yearly_data[y][metric] for y in recent_years if yearly_data[y].get(metric) is not None]
            avg_metrics[metric] = round(sum(series) / len(series), 2) if series else None
            worst_metrics[metric] = worst_value(metric, series)

        period_results[period] = {
            "years_used": window_years,
            "avg_metrics": avg_metrics,
            "worst_metrics": worst_metrics,
        }

    return {"base_year": base_year, "yearly_data": yearly_data, "period_results": period_results}


def sync_kor_stock_fundamental(stock_code, stock_name, df_krx=None, sector_map=None, use_ofs_for_manufacturing=True):
    try:
        print(f"🔄 [{stock_name} ({stock_code})] 데이터 수집 및 분석 시작...")

        if df_krx is None or df_krx.empty:
            df_krx = fdr.StockListing("KRX")
        if sector_map is None:
            sector_map = get_sector_map()

        target_stock = df_krx[df_krx["Code"] == stock_code]
        if target_stock.empty:
            print(f"❌ [{stock_name}] KRX 상장 정보를 찾을 수 없습니다.")
            return

        current_price = int(target_stock.iloc[0]["Close"])
        issued_shares = int(target_stock.iloc[0]["Stocks"])

        sector = sector_map.get(stock_code)
        financial_sector = is_financial_sector(sector)
        print(f"🏷️ [{stock_name}] 업종: {sector or '미상'} (금융업 여부: {financial_sector})")

        multi = fetch_multi_year_metrics(stock_code, use_ofs_for_manufacturing=use_ofs_for_manufacturing)
        if multi is None:
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
