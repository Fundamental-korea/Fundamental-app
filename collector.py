import os
from datetime import datetime

import FinanceDataReader as fdr
import OpenDartReader
import pandas as pd
from supabase import create_client

from scoring import calculate_fundamental_score

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


def get_latest_report_info():
    """현재 시점 기준 DART에서 가장 최근 공시된 보고서 연도와 코드 추적 (콜랩 로직 그대로)"""
    now = datetime.now()
    year, month = now.year, now.month

    if month < 4:
        return year - 1, "11014"  # 전년도 3분기
    elif month < 6:
        return year - 1, "11011"  # 전년도 사업보고서 (4분기)
    elif month < 9:
        return year, "11013"      # 올해 1분기
    elif month < 12:
        return year, "11012"      # 올해 반기 (2분기)
    else:
        return year, "11014"      # 올해 3분기


def fetch_dart_metrics(stock_code, use_ofs_for_manufacturing=True):
    """
    콜랩 fetch_and_analyze_dart_improved 로직 이식.
    1. 계정명 다중 매핑 (파싱 에러 방지)
    2. 제조업/금융자회사 보유 기업 -> 별도(OFS) 우선, 그 외 -> 연결(CFS) 우선
    3. ZeroDivision 및 데이터 예외 처리
    반환: (metrics dict | None)
    """
    target_year, target_report_code = get_latest_report_info()
    print(f"🔍 [{stock_code}] {target_year}년 {target_report_code} 보고서 분석 시작...")

    fin_data = dart.finstate(stock_code, target_year, reprt_code=target_report_code)
    if fin_data is None or fin_data.empty:
        print(f"❌ [{stock_code}] 재무제표 데이터를 불러올 수 없습니다.")
        return None

    cfs = fin_data[fin_data["fs_div"] == "CFS"]
    ofs = fin_data[fin_data["fs_div"] == "OFS"]

    if use_ofs_for_manufacturing and not ofs.empty:
        df = ofs
        print("💡 [별도재무제표(OFS)] 기준으로 분석합니다.")
    else:
        df = cfs if not cfs.empty else ofs
        print("💡 [연결재무제표(CFS)] 기준으로 분석합니다.")

    if df.empty:
        print(f"❌ [{stock_code}] CFS/OFS 데이터 모두 비어있습니다.")
        return None

    def get_value_by_keywords(keywords):
        for kw in keywords:
            row = df[df["account_nm"].str.contains(kw, na=False)]
            if not row.empty:
                val_str = str(row.iloc[0]["thstrm_amount"]).replace(",", "")
                if val_str and val_str != "-":
                    return float(val_str)
        return 0.0

    def get_prev_value_by_keywords(keywords):
        for kw in keywords:
            row = df[df["account_nm"].str.contains(kw, na=False)]
            if not row.empty:
                val_str = str(row.iloc[0]["frmtrm_amount"]).replace(",", "")
                if val_str and val_str != "-":
                    return float(val_str)
        return 0.0

    revenue = get_value_by_keywords(["매출액", "수익(매출액)", "영업수익"])
    op_profit = get_value_by_keywords(["영업이익", "영업이익(손실)"])
    net_income = get_value_by_keywords(["당기순이익", "당기순이익(손실)"])
    current_assets = get_value_by_keywords(["유동자산"])
    current_liab = get_value_by_keywords(["유동부채"])
    total_liab = get_value_by_keywords(["부채총계"])
    total_equity = get_value_by_keywords(["자본총계"])
    capital_stock = get_value_by_keywords(["자본금"])
    sga_costs = get_value_by_keywords(["판매비와관리비", "판매비와 관리비", "판관비"])
    operating_cf = get_value_by_keywords(["영업활동현금흐름", "영업활동으로 인한 현금흐름"])
    interest_exp = get_value_by_keywords(["이자비용", "금융원가"])

    prev_revenue = get_prev_value_by_keywords(["매출액", "수익(매출액)", "영업수익"])
    prev_net_income = get_prev_value_by_keywords(["당기순이익", "당기순이익(손실)"])

    rev_growth = ((revenue - prev_revenue) / prev_revenue * 100) if prev_revenue > 0 else 0.0
    eps_growth = ((net_income - prev_net_income) / abs(prev_net_income) * 100) if prev_net_income != 0 else 0.0
    opm = (op_profit / revenue * 100) if revenue > 0 else 0.0
    roe = (net_income / total_equity * 100) if total_equity > 0 else 0.0
    debt_rate = (total_liab / total_equity * 100) if total_equity > 0 else 999.0
    current_ratio = (current_assets / current_liab * 100) if current_liab > 0 else 0.0
    # 이자비용이 0이거나 거의 없으면 최고점 처리
    interest_coverage = (op_profit / interest_exp) if interest_exp > 0 else 25.0
    ocf_ratio = (operating_cf / net_income) if net_income > 0 else 0.0
    retained_earnings = ((total_equity - capital_stock) / capital_stock * 100) if capital_stock > 0 else 0.0
    sga_ratio = (sga_costs / revenue * 100) if revenue > 0 else 0.0

    computed_metrics = {
        "revenue_growth": round(rev_growth, 2),
        "eps_growth": round(eps_growth, 2),
        "opm": round(opm, 2),
        "roe": round(roe, 2),
        "debt_rate": round(debt_rate, 2),
        "current_ratio": round(current_ratio, 2),
        "interest_coverage": round(interest_coverage, 2),
        "ocf_ratio": round(ocf_ratio, 2),
        "retained_earnings": round(retained_earnings, 2),
        "sga_ratio": round(sga_ratio, 2),
    }

    raw_accounts = {
        "revenue": int(revenue),
        "operating_income": int(op_profit),
        "net_income": int(net_income),
        "total_assets": int(current_assets + get_value_by_keywords(["비유동자산"])) if current_assets else 0,
        "total_liabilities": int(total_liab),
        "total_equity": int(total_equity),
    }

    return computed_metrics, raw_accounts


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

        result = fetch_dart_metrics(stock_code, use_ofs_for_manufacturing=use_ofs_for_manufacturing)
        if result is None:
            return
        computed_metrics, raw_accounts = result

        net_income = raw_accounts["net_income"]
        total_equity = raw_accounts["total_equity"]
        eps = (net_income / issued_shares) if issued_shares > 0 else None
        bps = (total_equity / issued_shares) if issued_shares > 0 else None
        per = round(current_price / eps, 2) if (eps and eps > 0) else None
        pbr = round(current_price / bps, 2) if (bps and bps > 0) else None

        score_result = calculate_fundamental_score(computed_metrics, is_financial_sector=financial_sector)

        payload = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "market": "KOR",
            "sector": sector,
            "stock_price": current_price,
            "per": per,
            "pbr": pbr,
            **raw_accounts,
            **computed_metrics,
            "total_score": score_result["total_score"],
            "grade": score_result["grade"],
            "grade_desc": score_result["grade_desc"],
            "metric_scores": score_result["metric_scores"],
        }

        supabase.table("Fundamental").upsert(payload, on_conflict="stock_code").execute()

        print(
            f"✅ [{stock_name}] 완료! (주가: {current_price:,}원 / "
            f"스코어: {score_result['total_score']}점 / 등급: {score_result['grade']})"
        )

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
