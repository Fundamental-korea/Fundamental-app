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
from urllib.parse import parse_qsl, urlencode, urlparse
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


def _canonical_news_key(item: NaverNewsItem) -> str:
    """Build a stable dedupe key while ignoring common tracking parameters."""
    raw = str(item.original_link or item.link or "").strip()
    if raw:
        try:
            parsed = urlparse(raw)
            tracking = {
                "utm_source", "utm_medium", "utm_campaign", "utm_term",
                "utm_content", "utm_id", "gclid", "fbclid"
            }
            kept = [
                (key, value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                if key.lower() not in tracking
            ]
            clean_query = urlencode(kept)
            return parsed._replace(query=clean_query, fragment="").geturl().rstrip("/").lower()
        except Exception:
            return raw.lower()
    title = re.sub(r"\s+", " ", str(item.title or "")).strip().lower()
    return title


def _is_today_kst(item: NaverNewsItem) -> bool:
    try:
        published = pd.to_datetime(item.pub_date, utc=True)
        return published.tz_convert("Asia/Seoul").date() == datetime.now(
            ZoneInfo("Asia/Seoul")
        ).date()
    except Exception:
        return False


def _marketaux_error_detail(response) -> str:
    try:
        payload = response.json()
        error = payload.get("error") or payload.get("errors") or payload
        text_value = str(error)
    except Exception:
        text_value = response.text
    return re.sub(r"api_token[^,}]*", "api_token=***", text_value, flags=re.IGNORECASE)[:500]


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
    group_similar: bool = True,
) -> list[NaverNewsItem]:
    """Fetch Marketaux news and normalize it to the shared news item model."""
    token = _marketaux_token()
    if not token:
        return []

    limit = min(max(display, 1), 3)
    params = {
        "api_token": token,
        "language": language,
        "limit": limit,
        "group_similar": "true" if group_similar else "false",
        "must_have_entities": "true" if must_have_entities else "false",
        "sort": "published_at",
    }

    if today_only:
        kst = ZoneInfo("Asia/Seoul")
        now_kst = datetime.now(kst)
        start_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
        next_kst = start_kst + timedelta(days=1)
        params["published_after"] = start_kst.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        params["published_before"] = next_kst.astimezone(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
    else:
        params["published_after"] = (
            datetime.now(timezone.utc) - timedelta(days=2)
        ).strftime("%Y-%m-%dT%H:%M:%S")

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
        if not response.ok:
            print(
                f"[Marketaux] HTTP {response.status_code}: "
                f"{_marketaux_error_detail(response)}"
            )
            return []
        payload = response.json()
    except Exception as exc:
        print(f"[Marketaux] request failed: {type(exc).__name__}: {exc}")
        return []

    items: list[NaverNewsItem] = []
    for article in payload.get("data", []) or []:
        url = str(article.get("url") or "").strip()
        title = str(article.get("title") or "").strip()
        if not url or not title:
            continue

        entity_rows = []
        for entity in (article.get("entities") or [])[:8]:
            entity_name = str(entity.get("name") or "").strip()
            symbol = str(entity.get("symbol") or "").strip()
            industry = str(entity.get("industry") or "").strip()
            if entity_name or symbol:
                label = (
                    f"{entity_name} ({symbol})"
                    if entity_name and symbol
                    else (entity_name or symbol)
                )
                if industry:
                    label += f" · {industry}"
                entity_rows.append(label)

        description = str(
            article.get("description") or article.get("snippet") or ""
        ).strip()

        items.append(
            NaverNewsItem(
                title=_clean_html(title),
                description=_clean_html(description),
                link=url,
                original_link=url,
                pub_date=str(article.get("published_at") or "").strip(),
                query=query or "시장 뉴스",
                source=_marketaux_source_label(article),
                image_url=str(article.get("image_url") or "").strip(),
                snippet=_clean_html(str(article.get("snippet") or "")),
                keywords=_clean_html(str(article.get("keywords") or "")),
                entities=" | ".join(entity_rows),
            )
        )
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
        try:
            published_ts = datetime.fromisoformat(
                str(item.pub_date or "").replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            published_ts = 0
        source_rank = preferred.get(_source_domain(item), 999)
        # 최신 기사 우선, 동일 시각대에서 선호 출처를 앞세운다.
        return -published_ts, source_rank

    return sorted(items, key=rank)


def fetch_stock_news(
    stock_name: str,
    stock_code: Optional[str] = None,
    display: int = 3,
) -> list[NaverNewsItem]:
    """US: Marketaux first. KR: NAVER first. Both have provider fallback."""
    name = str(stock_name or "").strip()
    code = str(stock_code or "").strip()
    if not name and not code:
        return []

    target = min(max(display, 1), 3)

    # 한국 개별종목은 한국어 회사명 검색 품질이 좋은 NAVER를 먼저 사용한다.
    if code.isdigit():
        naver_queries = [name, f"{name} {code}".strip()]
        for query in naver_queries:
            if not query:
                continue
            try:
                items = search_naver_news(query, display=target, sort="date")
            except Exception as exc:
                print(f"[NAVER stock news] request failed: {type(exc).__name__}: {exc}")
                items = []
            if items:
                unique = []
                seen = set()
                for item in items:
                    key = _canonical_news_key(item)
                    if key in seen:
                        continue
                    seen.add(key)
                    unique.append(item)
                    if len(unique) >= target:
                        return _rank_global_news(unique)[:target]
        # NAVER가 비어 있으면 Marketaux 한국어 검색을 보조로 시도한다.
        marketaux_items = search_marketaux_news(
            query=name,
            language="ko",
            display=target,
            today_only=False,
            must_have_entities=False,
            group_similar=False,
        )
        if marketaux_items:
            return _rank_global_news(marketaux_items)[:target]
        return []

    # 미국 개별종목은 ticker entity 검색을 우선하고, 회사명 검색으로 보조한다.
    marketaux_items = search_marketaux_news(
        symbols=code,
        language="en",
        display=target,
        today_only=False,
        must_have_entities=True,
        group_similar=False,
    )
    if len(marketaux_items) < target and name:
        extra = search_marketaux_news(
            query=name,
            language="en",
            display=target,
            today_only=False,
            must_have_entities=False,
            group_similar=False,
        )
        marketaux_items.extend(extra)

    unique = []
    seen = set()
    for item in _rank_global_news(marketaux_items):
        key = _canonical_news_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= target:
            return unique[:target]

    # Marketaux가 비어 있으면 NAVER의 글로벌 검색을 마지막 보조 경로로 사용한다.
    if name:
        try:
            fallback = search_naver_news(name, display=target, sort="date")
        except Exception:
            fallback = []
        if fallback:
            return _rank_global_news(fallback)[:target]
    return []


def fetch_macro_news(
    queries: Optional[Iterable[str]] = None,
    display: int = 20,
) -> list[NaverNewsItem]:
    """Main Live News: 16 US + 4 KR, strictly today's KST news.

    Marketaux budget for the scheduled collector is fixed at 6 US + 1 KR
    requests per run (7 requests = 21 max returned articles). We deliberately
    disable Marketaux grouping for the macro feed so one popular story cannot
    collapse each query down to a single result.
    """
    target = min(max(display, 1), 20)

    if queries is not None:
        merged: list[NaverNewsItem] = []
        seen: set[str] = set()
        for query in list(queries):
            items = search_marketaux_news(
                query=query,
                language="en",
                display=3,
                today_only=True,
                must_have_entities=False,
                group_similar=False,
            )
            if not items:
                try:
                    items = [
                        item for item in search_naver_news(
                            query, display=min(target, 3), sort="date"
                        )
                        if _is_today_kst(item)
                    ]
                except Exception:
                    items = []
            for item in items:
                key = _canonical_news_key(item)
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)
                if len(merged) >= target:
                    return _rank_global_news(merged)[:target]
        return _rank_global_news(merged)[:target]

    us_queries = (
        "Federal Reserve interest rates inflation CPI PCE Treasury yields dollar",
        "US economy jobs payrolls GDP consumer spending retail sales wages",
        "S&P 500 Nasdaq Dow earnings corporate profits market outlook",
        "US Treasury bonds yields dollar financial markets credit conditions",
        "US tariffs trade policy manufacturing industrial activity business investment",
        "AI semiconductors technology companies energy oil prices US markets",
    )
    kr_queries = (
        "한국은행 기준금리 원화 환율 코스피 한국 경제 수출 반도체 증시",
    )

    us_candidates: list[NaverNewsItem] = []
    for query in us_queries:
        us_candidates.extend(
            search_marketaux_news(
                query=query,
                language="en",
                display=3,
                today_only=True,
                must_have_entities=False,
                group_similar=False,
            )
        )

    kr_candidates: list[NaverNewsItem] = []
    for query in kr_queries:
        kr_candidates.extend(
            search_marketaux_news(
                query=query,
                language="ko",
                countries="kr",
                display=3,
                today_only=True,
                must_have_entities=False,
                group_similar=False,
            )
        )

    us_ranked = []
    seen = set()
    for item in _rank_global_news([x for x in us_candidates if _is_today_kst(x)]):
        key = _canonical_news_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        us_ranked.append(item)

    kr_ranked = []
    seen = set()
    for item in _rank_global_news([x for x in kr_candidates if _is_today_kst(x)]):
        key = _canonical_news_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        kr_ranked.append(item)

    selected = us_ranked[: min(16, target)]
    kr_target = min(4, max(0, target - len(selected)))
    selected.extend(kr_ranked[:kr_target])

    # Marketaux 7회 예산을 절대로 넘기지 않고 NAVER로 부족분을 채운다.
    if len(selected) < target:
        try:
            fallback_us = [
                item for item in search_naver_news(
                    "미국 경제 연준 금리 물가 고용 증시 실적 채권 달러",
                    display=20,
                    sort="date",
                )
                if _is_today_kst(item)
            ]
        except Exception:
            fallback_us = []

        try:
            fallback_kr = [
                item for item in search_naver_news(
                    "한국은행 기준금리 원화 환율 수출 반도체 코스피 경제",
                    display=10,
                    sort="date",
                )
                if _is_today_kst(item)
            ]
        except Exception:
            fallback_kr = []

        selected_keys = {_canonical_news_key(item) for item in selected}

        # 먼저 US 부족분을 16개까지 채우고,
        # 이어 KR 부족분을 4개까지 채운다.
        current_us = len(selected[: min(16, target)])
        current_kr = max(0, len(selected) - current_us)

        for item in fallback_us:
            if len(selected) >= target or current_us >= min(16, target):
                break
            key = _canonical_news_key(item)
            if key and key not in selected_keys:
                selected_keys.add(key)
                selected.append(item)
                current_us += 1

        for item in fallback_kr:
            if len(selected) >= target or current_kr >= min(4, target):
                break
            key = _canonical_news_key(item)
            if key and key not in selected_keys:
                selected_keys.add(key)
                selected.append(item)
                current_kr += 1

        # 그래도 부족하면 남은 오늘 뉴스로 마지막 빈자리를 채운다.
        if len(selected) < target:
            for item in fallback_us + fallback_kr:
                if len(selected) >= target:
                    break
                key = _canonical_news_key(item)
                if key and key not in selected_keys:
                    selected_keys.add(key)
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
                "source": "NAVER" if str(item.source or "").strip().upper() == "NAVER" else "MARKETAUX",
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
    "persist_live_news_snapshot",
    "persist_naver_news",
    "to_records",
]
