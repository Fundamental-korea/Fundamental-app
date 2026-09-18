"""Read/write production integration smoke test for three Korean stocks.

This test intentionally writes the same fields used by daily_update.py, but only for
three already-known companies. It is NOT a read-only diagnostic.
"""

import collector
from kor_market_pipeline import install_market_snapshot_integration

TARGETS = ("005930", "000660", "009150")


def main():
    integration = install_market_snapshot_integration()
    if integration["market_snapshot_count"] < 1000:
        raise SystemExit("KRX market snapshot unexpectedly small.")

    rows = (
        collector.supabase.table("Fundamental")
        .select("stock_code,stock_name,sector,wics_sector,holding_company,period_scores")
        .in_("stock_code", list(TARGETS))
        .execute()
        .data
        or []
    )
    row_map = {str(r["stock_code"]).zfill(6): r for r in rows}
    missing = [code for code in TARGETS if code not in row_map]
    if missing:
        raise SystemExit(f"Missing Fundamental rows: {missing}")

    for code in TARGETS:
        row = row_map[code]
        ok = collector.sync_1y_only(
            code,
            row["stock_name"],
            row.get("sector"),
            row.get("wics_sector"),
            row.get("holding_company") or False,
            row.get("period_scores") or {},
            collector.get_kospi_mdd_cache(),
            use_ofs_for_manufacturing=False,
            force_refresh=False,
        )
        if not ok:
            raise SystemExit(f"Production sync failed: {code}")

    checked = (
        collector.supabase.table("Fundamental")
        .select(
            "stock_code,stock_name,stock_price,listed_shares,market_cap,"
            "market_snapshot_date,market_data_source,period_end_shares,"
            "shares_basis_date,shares_data_source,bps_basis_label,"
            "eps,bps,per,pbr,data_basis_label"
        )
        .in_("stock_code", list(TARGETS))
        .execute()
        .data
        or []
    )
    checked_map = {str(r["stock_code"]).zfill(6): r for r in checked}

    for code in TARGETS:
        r = checked_map.get(code)
        if not r:
            raise SystemExit(f"Post-write row missing: {code}")

        required = (
            "stock_price", "listed_shares", "market_cap",
            "market_snapshot_date", "market_data_source",
            "period_end_shares", "shares_data_source",
            "eps", "bps", "per", "pbr",
        )
        missing_fields = [k for k in required if r.get(k) in (None, 0, "")]
        if missing_fields:
            raise SystemExit(
                f"{code} missing production snapshot fields: {missing_fields}"
            )

        if r["market_data_source"] != "KRX_OPEN_API":
            raise SystemExit(
                f"{code} unexpected market source: {r['market_data_source']}"
            )

        if code == "009150":
            if r["shares_data_source"] not in {
                "KRX listed shares on report period end",
                "KRX listed shares on nearest prior trading day",
            }:
                raise SystemExit(
                    "Samsung Electro-Mechanics did not use the validated KRX period-end fallback: "
                    f"{r['shares_data_source']}"
                )
            if int(r["period_end_shares"]) != 74693696:
                raise SystemExit(
                    f"Samsung Electro-Mechanics period_end_shares unexpected: {r['period_end_shares']}"
                )

        print(
            f"OK {r['stock_name']} ({code}) | price={r['stock_price']} | "
            f"listed_shares={r['listed_shares']} | market_cap={r['market_cap']} | "
            f"period_end_shares={r['period_end_shares']} | EPS={r['eps']} | "
            f"BPS={r['bps']} | PER={r['per']} | PBR={r['pbr']} | "
            f"market_source={r['market_data_source']} | shares_source={r['shares_data_source']}"
        )

    print("ALL 3 PRODUCTION KRX/DART SNAPSHOT TESTS PASSED")


if __name__ == "__main__":
    main()
