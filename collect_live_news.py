import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from html import unescape
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests

from news_earnings import (
    fetch_macro_news,
    persist_live_news_snapshot,
    _canonical_news_key,
)

KST = ZoneInfo("Asia/Seoul")

MIN_COLLECTION_INTERVAL = timedelta(minutes=110)

_IMAGE_LOWRES_HINTS = (
    "thumbnail", "thumb", "small", "tiny", "lowres", "resizefill",
    "150x", "180x", "200x", "240x", "300x", "320x", "400x",
    "width=150", "width=180", "width=200", "width=240",
    "width=300", "width=320", "width=400",
    "w_150", "w_180", "w_200", "w_240", "w_300", "w_320", "w_400",
)


def _usable_news_image_url(image_url: str) -> bool:
    url = str(image_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False
    lowered = url.lower()
    if "bing.com" in lowered and (
        "th?id=" in lowered or "pid=news" in lowered or "/th" in lowered
    ):
        return False
    if any(hint in lowered for hint in _IMAGE_LOWRES_HINTS):
        return False
    # Catch common CDN transform tokens that encode a small width/height
    # without using the words "thumbnail"/"width".
    for pattern in (
        r"(?:^|[^a-z0-9])w(?:idth)?[_=-]?(\\d{2,5})(?:[^0-9]|$)",
        r"(?:^|[^a-z0-9])h(?:eight)?[_=-]?(\\d{2,5})(?:[^0-9]|$)",
    ):
        match = re.search(pattern, lowered)
        if match:
            size = int(match.group(1))
            if size <= 800:
                return False
    return True
def _usable_news_image_url(image_url: str) -> bool:
    url = str(image_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False
    lowered = url.lower()
    if "bing.com" in lowered and (
        "th?id=" in lowered or "pid=news" in lowered or "/th" in lowered
    ):
        return False
    return not any(hint in lowered for hint in _IMAGE_LOWRES_HINTS)


def _extract_best_source_image(article_url: str) -> str:
    """원문 HTML에서 srcset/JSON-LD/OG 이미지 중 가장 고해상도 후보를 찾는다.
    Google News 래퍼이면 canonical/og:url을 따라 실제 발행사 페이지도 한 번 더 검사한다."""
    url = str(article_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return ""

    headers = {"User-Agent": "Mozilla/5.0 (compatible; FundamentalNewsImageBot/1.0)"}

    def fetch_page(target_url):
        response = requests.get(
            target_url,
            timeout=8,
            headers=headers,
            allow_redirects=True,
        )
        response.raise_for_status()
        return response

    try:
        response = fetch_page(url)
        html = response.text[:1_000_000]

        # Google News RSS links can resolve to a wrapper page. Follow canonical
        # or og:url to the real publisher article when one is exposed.
        if "news.google.com" in (response.url or "").lower():
            publisher_url = ""
            for pattern in (
                r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)',
                r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)',
            ):
                match = re.search(pattern, html, flags=re.IGNORECASE)
                if match:
                    candidate = unescape(match.group(1)).strip()
                    if candidate.startswith(("http://", "https://")) and "news.google.com" not in candidate.lower():
                        publisher_url = candidate
                        break
            if publisher_url:
                try:
                    response = fetch_page(publisher_url)
                    html = response.text[:1_000_000]
                except Exception:
                    pass

        candidates = []

        # srcset/data-srcset: largest declared width wins.
        for match in re.finditer(
            r"""(?:srcset|data-srcset)=["']([^"']+)["']""",
            html,
            flags=re.IGNORECASE,
        ):
            for entry in re.split(r"\s*,\s*", match.group(1)):
                parts = entry.strip().split()
                if not parts:
                    continue
                raw = unescape(parts[0]).strip()
                width = 0
                if len(parts) > 1:
                    width_match = re.match(r"(\d+)w$", parts[1])
                    if width_match:
                        width = int(width_match.group(1))
                candidates.append((width, 5, raw))

        # JSON-LD.
        for match in re.finditer(
            r'"(?:image|contentUrl|thumbnailUrl)"\s*:\s*"([^"]+)"',
            html,
            flags=re.IGNORECASE,
        ):
            candidates.append((0, 4, unescape(match.group(1)).strip()))

        # OpenGraph / Twitter.
        patterns = (
            r'<meta[^>]+property=["\']og:image:secure_url["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image:secure_url["\']',
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            r'<meta[^>]+name=["\']twitter:image:src["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image:src["\']',
            r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
        )
        for pattern in patterns:
            for match in re.finditer(pattern, html, flags=re.IGNORECASE):
                candidates.append((0, 3, unescape(match.group(1)).strip()))

        normalized = []
        seen = set()
        base_url = response.url or url
        for width, priority, raw in candidates:
            if raw.startswith("//"):
                raw = "https:" + raw
            elif raw.startswith("/"):
                raw = urljoin(base_url, raw)
            if not raw.startswith(("http://", "https://")):
                continue
            if not _usable_news_image_url(raw):
                continue
            key = raw.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append((width, priority, raw))

        normalized.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return normalized[0][2] if normalized else ""
    except Exception as exc:
        print(
            f"[LIVE NEWS] source image lookup failed | {url[:120]} | "
            f"{type(exc).__name__}: {exc}"
        )
        return ""


def _enrich_news_items_with_source_images(items):
    items = list(items)
    if not items:
        return items

    targets = []
    for idx, item in enumerate(items):
        current = str(getattr(item, "image_url", "") or "").strip()
        if _usable_news_image_url(current):
            continue
        source_url = str(getattr(item, "original_link", "") or getattr(item, "link", "") or "").strip()
        if source_url:
            targets.append((idx, source_url))

    if not targets:
        return items

    results = {}
    with ThreadPoolExecutor(max_workers=min(6, len(targets))) as executor:
        fetched = executor.map(lambda pair: (pair[0], _extract_best_source_image(pair[1])), targets)
        for idx, image_url in fetched:
            if image_url:
                results[idx] = image_url

    enriched = []
    for idx, item in enumerate(items):
        image_url = results.get(idx) or getattr(item, "image_url", "")
        enriched.append(replace(item, image_url=image_url))
    print(f"[LIVE NEWS] source image enrichment: targets={len(targets)} resolved={len(results)}")
    return enriched


def _backfill_missing_db_images(client) -> int:
    """기존 20개 피드 중 image_url이 비어 있는 행도 원문 대표 이미지로 보강한다."""
    rows = (
        client.table("news_items")
        .select("id,article_url,original_url,metadata")
        .eq("is_macro", True)
        .in_("source", ["MARKETAUX", "NAVER", "RSS"])
        .order("published_at", desc=True)
        .limit(20)
        .execute()
        .data
        or []
    )
    targets = []
    for row in rows:
        metadata = row.get("metadata") or {}
        current = str(metadata.get("image_url") or "").strip() if isinstance(metadata, dict) else ""
        if _usable_news_image_url(current):
            continue
        source_url = str(row.get("original_url") or row.get("article_url") or "").strip()
        if source_url:
            targets.append((row, source_url))

    if not targets:
        return 0

    resolved = 0
    with ThreadPoolExecutor(max_workers=min(6, len(targets))) as executor:
        fetched = executor.map(lambda pair: _extract_best_source_image(pair[1]), targets)
        for (row, _), image_url in zip(targets, fetched):
            if not image_url:
                continue
            metadata = dict(row.get("metadata") or {})
            metadata["image_url"] = image_url
            client.table("news_items").update({"metadata": metadata}).eq("id", row["id"]).execute()
            resolved += 1
    print(f"[LIVE NEWS] DB image backfill: targets={len(targets)} resolved={resolved}")
    return resolved



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
    force_refresh = os.getenv("FORCE_LIVE_NEWS_REFRESH", "").strip() == "1"
    latest_collection_at = _latest_collection_at(client)
    now_utc = datetime.now(timezone.utc)
    if latest_collection_at is not None and not force_refresh:
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

    # 신규 기사도 수집 시점에 원문 대표 이미지를 보강해 웹페이지가 외부 HTML을 다시 조회하지 않도록 한다.
    new_items = _enrich_news_items_with_source_images(new_items)
    saved = persist_live_news_snapshot(new_items, supabase_client=client)

    # 기존 최신 20개 중 image_url이 비어 있는 과거 기사도 이번 실행에서 한 번 보강한다.
    _backfill_missing_db_images(client)

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