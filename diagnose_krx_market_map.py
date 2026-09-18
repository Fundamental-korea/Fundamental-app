"""Read-only diagnostic: verify the production KRX market map before any DB write.

This isolates the common failure seen in the 32 stocks that were present in raw KRX
data but had NULL market snapshot fields in Supabase.

It does NOT call collector.sync_1y_only and does NOT write to Supabase.
"""
import os

import collector
from kor_market_pipeline import fetch_market_snapshot_map

TARGETS = [
    "001080","001570","001720","002630","018500","020180","021820","021880",
    "030960","032800","033200","050860","060310","067010","082660","092440",
    "093240","097870","099750","169330","189690","190650","226340","289080",
    "334970","395400","417310","448730","900120","900290","950170","950210",
]

def norm(v):
    return str(v or "").strip().zfill(6)

def load_db():
    res = (
        collector.supabase.table("Fundamental")
        .select(
            "stock_code,stock_name,stock_price,listed_shares,market_cap,"
            "market_snapshot_date,market_data_source"
        )
        .in_("stock_code", TARGETS)
        .execute()
    )
    return {norm(r.get("stock_code")): r for r in (res.data or [])}

def main():
    if not os.environ.get("KRX_API_KEY", "").strip():
        raise SystemExit("KRX_API_KEY is missing")

    print("\n=== KRX MARKET MAP PRE-WRITE DIAGNOSTIC ===")
    print(f"Targets: {len(TARGETS)}")
    print("Calling fetch_market_snapshot_map() only; no collector sync and no DB writes.")

    market_map = fetch_market_snapshot_map()
    db = load_db()

    print("\n=== TARGET MAP MEMBERSHIP ===")
    present = 0
    missing = 0
    for code in TARGETS:
        snap = market_map.get(code)
        row = db.get(code, {})
        if snap:
            present += 1
            print(
                f"MAP_PRESENT {code} | {row.get('stock_name')} | "
                f"price={snap.get('stock_price')} shares={snap.get('listed_shares')} "
                f"cap={snap.get('market_cap')} date={snap.get('market_snapshot_date')} "
                f"source={snap.get('market_data_source')} | "
                f"DB source={row.get('market_data_source')} "
                f"DB shares={row.get('listed_shares')} DB cap={row.get('market_cap')}"
            )
        else:
            missing += 1
            print(
                f"MAP_MISSING {code} | {row.get('stock_name')} | "
                f"DB price={row.get('stock_price')} "
                f"DB source={row.get('market_data_source')}"
            )

    print("\n=== SUMMARY ===")
    print(f"Market map total: {len(market_map):,}")
    print(f"Targets in market map: {present}")
    print(f"Targets missing from market map: {missing}")

    if present == len(TARGETS):
        print("DIAGNOSIS: all 32 targets reach the production market map.")
        print("Next investigation point: stock iteration/filtering or wrapper invocation before DB upsert.")
    elif present > 0:
        print("DIAGNOSIS: partial map membership; investigate KRX map construction/date/market coverage.")
    else:
        print("DIAGNOSIS: none of the 32 targets reach the production market map.")

if __name__ == "__main__":
    main()
