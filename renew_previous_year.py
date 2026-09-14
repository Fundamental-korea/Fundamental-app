"""
'Renew Previous Year' 워크플로우가 실행하는 스크립트 - 연 1회 전체 재수집.

기본 동작: b_group_synced_at 기준으로 이어서 진행 (resume) - 중간에 멈췄다가
다시 실행해도 안전함.

RESET_BEFORE_RUN=true로 실행하면: 전체를 처음부터 다시 수집 대상으로 잡음
(진짜 "새로운 연간 사이클 시작"할 때만 true로 트리거할 것 - 예: 매년 4월).
"""

import os
from collector import sync_all_kor_stocks_b_group, supabase


def reset_b_group_flags():
    print("🔄 b_group_synced_at 전체 리셋 중 (처음부터 다시 수집)...")
    supabase.table("Fundamental").update({"b_group_synced_at": None}).neq("stock_code", "").execute()
    print("✅ 리셋 완료")


if __name__ == "__main__":
    reset_flag = os.environ.get("RESET_BEFORE_RUN", "false").lower() == "true"

    if reset_flag:
        reset_b_group_flags()
    else:
        print("ℹ️ 리셋 없이 이어서 진행 (이전에 하다 만 게 있으면 거기서부터 재개)")

    print("\n🔄 전체 재수집 실행 중...\n")
    succeeded, failed = sync_all_kor_stocks_b_group(max_workers=4)

    print(f"\n\n=== 완료 ===")
    print(f"성공: {len(succeeded)}개")
    print(f"실패: {len(failed)}개")
    if failed:
        print("실패 종목코드:", failed)

    print(
        "\nℹ️ GitHub Actions 단일 실행은 최대 몇 시간까지만 도니까, 다 못 끝났으면 "
        "이 워크플로우를 (reset 체크 없이) 다시 트리거해서 이어서 진행할 것."
    )
