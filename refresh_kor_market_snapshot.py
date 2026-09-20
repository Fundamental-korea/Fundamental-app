"""Refresh Korean current market snapshots only.

This job intentionally does not call or modify DART financial fields.
It updates current price, current listed shares, market cap, snapshot date,
and market source from the KRX market-wide daily endpoint.
"""

from __future__ import annotations

import sys

import collector
from kor_market_pipeline import fetch_market_snapshot_map


def load_all_fundamental_codes():
    rows = []
    start = 0
    page_size = 1000
    while True:
        res = (
            collector.supabase.table("Fundamental")
            .select("stock_code,stock_name")
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = res.data or []
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def main() -> int:
    print("🇰🇷 KRX current market snapshot full refresh 시작")
    market_map = fetch_market_snapshot_map(max_lookback_days=3)
    if not market_map:
        print("❌ KRX market snapshot 데이터를 확보하지 못했습니다.")
        return 1

    rows = load_all_fundamental_codes()
    print(f"📦 Fundamental 종목 수: {len(rows):,}")
    print(f"📈 KRX snapshot map: {len(market_map):,}")

    payloads = []
    missing = []
    for row in rows:
        code = str(row.get("stock_code") or "").strip().zfill(6)
        snapshot = market_map.get(code)
        if not snapshot:
            missing.append(code)
            continue
        payloads.append({
            "stock_code": code,
            "stock_price": snapshot.get("stock_price"),
            "listed_shares": snapshot.get("listed_shares"),
            "market_cap": snapshot.get("market_cap"),
            "market_snapshot_date": snapshot.get("market_snapshot_date"),
            "market_data_source": snapshot.get("market_data_source"),
        })

    # Chunked upserts avoid huge PostgREST payloads while leaving all financial fields untouched.
    chunk_size = 250
    updated = 0
    for i in range(0, len(payloads), chunk_size):
        chunk = payloads[i:i + chunk_size]
        collector.supabase.table("Fundamental").upsert(
            [collector._sanitize_json(x) for x in chunk],
            on_conflict="stock_code",
        ).execute()
        updated += len(chunk)
        print(f"  ✅ 시장 스냅샷 반영: {updated:,}/{len(payloads):,}")

    snapshot_dates = sorted({
        x["market_snapshot_date"]
        for x in payloads
        if x.get("market_snapshot_date")
    })

    print("\n=== KRX current market snapshot refresh 완료 ===")
    print(f"반영 성공: {updated:,}개")
    print(f"KRX map 미존재: {len(missing):,}개")
    print(f"스냅샷 기준일: {snapshot_dates}")
    if missing:
        print("미존재 종목코드(앞 30개):", missing[:30])

    return 0


if __name__ == "__main__":
    sys.exit(main())
