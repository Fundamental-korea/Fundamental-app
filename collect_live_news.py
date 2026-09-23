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

    # 수집 성공 메시지만으로는 잘못된 Supabase URL을 잡아낼 수 없으므로,
    # 동일 client로 오늘 KST 스냅샷을 다시 읽어 실제 DB 저장을 검증한다.
    start_kst = datetime.now(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    next_kst = start_kst + timedelta(days=1)
    verify = (
        client.table("news_items")
        .select("source,source_id,title,published_at")
        .eq("is_macro", True)
        .gte("published_at", start_kst.astimezone(timezone.utc).isoformat())
        .lt("published_at", next_kst.astimezone(timezone.utc).isoformat())
        .order("published_at", desc=True)
        .limit(50)
        .execute()
    )
    db_rows = verify.data or []
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    print(
        f"[LIVE NEWS] {now_kst} collected={len(items)} persisted={saved} "
        f"verified_db_rows={len(db_rows)}"
    )
    if len(db_rows) < min(len(items), 10):
        raise RuntimeError(
            f"Live News DB verification failed: collected={len(items)} "
            f"but today's KST snapshot has only {len(db_rows)} rows"
        )

if __name__ == "__main__":
    main()