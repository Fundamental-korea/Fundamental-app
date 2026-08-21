import os
import FinanceDataReader as fdr
import OpenDartReader
import pandas as pd
from scoring import calculate_fundamental_score

# 1. 환경변수 설정
SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
DART_API_KEY = os.environ.get("DART_API_KEY", "")

from supabase import create_client

try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    dart = OpenDartReader(DART_API_KEY)
except Exception as e:
    print(f"❌ 초기 설정 에러: {e}")


def _parse_standard_accounts(df_fin):
    """finstate()가 반환하는 표준 '주요계정'을 dict로 파싱"""
    if "fs_div" in df_fin.columns:
        cfs_df = df_fin[df_fin["fs_div"] == "CFS"]
        df_fin = cfs_df if not cfs_df.empty else df_fin[df_fin["fs_div"] == "OFS"]

    data = {}
    for _, row in df_fin.iterrows():
        name = str(row.get("account_nm", "")).strip()
        try:
            amount = float(str(row.get("thstrm_amount", "0")).replace(",", ""))
        except ValueError:
            amount = 0.0

        if name in ["매출액", "수익(매출액)", "매출"]: data["revenue"] = amount
        elif name in ["영업이익", "영업이익(손실)"]: data["operating_income"] = amount
        elif name in ["당기순이익", "당기순이익(손실)"]: data["net_income"] = amount
        elif name in ["자산총계"]: data["total_assets"] = amount
        elif name in ["부채총계"]: data["total_liabilities"] = amount
        elif name in ["자본총계"]: data["total_equity"] = amount
        elif name in ["유동자산"]: data["current_assets"] = amount
        elif name in ["유동부채"]: data["current_liabilities"] = amount
        elif name in ["자본금"]: data["capital_stock"] = amount
        elif name in ["이익잉여금", "이익잉여금(결손금)"]: data["retained_earnings_amt"] = amount

    return data


def _find_account_amount(df_all, keywords):
    """finstate_all() 전체 재무제표에서 키워드가 포함된 계정명을 찾아 금액 반환 (못 찾으면 None)"""
    if df_all is None or df_all.empty or "account_nm" not in df_all.columns:
        return None
    for kw in keywords:
        matched = df_all[df_all["account_nm"].astype(str).str.contains(kw, na=False)]
        if not matched.empty:
            try:
                return float(str(matched.iloc[0]["thstrm_amount"]).replace(",", ""))
            except (ValueError, KeyError):
                continue
    return None


def _fetch_extra_metrics(stock_code, bsns_year, revenue):
    """이자비용 / 판관비 / 영업활동현금흐름을 전체 재무제표 API에서 조회 (계정명은 종목별로 다를 수 있음)"""
    try:
        df_all = dart.finstate_all(stock_code, int(bsns_year), fs_div="CFS")
    except Exception:
        df_all = None

    interest_expense = _find_account_amount(df_all, ["이자비용", "금융비용"])
    sga = _find_account_amount(df_all, ["판매비와관리비", "판매비와 관리비"])
    ocf = _find_account_amount(df_all, ["영업활동현금흐름", "영업활동으로 인한 현금흐름"])

    sga_ratio = round(sga / revenue * 100, 2) if (sga and revenue > 0) else None

    return interest_expense, sga_ratio, ocf


