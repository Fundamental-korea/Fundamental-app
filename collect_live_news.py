from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from news_earnings import fetch_macro_news, persist_live_news_snapshot

KST = ZoneInfo("Asia/Seoul")

def main() -> None:
    items = fetch_macro_news(display=20)
    saved = persist_marketaux_news(items)

    from news_earnings import _get_supabase_client
    client = _get_supabase_client()
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    client.table("news_items").delete().eq("source", "MARKETAUX").eq(
        "is_macro", True
    ).lt("published_at", cutoff.isoformat()).execute()

    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    print(f"[LIVE NEWS] {now_kst} collected={len(items)} persisted={saved}")

if __name__ == "__main__":
    main()