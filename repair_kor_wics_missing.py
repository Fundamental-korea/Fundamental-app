"""
Repair missing Korean WICS sector classifications.

Only fills Fundamental.wics_sector where it is currently NULL, using the same
WiseIndex WICS 10-sector source already used by collector.py. Existing
classifications are never overwritten.
"""
import os
from datetime import datetime, timezone
from collector import get_wics_sector_map
from supabase import create_client

URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
sb = create_client(URL, KEY)

rows = []
start = 0
while True:
    batch = (
        sb.table("Fundamental")
        .select("stock_code,stock_name,sector,wics_sector")
        .is_("wics_sector", "null")
        .range(start, start + 199)
        .execute()
        .data or []
    )
    if not batch:
        break
    rows.extend(batch)
    if len(batch) < 200:
        break
    start += 200

print(f"Missing WICS before repair: {len(rows)}")
if not rows:
    raise SystemExit(0)

wics = get_wics_sector_map()
print(f"WICS map size: {len(wics)}")

def fallback_wics_sector(stock_name, sector):
    """
    WiseIndex가 응답하지 않거나 종목이 구성종목에서 빠진 경우의 보조 분류.
    KRX/DART 상세 업종(현재 Fundamental.sector)을 WICS 10대분류 수준으로
    보수적으로 매핑한다. 정확한 WiseIndex 값이 있으면 그것을 항상 우선한다.
    """
    text = f"{stock_name or ''} {sector or ''}"

    # 특수 종목명은 업종 문자열보다 우선한다.
    if stock_name in ("파라택시스코리아", "동양생명"):
        return "금융"
    if stock_name in ("알에프세미",):
        return "IT"

    # 특수 금융/보험/증권
    if any(k in text for k in ("스팩", "은행", "증권", "보험", "카드", "캐피탈", "금융", "리스", "여신", "신용", "부동산")):
        return "금융"

    # 건강관리
    if any(k in text for k in ("의약", "제약", "바이오", "의료", "의학", "진단", "연구개발", "과학기술 서비스", "과학 및 기술 서비스")):
        return "건강관리"

    # 통신/미디어
    if any(k in text for k in ("방송", "통신 서비스", "전기 통신", "광고업", "영화", "영상", "미디어", "엔터테인먼트", "출판", "교육")):
        return "통신서비스"

    # IT
    if any(k in text for k in ("소프트웨어", "컴퓨터", "반도체", "전자부품", "전자제품", "통신 및 방송 장비",
                               "전기 변환", "전기장비", "정밀기기", "광학", "인터넷", "자료처리", "프로그래밍")):
        return "IT"

    # 에너지
    if any(k in text for k in ("석유", "정유", "가스", "석탄", "에너지")):
        return "에너지"

    # 소재
    if any(k in text for k in ("화학", "금속", "철강", "비철", "플라스틱", "고무", "섬유", "의복", "가죽",
                               "종이", "목재", "시멘트", "유리", "귀금속")):
        return "소재"

    # 필수소비재
    if any(k in text for k in ("식품", "음료", "담배", "사료", "생활용품", "가정용품", "어로", "수산", "곡물가공품")):
        return "필수소비재"

    # 경기소비재
    if any(k in text for k in ("자동차", "차량", "부품", "의류", "신발", "화장품", "소매", "도매", "가정용 기기",
                               "유통", "가구", "레저", "호텔", "관광", "여행")):
        return "경기소비재"

    # 유틸리티
    if any(k in text for k in ("전기", "수도", "가스 공급", "유틸리티")):
        return "유틸리티"

    if "기타 제품 제조업" in text:
        return "소재"

    # 남은 건설/기계/조선/운송/도매 등은 산업재로 분류
    if any(k in text for k in ("건설", "건축", "기계", "조선", "선박", "운송", "도매", "금속가공",
                               "발전기", "건축자재", "토목")):
        return "산업재"

    return None


updates = []
unresolved = []
fallback_used = []
for row in rows:
    code = row["stock_code"]
    sector = wics.get(code)
    source = "wiseindex"
    if not sector:
        sector = fallback_wics_sector(row.get("stock_name"), row.get("sector"))
        source = "ksic_fallback"
    if sector:
        updates.append({"stock_code": code, "wics_sector": sector})
        if source == "ksic_fallback":
            fallback_used.append((code, row.get("stock_name"), row.get("sector"), sector))
    else:
        unresolved.append(row)

for i in range(0, len(updates), 100):
    batch = updates[i:i+100]
    sb.table("Fundamental").upsert(batch, on_conflict="stock_code").execute()
    print(f"Updated {min(i+100,len(updates))}/{len(updates)}")

print(f"Filled WICS: {len(updates)}")
print(f"  WiseIndex exact: {len(updates) - len(fallback_used)}")
print(f"  KSIC fallback: {len(fallback_used)}")
for item in fallback_used[:100]:
    print(f"FALLBACK {item[0]} {item[1]} | {item[2]} -> {item[3]}")
print(f"Still unresolved: {len(unresolved)}")
for row in unresolved[:100]:
    print(f"UNRESOLVED {row['stock_code']} {row['stock_name']}")