def sync_kor_stock_fundamental(stock_code, stock_name, df_krx=None, bsns_year="2024"):
    try:
        print(f"🔄 [{stock_name} ({stock_code})] 데이터 수집 및 분석 시작...")

        if df_krx is None or df_krx.empty:
            df_krx = fdr.StockListing("KRX")

        target_stock = df_krx[df_krx["Code"] == stock_code]
        if target_stock.empty:
            print(f"❌ [{stock_name}] KRX 상장 정보를 찾을 수 없습니다.")
            return

        current_price = int(target_stock.iloc[0]["Close"])
        issued_shares = int(target_stock.iloc[0]["Stocks"])

        # 당해년도 재무데이터
        df_fin = dart.finstate(stock_code, int(bsns_year))
        if df_fin is None or not isinstance(df_fin, pd.DataFrame) or df_fin.empty:
            print(f"❌ [{stock_name}] {bsns_year}년 DART 재무제표가 비어있습니다.")
            return
        fin = _parse_standard_accounts(df_fin)

        # 전년도 재무데이터 (성장률 계산용) - 실패해도 전체 흐름은 계속 진행
        prev_revenue, prev_net_income = None, None
        try:
            prev_year = int(bsns_year) - 1
            df_fin_prev = dart.finstate(stock_code, prev_year)
            if df_fin_prev is not None and not df_fin_prev.empty:
                fin_prev = _parse_standard_accounts(df_fin_prev)
                prev_revenue = fin_prev.get("revenue")
                prev_net_income = fin_prev.get("net_income")
        except Exception as e:
            print(f"⚠️ [{stock_name}] 전년도 데이터 조회 실패 (성장률 미계산): {e}")

        revenue = fin.get("revenue", 0.0)
        op_inc = fin.get("operating_income", 0.0)
        net_inc = fin.get("net_income", 0.0)
        assets = fin.get("total_assets", 0.0)
        liab = fin.get("total_liabilities", 0.0)
        equity = fin.get("total_equity", 0.0)
        current_assets = fin.get("current_assets")
        current_liabilities = fin.get("current_liabilities")
        capital_stock = fin.get("capital_stock")
        retained_earnings_amt = fin.get("retained_earnings_amt")

        eps = (net_inc / issued_shares) if issued_shares > 0 else None
        bps = (equity / issued_shares) if issued_shares > 0 else None

        per = round(current_price / eps, 2) if (eps and eps > 0) else None
        pbr = round(current_price / bps, 2) if (bps and bps > 0) else None
        roe = round(net_inc / equity * 100, 2) if equity > 0 else None
        debt_rate = round(liab / equity * 100, 2) if equity > 0 else None
        opm = round(op_inc / revenue * 100, 2) if revenue > 0 else None

        current_ratio = (
            round(current_assets / current_liabilities * 100, 2)
            if (current_assets and current_liabilities and current_liabilities > 0)
            else None
        )
        retained_earnings = (
            round(retained_earnings_amt / capital_stock * 100, 2)
            if (retained_earnings_amt and capital_stock and capital_stock > 0)
            else None
        )

        revenue_growth = (
            round((revenue - prev_revenue) / abs(prev_revenue) * 100, 2)
            if (prev_revenue and prev_revenue != 0)
            else None
        )
        prev_eps = (prev_net_income / issued_shares) if (prev_net_income and issued_shares > 0) else None
        eps_growth = (
            round((eps - prev_eps) / abs(prev_eps) * 100, 2)
            if (eps is not None and prev_eps and prev_eps != 0)
            else None
        )

        # 전체 재무제표에서 이자비용/판관비/영업현금흐름 조회 (계정명이 종목마다 다를 수 있어 못 찾으면 None)
        interest_expense, sga_ratio, ocf = _fetch_extra_metrics(stock_code, bsns_year, revenue)
        interest_coverage = (
            round(op_inc / interest_expense, 2)
            if (interest_expense and interest_expense > 0)
            else None
        )
        ocf_ratio = (
            round(ocf / current_liabilities, 2)
            if (ocf and current_liabilities and current_liabilities > 0)
            else None
        )

        metrics = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "market": "KOR",
            "bsns_year": str(bsns_year),
            "revenue": int(revenue),
            "operating_income": int(op_inc),
            "net_income": int(net_inc),
            "total_assets": int(assets),
            "total_liabilities": int(liab),
            "total_equity": int(equity),
            "stock_price": int(current_price),
            "per": per,
            "pbr": pbr,
            # scoring.py의 METRIC_KEYS와 이름을 맞춘 10개 지표
            "revenue_growth": revenue_growth,
            "eps_growth": eps_growth,
            "opm": opm,
            "roe": roe,
            "debt_rate": debt_rate,
            "current_ratio": current_ratio,
            "interest_coverage": interest_coverage,
            "ocf_ratio": ocf_ratio,
            "retained_earnings": retained_earnings,
            "sga_ratio": sga_ratio,
        }

        score_result = calculate_fundamental_score(metrics)
        payload = {**metrics, **score_result}

        supabase.table("Fundamental").upsert(payload, on_conflict="stock_code").execute()

        print(
            f"✅ [{stock_name}] 완료! (주가: {current_price:,}원 / "
            f"스코어: {payload.get('total_score')} [{payload.get('covered_count')}/10개 지표] / "
            f"등급: {payload.get('grade')})"
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

    for code, name in target_stocks:
        sync_kor_stock_fundamental(code, name, df_krx=df_krx_all)
