"""Read-only Step 3 diagnostic for the 52 KRX-missing stocks."""
from datetime import date, timedelta
import os
from kor_market_snapshot import _request_daily_trade

TARGETS = ["001080","001570","001720","002630","006380","018500","020180","021820","021880","030960","032800","033200","050860","060310","067010","082640","082660","086220","092440","093240","096610","097870","099750","102950","121850","169330","189690","190650","216400","217950","226340","250030","266170","266350","267080","278990","289080","311060","317860","334970","341170","351020","395400","417310","448730","465320","471050","472220","900120","900290","950170","950210"]
MARKETS = (("KOSPI","stk_bydd_trd"), ("KOSDAQ","ksq_bydd_trd"), ("KONEX","knx_bydd_trd"))

def norm_code(value):
    return str(value or "").strip().zfill(6)

def main():
    if not os.environ.get("KRX_API_KEY", "").strip():
        raise SystemExit("KRX_API_KEY is missing")
    targets = set(TARGETS)
    found = {}
    dates = {}
    today = date.today()

    for market_name, api_id in MARKETS:
        loaded = False
        d = today
        for _ in range(8):
            if d.weekday() >= 5:
                d -= timedelta(days=1)
                continue
            bas_dd = d.strftime("%Y%m%d")
            try:
                rows = _request_daily_trade(api_id, bas_dd)
            except Exception as exc:
                print(f"ERROR {market_name} {bas_dd}: {exc}")
                rows = []
            dates[market_name] = bas_dd
            if rows:
                loaded = True
                for row in rows:
                    code = norm_code(row.get("ISU_CD") or row.get("isu_cd"))
                    if code in targets:
                        found[code] = {
                            "market": market_name, "api_id": api_id, "date": bas_dd,
                            "name": row.get("ISU_NM") or row.get("isu_nm"),
                            "price": row.get("TDD_CLSPRC") or row.get("tdd_cls_prc"),
                            "listed_shares": row.get("LIST_SHRS") or row.get("list_shrs"),
                            "market_cap": row.get("MKTCAP") or row.get("mktcap"),
                        }
                print(f"LOADED {market_name} {bas_dd}: {len(rows):,} raw rows")
                break
            d -= timedelta(days=1)
        if not loaded:
            print(f"MISSING API DATA {market_name}: no rows in last 8 calendar days")

    print("\n=== STEP 3 RESULT ===")
    missing = []
    for code in TARGETS:
        item = found.get(code)
        if item:
            print(f"FOUND  {code} | {item['name']} | {item['market']} | date={item['date']} | price={item['price']} | listed_shares={item['listed_shares']} | market_cap={item['market_cap']}")
        else:
            missing.append(code)
            print(f"ABSENT {code}")

    print("\n=== SUMMARY ===")
    print(f"Targets: {len(TARGETS)}")
    print(f"Found in raw KRX response: {len(found)}")
    print(f"Absent from raw KRX response: {len(missing)}")
    print(f"API dates used: {dates}")
    if missing:
        print("ABSENT CODES:")
        print(",".join(missing))
    else:
        print("ALL 52 TARGETS FOUND IN RAW KRX RESPONSE")

if __name__ == "__main__":
    main()
