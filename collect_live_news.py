from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from news_earnings import fetch_macro_news, persist_live_news_snapshot

KST = ZoneInfo("Asia/Seoul")

def main() -> None:
    items = fetch_macro_news(display=20)
    saved = persist_live_news_snapshot(items)

    from news_earnings import _get_supabase_client
    client = _get_supabase_client()
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    client.table("news_items").delete().in_("source", ["MARKETAUX", "NAVER", "RSS", "BING NEWS"]).eq(
        "is_macro", True
    ).lt("published_at", cutoff.isoformat()).execute()

    # 수집 시각을 기준으로 같은 실행의 스냅샷이 실제 DB에 존재하는지 검증한다.
    snapshot_cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
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
    unique_ids = {str(row.get("source_id") or "") for row in db_rows if row.get("source_id")}
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    print(
        f"[LIVE NEWS] {now_kst} collected={len(items)} persisted={saved} "
        f"verified_snapshot_rows={len(db_rows)} unique_source_ids={len(unique_ids)}"
    )
    if len(unique_ids) < min(len(items), 10):
        raise RuntimeError(
            f"Live News DB verification failed: collected={len(items)} "
            f"but latest snapshot has only {len(unique_ids)} unique rows"
        )
if __name__ == "__main__":
    main()