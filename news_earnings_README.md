# Live News + Earnings Calendar (+알파)

## 현재 연결된 기능

- 메인 `Live News`: NAVER News Search API를 통해 금리·환율·미국 증시·국내 증시·정책 관련 검색결과를 직접 조회
- 종목 상세 뉴스: 종목명/티커 기준 NAVER 뉴스 검색결과 표시
- `Earnings Calendar`: 최근 30일 DART 공시 중 `잠정실적`과 `정기보고서`를 구분해 표시
- `US Market Overview`: S&P 500 / Nasdaq / Dow Jones 최신 일봉 스냅샷
- `Korea Market Overview`: KOSPI / KOSDAQ 최신 일봉 스냅샷
- `Chart Analysis`: 기존 차트 분석 화면으로 연결

## 인증키

Streamlit Secrets 또는 환경변수로 다음 값을 설정한다.

- `DART_API_KEY`
- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`
- `MARKETAUX_API_TOKEN`
- `GEMINI_API_KEY` (뉴스 AI 브리핑을 사용할 경우)
- 선택: `GEMINI_NEWS_MODEL` (기본값 `gemini-3.5-flash-lite`)

기존 `SUPABASE_URL`, `SUPABASE_KEY`는 그대로 사용한다.

## NAVER 검색결과 처리 원칙

2026-09-07 개정 NAVER API 약관에 맞춰 검색결과를 애플리케이션에서 임의로 재정렬하거나 내용을 변형하지 않는다. 질의어 자체로 시장/종목 범위를 정하고, 화면에는 NAVER 검색결과임을 표시하며 원문 링크를 제공한다.

따라서 기존 `filter_investor_news()`는 별도 유틸리티로 남겨두고 공개 Live News 렌더링에는 적용하지 않는다.

## 자동화

현재 GitHub Actions 자동 수집은 추가하지 않는다. API 연동과 화면 검증이 끝난 뒤 마지막 단계에서 자동화를 붙인다.


## 뉴스 AI 브리핑

뉴스 리더는 Marketaux가 제공하는 제목·설명·snippet·keywords·entities를 바탕으로 한국어 금융뉴스 브리핑을 생성할 수 있다.
Streamlit Secrets에 다음 값을 추가한다.

- `GEMINI_API_KEY`
- 선택: `GEMINI_NEWS_MODEL` (기본값 `gemini-3.5-flash-lite`)

AI 브리핑 본문은 Supabase에 별도 저장하지 않고 애플리케이션 캐시에 24시간 보관한다.
따라서 뉴스 AI 기능을 추가해도 뉴스 DB의 저장 용량 증가를 최소화한다.
