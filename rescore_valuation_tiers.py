# rescore_valuation_tiers.py
# ==========================================================================
# PER/PBR 업종 내 상대적 저평가/고평가 배지(Tier A/B/C) 계산 스크립트.
# DART 재조회 전혀 없음. Supabase에 이미 저장된 per/pbr(collector.py가 수집
# 시점에 이미 계산해둔 값)을 절대 건드리지 않고, 그 분포를 업종(wics_sector)
# 단위로 나눠서 per_tier/pbr_tier/per_sector_percentile/pbr_sector_percentile만 갱신.
#
# ⚠️ 이 스크립트는 "저평가=좋은 투자"라고 판단하지 않는다. 단순히 업종 내
#    상대적 위치(percentile)만 계산할 뿐이고, 실제 투자 판단(밸류 트랩 가능성 등)은
#    app.py에서 사용자에게 안내 문구로 전달한다.
#
# 실행 전 꼭 확인:
#   1. DRY_RUN = True 로 먼저 돌려서 업종별 tier 분포를 콘솔로 확인
#   2. 문제 없으면 DRY_RUN = False 로 바꿔서 실제 Supabase에 반영
# ==========================================================================

import os
from collections import defaultdict

from supabase import create_client

# --------------------------------------------------------------------------
# 설정
# --------------------------------------------------------------------------
DRY_RUN = True  # True: DB에 쓰지 않고 통계/샘플만 출력. False: 실제 반영.

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# 업종 표본이 이보다 적으면 tier 계산을 건너뜀 (통계적으로 의미 없는 비교 방지) -
# sector_percentile(rescore_final_grades.py)과 동일한 기준으로 일관성 유지
MIN_SECTOR_SAMPLE = 5

# 하위 TIER_A_PCT% = A(저평가), 상위 (100-TIER_C_PCT)% = C(고평가), 나머지 = B(적정)
TIER_A_PCT = 33.0
TIER_C_PCT = 67.0


def fetch_all_rows():
    """PostgREST 1000행 기본 제한 페이지네이션 처리."""
    all_rows = []
    page_size = 500
    start = 0
    while True:
        res = (
            supabase.table("Fundamental")
            .select("stock_code, wics_sector, per, pbr")
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


def compute_percentile(value, sorted_sector_values):
    """sorted_sector_values(오름차순) 안에서 value의 백분위(%) 계산.
    percentile이 높을수록 = 업종 내에서 값이 더 큼(PER/PBR이 높음 = 더 고평가)."""
    n = len(sorted_sector_values)
    if n < MIN_SECTOR_SAMPLE:
        return None
    below_or_equal = sum(1 for v in sorted_sector_values if v <= value)
    return round(100 * below_or_equal / n, 1)


def assign_tier(percentile):
    if percentile is None:
        return None
    if percentile <= TIER_A_PCT:
        return "A"  # 업종 내 저평가 (하위 1/3)
    elif percentile >= TIER_C_PCT:
        return "C"  # 업종 내 고평가 (상위 1/3)
    else:
        return "B"  # 업종 내 적정 수준


def main():
    print("📥 Supabase에서 전체 종목의 per/pbr/wics_sector 조회 중...")
    rows = fetch_all_rows()
    print(f"   전체 종목: {len(rows)}개")

    # ---- 업종별 per/pbr 분포 수집 (0 이하나 None은 비교 대상에서 제외 - 적자/자본잠식 등) ----
    per_pool = defaultdict(list)
    pbr_pool = defaultdict(list)
    for row in rows:
        sector = row.get("wics_sector")
        if not sector:
            continue
        per_val = row.get("per")
        pbr_val = row.get("pbr")
        if per_val is not None and per_val > 0:
            per_pool[sector].append(per_val)
        if pbr_val is not None and pbr_val > 0:
            pbr_pool[sector].append(pbr_val)

    for sector in per_pool:
        per_pool[sector].sort()
    for sector in pbr_pool:
        pbr_pool[sector].sort()

    print("\n📊 업종별 표본 수 (PER 기준, 5개 미만이면 tier 계산 안 함):")
    for sector, vals in sorted(per_pool.items(), key=lambda x: -len(x[1])):
        flag = "" if len(vals) >= MIN_SECTOR_SAMPLE else "  ⚠️ 표본 부족"
        print(f"   {sector}: {len(vals)}개{flag}")

    # ---- 종목별 tier 계산 ----
    updates = []
    tier_dist_check = defaultdict(int)  # 검증용 - PER tier 분포 카운트

    for row in rows:
        code = row["stock_code"]
        sector = row.get("wics_sector")
        per_val = row.get("per")
        pbr_val = row.get("pbr")

        per_pct = per_tier = None
        pbr_pct = pbr_tier = None

        if sector and per_val is not None and per_val > 0 and sector in per_pool:
            per_pct = compute_percentile(per_val, per_pool[sector])
            per_tier = assign_tier(per_pct)
        if sector and pbr_val is not None and pbr_val > 0 and sector in pbr_pool:
            pbr_pct = compute_percentile(pbr_val, pbr_pool[sector])
            pbr_tier = assign_tier(pbr_pct)

        if per_tier:
            tier_dist_check[per_tier] += 1

        updates.append({
            "stock_code": code,
            "per_tier": per_tier,
            "pbr_tier": pbr_tier,
            "per_sector_percentile": per_pct,
            "pbr_sector_percentile": pbr_pct,
        })

    print(f"\n✅ 계산 완료: {len(updates)}개 종목")
    print("\n📊 PER Tier 분포 (검증용 - A=저평가/B=적정/C=고평가):")
    total_with_tier = sum(tier_dist_check.values())
    for tier in ["A", "B", "C"]:
        cnt = tier_dist_check.get(tier, 0)
        pct = round(100 * cnt / total_with_tier, 1) if total_with_tier else 0
        print(f"   {tier}: {cnt}개 ({pct}%)")

    sample = next((u for u in updates if u["stock_code"] == "005930"), updates[0] if updates else None)
    if sample:
        print("\n🔍 샘플 결과 (참고용):")
        print(f"   {sample}")

    if DRY_RUN:
        print("\n🛑 DRY_RUN=True 라서 실제 DB에는 반영하지 않았습니다.")
        print("   위 분포/샘플을 확인하고 문제 없으면 DRY_RUN=False로 바꿔 다시 실행하세요.")
        return

    print("\n💾 Supabase에 반영 중...")
    for i, u in enumerate(updates, 1):
        for attempt in range(3):
            try:
                supabase.table("Fundamental").update({
                    "per_tier": u["per_tier"],
                    "pbr_tier": u["pbr_tier"],
                    "per_sector_percentile": u["per_sector_percentile"],
                    "pbr_sector_percentile": u["pbr_sector_percentile"],
                }).eq("stock_code", u["stock_code"]).execute()
                break
            except Exception as e:
                if attempt == 2:
                    print(f"   ⚠️ [{u['stock_code']}] 업데이트 실패(3회 재시도 후 포기): {e}")
                else:
                    print(f"   ⚠️ [{u['stock_code']}] 업데이트 재시도 중... ({e})")
        if i % 200 == 0:
            print(f"   {i}/{len(updates)} 완료...")

    print(f"\n🎉 전체 {len(updates)}개 종목 PER/PBR Tier 반영 완료!")


if __name__ == "__main__":
    main()
