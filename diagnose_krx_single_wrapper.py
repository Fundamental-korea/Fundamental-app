"""Read-only Step 7 diagnostic for one KRX wrapper path.

The goal is to distinguish:
1. original collector.sync_1y_only returning False,
2. wrapper never reaching the KRX section,
3. KRX snapshot lookup missing,
4. wrapper payload construction failing.

No Supabase writes are allowed. The diagnostic replaces the underlying original
sync function with a probe, then runs the production wrapper against one target.
All reads used for the wrapper are real; the final upsert is intercepted.
"""
from __future__ import annotations

import collector
import kor_market_pipeline as pipeline

TARGET_CODE = "001080"
TARGET_NAME = "한신공영"


class _NoWriteQuery:
    def __init__(self, table_name):
        self.table_name = table_name

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def upsert(self, *args, **kwargs):
        raise AssertionError("STEP 7 READ-ONLY GUARD: upsert() was reached")

    def execute(self):
        # The Fundamental row is read from the real client by the outer probe below.
        return type("R", (), {"data": []})()


class _NoWriteSupabase:
    def __init__(self, real):
        self.real = real
        self.upsert_seen = False

    def table(self, name):
        real_table = self.real.table(name)

        class _TableProxy:
            def select(self, *args, **kwargs):
                return real_table.select(*args, **kwargs)

            def eq(self, *args, **kwargs):
                return self

            def limit(self, *args, **kwargs):
                return self

            def execute(self):
                return real_table.execute()

            def upsert(self, *args, **kwargs):
                raise AssertionError("STEP 7 READ-ONLY GUARD: upsert() was reached")

        return _TableProxy()


def main():
    print("=== STEP 7: SINGLE STOCK WRAPPER PATH DIAGNOSTIC ===")
    print(f"target: {TARGET_CODE} {TARGET_NAME}")

    # Read the existing target row with the real Supabase client.
    row_res = (
        collector.supabase.table("Fundamental")
        .select("stock_code,stock_name,sector,wics_sector,holding_company,period_scores,eps,bps,issued_shares")
        .eq("stock_code", TARGET_CODE)
        .limit(1)
        .execute()
    )
    row = (row_res.data or [None])[0]
    if not row:
        print("TARGET_ROW_MISSING")
        return

    print("TARGET_ROW_FOUND")
    print(f"period_scores present: {row.get('period_scores') is not None}")
    print(f"existing stock_price fields: eps={row.get('eps')} bps={row.get('bps')}")

    original = collector.sync_1y_only
    original_calls = []

    def probe_original(*args, **kwargs):
        original_calls.append({
            "stock_code": args[0] if args else kwargs.get("stock_code"),
            "stock_name": args[1] if len(args) > 1 else kwargs.get("stock_name"),
        })
        print("ORIGINAL_SYNC_PROBE_CALLED")
        print("ORIGINAL_SYNC_PROBE_RETURN=True")
        return True

    collector.sync_1y_only = probe_original

    # Install production wrapper. It captures the probe as original_sync.
    integration = pipeline.install_market_snapshot_integration()
    wrapper = collector.sync_1y_only

    print("\n=== WIRING ===")
    print(f"market map count: {integration['market_snapshot_count']:,}")
    print(f"wrapper name: {wrapper.__name__}")
    print(f"original probe preserved: {integration['original_sync'] is probe_original}")
    print(f"target in market map: {TARGET_CODE in getattr(wrapper, '__closure__', ()) if False else 'checked below'}")

    # The wrapper's market map is closure state; confirm the same production map independently.
    market_map = pipeline.fetch_market_snapshot_map()
    snapshot = market_map.get(TARGET_CODE)
    print(f"market map target present: {snapshot is not None}")
    if snapshot:
        print(
            "KRX snapshot:",
            f"price={snapshot.get('stock_price')}",
            f"listed_shares={snapshot.get('listed_shares')}",
            f"market_cap={snapshot.get('market_cap')}",
            f"date={snapshot.get('market_snapshot_date')}",
        )

    # Prevent the wrapper's final write while leaving reads and DART calculations real.
    real_supabase = collector.supabase
    collector.supabase = _NoWriteSupabase(real_supabase)

    # Avoid duplicate market API calls in the wrapper by using the installed wrapper's closure.
    try:
        result = wrapper(
            TARGET_CODE,
            TARGET_NAME,
            row.get("sector"),
            row.get("wics_sector"),
            row.get("holding_company") or False,
            row.get("period_scores") or {},
            collector.get_kospi_mdd_cache(),
            use_ofs_for_manufacturing=False,
            force_refresh=False,
        )
        print("\n=== RESULT ===")
        print(f"wrapper return: {result}")
        print(f"original probe calls: {len(original_calls)}")
        print("READ_ONLY_COMPLETED")
    except AssertionError as exc:
        print("\n=== RESULT ===")
        print("WRAPPER_REACHED_UPSERT")
        print(str(exc))
        print("This proves the wrapper path is reached; no DB write occurred.")
    except Exception as exc:
        print("\n=== RESULT ===")
        print(f"WRAPPER_EXCEPTION: {type(exc).__name__}: {exc}")
        print("This is the next concrete failure point.")
    finally:
        collector.supabase = real_supabase


if __name__ == "__main__":
    main()
