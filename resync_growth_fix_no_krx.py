# resync_growth_fix_no_krx.py
# 목적: KRX(정보데이터시스템, 2025-12-27부터 로그인 필수로 정책 변경됨)가 막혀있어도
#       eps_growth/revenue_growth 재수집을 진행할 수 있도록, KRX/pykrx/fdr.StockListing을
#       전혀 쓰지 않고 DART 데이터만으로 period_scores와 eps 필드를 재계산한다.
#       stock_price/per/pbr/bps 등 "현재가·발행주식수"가 필요한 필드는 건드리지 않고
#       기존 값 그대로 둔다 (이건 KRX 로그인 문제 해결 후 별도로 갱신).
#
# 전제: collector.py에 fetch_multi_year_metrics(..., downturn_defense_override=...) 패치가
#       반영되어 있어야 함 (다운턴 방어력 재계산 없이 기존 저장값 재사용 가능하게 하는 패치).
#
# 실행 위치: collector.py / scoring.py 와 같은 디렉토리 (colab 등)
# 예상 DART 호출: 대상 종목 수 x 약 24회 (3/5/10y annual 데이터까지 포함)
# KRX/pykrx 호출: 0건

import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from collector import (
    supabase,
    calculate_fundamental_score,
    fetch_multi_year_metrics,
    is_financial_sector,
    is_extreme_growth,
    LEVERAGE_EXEMPT_WICS_SECTORS,
    _sanitize_json,
)

GROWTH_KEYS_TO_CHECK = ("revenue_growth", "eps_growth")
PERIODS_LABELS = ["1y", "3y", "5y", "10y"]


def resync_period_scores_only(stock_code, stock_name, sector, wics_sector, holding_company,
                               existing_period_scores, use_ofs_for_manufacturing=True):
    """
    KRX/pykrx(현재가, 발행주식수, 종목리스트)를 전혀 쓰지 않고 DART 데이터만으로
    period_scores(1y/3y/5y/10y 전부)를 재계산. stock_price/per/pbr/bps 등 시세 관련
    필드는 건드리지 않고 그대로 둔다. sector/wics_sector/holding_company는 이미
    저장된 값을 그대로 재사용 (재분류는 안 함 - 그건 연 1회 b_group 전체 재수집 몫).
    downturn_defense도 기존 저장값을 그대로 재사용해서 fdr.DataReader 호출 자체가 없음.
    eps 필드는 DART가 직접 공시하는 "기본주당순이익"(reported_eps)으로 갱신 - 이것도
    발행주식수 필요 없이 DART에서 바로 나오는 값이라 KRX 무관.
    """
    try:
        financial_sector = is_financial_sector(sector, wics_sector=wics_sector)
        leverage_exempt = financial_sector or holding_company or (wics_sector in LEVERAGE_EXEMPT_WICS_SECTORS)
        effective_use_ofs = use_ofs_for_manufacturing and not (financial_sector or holding_company)

        # 기존 downturn_defense 재사용 (sync_1y_only와 동일한 패턴 - fdr.DataReader 호출 없음)
        downturn_defense = None
        for _period_key in PERIODS_LABELS:
            _pdata = (existing_period_scores or {}).get(_period_key)
            if _pdata:
                _dd_entry = (_pdata.get("avg") or {}).get("metric_scores", {}).get("downturn_defense")
                if _dd_entry and _dd_entry.get("value") is not None:
                    downturn_defense = _dd_entry["value"]
                    break

        multi = fetch_multi_year_metrics(
            stock_code, use_ofs_for_manufacturing=effective_use_ofs,
            kospi_mdd_cache=None, downturn_defense_override=downturn_defense,
        )
        if multi is None:
            print(f"  ⚠️ [{stock_name}] DART 데이터를 가져오지 못해 건너뜁니다.")
            return False

        base_year = multi["base_year"]
        latest = multi["yearly_data"][base_year]
        capital_impairment = latest["total_equity"] < 0
        reported_eps = latest.get("reported_eps")

        period_scores = dict(existing_period_scores or {})
        for period, pdata in multi["period_results"].items():
            if pdata is None:
                continue
            avg_score = calculate_fundamental_score(pdata["avg_metrics"], leverage_exempt=leverage_exempt, is_financial=financial_sector)
            worst_score = calculate_fundamental_score(pdata["worst_metrics"], leverage_exempt=leverage_exempt, is_financial=financial_sector)

            if pdata["avg_metrics"].get("interest_coverage_is_approx") and "interest_coverage" in avg_score["metric_scores"]:
                avg_score["metric_scores"]["interest_coverage"]["is_approximate"] = True
            if pdata["worst_metrics"].get("interest_coverage_is_approx") and "interest_coverage" in worst_score["metric_scores"]:
                worst_score["metric_scores"]["interest_coverage"]["is_approximate"] = True

            for growth_key in ("revenue_growth", "eps_growth"):
                avg_val = pdata["avg_metrics"].get(growth_key)
                if is_extreme_growth(avg_val):
                    avg_score["metric_scores"][growth_key]["is_extreme"] = True
                worst_val = pdata["worst_metrics"].get(growth_key)
                if is_extreme_growth(worst_val):
                    worst_score["metric_scores"][growth_key]["is_extreme"] = True

            period_scores[f"{period}y"] = {
                "years_used": pdata["years_used"],
                "yearly_breakdown": pdata.get("yearly_breakdown", {}),
                "avg": {
                    "total_score": avg_score["total_score"],
                    "grade": avg_score["grade"],  # 잠정 등급 - rescore_final_grades.py가 최종 재산정
                    "metric_scores": avg_score["metric_scores"],
                    "sub_scores": avg_score["sub_scores"],
                    "financial_adjusted": avg_score["financial_adjusted"],
                    "missing_metric_count": avg_score["missing_metric_count"],
                },
                "worst": {
                    "total_score": worst_score["total_score"],
                    "grade": worst_score["grade"],
                    "metric_scores": worst_score["metric_scores"],
                    "sub_scores": worst_score["sub_scores"],
                    "financial_adjusted": worst_score["financial_adjusted"],
                    "missing_metric_count": worst_score["missing_metric_count"],
                },
            }

        payload_dict = {
            "stock_code": stock_code,
            "period_scores": period_scores,
            "capital_impairment": capital_impairment,
        }
        # reported_eps가 있을 때만 eps 필드 갱신 - 없으면 기존 근사치를 None으로 덮어쓰지 않게 보호
        if reported_eps is not None:
            payload_dict["eps"] = round(reported_eps, 2)
            payload_dict["eps_is_reported"] = True

        payload = _sanitize_json(payload_dict)
        supabase.table("Fundamental").upsert(payload, on_conflict="stock_code").execute()

        print(f"  ✅ [{stock_name}] period_scores + eps 재계산 완료 (KRX 미사용)")
        return True

    except Exception as e:
        print(f"❌ [{stock_name}] period_scores 재계산 에러: {e}")
        return False


