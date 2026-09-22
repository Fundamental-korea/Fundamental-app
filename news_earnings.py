"""Live news + earnings data sources for fundamentals.kr.

This module is intentionally isolated from the existing fundamental-analysis code.
It implements only the additive news/earnings data layer described in the project
planning document:

- DART disclosures: primary source for stock-specific investor-relevant events.
- NAVER News Search API: Korean macro/company news.
- Korean earnings calendar: separate preliminary earnings (잠정실적) from
  periodic reports (정기보고서).
- US news/earnings are deliberately not implemented here yet; they are a later
  roadmap item.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import io
import os
import re
import zipfile
import time
from html import unescape
from typing import Iterable, Optional

import pandas as pd
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urlparse


DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_CORPCODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
NAVER_NEWS_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"

# Planning-document policy: exclude short-term/speculative content.
EXCLUDED_NEWS_TERMS = (
    "급등",
    "테마주",
    "특징주",
    "단타",
    "차트 분석",
    "차트분석",
)

# Investor-relevant stock/event terms from the planning document.
INVESTOR_EVENT_TERMS = (
    "자사주",
    "자기주식",
    "소각",
    "배당",
    "밸류업",
    "저pbr",
    "최대주주",
    "지분변동",
    "지분 변동",
    "영업(잠정)실적",
    "잠정실적",
    "실적",
)

# Report-name matching is deliberately conservative. Unknown DART reports are
# kept as "other" instead of being silently promoted to an earnings event.
PRELIMINARY_EARNINGS_PATTERNS = (
    "영업(잠정)실적",
    "영업잠정실적",
    "잠정실적",
    "잠정 영업실적",
)

PERIODIC_REPORT_PATTERNS = (
    "분기보고서",
    "반기보고서",
    "사업보고서",
)


@dataclass(frozen=True)
class DartDisclosure:
    corp_code: str
    stock_code: Optional[str]
    corp_name: str
    report_name: str
    receipt_date: str
    receipt_no: str
    report_url: str
    disclosure_type: Optional[str] = None
    event_type: str = "other"
    importance_reason: str = ""


@dataclass(frozen=True)
class NaverNewsItem:
    title: str
    description: str
    link: str
    original_link: str
    pub_date: str
    query: str
    source: str = "NAVER"
    image_url: str = ""
    snippet: str = ""
    keywords: str = ""
    entities: str = ""


@dataclass(frozen=True)
class EarningsEvent:
    stock_code: Optional[str]
    corp_name: str
    event_date: str
    event_type: str
    report_name: str
    receipt_no: str
    source_url: str
    yoy_note: str = ""


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        try:
            import streamlit as st
            value = str(st.secrets.get(name, "")).strip()
        except Exception:
            value = ""
    if not value:
        raise RuntimeError(f"환경변수 또는 Streamlit secret {name}가 설정되어 있지 않습니다.")
    return value


def _clean_html(value: str) -> str:
    # Marketaux/NAVER가 HTML entity(&quot;, &amp; 등)를 포함해 반환하는 경우
    # 태그 제거 후 entity도 사람이 읽는 문자로 복원한다.
    cleaned = re.sub(r"<[^>]+>", "", value or "")
    return unescape(cleaned).strip()


def _classify_dart_report(report_name: str) -> tuple[str, str]:
    name = (report_name or "").replace(" ", "").lower()

    if any(pattern.replace(" ", "").lower() in name for pattern in PRELIMINARY_EARNINGS_PATTERNS):
        return "preliminary_earnings", "잠정 영업실적 공시 — 정식 보고서보다 먼저 발표되는 실적 이벤트"

    if any(pattern.replace(" ", "").lower() in name for pattern in PERIODIC_REPORT_PATTERNS):
        return "periodic_report", "분기·반기·사업보고서 — 법정 정기보고서"

    if "자사주" in name or "자기주식" in name:
        if "소각" in name:
            return "treasury_share_cancellation", "자사주 소각 관련 공시"
        return "treasury_share", "자사주 매입·처분 등 관련 공시"

    if "배당" in name:
        return "dividend", "배당 관련 공시"

    if "최대주주" in name and ("변경" in name or "지분" in name):
        return "major_shareholder", "최대주주·지분 변동 관련 공시"

    if "밸류업" in name or "저pbr" in name:
        return "value_up", "기업가치 제고·밸류업 관련 공시"

    if "실적" in name or "매출액" in name or "영업이익" in name:
        return "earnings_related", "실적 관련 공시"

    return "other", ""


def load_dart_corp_codes(cache_path: str = ".cache/dart_corp_codes.xml") -> pd.DataFrame:
    """Download and parse DART's corporation-code file once, then reuse the cache."""
    api_key = _env("DART_API_KEY")
    cache = os.path.abspath(cache_path)
    os.makedirs(os.path.dirname(cache), exist_ok=True)

    if not os.path.exists(cache):
        response = requests.get(
            DART_CORPCODE_URL,
            params={"crtfc_key": api_key},
            timeout=30,
        )
        response.raise_for_status()
        with open(cache, "wb") as fp:
            fp.write(response.content)

    with zipfile.ZipFile(cache) as zf:
        xml_name = next(name for name in zf.namelist() if name.lower().endswith(".xml"))
        xml_bytes = zf.read(xml_name)

    root = ET.fromstring(xml_bytes)
    rows = []
    for item in root.findall("list"):
        rows.append(
            {
                "corp_code": (item.findtext("corp_code") or "").strip(),
                "corp_name": (item.findtext("corp_name") or "").strip(),
                "stock_code": (item.findtext("stock_code") or "").strip() or None,
                "modify_date": (item.findtext("modify_date") or "").strip(),
            }
        )
    return pd.DataFrame(rows)


