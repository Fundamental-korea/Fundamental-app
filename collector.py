import os
import FinanceDataReader as fdr
import OpenDartReader
import pandas as pd
from supabase import create_client

# 환경변수 설정 (비어있을 경우 기본값 적용)
SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
DART_API_KEY = os.environ.get("DART_API_KEY", "01c7269f5655e2d846d0e9d7f79f46aef8ac417f")

# 💡 스크립트 실행 시점에 전역으로 OpenDartReader를 선언하면 DART 서버 타임아웃이 발생하므로 제거했습니다.
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def sync_kor_stock_fundamental(stock_code, stock_name, bsns_year="2024"):
    try:
        print(f"🔄 [{stock_name} ({stock_code})] 최신 데이터 업데이트 시작...")
        
        # 💡 DART 객체를 함수 내부에서 필요할 때만 생성하여 타임아웃 및 차단 방어
        dart = OpenDartReader(DART_API_KEY)

        df_krx = fdr.StockListing("KRX")
        target_stock = df_krx[df_krx["Code"] == stock_code]

        if target_stock.empty:
            print(f"❌ [{stock_name}] KRX 상장 정보를 찾을 수 없습니다.")
            return

        current_price = int(target_stock.iloc[0]["Close"])
        issued_shares = int(target_stock.iloc[0]["Stocks"])

        df_fin = dart.finstate(stock_code, int(bsns_year))
        if df_fin is None or df_fin.empty:
            print(f"❌ [{stock_name}] {bsns_year}년 DART 재무제표가 없습니다.")
            return

        if "fs_div" in df_fin.columns:
            cfs_df = df_fin[df_fin["fs_div"] == "CFS"]
            df_fin = cfs_df if not cfs_df.empty else df_fin[df_fin["fs_div"] == "OFS"]

        fin_data = {}
        for _, row in df_fin.iterrows():
            account_nm = str(row.get("account_nm", "")).strip()
            try:
                amount = float(str(row.get("thstrm_amount", "0")).replace(",", ""))
            except ValueError:
                amount = 0.0

            if account_nm in ["매출액", "수익(매출액)", "매출"]:
                fin_data["revenue"] = amount
            elif account_nm in ["영업이익", "영업이익(손실)"]:
                fin_data["operating_income"] = amount
            elif account_nm in ["당기순이익", "당기순이익(손실)"]:
                fin_data["net_income"] = amount
            elif account_nm in ["자산총계"]:
                fin_data["total_assets"] = amount
            elif account_nm in ["부채총계"]:
                fin_data["total_liabilities"] = amount
            elif account_nm in ["자본총계"]:
                fin_data["total_equity"] = amount

        revenue = fin_data.get("revenue", 0.0)
        op_inc = fin_data.get("operating_income", 0.0)
        net_inc = fin_data.get("net_income", 0.0)
        assets = fin_data.get("total_assets", 0.0)
        liab = fin_data.get("total_liabilities", 0.0)
        equity = fin_data.get("total_equity", 0.0)

        eps = (net_inc / issued_shares) if issued_shares > 0 else None
        bps = (equity / issued_shares) if issued_shares > 0 else None
        per = (current_price / eps) if (eps and eps > 0) else None
        pbr = (current_price / bps) if (bps and bps > 0) else None
        roe = (net_inc / equity * 100) if equity > 0 else None
        roa = (net_inc / assets * 100) if assets > 0 else None
        debt_ratio = (liab / equity * 100) if equity > 0 else None
        op_margin = (op_inc / revenue * 100) if revenue > 0 else None
        net_margin = (net_inc / revenue * 100) if revenue > 0 else None

        payload = {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "market": "KOR",
            "bsns_year": str(bsns_year),
            "reprt_code": "11011",
            "revenue": int(revenue),
            "operating_income": int(op_inc),
            "net_income": int(net_inc),
            "total_assets": int(assets),
            "total_liabilities": int(liab),
            "total_equity": int(equity),
            "issued_shares": int(issued_shares),
            "stock_price": int(current_price),
            "per": round(per, 2) if per is not None else None,
            "pbr": round(pbr, 2) if pbr is not None else None,
            "roe": round(roe, 2) if roe is not None else None,
            "roa": round(roa, 2) if roa is not None else None,
            "debt_ratio": round(debt_ratio, 2) if debt_ratio is not None else None,
            "op_margin": round(op_margin, 2) if op_margin is not None else None,
            "net_margin": round(net_margin, 2) if net_margin is not None else None,
        }

        supabase.table("Fundamental").upsert(payload, on_conflict="stock_code").execute()
        print(f"✅ [{stock_name}] DB 최신화 완료! (현재가: {current_price:,}원)")

    except Exception as e:
        print(f"❌ [{stock_name}] 업데이트 에러: {e}")

if __name__ == "__main__":
    # 모니터링 주요 종목 리스트
    target_stocks = [
        ("005930", "삼성전자"),
        ("000660", "SK하이닉스"),
        ("005935", "삼성전자우"),
        ("005380", "현대차"),
        ("000270", "기아"),
    ]
    for code, name in target_stocks:
        sync_kor_stock_fundamental(code, name)
