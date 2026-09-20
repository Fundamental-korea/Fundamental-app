"""Korean/alternate search aliases for the unified stock search.

The canonical company/ticker data still comes from Supabase. This module adds
human-friendly aliases so common Korean company names can resolve to the
canonical ticker even when the stored company_name is English.
"""
from __future__ import annotations

SEARCH_ALIASES_BY_TICKER = {
    "AAPL": ["애플", "애플컴퓨터", "Apple"],
    "MSFT": ["마이크로소프트", "Microsoft"],
    "GOOGL": ["구글", "알파벳", "알파벳A", "Alphabet", "Google"],
    "GOOG": ["구글", "알파벳", "알파벳C", "Alphabet", "Google"],
    "AMZN": ["아마존", "아마존닷컴", "Amazon"],
    "NVDA": ["엔비디아", "Nvidia"],
    "META": ["메타", "페이스북", "Facebook", "Meta"],
    "TSLA": ["테슬라", "Tesla"],
    "AVGO": ["브로드컴", "Broadcom"],
    "ORCL": ["오라클", "Oracle"],
    "CRM": ["세일즈포스", "Salesforce"],
    "ADBE": ["어도비", "Adobe"],
    "INTC": ["인텔", "Intel"],
    "AMD": ["에이엠디", "AMD"],
    "QCOM": ["퀄컴", "Qualcomm"],
    "CSCO": ["시스코", "Cisco"],
    "IBM": ["아이비엠", "IBM"],
    "TXN": ["텍사스인스트루먼트", "텍사스 인스트루먼츠", "Texas Instruments"],
    "MU": ["마이크론", "마이크론테크놀로지", "Micron"],
    "AMAT": ["어플라이드머티어리얼즈", "어플라이드 머티어리얼즈", "Applied Materials"],
    "LRCX": ["램리서치", "Lam Research"],
    "NOW": ["서비스나우", "ServiceNow"],
    "PANW": ["팔로알토네트웍스", "팔로알토", "Palo Alto Networks"],
    "CRWD": ["크라우드스트라이크", "CrowdStrike"],
    "PLTR": ["팔란티어", "Palantir"],
    "SNOW": ["스노우플레이크", "Snowflake"],
    "NFLX": ["넷플릭스", "Netflix"],
    "UBER": ["우버", "Uber"],
    "ABNB": ["에어비앤비", "Airbnb"],
    "SPOT": ["스포티파이", "Spotify"],
    "PYPL": ["페이팔", "PayPal"],
    "SHOP": ["쇼피파이", "Shopify"],
    "COST": ["코스트코", "코스트코홀세일", "Costco"],
    "WMT": ["월마트", "Walmart"],
    "TGT": ["타깃", "Target"],
    "HD": ["홈디포", "Home Depot"],
    "LOW": ["로우스", "Lowe's", "Lowes"],
    "KO": ["코카콜라", "코카콜라컴퍼니", "Coca-Cola"],
    "PEP": ["펩시", "펩시코", "PepsiCo"],
    "MCD": ["맥도날드", "McDonald's", "McDonalds"],
    "SBUX": ["스타벅스", "Starbucks"],
    "NKE": ["나이키", "Nike"],
    "DIS": ["디즈니", "월트디즈니", "Walt Disney"],
    "CMCSA": ["컴캐스트", "Comcast"],
    "BKNG": ["부킹닷컴", "부킹홀딩스", "Booking Holdings"],
    "JPM": ["제이피모건", "JP모건", "JP모건체이스", "JPMorgan"],
    "BAC": ["뱅크오브아메리카", "Bank of America"],
    "WFC": ["웰스파고", "Wells Fargo"],
    "C": ["씨티", "씨티그룹", "Citigroup"],
    "GS": ["골드만삭스", "Goldman Sachs"],
    "MS": ["모건스탠리", "Morgan Stanley"],
    "BLK": ["블랙록", "BlackRock"],
    "AXP": ["아메리칸익스프레스", "아메리칸 익스프레스", "American Express"],
    "V": ["비자", "Visa"],
    "MA": ["마스터카드", "Mastercard"],
    "COIN": ["코인베이스", "Coinbase"],
    "BRK.B": ["버크셔해서웨이", "버크셔", "버크셔 해서웨이", "Berkshire Hathaway"],
    "LLY": ["일라이릴리", "릴리", "Eli Lilly"],
    "JNJ": ["존슨앤드존슨", "존슨앤존슨", "Johnson & Johnson"],
    "PFE": ["화이자", "Pfizer"],
    "MRK": ["머크", "Merck"],
    "ABBV": ["애브비", "AbbVie"],
    "UNH": ["유나이티드헬스", "유나이티드헬스그룹", "UnitedHealth"],
    "MRNA": ["모더나", "Moderna"],
    "AMGN": ["암젠", "Amgen"],
    "XOM": ["엑슨모빌", "Exxon Mobil"],
    "CVX": ["셰브론", "Chevron"],
    "COP": ["코노코필립스", "ConocoPhillips"],
    "NEE": ["넥스트에라에너지", "넥스트에라", "NextEra Energy"],
    "DUK": ["듀크에너지", "Duke Energy"],
    "SO": ["서던컴퍼니", "서던", "Southern Company"],
    "D": ["도미니언에너지", "Dominion Energy"],
    "EXC": ["엑셀론", "Exelon"],
    "AEP": ["아메리칸일렉트릭파워", "아메리칸 일렉트릭 파워", "American Electric Power"],
    "VST": ["비스트라", "Vistra"],
    "CEG": ["컨스텔레이션에너지", "Constellation Energy"],
    "RTX": ["레이시온", "RTX", "레이시온테크놀로지스", "Raytheon"],
    "LMT": ["록히드마틴", "록히드 마틴", "Lockheed Martin"],
    "NOC": ["노스럽그루먼", "노스럽 그루먼", "Northrop Grumman"],
    "GD": ["제너럴다이내믹스", "제너럴 다이내믹스", "General Dynamics"],
    "BA": ["보잉", "Boeing"],
    "CAT": ["캐터필러", "Caterpillar"],
    "DE": ["존디어", "디어", "John Deere"],
    "HON": ["허니웰", "Honeywell"],
    "GE": ["제너럴일렉트릭", "GE"],
    "GEV": ["GE버노바", "GE Vernova"],
    "PWR": ["퀀타서비스", "Quanta Services"],
    "AVAV": ["에어로바이런먼트", "AeroVironment"],
    "KTOS": ["크라토스", "크라토스디펜스", "Kratos"],
    "RKLB": ["로켓랩", "Rocket Lab"],
    "TSM": ["TSMC", "티에스엠씨", "대만반도체", "대만반도체제조"],
    "ASML": ["에이에스엠엘", "ASML홀딩", "ASML"],
    "ARM": ["암", "ARM홀딩스", "Arm Holdings"],
    "035420": ["네이버", "NAVER", "Naver"],
    "035720": ["카카오", "Kakao"],
    "005930": ["삼성전자", "Samsung Electronics", "Samsung"],
    "000660": ["에스케이하이닉스", "SK하이닉스", "하이닉스", "SK hynix"],
    "005380": ["현대차", "현대자동차", "Hyundai Motor"],
    "000270": ["기아", "기아자동차", "Kia"],
    "373220": ["LG에너지솔루션", "LG엔솔", "LG Energy Solution"],
    "207940": ["삼성바이오로직스", "삼바", "Samsung Biologics"],
    "005490": ["포스코", "POSCO", "POSCO홀딩스", "포스코홀딩스"],
    "105560": ["KB금융", "KB금융지주", "KB Financial"],
    "055550": ["신한지주", "신한금융지주", "신한금융", "Shinhan Financial"],
    "086790": ["하나금융지주", "하나금융", "Hana Financial"],
    "316140": ["우리금융지주", "우리금융", "Woori Financial"],
    "068270": ["셀트리온", "Celltrion"],
}


def aliases_for(ticker: str, company_name: str = "", company_name_ko: str = "") -> list[str]:
    """Return deduplicated aliases while preserving canonical ticker/name."""
    key = str(ticker or "").upper().strip()
    values = [
        str(ticker or "").strip(),
        str(company_name or "").strip(),
        str(company_name_ko or "").strip(),
    ]
    values.extend(SEARCH_ALIASES_BY_TICKER.get(key, []))

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value:
            continue
        k = value.casefold()
        if k in seen:
            continue
        seen.add(k)
        result.append(value)
    return result
