# backfill_eps_bps.py
# ==========================================================================
# eps/bps/per/pbr 백필 스크립트 - DART 재조회 전혀 없음.
#
# eps = 순이익(net_income) / 발행주식수, bps = 자본총계(total_equity) / 발행주식수
# net_income/total_equity는 이미 Fundamental 테이블에 저장돼 있고(예전 수집 결과),
# 발행주식수/현재가는 DART가 아니라 FinanceDataReader로 전체 종목을 한 번에 가져올 수
# 있어서, 이 스크립트는 순수 계산 + Supabase 갱신만 한다 (DART 호출 0건).
#
# collector.py의 eps/bps 저장 코드가 뒤늦게 추가되는 바람에, 그 전에 돌았던 수집들은
# net_income/total_equity는 저장했지만 eps/bps/per/pbr은 저장하지 못했음 - 이 스크립트로
# 기존 데이터에서 역산해서 채워넣는다.
#
# 실행 전 꼭 확인:
#   1. DRY_RUN = True 로 먼저 돌려서 몇 개나 채워지는지, 샘플이 정상인지 확인
#   2. 문제 없으면 파일에서 DRY_RUN = False로 직접 고친 뒤 다시 실행
#      (⚠️ 노트북 셀에서 변수만 바꾸지 말고 이 파일 자체를 수정할 것 - %run은 파일에
#       적힌 값을 그대로 다시 읽어옴)
# ==========================================================================

import os

import FinanceDataReader as fdr
from supabase import create_client

DRY_RUN = True

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_rows_missing_eps():
    """eps가 비어있는(또는 net_income은 있는데 eps가 없는) 종목만 조회."""
    all_rows = []
    page_size = 500
    start = 0
    while True:
        res = (
            supabase.table("Fundamental")
            .select("stock_code, net_income, total_equity, eps, stock_price")
            .not_.is_("net_income", "null")
            .is_("eps", "null")
            .range(start, start + page_size - 1)
            .execute()
        )
        rows = res.data
        if not rows:
            break
        all_rows.extend(rows)
        print(f"   ...{len(all_rows)}개 조회됨")
        if len(rows) < page_size:
            break
        start += page_size
    return all_rows


def main():
    print("📥 eps 비어있는 종목 조회 중...")
    rows = fetch_rows_missing_eps()
    print(f"   대상: {len(rows)}개")

    print("\n📥 KRX 전체 종목(현재가/발행주식수) 조회 중 (DART 아님, FinanceDataReader)...")
    df_krx = fdr.StockListing("KRX")
    krx_map = {row.Code: row for row in df_krx.itertuples()}
    print(f"   KRX 종목 {len(krx_map)}개 확보")

    updates = []
    skipped_no_krx = 0
    skipped_no_shares = 0

    for row in rows:
        code = row["stock_code"]
        net_income = row.get("net_income")
        total_equity = row.get("total_equity")

        krx_row = krx_map.get(code)
        if krx_row is None:
            skipped_no_krx += 1
            continue

        issued_shares = getattr(krx_row, "Stocks", None)
        current_price = getattr(krx_row, "Close", None)
        if not issued_shares or issued_shares <= 0:
            skipped_no_shares += 1
            continue

        eps = round(net_income / issued_shares, 2) if net_income is not None else None
        bps = round(total_equity / issued_shares, 2) if total_equity is not None else None
        per = round(current_price / eps, 2) if (eps and eps > 0 and current_price) else None
        pbr = round(current_price / bps, 2) if (bps and bps > 0 and current_price) else None

        updates.append({
            "stock_code": code,
            "eps": eps,
            "bps": bps,
            "per": per,
            "pbr": pbr,
        })

    print(f"\n✅ 계산 완료: {len(updates)}개 종목")
    print(f"   ⚠️ KRX 상장정보 없어서 건너뜀: {skipped_no_krx}개 (상장폐지/거래정지 등 가능성)")
    print(f"   ⚠️ 발행주식수 없어서 건너뜀: {skipped_no_shares}개")

    if updates:
        print("\n🔍 샘플 (처음 5개):")
        for u in updates[:5]:
            print(f"   {u}")

    if DRY_RUN:
        print("\n🛑 DRY_RUN=True 라서 실제 DB에는 반영하지 않았습니다.")
        print("   위 샘플을 확인하고 문제 없으면 이 파일의 DRY_RUN을 False로 고친 뒤 다시 실행하세요.")
        return

    print("\n💾 Supabase에 반영 중...")
    for i, u in enumerate(updates, 1):
        for attempt in range(3):
            try:
                supabase.table("Fundamental").update({
                    "eps": u["eps"], "bps": u["bps"], "per": u["per"], "pbr": u["pbr"],
                }).eq("stock_code", u["stock_code"]).execute()
                break
            except Exception as e:
                if attempt == 2:
                    print(f"   ⚠️ [{u['stock_code']}] 업데이트 실패(3회 재시도 후 포기): {e}")
                else:
                    print(f"   ⚠️ [{u['stock_code']}] 업데이트 재시도 중... ({e})")
        if i % 200 == 0:
            print(f"   {i}/{len(updates)} 완료...")

    print(f"\n🎉 전체 {len(updates)}개 종목 eps/bps/per/pbr 백필 완료!")


if __name__ == "__main__":
    main()
