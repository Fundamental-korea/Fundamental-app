"""Read-only smoke test for KRX market snapshot integration.

No Supabase writes. This is intentionally limited to three known Korean stocks before
we allow the daily updater to touch the whole universe.
"""

from kor_market_pipeline import fetch_market_snapshot_map


TARGETS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "229640": "LS에코에너지",
}


if __name__ == "__main__":
    snapshots = fetch_market_snapshot_map()

    print("\n=== KRX READ-ONLY SMOKE TEST ===")
    for code, name in TARGETS.items():
        snapshot = snapshots.get(code)
        if snapshot is None:
            print(f"❌ {name} ({code}) -> KRX snapshot 없음")
            continue
        print(
            f"✅ {name} ({code}) | "
            f"price={snapshot['stock_price']:,} | "
            f"listed_shares={snapshot['listed_shares']:,} | "
            f"market_cap={snapshot['market_cap']:,} | "
            f"date={snapshot['market_snapshot_date']}"
        )
