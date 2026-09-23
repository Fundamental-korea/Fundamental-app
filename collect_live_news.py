from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from news_earnings import fetch_macro_news, persist_live_news_snapshot

KST = ZoneInfo("Asia/Seoul")

def main() -> None:
    items = fetch_macro_news(display=20)
    if len(items) < 10:
        raise RuntimeError(f"Live News fetch returned only {len(items)} items; refusing to replace existing snapshot")

    from news_earnings import _get_supabase_client
    client = _get_supabase_client()

    # Live News는 '현재 스냅샷' 테이블처럼 관리한다. 먼저 이 피드가 소유하는
    # 기존 macro rows를 교체한 뒤 새 20건을 같은 client로 저장한다.
    client.table("news_items").delete().in_("source", ["MARKETAUX", "NAVER", "RSS"]).eq(
        "is_macro", True
    ).execute()

    saved = persist_live_news_snapshot(items, supabase_client=client)

    # published_at은 기사마다 과거 시각일 수 있으므로 저장 성공 검증에는
    # collected_at을 사용한다. 이번 실행 직후의 snapshot이 정확히 20건인지 확인한다.
    snapshot_cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    verify = (
        client.table("news_items")
        .select("source,source_id,title,published_at,collected_at")
        .eq("is_macro", True)
        .gte("collected_at", snapshot_cutoff.isoformat())
        .order("published_at", desc=True)
        .limit(50)
        .execute()
    )
    db_rows = verify.data or []
    unique_pairs = {
        (str(row.get("source") or ""), str(row.get("source_id") or ""))
        for row in db_rows
        if row.get("source_id")
    }
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    print(
        f"[LIVE NEWS] {now_kst} collected={len(items)} persisted={saved} "
        f"verified_snapshot_rows={len(db_rows)} unique_source_pairs={len(unique_pairs)}"
    )
    if len(unique_pairs) < len(items):
        raise RuntimeError(
            f"Live News DB verification failed: collected={len(items)} "
            f"but latest snapshot has only {len(unique_pairs)} unique rows"
        )
if __name__ == "__main__":
    main()