def get_growth_null_affected_rows():
    """revenue_growth 또는 eps_growth 값이 어느 period/mode에서든 None인 종목의 전체 행
    (sector/wics_sector/holding_company/stock_name 포함) 조회. 페이지네이션 처리."""
    all_rows = []
    page_size = 500
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
                for gk in GROWTH_KEYS_TO_CHECK:
                    entry = mscores.get(gk)
                    if entry and entry.get("value") is None:
                        is_affected = True
                        break
                if is_affected:
                    break
            if is_affected:
                break
        if is_affected:
            affected.append(row)

    print(f"🔍 growth null 영향 추정 종목: {len(affected)}개 / 전체 스코어 보유 {len(all_rows)}개")
    return affected


def resync_growth_fix_no_krx(limit=None, sleep_sec=0.1, use_ofs_for_manufacturing=True, max_workers=8):
    """
    KRX 완전 우회 버전. get_growth_null_affected_rows()로 추린 종목만 DART로 재계산.
    fdr/pykrx 호출이 전혀 없어서 KRX 로그인 정책 변경과 무관하게 지금 바로 돌릴 수 있음.
    """
    targets = get_growth_null_affected_rows()
    if limit:
        targets = targets[:limit]

    total = len(targets)
    est_calls = total * 24
    print(f"📋 재수집 대상 {total}개 (예상 DART 호출 약 {est_calls:,}건 / 일일 한도 40,000건, 동시 {max_workers}개 처리, KRX 호출 0건)")
    if est_calls > 40000:
        print("   ⚠️ 예상 호출 건수가 일일 한도를 초과합니다 - limit을 나눠서 여러 번에 걸쳐 실행하세요.")

    succeeded, failed = [], []
    completed = 0
    lock = threading.Lock()

    def _worker(row):
        code, name = row["stock_code"], row["stock_name"]
        try:
            ok = resync_period_scores_only(
                code, name,
                sector=row.get("sector"), wics_sector=row.get("wics_sector"),
                holding_company=row.get("holding_company") or False,
                existing_period_scores=row.get("period_scores") or {},
                use_ofs_for_manufacturing=use_ofs_for_manufacturing,
            )
        except Exception as e:
            print(f"❌ [{name}({code})] 처리 중 예외 발생: {e}")
            ok = False
        if sleep_sec:
            time.sleep(sleep_sec)
        return code, name, ok

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, row): row for row in targets}
        for future in as_completed(futures):
            code, name, ok = future.result()
            with lock:
                completed += 1
                (succeeded if ok else failed).append(code)
                status = "✅" if ok else "⚠️"
                print(f"[{completed}/{total}] {status} [{name} ({code})]")

    print(f"\n\n=== growth 보정 재수집 완료(KRX 미사용): 성공 {len(succeeded)} / 실패 {len(failed)} / 전체 {total} ===")
    if failed:
        print("실패한 종목코드:", failed)
    print("\n⚠️ 다음 단계: total_score 분포가 바뀌었으므로 rescore_final_grades.py를 다시 실행해")
    print("   13-tier 등급(S+~D)을 최신 분포 기준으로 재산정하세요.")
    print("⚠️ stock_price/per/pbr은 이번에 갱신 안 됐습니다 - KRX 로그인 문제 해결 후 별도로 갱신 필요.")
    return succeeded, failed


if __name__ == "__main__":
    # 먼저 소수 종목으로 검증 권장 (예: limit=10, 삼성전자/SK하이닉스 포함되게 확인)
    resync_growth_fix_no_krx()