def get_corp_code(stock_code: str, corp_codes: Optional[pd.DataFrame] = None) -> str:
    frame = corp_codes if corp_codes is not None else load_dart_corp_codes()
    target = str(stock_code).zfill(6)
    rows = frame[frame["stock_code"].astype(str).str.zfill(6) == target]
    if rows.empty:
        raise ValueError(f"DART에서 종목코드를 찾지 못했습니다: {target}")
    return str(rows.iloc[0]["corp_code"])


def fetch_dart_disclosures(
    *,
    start_date: date,
    end_date: date,
    corp_code: Optional[str] = None,
    page_count: int = 100,
    max_pages: int = 20,
) -> list[DartDisclosure]:
    """Fetch DART disclosure list and classify investor-relevant events.

    The DART API supports date/company/type filters. We keep the source response
    fields intact and only add our own event classification.
    """
    api_key = _env("DART_API_KEY")
    params = {
        "crtfc_key": api_key,
        "bgn_de": start_date.strftime("%Y%m%d"),
        "end_de": end_date.strftime("%Y%m%d"),
        "sort": "date",
        "sort_mth": "desc",
        "page_no": 1,
        "page_count": min(max(page_count, 1), 100),
    }
    if corp_code:
        params["corp_code"] = corp_code

    output: list[DartDisclosure] = []

    for page in range(1, max_pages + 1):
        params["page_no"] = page
        last_error = None
        for attempt in range(3):
            try:
                response = requests.get(DART_LIST_URL, params=params, timeout=10)
                response.raise_for_status()
                payload = response.json()
                last_error = None
                break
            except requests.RequestException as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(1 + attempt)
        else:
            raise RuntimeError(f"DART API 연결 실패(3회 시도): {last_error}") from last_error

        if payload.get("status") == "013":
            break
        if payload.get("status") not in (None, "000"):
            raise RuntimeError(
                f"DART API 오류 {payload.get('status')}: {payload.get('message', '')}"
            )

        items = payload.get("list") or []
        if not items:
            break

        for item in items:
            report_name = item.get("report_nm", "")
            event_type, reason = _classify_dart_report(report_name)
            output.append(
                DartDisclosure(
                    corp_code=str(item.get("corp_code", "")),
                    stock_code=(str(item.get("stock_code", "")).zfill(6)
                                if item.get("stock_code") else None),
                    corp_name=item.get("corp_name", ""),
                    report_name=report_name,
                    receipt_date=item.get("rcept_dt", ""),
                    receipt_no=item.get("rcept_no", ""),
                    report_url=(
                        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
                        + str(item.get("rcept_no", ""))
                    ),
                    disclosure_type=item.get("pblntf_ty"),
                    event_type=event_type,
                    importance_reason=reason,
                )
            )

        total_page = int(payload.get("total_page", page))
        if page >= total_page:
            break

    return output


