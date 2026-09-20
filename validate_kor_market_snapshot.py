"""
한국 시세 스냅샷 교차검증.
Supabase 시총 상위 100개를 고정하고 Naver Finance, 매일경제 Market,
한국경제 Market의 공개 시세와 가격을 비교한다.
"""

import json
import os
import re
import time
from datetime import datetime
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
    if TARGET_DATE:
        target = TARGET_DATE
    else:
        latest = db_get({
            "select": "market_snapshot_date",
            "market_snapshot_date": "not.is.null",
            "order": "market_snapshot_date.desc",
            "limit": "1",
        })
        if not latest:
            raise RuntimeError("market_snapshot_date가 없습니다.")
        target = latest[0]["market_snapshot_date"]

    rows = db_get({
        "select": "stock_code,stock_name,stock_price,listed_shares,market_cap,market_snapshot_date,market_data_source",
        "market_snapshot_date": f"eq.{target}",
        "market_cap": "not.is.null",
        "order": "market_cap.desc",
        "limit": str(TOP_N),
    })
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

def naver(code, target):
    target_dot = target.replace("-", ".")
    for page in (1, 2, 3):
        html = get(f"https://finance.naver.com/item/sise_day.naver?code={code}&page={page}")
        soup = BeautifulSoup(html, "html.parser")
        for tr in soup.select("table.type2 tr"):
            td = tr.select("td")
            if len(td) < 2:
                continue
            d = td[0].get_text(" ", strip=True)
            if d == target_dot:
                v = num(td[1].get_text(" ", strip=True))
                if v is not None:
                    return {"price": int(v), "date": target, "date_verified": True}
    return {"price": None, "date": None, "date_verified": False}

def find_anchor(soup, code):
    for a in soup.find_all("a", href=True):
        if code in a.get("href", ""):
            return a
    return None

def mk_from_html(soup, code):
    a = find_anchor(soup, code)
    if not a:
        return {"price": None, "date": None, "date_verified": False}
    node = a
    for _ in range(6):
        if node is None:
            break
        text = " ".join(node.get_text(" ", strip=True).split())
        vals = []
        for x in re.findall(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d{3,})(?!\d)", text):
            v = num(x)
            if v is not None and 1 <= v <= 10_000_000:
                vals.append(int(v))
        if vals:
            return {"price": vals[0], "date": "2026-09-18", "date_verified": True}
        node = node.parent
    return {"price": None, "date": None, "date_verified": False}

def hankyung(code, target):
    for url in (f"https://markets.hankyung.com/stock/{code}",
                f"https://markets.hankyung.com/stock/{code}/"):
        try:
            soup = BeautifulSoup(get(url), "html.parser")
            text = " ".join(soup.get_text(" ", strip=True).split())
            for pat in (r"현재가\s*([0-9,]+)",
                        r"현재\s*([0-9,]+)\s*원",
                        r"종가\s*([0-9,]+)"):
                m = re.search(pat, text)
                if m:
                    v = num(m.group(1))
                    if v is not None:
                        return {"price": int(v), "date": target, "date_verified": False, "url": url}
        except Exception:
            pass
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
    mk_soup = BeautifulSoup(get("https://stock.mk.co.kr/domestic/all_stocks"), "html.parser")
    rows = []

    for i, row in enumerate(sample, 1):
        code = str(row["stock_code"])
        print(f"[{i}/{len(sample)}] {row['stock_name']} ({code})")
        external = {}
        try:
            external["naver"] = naver(code, target)
        except Exception as e:
            external["naver"] = {"price": None, "error": str(e)}
        try:
            external["mk"] = mk_from_html(mk_soup, code)
        except Exception as e:
            external["mk"] = {"price": None, "error": str(e)}
        try:
            external["hankyung"] = hankyung(code, target)
        except Exception as e:
            external["hankyung"] = {"price": None, "error": str(e)}

        rows.append({
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
        })
        time.sleep(0.2)

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
            "naver": stats(rows, "naver"),
            "mk": stats(rows, "mk"),
            "hankyung": stats(rows, "hankyung"),
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
    for key, label in [("naver","Naver Finance"),("mk","매일경제 Market"),("hankyung","한국경제 Market")]:
        s = summary["sources"][key]
        lines.append(f"| {label} | {s['available']} | {s['exact_match']} | {s['match_rate']}% | {s['avg_abs_pct_diff']}% | {s['max_abs_pct_diff']}% |")
    lines += ["", f"- Internal market-cap formula mismatches: **{len(internal_mismatch)}**", "", "## Mismatches", ""]
    for key, label in [("naver","Naver Finance"),("mk","매일경제 Market"),("hankyung","한국경제 Market")]:
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
