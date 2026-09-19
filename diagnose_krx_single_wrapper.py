"""Read-only Step 7 diagnostic for one KRX wrapper path."""
from __future__ import annotations
import collector
import kor_market_pipeline as pipeline

TARGET_CODE = "001080"
TARGET_NAME = "한신공영"

class _ReadOnlyBuilder:
    def __init__(self, real_builder):
        self.real_builder = real_builder
    def select(self, *a, **k):
        self.real_builder = self.real_builder.select(*a, **k); return self
    def eq(self, *a, **k):
        self.real_builder = self.real_builder.eq(*a, **k); return self
    def limit(self, *a, **k):
        self.real_builder = self.real_builder.limit(*a, **k); return self
    def execute(self):
        return self.real_builder.execute()
    def upsert(self, *a, **k):
        raise AssertionError("STEP 7 READ-ONLY GUARD: upsert() was reached")

class _ReadOnlySupabase:
    def __init__(self, real):
        self.real = real
    def table(self, name):
        return _ReadOnlyBuilder(self.real.table(name))

def main():
    print("=== STEP 7: SINGLE STOCK WRAPPER PATH DIAGNOSTIC ===")
    print(f"target: {TARGET_CODE} {TARGET_NAME}")

    real_supabase = collector.supabase
    row_res = (real_supabase.table("Fundamental")
        .select("stock_code,stock_name,sector,wics_sector,holding_company,period_scores,eps,bps,issued_shares")
        .eq("stock_code", TARGET_CODE).limit(1).execute())
    row = (row_res.data or [None])[0]
    if not row:
        print("TARGET_ROW_MISSING"); return
    print("TARGET_ROW_FOUND")
    print(f"period_scores present: {row.get('period_scores') is not None}")
    print(f"existing eps/bps: {row.get('eps')} / {row.get('bps')}")

    probe_calls = []
    def probe_original(*args, **kwargs):
        probe_calls.append(args[0] if args else kwargs.get("stock_code"))
        print("ORIGINAL_SYNC_PROBE_CALLED")
        print("ORIGINAL_SYNC_PROBE_RETURN=True")
        return True

    collector.sync_1y_only = probe_original
    integration = pipeline.install_market_snapshot_integration()
    wrapper = collector.sync_1y_only

    print("\n=== WIRING ===")
    print(f"market map count: {integration['market_snapshot_count']:,}")
    print(f"wrapper name: {wrapper.__name__}")
    print(f"original probe preserved: {integration['original_sync'] is probe_original}")

    snapshot = pipeline.fetch_market_snapshot_map().get(TARGET_CODE)
    print(f"market map target present: {snapshot is not None}")
    if snapshot:
        print(f"KRX snapshot: price={snapshot.get('stock_price')} listed_shares={snapshot.get('listed_shares')} market_cap={snapshot.get('market_cap')} date={snapshot.get('market_snapshot_date')}")

    collector.supabase = _ReadOnlySupabase(real_supabase)
    try:
        result = wrapper(
            TARGET_CODE, TARGET_NAME, row.get("sector"), row.get("wics_sector"),
            row.get("holding_company") or False, row.get("period_scores") or {},
            collector.get_kospi_mdd_cache(), use_ofs_for_manufacturing=False,
            force_refresh=False,
        )
        print("\n=== RESULT ===")
        print(f"wrapper return: {result}")
        print(f"original probe calls: {len(probe_calls)}")
        print("READ_ONLY_COMPLETED")
    except AssertionError as exc:
        print("\n=== RESULT ===")
        print("WRAPPER_REACHED_UPSERT")
        print(str(exc))
    except Exception as exc:
        print("\n=== RESULT ===")
        print(f"WRAPPER_EXCEPTION: {type(exc).__name__}: {exc}")
    finally:
        collector.supabase = real_supabase

if __name__ == "__main__":
    main()
