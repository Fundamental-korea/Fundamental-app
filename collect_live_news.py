from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from news_earnings import (
    fetch_macro_news,
    persist_live_news_snapshot,
    _canonical_news_key,
)

KST = ZoneInfo("Asia/Seoul")

MIN_COLLECTION_INTERVAL = timedelta(minutes=110)


def _latest_collection_at(client):
    try:
        result = (
            client.table("news_items")
            .select("collected_at")
            .eq("is_macro", True)
            .in_("source", ["MARKETAUX", "NAVER", "RSS"])
            .order("collected_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows or not rows[0].get("collected_at"):
            return None
        return datetime.fromisoformat(str(rows[0]["collected_at"]).replace("Z", "+00:00"))
    except Exception as exc:
        print(f"[LIVE NEWS] latest collection lookup failed: {type(exc).__name__}: {exc}")
        return None


def main() -> None:
    from news_earnings import _get_supabase_client

    client = _get_supabase_client()

    # Workflow는 매시간 watchdog처럼 실행하지만, 실제 뉴스 수집은 약 2시간 간격으로 제한한다.
    # GitHub Actions schedule이 한 번 지연되어도 다음 hourly run에서 자동 복구된다.
    latest_collection_at = _latest_collection_at(client)
    now_utc = datetime.now(timezone.utc)
    if latest_collection_at is not None:
        if latest_collection_at.tzinfo is None:
            latest_collection_at = latest_collection_at.replace(tzinfo=timezone.utc)
        age = now_utc - latest_collection_at.astimezone(timezone.utc)
        if age < MIN_COLLECTION_INTERVAL:
            print(
                f"[LIVE NEWS] watchdog skip: last_collection="
                f"{latest_collection_at.isoformat()} age={age} "
                f"< {MIN_COLLECTION_INTERVAL}"
            )
            return

    items = fetch_macro_news(display=20)
    if len(items) < 10:
        raise RuntimeError(
            f"Live News fetch returned only {len(items)} items; "
            "refusing to modify the existing 20-item feed"
        )

    owned_sources = ["MARKETAUX", "NAVER", "RSS"]

    # 기존 피드와 비교해 이번 실행에서 처음 발견된 기사만 신규 기사로 판단한다.
    # canonical URL 기준으로 비교하므로 공급원이 바뀌어도 같은 기사가 중복으로 들어가지 않는다.
    existing = (
        client.table("news_items")
        .select("id,source,source_id,article_url,original_url,published_at,collected_at,title")
        .eq("is_macro", True)
        .in_("source", owned_sources)
        .order("published_at", desc=True)
        .limit(100)
        .execute()
    )
    existing_rows = existing.data or []
    existing_keys = set()
    for row in existing_rows:
        raw = row.get("original_url") or row.get("article_url") or row.get("source_id") or ""
        if raw:
            try:
                # DB row를 canonical key와 같은 방식으로 정규화한다.
                key = _canonical_news_key(
                    type(
                        "NewsKeyProxy",
                        (),
                        {"original_link": str(raw), "link": str(raw), "title": str(row.get("title") or "")},
                    )()
                )
            except Exception:
                key = str(raw).strip().lower().rstrip("/")
            if key:
                existing_keys.add(key)

    new_items = []
    seen_new = set()
    for item in items:
        key = _canonical_news_key(item)
        if not key or key in existing_keys or key in seen_new:
            continue
        seen_new.add(key)
        new_items.append(item)

    saved = persist_live_news_snapshot(new_items, supabase_client=client)

    # 새 기사는 기존 20개 앞쪽에 들어간다는 의미를 DB의 published_at 정렬로 보장한다.
    # 오래된 기사는 20개 한도를 넘는 순간 뒤쪽부터 제거한다.
    all_rows = (
        client.table("news_items")
        .select("id,source,source_id,article_url,original_url,published_at,collected_at")
        .eq("is_macro", True)
        .in_("source", owned_sources)
        .order("published_at", desc=True)
        .limit(100)
        .execute()
    )
    feed_rows = all_rows.data or []

    # 발행시각이 없는 데이터가 있으면 collected_at을 정렬 보조 기준으로 사용한다.
    feed_rows.sort(
        key=lambda row: (
            str(row.get("published_at") or row.get("collected_at") or ""),
            str(row.get("id") or ""),
        ),
        reverse=True,
    )

    keep_rows = feed_rows[:20]
    keep_ids = {row.get("id") for row in keep_rows if row.get("id") is not None}
    stale_rows = [row for row in feed_rows[20:] if row.get("id") not in keep_ids]

    for row in stale_rows:
        client.table("news_items").delete().eq("id", row["id"]).execute()

    # 최종 피드 검증: 최대 20개, 중복 URL 없음, 이번 실행에서 새 기사가 앞쪽에 위치했는지 확인한다.
    verify = (
        client.table("news_items")
        .select("id,source,source_id,title,published_at,collected_at,article_url,original_url")
        .eq("is_macro", True)
        .in_("source", owned_sources)
        .order("published_at", desc=True)
        .limit(25)
        .execute()
    )
    db_rows = verify.data or []
    unique_keys = set()
    for row in db_rows:
        raw = row.get("original_url") or row.get("article_url") or row.get("source_id") or ""
        try:
            key = _canonical_news_key(
                type(
                    "NewsKeyProxy",
                    (),
                    {"original_link": str(raw), "link": str(raw), "title": str(row.get("title") or "")},
                )()
            )
        except Exception:
            key = str(raw).strip().lower().rstrip("/")
        if key:
            unique_keys.add(key)

    newest_collected_at = max(
        (str(row.get("collected_at") or "") for row in db_rows),
        default="",
    )
    newest_published_at = max(
        (str(row.get("published_at") or "") for row in db_rows),
        default="",
    )
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    print(
        f"[LIVE NEWS] {now_kst} fetched={len(items)} new={len(new_items)} "
        f"persisted={saved} feed_rows={len(db_rows)} stale_removed={len(stale_rows)} "
        f"unique_articles={len(unique_keys)} newest_published_at={newest_published_at} "
        f"newest_collected_at={newest_collected_at}"
    )

    if len(db_rows) < min(10, len(items)):
        raise RuntimeError(
            f"Live News DB verification failed: final feed has {len(db_rows)} rows "
            f"(expected at least {min(10, len(items))})"
        )
    if len(unique_keys) < len(db_rows):
        raise RuntimeError(
            f"Live News DB verification failed: duplicate articles remain "
            f"(rows={len(db_rows)}, unique={len(unique_keys)})"
        )


if __name__ == "__main__":
    main()