# News / Earnings additive pipeline

이 디렉터리의 코드는 기존 Fundamental 분석 로직과 분리된 **+알파 데이터 계층**입니다.

현재 범위:
1. DART 공시 수집 및 투자자 관점 이벤트 분류
2. 네이버 뉴스 검색 API 연결 및 단기/테마성 키워드 필터링
3. 한국 실적발표 캘린더: `잠정실적` / `정기보고서` 분리
4. 메인 화면용 거시 뉴스와 종목 상세용 개별 뉴스에 재사용할 수 있는 데이터 모델

아직 하지 않는 것:
- 기존 `app.py`, `collector.py`, `scoring.py` 수정
- Supabase 기존 테이블 구조 변경
- 미국 실적 캘린더/미국 원문 뉴스
- GitHub Actions 자동화

## 환경변수

- `DART_API_KEY`
- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`

네이버 Search API의 공식 News API를 사용합니다. 검색 결과의 링크/제목/요약은 원문 결과를 변형하지 않고 저장·표시하는 것을 전제로 합니다.
