"""Small live Supabase test for KRX market snapshot fields.

Updates only three known Korean stocks. This does NOT run the full DART collector.
The test preserves legacy issued_shares and updates only the new market snapshot fields.
"""

from kor_market_pipeline import _recalculate_valuation, fetch_market_snapshot_map
import collector


TARGETS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "229640": "LS에코에너지",
}


if __name__ == "__main__":
    snapshots = fetch_market_snapshot_map()
    if not snapshots:
        raise SystemExit("KRX snapshot cache is empty; refusing to write to Supabase.")

    print("\n=== KRX LIVE DB TEST (3 STOCKS ONLY) ===")

    for code, name in TARGETS.items():
        snapshot = snapshots.get(code)
        if snapshot is None:
            raise SystemExit(f"Missing KRX snapshot for {name} ({code}); refusing to write.")

        current = (
            collector.supabase.table("Fundamental")
            .select("stock_code,stock_name,eps,bps,issued_shares,listed_shares,market_cap")
            .eq("stock_code", code)
            .limit(1)
            .execute()
        )
        row = (current.data or [None])[0]
        if not row:
            raise SystemExit(f"No Fundamental row for {name} ({code}); refusing to write.")

        per, pbr = _recalculate_valuation(
            snapshot.get("stock_price"), row.get("eps"), row.get("bps")
        )

        payload = {
            "stock_price": snapshot["stock_price"],
            "listed_shares": snapshot["listed_shares"],
            "market_cap": snapshot["market_cap"],
            "market_snapshot_date": snapshot["market_snapshot_date"],
            "market_data_source": snapshot["market_data_source"],
            "per": per,
            "pbr": pbr,
        }

        # Intentionally do not touch issued_shares in this test.
        collector.supabase.table("Fundamental").update(payload).eq("stock_code", code).execute()

        verify = (
            collector.supabase.table("Fundamental")
            .select("stock_code,stock_name,stock_price,issued_shares,listed_shares,market_cap,market_snapshot_date,market_data_source,eps,bps,per,pbr")
            .eq("stock_code", code)
            .limit(1)
            .execute()
        )
        saved = (verify.data or [None])[0]
        if not saved:
            raise SystemExit(f"Verification read failed for {name} ({code}).")

        checks = {
            "stock_price": saved.get("stock_price") == snapshot.get("stock_price"),
            "listed_shares": saved.get("listed_shares") == snapshot.get("listed_shares"),
            "market_cap": saved.get("market_cap") == snapshot.get("market_cap"),
            "market_snapshot_date": saved.get("market_snapshot_date") == snapshot.get("market_snapshot_date"),
            "market_data_source": saved.get("market_data_source") == "KRX_OPEN_API",
        }
        if not all(checks.values()):
            raise SystemExit(f"Verification mismatch for {name}: {checks}")

        print(
            f"OK {name} ({code}) | "
            f"price={saved.get('stock_price'):,} | "
            f"listed_shares={saved.get('listed_shares'):,} | "
            f"market_cap={saved.get('market_cap'):,} | "
            f"issued_shares_legacy={saved.get('issued_shares'):,} | "
            f"PER={saved.get('per')} | PBR={saved.get('pbr')}"
        )

    print("\nALL 3 LIVE DB TESTS PASSED")
