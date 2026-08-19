import os
import FinanceDataReader as fdr
import OpenDartReader
import pandas as pd
from supabase import create_client

# 1. 환경변수 설정 (GitHub Secrets가 비어있어도 안전하도록 기본값 하드코딩)
SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImNud2VnZ2VjaGlwZ2hjaXZydWllIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODcwODU5ODksImV4cCI6MjEwMjY2MTk4OX0.mYi7QB0ekkC0Jg49M18tqrMdCZBQgRHEK2J1EdIBZhc")
DART_API_KEY = os.environ.get("DART_API_KEY", "01c7269f5655e2d846d0e9d7f79f46aef8ac417f")

# 2. 클라이언트 및 DART 객체 전역 생성 (반복 호출 방지)
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    dart = OpenDartReader(DART_API_KEY)
except Exception as e:
    print(f"❌ 초기 설정 에러: {e}")

def sync_kor_stock_fundamental(stock_code, stock_name, bsns_year="2024"):
    try:
        print(f"🔄 [{stock_name} ({stock_code})] 데이터 업데이트 시작...")
        
        # 주가 데이터 가져오기
        df_krx = fdr.StockListing("KRX")
        target_stock = df_krx[df_krx["Code"] == stock_code]

        if target_stock.empty:
            print(f"❌ [{stock_name}] KRX 상장 정보를 찾을 수 없습니다.")
            return

        current_price = int(target_stock.iloc[0]["Close"])
        issued_shares = int(target_stock.iloc[0]["Stocks"])

       # DART 재무 데이터 호출
        df_fin = dart.finstate(stock_code, int(bsns_year))
        
        # 🛡️ [수정/보완] 데이터가 없거나, 데이터프레임 형식이 아니거나, 비어있는 경우 완벽 방어
        if df_fin is None or not isinstance(df_fin, pd.DataFrame) or df_fin.empty:
            print(f"❌ [{stock_name}] {bsns_year}년 DART 재무제표가 없거나 형식이 올바르지 않습니다.")
            return

        # 연결재무제표(CFS) 우선 사용
        if "fs_div" in df_fin.columns:
            cfs_df = df_fin[df_fin["fs_div"] == "CFS"]
            df_fin = cfs_df if not cfs_df.empty else df_fin[df_fin["fs_div"] == "OFS"]

        # 데이터 파싱
        fin_data = {}
        for _, row in df_fin.iterrows():
            account_nm = str(row.get("account_nm", "")).strip()
            try:
                amount = float(str(row.get("thstrm_amount", "0")).replace(",", ""))
            except:
                amount = 0.0
            
            # 매핑 로직
            if account_nm in ["매출액", "수익(매출액)", "매출"]: fin_data["revenue"] = amount
            elif account_nm in ["영업이익", "영업이익(손실)"]: fin_data["operating_income"] = amount
            elif account_nm in ["당기순이익", "당기순이익(손실)"]: fin_data["net_income"] = amount
            elif account_nm in ["자산총계"]: fin_data["total_assets"] = amount
            elif account_nm in ["부채총계"]: fin_data["total_liabilities"] = amount
            elif account_nm in ["자본총계"]: fin_data["total_equity"] = amount

        # 밸류에이션 계산
        revenue = fin_data.get("revenue", 0.0)
        op_inc = fin_data.get("operating_income", 0.0)
        net_inc = fin_data.get("net_income", 0.0)
        assets = fin_data.get("total_assets", 0.0)
        liab = fin_data.get("total_liabilities", 0.0)
        equity = fin_data.get("total_equity", 0.0)

        eps = (net_inc / issued_shares) if issued_shares > 0 else None
        bps = (equity / issued_shares) if issued_shares > 0 else None
        
        payload = {
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
            "per": round(current_price / eps, 2) if (eps and eps > 0) else None,
            "pbr": round(current_price / bps, 2) if (bps and bps > 0) else None,
            "roe": round(net_inc / equity * 100, 2) if equity > 0 else None,
            "debt_ratio": round(liab / equity * 100, 2) if equity > 0 else None,
            "op_margin": round(op_inc / revenue * 100, 2) if revenue > 0 else None,
        }

        supabase.table("Fundamental").upsert(payload, on_conflict="stock_code").execute()
        print(f"✅ [{stock_name}] 완료! (주가: {current_price:,}원)")

    except Exception as e:
        print(f"❌ [{stock_name}] 업데이트 에러: {e}")

if __name__ == "__main__":
    target_stocks = [
        ("005930", "삼성전자"), ("000660", "SK하이닉스"),
        ("005935", "삼성전자우"), ("005380", "현대차"), ("000270", "기아"),
    ]
    for code, name in target_stocks:
        sync_kor_stock_fundamental(code, name)
