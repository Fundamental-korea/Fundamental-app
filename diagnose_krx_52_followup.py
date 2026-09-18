"""Read-only follow-up diagnostic for the 52 KRX-missing stocks.

Goal:
1. For stocks found in current KRX raw data, compare raw values with DB values.
2. For stocks absent today, make only a lightweight 10-trading-day historical scan.
No DB writes.
"""
from datetime import date, timedelta
import os
from kor_market_snapshot import _request_daily_trade, _to_int
import collector

TARGETS = ["001080","001570","001720","002630","006380","018500","020180","021820","021880","030960","032800","033200","050860","060310","067010","082640","082660","086220","092440","093240","096610","097870","099750","102950","121850","169330","189690","190650","216400","217950","226340","250030","266170","266350","267080","278990","289080","311060","317860","334970","341170","351020","395400","417310","448730","465320","471050","472220","900120","900290","950170","950210"]
MARKETS = (("KOSPI","stk_bydd_trd"), ("KOSDAQ","ksq_bydd_trd"), ("KONEX","knx_bydd_trd"))

def norm(v):
    return str(v or "").strip().zfill(6)

def load_day(api_id, bas_dd):
    try:
        return _request_daily_trade(api_id, bas_dd)
    except Exception as exc:
        print(f"ERROR {api_id} {bas_dd}: {exc}")
        return []

def current_raw_targets():
    found = {}
    today = date.today()
    for market, api_id in MARKETS:
        d = today
        for _ in range(8):
            if d.weekday() >= 5:
                d -= timedelta(days=1)
                continue
            dd = d.strftime("%Y%m%d")
            rows = load_day(api_id, dd)
            if rows:
                for row in rows:
                    code = norm(row.get("ISU_CD") or row.get("isu_cd"))
                    if code in TARGETS:
                        found[code] = {
                            "market": market, "date": dd,
                            "name": row.get("ISU_NM") or row.get("isu_nm"),
                            "price": _to_int(row.get("TDD_CLSPRC") or row.get("tdd_cls_prc")),
                            "listed_shares": _to_int(row.get("LIST_SHRS") or row.get("list_shrs")),
                            "market_cap": _to_int(row.get("MKTCAP") or row.get("mktcap")),
                        }
                break
            d -= timedelta(days=1)
    return found

def db_rows():
    result = {}
    res = (
        collector.supabase.table("Fundamental")
        .select("stock_code,stock_name,stock_price,listed_shares,market_cap,market_snapshot_date,market_data_source,period_end_shares,shares_basis_date,shares_data_source")
        .in_("stock_code", TARGETS)
        .execute()
    )
    for row in res.data or []:
        result[norm(row.get("stock_code"))] = row
    return result

def last_seen_absent_codes(codes, max_calendar_days=14):
    found = {}
    remaining = set(codes)
    d = date.today()
    scanned = 0
    while scanned < max_calendar_days and remaining:
        if d.weekday() < 5:
            dd = d.strftime("%Y%m%d")
            for market, api_id in MARKETS:
                rows = load_day(api_id, dd)
                for row in rows:
                    code = norm(row.get("ISU_CD") or row.get("isu_cd"))
                    if code in remaining:
                        found[code] = {
                            "market": market,
                            "date": dd,
                            "name": row.get("ISU_NM") or row.get("isu_nm"),
                            "price": _to_int(row.get("TDD_CLSPRC") or row.get("tdd_cls_prc")),
                        }
            remaining -= set(found)
            scanned += 1
        d -= timedelta(days=1)
    return found

def main():
    if not os.environ.get("KRX_API_KEY", "").strip():
        raise SystemExit("KRX_API_KEY is missing")

    raw = current_raw_targets()
    db = db_rows()

    present = [c for c in TARGETS if c in raw]
    absent = [c for c in TARGETS if c not in raw]

    print("\n=== PART A: RAW-PRESENT / DB COMPARISON ===")
    for code in present:
        r = raw[code]
        d = db.get(code, {})
        print(
            f"{code} | {r['name']} | {r['market']} | KRX {r['date']} "
            f"| raw_price={r['price']} raw_shares={r['listed_shares']} raw_cap={r['market_cap']} "
            f"| DB price={d.get('stock_price')} db_source={d.get('market_data_source')} "
            f"db_shares={d.get('listed_shares')} db_cap={d.get('market_cap')}"
        )

    print("\n=== PART B: RAW-ABSENT LIGHTWEIGHT HISTORY ===")
    historical = last_seen_absent_codes(absent)
    for code in absent:
        if code in historical:
            h = historical[code]
            d = db.get(code, {})
            print(
                f"SEEN_RECENTLY {code} | {h['name']} | {h['market']} | last_seen={h['date']} "
                f"| price={h['price']} | DB price={d.get('stock_price')}"
            )
        else:
            d = db.get(code, {})
            print(
                f"NOT_SEEN_14D {code} | DB name={d.get('stock_name')} "
                f"| DB price={d.get('stock_price')}"
            )

    print("\n=== SUMMARY ===")
    print(f"Targets: {len(TARGETS)}")
    print(f"Current raw-present: {len(present)}")
    print(f"Current raw-absent: {len(absent)}")
    print(f"Absent but seen within lightweight history: {len(historical)}")
    print(f"Absent not seen in lightweight history: {len(absent) - len(historical)}")

if __name__ == "__main__":
    main()
