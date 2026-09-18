"""Top-10 market-cap DART + KRX validation.

Selects the 10 largest Korean stocks from the current KRX snapshot, then
excludes Samsung Electronics (005930) and SK hynix (000660), which were already
validated. The remaining rank-3..10 names are refreshed from DART and overlaid
with the KRX market snapshot.

This is a live DB test for only eight stocks. It must never be expanded to the
full universe here.
"""

from kor_market_pipeline import _recalculate_valuation, fetch_market_snapshot_map
import collector


EXCLUDE_CODES = {"005930", "000660"}


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

    ranked = sorted(
        (
            (code, snap)
            for code, snap in snapshots.items()
            if snap.get("market_cap") is not None
        ),
        key=lambda item: item[1]["market_cap"],
        reverse=True,
    )

    top10 = ranked[:10]
    targets = [(rank, code, snap) for rank, (code, snap) in enumerate(top10, start=1)
               if code not in EXCLUDE_CODES]

    if len(targets) != 8:
        raise SystemExit(
            f"Expected 8 test targets after excluding Samsung Electronics and SK hynix; "
            f"got {len(targets)}."
        )

    print("\n=== KRX MARKET-CAP TOP 10 (CURRENT SNAPSHOT) ===")
    for rank, (code, snap) in enumerate(top10, start=1):
        print(
            f"{rank:>2}. {code} | {snap.get('stock_name', code)} | "
            f"market_cap={snap.get('market_cap'):,}"
        )

    kospi_mdd_cache = collector.get_kospi_mdd_cache()
    print("\n=== DART + KRX FINANCIAL TEST (TOP 10 EXCLUDING SAMSUNG + SK HYNIX) ===")

    for rank, code, snapshot in targets:
        row = _load_row(code)
        if not row:
            raise SystemExit(
                f"No Fundamental row for rank {rank} ({code}); refusing to run."
            )

        name = row.get("stock_name") or code
        print(f"\n--- Rank {rank}: {name} ({code}) ---")

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

        after = (
            collector.supabase.table("Fundamental")
            .select(
                "stock_price,eps,bps,issued_shares,period_end_shares,"
                "per,pbr,data_basis_label"
            )
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
            .select(
                "stock_code,stock_name,stock_price,eps,bps,issued_shares,"
                "period_end_shares,listed_shares,market_cap,market_snapshot_date,"
                "market_data_source,per,pbr,data_basis_label"
            )
            .eq("stock_code", code)
            .limit(1)
            .execute()
        )
        saved = (verify.data or [None])[0]
        if not saved:
            raise SystemExit(f"Final verification read failed for {name} ({code}).")

        if saved.get("eps") is None:
            raise SystemExit(f"EPS is still NULL for {name} ({code}); stop before rollout.")
        if saved.get("bps") is None or float(saved.get("bps") or 0) <= 0:
            raise SystemExit(f"BPS is invalid for {name} ({code}): {saved.get('bps')}")
        if saved.get("per") is None:
            raise SystemExit(f"PER is still NULL for {name} ({code}); stop before rollout.")
        if saved.get("pbr") is None:
            raise SystemExit(f"PBR is still NULL for {name} ({code}); stop before rollout.")

        print(
            f"OK rank={rank} {name} ({code}) | "
            f"price={saved.get('stock_price'):,} | "
            f"EPS={saved.get('eps')} | BPS={saved.get('bps')} | "
            f"PER={saved.get('per')} | PBR={saved.get('pbr')} | "
            f"listed_shares={saved.get('listed_shares'):,} | "
            f"period_end_shares={saved.get('period_end_shares'):,}"
        )

    print("\nALL 8 TOP-MARKET-CAP DART + KRX FINANCIAL TESTS PASSED")
