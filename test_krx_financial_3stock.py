"""Three-stock financial + KRX validation.

Runs the existing DART 1y refresh for only three stocks, then applies the KRX
market snapshot layer and verifies that the stored EPS/BPS/PER/PBR are usable.
This is a live DB test; it must never be expanded to the full universe here.
"""

from kor_market_pipeline import _recalculate_valuation, fetch_market_snapshot_map
import collector


TARGETS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "229640": "LS에코에너지",
}


def _load_row(code):
    res = (
        collector.supabase.table("Fundamental")
        .select("stock_code,stock_name,sector,wics_sector,holding_company,period_scores")
        .eq("stock_code", code)
        .limit(1)
        .execute()
    )
    return (res.data or [None])[0]


if __name__ == "__main__":
    snapshots = fetch_market_snapshot_map()
    if not snapshots:
        raise SystemExit("KRX snapshot cache is empty; refusing to run live financial test.")

    kospi_mdd_cache = collector.get_kospi_mdd_cache()
    print("\n=== DART + KRX FINANCIAL TEST (3 STOCKS ONLY) ===")

    for code, name in TARGETS.items():
        row = _load_row(code)
        if not row:
            raise SystemExit(f"No Fundamental row for {name} ({code}); refusing to run.")

        print(f"\n--- {name} ({code}) ---")
        ok = collector.sync_1y_only(
            code,
            name,
            sector=row.get("sector"),
            wics_sector=row.get("wics_sector"),
            holding_company=row.get("holding_company") or False,
            existing_period_scores=row.get("period_scores") or {},
            kospi_mdd_cache=kospi_mdd_cache,
            force_refresh=True,
        )
        if not ok:
            raise SystemExit(f"DART 1y refresh failed for {name} ({code}).")

        snapshot = snapshots.get(code)
        if snapshot is None:
            raise SystemExit(f"Missing KRX snapshot for {name} ({code}).")

        after = (
            collector.supabase.table("Fundamental")
            .select("stock_price,eps,bps,issued_shares,period_end_shares,per,pbr,data_basis_label")
            .eq("stock_code", code)
            .limit(1)
            .execute()
        )
        saved = (after.data or [None])[0]
        if not saved:
            raise SystemExit(f"Verification read failed for {name} ({code}).")

        per, pbr = _recalculate_valuation(
            snapshot.get("stock_price"), saved.get("eps"), saved.get("bps")
        )

        # Apply KRX market snapshot after DART refresh.
        payload = {
            "stock_price": snapshot["stock_price"],
            "listed_shares": snapshot["listed_shares"],
            "market_cap": snapshot["market_cap"],
            "market_snapshot_date": snapshot["market_snapshot_date"],
            "market_data_source": snapshot["market_data_source"],
            "per": per,
            "pbr": pbr,
            "period_end_shares": saved.get("period_end_shares") or saved.get("issued_shares"),
            "shares_data_source": "DART_STOCK_TOTAL",
        }
        collector.supabase.table("Fundamental").update(payload).eq("stock_code", code).execute()

        verify = (
            collector.supabase.table("Fundamental")
            .select("stock_code,stock_name,stock_price,eps,bps,issued_shares,period_end_shares,listed_shares,market_cap,market_snapshot_date,market_data_source,per,pbr,data_basis_label")
            .eq("stock_code", code)
            .limit(1)
            .execute()
        )
        saved = (verify.data or [None])[0]
        if not saved:
            raise SystemExit(f"Final verification read failed for {name} ({code}).")

        if saved.get("eps") is None:
            raise SystemExit(f"EPS is still NULL for {name} ({code}); stop before full rollout.")
        if saved.get("bps") is None or float(saved.get("bps") or 0) <= 0:
            raise SystemExit(f"BPS is invalid for {name} ({code}): {saved.get('bps')}")
        if saved.get("per") is None:
            raise SystemExit(f"PER is still NULL for {name} ({code}); stop before full rollout.")
        if saved.get("pbr") is None:
            raise SystemExit(f"PBR is still NULL for {name} ({code}); stop before full rollout.")

        print(
            f"OK {name} ({code}) | "
            f"price={saved.get('stock_price'):,} | "
            f"EPS={saved.get('eps')} | BPS={saved.get('bps')} | "
            f"PER={saved.get('per')} | PBR={saved.get('pbr')} | "
            f"listed_shares={saved.get('listed_shares'):,} | "
            f"period_end_shares={saved.get('period_end_shares'):,} | "
            f"basis={saved.get('data_basis_label')}"
        )

    print("\nALL 3 DART + KRX FINANCIAL TESTS PASSED")
