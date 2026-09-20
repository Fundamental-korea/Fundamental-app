"""Review the Korean 1y-failed universe without writing to Supabase.

Uses official KRX daily market data for current market-cap ordering and the existing
Fundamental table for names / financial context. Intended only to identify a small
set of economically meaningful repair candidates.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
from supabase import create_client

from kor_market_snapshot import _request_daily_trade, _to_int

FAILED_CODES = [
    "448730","351020","395400","001570","086220","900290","018500","097870",
    "067010","311060","050860","030960","060310","317860","189690","950170",
    "002630","266170","950210","020180","021820","289080","092440","216400",
    "278990","082660","267080","093240","169330","001080","341170","266350",
    "001720","217950","226340","900120","190650","033200","102950","417310",
    "099750","334970","021880","032800",
]


def load_db_rows():
    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SECRET_KEY") or os.environ["SUPABASE_KEY"]
    sb = create_client(url, key)
    rows = []
    start = 0
    while True:
        data = (
            sb.table("Fundamental")
            .select("stock_code,stock_name,sector,wics_sector,stock_price,eps,bps,per,pbr,last_1y_updated_at,data_basis_label,listed_shares,market_cap")
            .range(start, start + 999)
            .execute()
        ).data or []
        if not data:
            break
        rows.extend(data)
        if len(data) < 1000:
            break
        start += 1000
    return {str(x.get("stock_code")).zfill(6): x for x in rows}


def load_market_map(max_lookback_days=5):
    result = {}
    today = date.today()
    for api_id in ("stk_bydd_trd", "ksq_bydd_trd", "knx_bydd_trd"):
        loaded = False
        d = today
        for _ in range(max_lookback_days + 1):
            if d.weekday() >= 5:
                d -= timedelta(days=1)
                continue
            try:
                rows = _request_daily_trade(api_id, d.strftime("%Y%m%d"))
            except Exception as exc:
                print(f"[WARN] {api_id} {d}: {exc}")
                rows = []
            if rows:
                snap_date = d.isoformat()
                for row in rows:
                    code = str(row.get("ISU_CD") or row.get("isu_cd") or "").strip().zfill(6)
                    if not code or code == "000000":
                        continue
                    result[code] = {
                        "snapshot_date": snap_date,
                        "price": _to_int(row.get("TDD_CLSPRC") or row.get("tdd_cls_prc")),
                        "listed_shares": _to_int(row.get("LIST_SHRS") or row.get("list_shrs")),
                        "market_cap": _to_int(row.get("MKTCAP") or row.get("mktcap")),
                    }
                loaded = True
                break
            d -= timedelta(days=1)
        if not loaded:
            print(f"[WARN] {api_id}: no data in lookback window")
    return result


def main():
    db = load_db_rows()
    market = load_market_map()

    rows = []
    for code in FAILED_CODES:
        rec = db.get(code, {})
        # Only codes that were still unrepaired at the time of this review.
        if rec.get("last_1y_updated_at") is not None:
            continue
        m = market.get(code, {})
        price = m.get("price")
        cap = m.get("market_cap")
        eps = float(rec["eps"]) if rec.get("eps") is not None else None
        bps = float(rec["bps"]) if rec.get("bps") is not None else None
        rows.append({
            "code": code,
            "name": rec.get("stock_name") or code,
            "sector": rec.get("wics_sector") or rec.get("sector") or "",
            "market_cap": cap,
            "price": price,
            "eps": eps,
            "bps": bps,
            "per": rec.get("per"),
            "pbr": rec.get("pbr"),
            "basis": rec.get("data_basis_label"),
            "snapshot_date": m.get("snapshot_date"),
        })

    rows.sort(key=lambda x: (x["market_cap"] is None, -(x["market_cap"] or 0)))
    print("\n=== Remaining Korean 1y failures: KRX market-cap review ===")
    print(f"Count: {len(rows)}")
    print("code | name | market_cap | price | EPS | BPS | PER | PBR | sector")
    print("-" * 150)
    for x in rows:
        cap = f'{x["market_cap"]:,}' if x["market_cap"] is not None else "-"
        price = f'{x["price"]:,}' if x["price"] is not None else "-"
        eps = f'{x["eps"]:.2f}' if x["eps"] is not None else "-"
        bps = f'{x["bps"]:.2f}' if x["bps"] is not None else "-"
        print(f'{x["code"]} | {x["name"]} | {cap} | {price} | {eps} | {bps} | {x["per"] or "-"} | {x["pbr"] or "-"} | {x["sector"]}')

    print("\n=== Candidates with both positive EPS and BPS ===")
    candidates = [x for x in rows if x["eps"] is not None and x["eps"] > 0 and x["bps"] is not None and x["bps"] > 0]
    candidates.sort(key=lambda x: (x["market_cap"] is None, -(x["market_cap"] or 0)))
    print(", ".join(f'{x["code"]} {x["name"]}' for x in candidates) or "none")

    return 0


if __name__ == "__main__":
    sys.exit(main())
