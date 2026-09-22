import json, requests, sys
from collector_us_fundamental import build_fact_index, annual_records
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

ua = sys.argv[1]
s = requests.Session()
s.headers.update({"User-Agent": ua})

for ticker, cik in [("AAPL", "0000320193"), ("AAL", "0000006201"), ("AAON", "0000824142")]:
    facts = s.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json").json()
    subs = s.get(f"https://data.sec.gov/submissions/CIK{cik}.json").json()
    idx = build_fact_index(facts)
    years = sorted(set(idx.get("revenue", {})) | set(idx.get("operating_income", {})))
    y = max(years)
    direct = annual_records((facts.get("facts", {}).get("us-gaap", {}).get("InterestExpenseNonOperating") or {}))
    print("\n", ticker, "year", y)
    print("DIRECT_ANNUAL_INTEREST", direct)
    print("INDEX_INTEREST", idx.get("interest_expense", {}).get(y))
    resolver = SECXBRLSearchV2_3_8(user_agent=ua, session=s)
    resolver.prime_company(cik, facts, subs)
    rr = resolver.resolve(cik, "interest_expense", year=y, limit=10)
    print("RESOLVE", json.dumps(rr, default=str)[:12000])
    print("RAW_INTEREST_CONCEPTS")
    for ns, fs in (facts.get("facts") or {}).items():
        for concept, body in fs.items():
            if "interest" not in concept.lower() and "interest" not in str(body.get("label", "")).lower():
                continue
            rows = []
            for unit, rs in (body.get("units") or {}).items():
                for row in rs:
                    if row.get("form") in ("10-K", "10-K/A") and row.get("fy") == y:
                        rows.append({k: row.get(k) for k in ("fy", "fp", "start", "end", "val", "form", "filed")} | {"unit": unit})
            if rows:
                print(ns, concept, body.get("label"), rows[-5:])