def build_earnings_events(disclosures: Iterable[DartDisclosure]) -> list[EarningsEvent]:
    """Turn DART disclosures into the Korean earnings-calendar event model."""
    events: list[EarningsEvent] = []

    for item in disclosures:
        if item.event_type not in {"preliminary_earnings", "periodic_report"}:
            continue

        events.append(
            EarningsEvent(
                stock_code=item.stock_code,
                corp_name=item.corp_name,
                event_date=item.receipt_date,
                event_type=item.event_type,
                report_name=item.report_name,
                receipt_no=item.receipt_no,
                source_url=item.report_url,
            )
        )

    # Preliminary earnings are the main event, periodic reports are secondary.
    priority = {"preliminary_earnings": 0, "periodic_report": 1}
    events.sort(key=lambda x: (x.event_date, priority.get(x.event_type, 9)), reverse=True)
    return events


def filter_investor_news(items: Iterable[NaverNewsItem]) -> list[NaverNewsItem]:
    """Apply the project's beginner/value-investing content filter."""
    result = []
    for item in items:
        text = _clean_html(f"{item.title} {item.description}")
        if any(term.lower() in text.lower() for term in EXCLUDED_NEWS_TERMS):
            continue
        result.append(item)
    return result


def search_naver_news(
    query: str,
    *,
    display: int = 20,
    sort: str = "date",
) -> list[NaverNewsItem]:
    """Search NAVER News using the official NAVER API HUB Search News API."""
    client_id = _env("NAVER_CLIENT_ID")
    client_secret = _env("NAVER_CLIENT_SECRET")

    response = requests.get(
        NAVER_NEWS_URL,
        params={
            "query": query,
            "display": min(max(display, 1), 100),
            "start": 1,
            "sort": sort,
        },
        headers={
            "X-NCP-APIGW-API-KEY-ID": client_id,
            "X-NCP-APIGW-API-KEY": client_secret,
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()

    items = []
    for item in payload.get("items", []):
        items.append(
            NaverNewsItem(
                title=_clean_html(item.get("title", "")),
                description=_clean_html(item.get("description", "")),
                link=item.get("link", ""),
                original_link=item.get("originallink", "") or item.get("link", ""),
                pub_date=item.get("pubDate", ""),
                query=query,
            )
        )
    # NAVER 검색 API 약관(2026-09-07 개정)에 따라 검색결과 자체를 임의로
    # 재정렬/변형/삭제하지 않고, 질의어로 범위를 좁힌 검색결과를 그대로 반환한다.
    return items


def _marketaux_token() -> str:
    """Marketaux API token. Optional: absent token falls back to the NAVER provider."""
    return _env("MARKETAUX_API_TOKEN") if _env_optional("MARKETAUX_API_TOKEN") else ""


def _env_optional(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        try:
            import streamlit as st
            value = str(st.secrets.get(name, "")).strip()
        except Exception:
            value = ""
    return value


def _marketaux_source_label(article: dict) -> str:
    source = article.get("source") or ""
    if source:
        return str(source)
    domain = urlparse(str(article.get("url") or "")).netloc.lower().split(":")[0]
    return domain.removeprefix("www.") or "News"


def search_marketaux_news(
    query: str = "",
    *,
    symbols: Optional[str] = None,
    language: str = "en",
    countries: str = "",
    domains: str = "",
    display: int = 3,
) -> list[NaverNewsItem]:
    """Fetch global financial news from Marketaux and normalize it to NaverNewsItem."""
    token = _marketaux_token()
    if not token:
        return []

    params = {
        "api_token": token,
        "language": language,
        "limit": min(max(display, 1), 3),
        "published_after": (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
        "must_have_entities": "true",
        "group_similar": "true",
        "sort": "published_at",
    }
    if query.strip():
        params["search"] = query.strip()
    if symbols:
        params["symbols"] = symbols.strip()
    if countries:
        params["countries"] = countries.strip()
    if domains:
        params["domains"] = domains.strip()

    try:
        response = requests.get(
            "https://api.marketaux.com/v1/news/all",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    items: list[NaverNewsItem] = []
    for article in payload.get("data", []) or []:
        url = str(article.get("url") or "").strip()
        title = str(article.get("title") or "").strip()
        if not url or not title:
            continue
        description = str(article.get("description") or article.get("snippet") or "").strip()
        image_url = str(article.get("image_url") or "").strip()
        source = _marketaux_source_label(article)
        published_at = str(article.get("published_at") or "").strip()

        entity_rows = []
        for entity in (article.get("entities") or [])[:8]:
            entity_name = str(entity.get("name") or "").strip()
            symbol = str(entity.get("symbol") or "").strip()
            industry = str(entity.get("industry") or "").strip()
            if entity_name or symbol:
                label = f"{entity_name} ({symbol})" if entity_name and symbol else (entity_name or symbol)
                if industry:
                    label += f" · {industry}"
                entity_rows.append(label)

        items.append(
            NaverNewsItem(
                title=_clean_html(title),
                description=_clean_html(description),
                link=url,
                original_link=url,
                pub_date=published_at,
                query=query or "US market news",
                source=source,
                image_url=image_url,
                snippet=_clean_html(str(article.get("snippet") or "")),
                keywords=_clean_html(str(article.get("keywords") or "")),
                entities=" | ".join(entity_rows),
            )
        )
        # NaverNewsItem에는 이미지 필드가 없으므로 대표 이미지는 app.py에서 URL을 다시 확인한다.
    return items


def _source_domain(item: NaverNewsItem) -> str:
    try:
        return urlparse(item.original_link or item.link).netloc.lower().removeprefix("www.")
    except Exception:
        return ""


PREFERRED_GLOBAL_NEWS_DOMAINS = (
    "reuters.com",
    "bloomberg.com",
    "wsj.com",
    "ft.com",
    "cnbc.com",
    "marketwatch.com",
    "barrons.com",
    "apnews.com",
    "finance.yahoo.com",
)


def _rank_global_news(items: list[NaverNewsItem]) -> list[NaverNewsItem]:
    preferred = {domain: idx for idx, domain in enumerate(PREFERRED_GLOBAL_NEWS_DOMAINS)}

    def rank(item: NaverNewsItem):
        source_rank = preferred.get(_source_domain(item), 999)
        try:
            published_ts = datetime.fromisoformat(
                (item.pub_date or "").replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            published_ts = 0
        return source_rank, -published_ts

    return sorted(items, key=rank)


def fetch_stock_news(stock_name: str, stock_code: Optional[str] = None, display: int = 3) -> list[NaverNewsItem]:
    """Fetch company news with one Marketaux request, then NAVER fallback."""
    name = str(stock_name or "").strip()
    code = str(stock_code or "").strip()
    if not name and not code:
        return []

    target = min(max(display, 1), 3)
    marketaux_items: list[NaverNewsItem] = []

    if code and not code.isdigit():
        marketaux_items = search_marketaux_news(
            symbols=code,
            language="en",
            countries="us",
            display=target,
        )
    elif name:
        marketaux_items = search_marketaux_news(
            query=name,
            language="ko",
            countries="kr",
            display=target,
        )

    if marketaux_items:
        return _rank_global_news(marketaux_items)[:target]

    terms = [term for term in (name, code) if term and not (term == code and code.isdigit())]
    query = " ".join(terms)
    return search_naver_news(query, display=target, sort="date") if query else []


def fetch_macro_news(queries: Optional[Iterable[str]] = None, display: int = 10) -> list[NaverNewsItem]:
    """Main Live News feed optimized for Marketaux Free: US 8 + KR 2.

    Normal path uses exactly 5 Marketaux requests per refresh (4 US topics + 1 KR topic).
    The app layer caches this feed, so the public page does not spend a request per visitor.
    """
    target = min(max(display, 1), 10)

    if queries is not None:
        merged: list[NaverNewsItem] = []
        seen: set[str] = set()
        for query in list(queries):
            items = search_marketaux_news(query=query, language="en", display=3)
            if not items:
                items = search_naver_news(query, display=min(target, 3), sort="date")
            for item in items:
                key = item.original_link or item.link
                if key and key not in seen:
                    seen.add(key)
                    merged.append(item)
                if len(merged) >= target:
                    break
            if len(merged) >= target:
                break
        return _rank_global_news(merged)[:target]

    preferred_domains = (
        "reuters.com,bloomberg.com,wsj.com,ft.com,cnbc.com,"
        "marketwatch.com,barrons.com,apnews.com,finance.yahoo.com"
    )

    us_queries = (
        "Federal Reserve interest rates inflation CPI PCE Treasury yields dollar",
        "US economy jobs GDP consumer spending tariffs trade policy",
        "S&P 500 Nasdaq earnings corporate profits market outlook",
        "US economic growth recession outlook financial markets",
    )
    kr_query = "한국은행 기준금리 원화 코스피 한국 경제 증시"

    # Free plan: 4 US x 3 articles + 1 KR x 3 articles = at most 15 Marketaux articles/request payloads.
    us_candidates: list[NaverNewsItem] = []
    for query in us_queries:
        items = search_marketaux_news(
            query=query,
            language="en",
            countries="us",
            domains=preferred_domains,
            display=3,
        )
        us_candidates.extend(items)

    # If the preferred-source filter is too restrictive at a given moment, make one broad US fallback.
    if len(us_candidates) < 8:
        broad_us = search_marketaux_news(
            query="United States economy Federal Reserve inflation jobs markets earnings tariffs",
            language="en",
            countries="us",
            display=3,
        )
        us_candidates.extend(broad_us)

    kr_candidates = search_marketaux_news(
        query=kr_query,
        language="ko",
        countries="kr",
        display=3,
    )

    def dedupe(items: list[NaverNewsItem]) -> list[NaverNewsItem]:
        out: list[NaverNewsItem] = []
        seen: set[str] = set()
        for item in _rank_global_news(items):
            key = item.original_link or item.link
            if key and key not in seen:
                seen.add(key)
                out.append(item)
        return out

    us_ranked = dedupe(us_candidates)
    kr_ranked = dedupe(kr_candidates)
    selected = us_ranked[:min(8, target)]
    remaining = max(0, target - len(selected))
    selected.extend(kr_ranked[:min(2, remaining)])

    # NAVER is only a last-resort gap filler; normal operation does not consume NAVER for a full Marketaux feed.
    if len(selected) < target:
        fallback_us = search_naver_news(
            "미국 경제 연준 금리 물가 증시 실적",
            display=max(3, target),
            sort="date",
        )
        fallback_kr = search_naver_news(
            "한국은행 기준금리 한국 증시 경제 정책",
            display=max(3, target),
            sort="date",
        )
        seen = {item.original_link or item.link for item in selected}
        for item in fallback_us:
            if len(selected) >= min(8, target):
                break
            key = item.original_link or item.link
            if key and key not in seen:
                seen.add(key)
                selected.append(item)
        for item in fallback_kr:
            if len(selected) >= target:
                break
            key = item.original_link or item.link
            if key and key not in seen:
                seen.add(key)
                selected.append(item)

    return selected[:target]


def _get_supabase_client():
    """Create a server-side Supabase client for ingestion.

    This function is intentionally separate from the Streamlit/UI client path.
    The key used here must never be exposed to the browser.
    """
    from supabase import create_client

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip() or os.getenv("SUPABASE_KEY", "").strip()
    if not url or not key:
        raise RuntimeError(
            "Supabase ingestion 환경변수가 필요합니다: SUPABASE_URL + "
            "SUPABASE_SERVICE_ROLE_KEY(권장) 또는 SUPABASE_KEY"
        )
    return create_client(url, key)


def persist_earnings_events(events: Iterable[EarningsEvent], *, supabase_client=None) -> int:
    """Persist Korean reported-earnings events idempotently.

    The database unique key is (market, receipt_no), so re-running the same
    manual collection does not create duplicate events.
    """
    rows = []
    for event in events:
        if not event.receipt_no or not event.corp_name or not event.event_date:
            continue
        rows.append(
            {
                "market": "KR",
                "stock_code": event.stock_code,
                "stock_name": event.corp_name,
                "event_date": event.event_date,
                "event_type": event.event_type,
                "report_name": event.report_name,
                "receipt_no": event.receipt_no,
                "source_url": event.source_url,
                "announced_at": None,
                "is_primary_event": event.event_type == "preliminary_earnings",
                "metadata": {"source": "DART"},
            }
        )

    if not rows:
        return 0

    client = supabase_client or _get_supabase_client()
    response = (
        client.table("earnings_events")
        .upsert(rows, on_conflict="market,receipt_no")
        .execute()
    )
    return len(response.data or rows)


def persist_naver_news(
    items: Iterable[NaverNewsItem],
    *,
    market: str = "KR",
    stock_code: Optional[str] = None,
    stock_name: Optional[str] = None,
    category: str = "macro",
    supabase_client=None,
) -> int:
    """Persist filtered NAVER news for either macro or a specific stock."""
    rows = []
    for item in items:
        source_id = item.original_link or item.link
        if not item.title or not source_id:
            continue
        try:
            published_at = datetime.strptime(
                item.pub_date, "%a, %d %b %Y %H:%M:%S %z"
            ).isoformat()
        except ValueError:
            published_at = None

        rows.append(
            {
                "source": "NAVER",
                "source_id": source_id,
                "market": market,
                "stock_code": stock_code,
                "stock_name": stock_name,
                "category": category,
                "title": item.title,
                "description": item.description,
                "article_url": item.link,
                "original_url": item.original_link,
                "published_at": published_at,
                "is_macro": category == "macro",
                "is_investor_relevant": True,
                "event_type": None,
                "filter_reason": None,
                "metadata": {"query": item.query},
            }
        )

    if not rows:
        return 0

    client = supabase_client or _get_supabase_client()
    response = (
        client.table("news_items")
        .upsert(rows, on_conflict="source,source_id")
        .execute()
    )
    return len(response.data or rows)

def to_records(items: Iterable[object]) -> list[dict]:
    return [asdict(item) for item in items]


__all__ = [
    "DartDisclosure",
    "NaverNewsItem",
    "EarningsEvent",
    "load_dart_corp_codes",
    "get_corp_code",
    "fetch_dart_disclosures",
    "build_earnings_events",
    "search_naver_news",
    "search_marketaux_news",
    "fetch_stock_news",
    "fetch_macro_news",
    "filter_investor_news",
    "persist_earnings_events",
    "persist_naver_news",
    "to_records",
]
