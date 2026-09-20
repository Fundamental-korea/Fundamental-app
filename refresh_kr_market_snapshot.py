"""Refresh Korean market snapshot only.

No DART financial calculations are performed here. The job updates only:
- stock_price
- listed_shares
- market_cap
- market_snapshot_date
- market_data_source

KRX Open API is queried market-wide once per supported market.
"""

from __future__ import annotations

import sys

import collector
from kor_market_pipeline import fetch_market_snapshot_map


def _load_all_codes() -> list[str]:
    rows = []
    start = 0
    page_size = 1000
    while True:
        res = (
            collector.supabase.table("Fundamental")
            .select("stock_code")
            .range(start, start + page_size - 1)
            .execute()
        )
        page = res.data or []
        if not page:
            break
        rows.extend(str(r.get("stock_code") or "").zfill(6) for r in page if r.get("stock_code"))
        if len(page) < page_size:
            break
        start += page_size
    return sorted(set(rows))


def main() -> int:
    market_map = fetch_market_snapshot_map(max_lookback_days=7)
    codes = _load_all_codes()

    payload = []
    missing = []
    for code in codes:
        snapshot = market_map.get(code)
        if snapshot is None:
            missing.append(code)
            continue
        payload.append({
            "stock_code": code,
            "stock_price": snapshot.get("stock_price"),
            "listed_shares": snapshot.get("listed_shares"),
            "market_cap": snapshot.get("market_cap"),
            "market_snapshot_date": snapshot.get("market_snapshot_date"),
            "market_data_source": snapshot.get("market_data_source"),
        })

    updated = 0
    for i in range(0, len(payload), 250):
        batch = payload[i:i + 250]
        collector.supabase.table("Fundamental").upsert(
            batch, on_conflict="stock_code"
        ).execute()
        updated += len(batch)
        print(f"  ✅ market snapshot batch: {updated:,}/{len(payload):,}")

    print("\n=== Korean market snapshot refresh complete ===")
    print(f"DB 종목: {len(codes):,}")
    print(f"KRX snapshot: {len(market_map):,}")
    print(f"업데이트: {updated:,}")
    print(f"매칭 실패/기존값 유지: {len(missing):,}")
    if missing:
        print("매칭 실패 종목코드:", missing)

    return 0


if __name__ == "__main__":
    sys.exit(main())
