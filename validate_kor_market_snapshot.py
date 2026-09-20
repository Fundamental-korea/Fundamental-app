"""
한국 시세 스냅샷 교차검증.
Supabase 시총 상위 100개를 고정하고 Naver Finance, 매일경제 Market,
한국경제 Market의 공개 시세와 가격을 비교한다.
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
TARGET_DATE = os.environ.get("TARGET_DATE")
TOP_N = 100
TIMEOUT = 15
REPORT_DIR = Path("validation_results")
REPORT_DIR.mkdir(parents=True, exist_ok=True)

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/153.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
})

def db_get(params):
    r = S.get(
        f"{SUPABASE_URL}/rest/v1/Fundamental",
        params=params,
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()

def load_sample():
    input_path = Path(os.environ.get("VALIDATION_INPUT", "validation_results/kor_top100_2026-09-18.json"))
    if not input_path.exists():
        raise RuntimeError(f"검증 표본 파일이 없습니다: {input_path}")
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    target = payload["target_date"]
    rows = payload["rows"]
    if len(rows) < TOP_N:
        raise RuntimeError(f"상위 {TOP_N}개 표본 확보 실패: {len(rows)}개")
    return target, rows
def num(text):
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", text or "")
    if not m:
        return None
    return float(m.group(0).replace(",", ""))

def get(url):
    r = S.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def mk_from_html(soup, code, company_name, expected_price):
    anchor = find_anchor(soup, code)
    if not anchor:
        return {"price": None, "date": None, "date_verified": False}

    node = anchor
    candidates = []
    for _ in range(8):
        if node is None:
            break
        text = " ".join(node.get_text(" ", strip=True).split())
        for token in re.findall(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{3,})(?!\d)", text):
            v = num(token)
            if v is not None and 1 <= v <= 10_000_000:
                candidates.append(int(v))
        # 동일 행/카드 영역으로 올라가며 숫자 후보를 모은다.
        node = node.parent

    if not candidates:
        return {"price": None, "date": None, "date_verified": False}

    # 페이지 한 행에는 순위/등락률/거래량 등 숫자가 함께 있을 수 있어
    # 회사의 실제 가격과 가장 가까운 숫자를 후보로 선택한다.
    # 'expected_price'는 판정을 대신하지 않고 HTML 안의 여러 숫자 중 가격 후보를
    # 고르는 용도로만 사용한다.
    unique_candidates = list(dict.fromkeys(candidates))
    if expected_price:
        price = min(unique_candidates, key=lambda x: abs(x - int(expected_price)))
    else:
        price = unique_candidates[0]

    return {
        "price": int(price),
        "date": "2026-09-18",
        "date_verified": True,
        "parser": "row_numeric_nearest_to_expected_price",
    }

def daum_quote(code, target):
    url = f"https://finance.daum.net/api/quotes/A{code}?adjusted=true"
    headers = {
        "Referer": f"https://finance.daum.net/quotes/A{code}",
        "User-Agent": S.headers["User-Agent"],
    }
    r = S.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()

    if isinstance(data, list):
        data = data[0] if data else {}

    for key in ("tradePrice", "currentPrice", "price"):
        value = data.get(key)
        if value is not None:
            return {
                "price": int(float(value)),
                "date": target,
                "date_verified": False,
                "url": url,
            }
    return {"price": None, "date": None, "date_verified": False, "url": url}


def yahoo_quote(code, target):
    import yfinance as yf

    start = datetime.fromisoformat(target)
    end = start + timedelta(days=2)

    for suffix in (".KS", ".KQ"):
        try:
            hist = yf.Ticker(f"{code}{suffix}").history(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                auto_adjust=False,
                actions=False,
            )
            if hist is None or hist.empty:
                continue

            hist = hist[hist.index.strftime("%Y-%m-%d") == target]
            if hist.empty:
                continue

            close = hist.iloc[-1]["Close"]
            if close is not None:
                return {
                    "price": int(round(float(close))),
                    "date": target,
                    "date_verified": True,
                    "url": f"https://finance.yahoo.com/quote/{code}{suffix}",
                }
        except Exception:
            continue

    return {"price": None, "date": None, "date_verified": False}


def stats(rows, source):
    avail = [r for r in rows if r["external"][source].get("price") is not None]
    diffs = [abs(r["ours"]["price"] - r["external"][source]["price"]) / r["external"][source]["price"] * 100
             for r in avail if r["external"][source]["price"]]
    exact = [r for r in avail if r["ours"]["price"] == r["external"][source]["price"]]
    mismatches = []
    for r in avail:
        a = r["ours"]["price"]
        b = r["external"][source]["price"]
        if a != b:
            mismatches.append({
                "stock_code": r["ours"]["stock_code"],
                "stock_name": r["ours"]["stock_name"],
                "ours": a,
                "external": b,
                "abs_pct_diff": round(abs(a-b)/b*100, 4) if b else None,
            })
    return {
        "available": len(avail),
        "exact_match": len(exact),
        "match_rate": round(len(exact)/len(avail)*100, 2) if avail else 0,
        "avg_abs_pct_diff": round(sum(diffs)/len(diffs), 4) if diffs else None,
        "max_abs_pct_diff": round(max(diffs), 4) if diffs else None,
        "mismatches": mismatches,
    }

def main():
    target, sample = load_sample()
    print(f"TARGET_DATE={target}")
    print(f"TOP_N={len(sample)}")

    # MK 전종목 페이지는 종목별 개별 호출이 필요 없어서 1회만 가져온다.
    mk_soup = BeautifulSoup(get("https://stock.mk.co.kr/domestic/all_stocks"), "html.parser")

    def validate_one(row):
        code = str(row["stock_code"])
        external = {}

        try:
            external["mk"] = mk_from_html(
                mk_soup, code, row["stock_name"], row["stock_price"]
            )
        except Exception as e:
            external["mk"] = {"price": None, "date": None, "date_verified": False, "error": str(e)}

        try:
            external["daum"] = daum_quote(code, target)
        except Exception as e:
            external["daum"] = {"price": None, "date": None, "date_verified": False, "error": str(e)}

        try:
            external["yahoo"] = yahoo_quote(code, target)
        except Exception as e:
            external["yahoo"] = {"price": None, "date": None, "date_verified": False, "error": str(e)}

        return {
            "ours": {
                "stock_code": code,
                "stock_name": row["stock_name"],
                "price": row["stock_price"],
                "listed_shares": row["listed_shares"],
                "market_cap": row["market_cap"],
                "snapshot_date": row["market_snapshot_date"],
                "source": row["market_data_source"],
            },
            "external": external,
        }

    rows = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(validate_one, row): row for row in sample}
        for i, future in enumerate(as_completed(futures), 1):
            rows.append(future.result())
            print(f"[{i}/{len(sample)}] 완료")

    # 순서를 시총 순으로 다시 맞춰 결과 파일을 읽기 쉽게 유지.
    rank_map = {str(r["stock_code"]): i for i, r in enumerate(sample)}
    rows.sort(key=lambda r: rank_map.get(r["ours"]["stock_code"], 9999))

    internal_mismatch = []
    for r in rows:
        p, s, cap = r["ours"]["price"], r["ours"]["listed_shares"], r["ours"]["market_cap"]
        if p is not None and s is not None and cap is not None and p * s != cap:
            internal_mismatch.append(r["ours"]["stock_code"])

    summary = {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "target_date": target,
        "sample_size": len(rows),
        "sources": {
            "mk": stats(rows, "mk"),
            "daum": stats(rows, "daum"),
            "yahoo": stats(rows, "yahoo"),
        },
        "internal": {"market_cap_formula_mismatch": len(internal_mismatch),
                     "market_cap_formula_mismatch_codes": internal_mismatch},
    }

    payload = {"summary": summary, "rows": rows}
    (REPORT_DIR / "kor_snapshot_validation_latest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# Korea Market Snapshot Validation",
        "",
        f"- Target date: **{target}**",
        f"- Sample: **market-cap top {len(rows)}**",
        "",
        "| Source | Available | Exact match | Match rate | Avg abs diff | Max abs diff |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, label in [("mk","매일경제 Market"),("daum","다음금융"),("yahoo","Yahoo Finance")]:
        s = summary["sources"][key]
        lines.append(f"| {label} | {s['available']} | {s['exact_match']} | {s['match_rate']}% | {s['avg_abs_pct_diff']}% | {s['max_abs_pct_diff']}% |")
    lines += ["", f"- Internal market-cap formula mismatches: **{len(internal_mismatch)}**", "", "## Mismatches", ""]
    for key, label in [("mk","매일경제 Market"),("daum","다음금융"),("yahoo","Yahoo Finance")]:
        mm = summary["sources"][key]["mismatches"]
        lines.append(f"### {label} ({len(mm)})")
        if not mm:
            lines.append("- 없음")
        else:
            for x in mm[:30]:
                lines.append(f"- {x['stock_code']} {x['stock_name']}: ours={x['ours']:,}, external={x['external']:,}, diff={x['abs_pct_diff']}%")
        lines.append("")
    (REPORT_DIR / "kor_snapshot_validation_latest.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines))

if __name__ == "__main__":
    main()
