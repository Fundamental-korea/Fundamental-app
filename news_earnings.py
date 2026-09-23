"""Live news + earnings data sources for fundamentals.kr.

This module is intentionally isolated from the existing fundamental-analysis code.
It implements only the additive news/earnings data layer described in the project
planning document:

- DART disclosures: primary source for stock-specific investor-relevant events.
- NAVER News Search API: Korean macro/company news.
- Korean earnings calendar: separate preliminary earnings (잠정실적) from
  periodic reports (정기보고서).
- US macro and individual-stock news use Marketaux with NAVER/Bing/Google RSS fallbacks;
  Korean individual-stock news uses the same multi-provider aggregation path.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
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
from urllib.parse import urlparse, parse_qsl, urlencode
from zoneinfo import ZoneInfo


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
    today_only: bool = False,
    must_have_entities: bool = True,
) -> list[NaverNewsItem]:
    """Fetch global financial news from Marketaux and normalize it to NaverNewsItem."""
    token = _marketaux_token()
    if not token:
        return []

    params = {
        "api_token": token,
        "language": language,
        "limit": min(max(display, 1), 3),
        "group_similar": "true",
        "sort": "published_at",
    }

    # Marketaux timestamps are UTC. Live News "today" is defined by the
    # Korea calendar day (Asia/Seoul), then converted to UTC for the API.
    params["must_have_entities"] = "true" if must_have_entities else "false"

    if today_only:
        kst = ZoneInfo("Asia/Seoul")
        now_kst = datetime.now(kst)
        start_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
        next_kst = start_kst + timedelta(days=1)
        params["published_after"] = start_kst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        params["published_before"] = next_kst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    else:
        params["published_after"] = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
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


def _news_timestamp(item: NaverNewsItem) -> float:
    try:
        return float(pd.to_datetime(item.pub_date, utc=True).timestamp())
    except Exception:
        return 0.0


def _sort_news_latest_first(items: Iterable[NaverNewsItem]) -> list[NaverNewsItem]:
    return sorted(list(items), key=lambda item: (_news_timestamp(item), str(item.title or "")), reverse=True)

def _within_last_days(items: Iterable[NaverNewsItem], days: int = 7) -> list[NaverNewsItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return [item for item in items if _news_timestamp(item) >= cutoff.timestamp()]




def _rank_global_news(items: list[NaverNewsItem]) -> list[NaverNewsItem]:
    return _sort_news_latest_first(items)

def fetch_stock_news(stock_name: str, stock_code: Optional[str] = None, display: int = 3) -> list[NaverNewsItem]:
    """개별 종목 뉴스: 여러 공급원을 합쳐 최근 7일 내 최신 3건을 채운다."""
    name = str(stock_name or "").strip()
    raw_code = str(stock_code or "").strip()
    target = min(max(display, 1), 3)
    if not name and not raw_code:
        print("[STOCK NEWS] empty stock identity")
        return []

    # 국내 종목코드는 6자리 숫자를 기준으로 판별한다.
    digits = re.sub(r"[^0-9]", "", raw_code)
    is_kr = len(digits) == 6 and digits == digits.zfill(6)
    code = digits if is_kr else raw_code.upper()

    def fresh(items: Iterable[NaverNewsItem]) -> list[NaverNewsItem]:
        return _sort_news_latest_first(_within_last_days(items, 7))

    collected: list[NaverNewsItem] = []
    seen: set[str] = set()

    def add_candidates(provider: str, items: Iterable[NaverNewsItem]) -> None:
        fresh_items = fresh(items)
        added = 0
        for item in fresh_items:
            key = _canonical_news_key(item)
            if not key or key in seen:
                continue
            seen.add(key)
            collected.append(item)
            added += 1
        if added:
            print(f"[STOCK NEWS] {provider} added={added} total={len(collected)}")

    # 1) 구조화 금융뉴스
    if is_kr and name:
        add_candidates("MARKETAUX-KR", search_marketaux_news(query=name, language="ko", countries="kr", display=max(target, 10), must_have_entities=False))
    elif code:
        add_candidates("MARKETAUX-US", search_marketaux_news(symbols=code, language="en", countries="us", display=max(target, 10), must_have_entities=False))

    # 2) 검색어를 여러 형태로 만들어 한 검색원이 부족해도 다음 검색원이 채우게 한다.
    if is_kr:
        queries = [q for q in (
            f'"{name}" "{code}"' if name else "",
            f'"{name}"' if name else "",
            code,
        ) if q]
        languages = ("ko", "en")
    else:
        queries = [q for q in (
            f'"{name}" {code}'.strip() if name or code else "",
            f'"{name}"' if name else "",
            code,
        ) if q]
        languages = ("en", "ko")

    # 3) NAVER Search API
    if _env_optional("NAVER_CLIENT_ID") and _env_optional("NAVER_CLIENT_SECRET"):
        for q in queries:
            try:
                add_candidates("NAVER", search_naver_news(q, display=30, sort="date"))
            except Exception as exc:
                print(f"[STOCK NEWS] NAVER failed | {q!r} | {type(exc).__name__}: {exc}")

    # 4) Bing News RSS
    for lang in languages:
        for q in queries:
            add_candidates(f"BING-{lang}", search_bing_news_rss(q, language=lang, display=30, recent_days=7))

    # 5) Google News RSS
    for lang in languages:
        for q in queries:
            add_candidates(f"GOOGLE-{lang}", search_google_news_rss(q, language=lang, display=30, recent_days=7))

    result = _sort_news_latest_first(collected)[:target]
    print(f"[STOCK NEWS] END name={name!r} code={code!r} market={'KR' if is_kr else 'US'} total={len(result)}/{target}")
    return result

BING_NEWS_RSS_URL = "https://www.bing.com/news/search"


def search_bing_news_rss(query: str, *, language: str = "en", display: int = 20, recent_days: Optional[int] = None) -> list[NaverNewsItem]:
    query = str(query or "").strip()
    if not query:
        return []
    params = {
        "q": query,
        "format": "rss",
        "count": max(10, min(display, 50)),
        "setlang": "ko-KR" if language.startswith("ko") else "en-US",
        "cc": "KR" if language.startswith("ko") else "US",
    }
    if recent_days:
        qft_map = {1: 'interval="7"', 7: 'interval="8"', 30: 'interval="9"'}
        freshness_key = 1 if int(recent_days) <= 1 else (7 if int(recent_days) <= 7 else 30)
        params["qft"] = qft_map[freshness_key]
    try:
        response = requests.get(
            BING_NEWS_RSS_URL,
            params=params,
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0 (compatible; FundamentalNews/1.0)"},
        )
        response.raise_for_status()
        root = ET.fromstring(response.content)
    except Exception as exc:
        print(f"[STOCK NEWS] Bing RSS failed | {query!r} | {type(exc).__name__}: {exc}")
        return []

    out = []
    for node in root.findall(".//item")[:max(1, min(display, 50))]:
        title = _clean_html(node.findtext("title") or "")
        raw_link = (node.findtext("link") or "").strip()
        pub = (node.findtext("pubDate") or "").strip()
        desc = _clean_html(node.findtext("description") or "")
        image_url = ""
        for child in node.iter():
            if str(child.tag).lower().endswith("image"):
                image_url = _clean_html(child.text or "")
                if image_url:
                    break
        link = raw_link
        try:
            parsed = urlparse(raw_link)
            q = dict(parse_qsl(parsed.query))
            if q.get("url"):
                link = q["url"]
        except Exception:
            pass
        source = "Bing News"
        if title and link:
            out.append(
                NaverNewsItem(
                    title=title,
                    description=desc,
                    link=link,
                    original_link=link,
                    pub_date=pub,
                    query=query,
                    source=source,
                    image_url=image_url,
                )
            )
    print(f"[STOCK NEWS] Bing RSS | {query!r} | items={len(out)}")
    return out


GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"

def search_google_news_rss(query: str, *, language: str = "en", display: int = 20, recent_days: Optional[int] = None) -> list[NaverNewsItem]:
    query = str(query or "").strip()
    if not query: return []
    search_query = query
    if recent_days:
        days = max(1, min(int(recent_days), 30))
        search_query = f"{search_query} when:{days}d"
    params = {"q": search_query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"} if language.startswith("ko") else {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    try:
        response = requests.get(GOOGLE_NEWS_RSS_URL, params=params, timeout=10, headers={"User-Agent":"Mozilla/5.0 (compatible; FundamentalNews/1.0)"})
        response.raise_for_status(); root = ET.fromstring(response.content)
    except Exception as exc:
        print(f"[LIVE NEWS DEBUG] RSS failed | {query!r} | {type(exc).__name__}: {exc}"); return []
    out=[]
    for node in root.findall(".//item")[:max(1,min(display,20))]:
        title=_clean_html(node.findtext("title") or ""); link=(node.findtext("link") or "").strip(); pub=(node.findtext("pubDate") or "").strip(); desc=_clean_html(node.findtext("description") or "")
        src=node.find("source"); source=_clean_html(src.text or "") if src is not None and src.text else "Google News"
        if title and link: out.append(NaverNewsItem(title=title,description=desc,link=link,original_link=link,pub_date=pub,query=query,source=source))
    print(f"[LIVE NEWS DEBUG] RSS response | {query!r} | items={len(out)}"); return out


def _is_today_kst(item: NaverNewsItem) -> bool:
    try:
        published = pd.to_datetime(item.pub_date, utc=True)
        return published.tz_convert("Asia/Seoul").date() == datetime.now(ZoneInfo("Asia/Seoul")).date()
    except Exception:
        return False


def _canonical_news_key(item: NaverNewsItem) -> str:
    raw = str(item.original_link or item.link or "").strip()
    if raw:
        try:
            parsed=urlparse(raw)
            tracking={"utm_source","utm_medium","utm_campaign","utm_term","utm_content","utm_id","gclid","fbclid"}
            kept=[(k,v) for k,v in parse_qsl(parsed.query,keep_blank_values=True) if k.lower() not in tracking]
            return parsed._replace(query=urlencode(kept),fragment="").geturl().rstrip("/").lower()
        except Exception:
            return raw.lower()
    return re.sub(r"\s+"," ",str(item.title or "")).strip().lower()

def fetch_macro_news(queries: Optional[Iterable[str]] = None, display: int = 20) -> list[NaverNewsItem]:
    """Main Live News: fill requested quota, newest-first, RSS fallback."""
    target=min(max(display,1),20)
    naver_ok=bool(_env_optional("NAVER_CLIENT_ID")) and bool(_env_optional("NAVER_CLIENT_SECRET"))
    if queries is not None:
        merged=[]; seen=set()
        for query in list(queries):
            candidates=search_marketaux_news(query=query,language="en",display=min(target,10),today_only=True)
            if naver_ok:
                try: candidates += search_naver_news(query,display=min(target,20),sort="date")
                except Exception: pass
            candidates += search_bing_news_rss(query,language="en",display=min(target,30))
            candidates += search_google_news_rss(query,language="en",display=min(target,30))
            for item in _sort_news_latest_first(candidates):
                key=_canonical_news_key(item)
                if key and key not in seen: seen.add(key); merged.append(item)
                if len(merged)>=target: return _sort_news_latest_first(merged)[:target]
        return _sort_news_latest_first(merged)[:target]

    us_target=min(16,target); kr_target=min(4,max(0,target-us_target))
    selected_us=[]; selected_kr=[]; selected_keys=set()
    def add_unique(bucket,candidates,needed):
        for item in _sort_news_latest_first(candidates):
            if len(bucket)>=needed: break
            key=_canonical_news_key(item)
            if key and key not in selected_keys:
                selected_keys.add(key); bucket.append(item)

    us_queries=("Federal Reserve interest rates inflation CPI PCE Treasury yields dollar","US economy jobs payrolls GDP consumer spending retail sales wages","S&P 500 Nasdaq Dow earnings corporate profits market outlook","US Treasury bonds yields dollar financial markets credit conditions","US tariffs trade policy manufacturing industrial activity business investment","AI semiconductors technology companies energy oil prices US markets")
    for q in us_queries:
        add_unique(selected_us,search_marketaux_news(query=q,language="en",display=3,today_only=True,must_have_entities=False),us_target)
    kr_queries=("한국은행 기준금리 원화 환율 코스피 한국 경제 수출 반도체 증시","한국 경제 증시 기업 금리 환율 수출","코스피 반도체 수출 한국은행")
    for q in kr_queries:
        add_unique(selected_kr,search_marketaux_news(query=q,language="ko",countries="kr",display=3,today_only=True,must_have_entities=False),kr_target)

    if naver_ok and len(selected_us)<us_target:
        try: add_unique(selected_us,search_naver_news("미국 경제 연준 금리 물가 고용 증시 실적 채권 달러",display=30,sort="date"),us_target)
        except Exception: pass
    if naver_ok and len(selected_kr)<kr_target:
        try: add_unique(selected_kr,search_naver_news("한국은행 기준금리 원화 환율 수출 반도체 코스피 경제",display=30,sort="date"),kr_target)
        except Exception: pass

    if len(selected_us)<us_target:
        for q in ("Federal Reserve inflation interest rates US economy markets earnings","US stocks Treasury yields dollar tariffs technology energy","US economy markets stocks companies finance"):
            add_unique(selected_us,search_bing_news_rss(q,language="en",display=30,recent_days=1),us_target)
            if len(selected_us)>=us_target: break
    if len(selected_kr)<kr_target:
        for q in kr_queries:
            add_unique(selected_kr,search_bing_news_rss(q,language="ko",display=30,recent_days=1),kr_target)
            if len(selected_kr)>=kr_target: break
    if len(selected_us)<us_target:
        for q in ("Federal Reserve inflation interest rates US economy markets earnings","US stocks Treasury yields dollar tariffs technology energy","US economy markets stocks companies finance"):
            add_unique(selected_us,search_google_news_rss(q,language="en",display=30,recent_days=1),us_target)
            if len(selected_us)>=us_target: break
    if len(selected_kr)<kr_target:
        for q in kr_queries:
            add_unique(selected_kr,search_google_news_rss(q,language="ko",display=30,recent_days=1),kr_target)
            if len(selected_kr)>=kr_target: break

    # Marketaux가 먼저 채우더라도 RSS 최신 결과를 반드시 한 번 더 섞는다.
    # 공급원별 인덱싱 지연 때문에 Marketaux 1개 소스만 쓰면 오래된 기사가
    # 최신 20개를 막아버릴 수 있으므로, Bing/Google의 최근 1일 결과를
    # 항상 최신순 경쟁 풀에 포함시킨다.
    fresh_us = []
    fresh_kr = []
    fresh_us_queries = (
        "US stocks markets finance economy today",
        "Federal Reserve Treasury yields stocks oil markets",
    )
    fresh_kr_queries = (
        "한국 증시 경제 금리 환율 오늘",
        "코스피 코스닥 수출 반도체 금융시장",
    )

    for q in fresh_us_queries:
        fresh_us.extend(search_bing_news_rss(q, language="en", display=20, recent_days=1))
        fresh_us.extend(search_google_news_rss(q, language="en", display=20, recent_days=1))

    for q in fresh_kr_queries:
        fresh_kr.extend(search_bing_news_rss(q, language="ko", display=20, recent_days=1))
        fresh_kr.extend(search_google_news_rss(q, language="ko", display=20, recent_days=1))

    def _dedupe_latest(candidates):
        out = []
        seen = set()
        for item in _sort_news_latest_first(candidates):
            key = _canonical_news_key(item)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            out.append(item)
        return out

    selected_us = _dedupe_latest(selected_us + fresh_us)[:us_target]
    selected_kr = _dedupe_latest(selected_kr + fresh_kr)[:kr_target]

    final=_sort_news_latest_first(selected_us+selected_kr)[:target]
    print(f"[LIVE NEWS DEBUG] END total={len(final)} US={len(selected_us)}/{us_target} KR={len(selected_kr)}/{kr_target}")
    return final


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


def persist_live_news_snapshot(
    items: Iterable[NaverNewsItem],
    *,
    supabase_client=None,
) -> int:
    """Persist the automated Marketaux macro feed into news_items."""
    rows = []
    for item in items:
        source_id = item.original_link or item.link
        if not item.title or not source_id:
            continue
        try:
            published_at = pd.to_datetime(item.pub_date, utc=True).isoformat()
        except Exception:
            published_at = None

        rows.append(
            {
                "source": (
                    "NAVER"
                    if str(item.source or "").strip().upper() == "NAVER"
                    else (
                        "RSS"
                        if str(item.source or "").strip().upper() in {"GOOGLE NEWS", "RSS", "BING NEWS"}
                        else "MARKETAUX"
                    )
                ),
                "source_id": source_id,
                "market": "GLOBAL",
                "stock_code": None,
                "stock_name": None,
                "category": "macro",
                "title": item.title,
                "description": item.description,
                "article_url": item.link,
                "original_url": item.original_link,
                "published_at": published_at,
                "collected_at": datetime.now(timezone.utc).isoformat(),
                "is_macro": True,
                "is_investor_relevant": True,
                "event_type": None,
                "filter_reason": None,
                "metadata": {
                    "query": item.query,
                    "source_label": item.source,
                    "image_url": item.image_url,
                    "snippet": item.snippet,
                    "keywords": item.keywords,
                    "entities": item.entities,
                },
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
    return len(response.data or [])


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
    "persist_live_news_snapshot",
    "persist_naver_news",
    "to_records",
]
