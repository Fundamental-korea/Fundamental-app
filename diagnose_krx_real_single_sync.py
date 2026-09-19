"""Read-only Step 8: run the real 001080 sync path and intercept all Supabase writes."""
from __future__ import annotations
import collector
import kor_market_pipeline as pipeline

TARGET_CODE = "001080"
TARGET_NAME = "한신공영"

class _WriteInterceptBuilder:
    def __init__(self, real, table_name, recorder):
        self._real = real
        self._table_name = table_name
        self._recorder = recorder
    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if name == "not_":
            return _WriteInterceptBuilder(attr, self._table_name, self._recorder)
        if callable(attr):
            def call(*args, **kwargs):
                result = attr(*args, **kwargs)
                return self if result is self._real else result
            return call
        return attr
    def upsert(self, payload, *args, **kwargs):
        self._record_write("upsert", payload)
    def insert(self, payload, *args, **kwargs):
        self._record_write("insert", payload)
    def update(self, payload, *args, **kwargs):
        self._record_write("update", payload)
    def delete(self, *args, **kwargs):
        self._record_write("delete", None)
    def _record_write(self, operation, payload):
        self._recorder.append((self._table_name, operation, payload))
        if self._table_name == "Fundamental" and operation == "upsert":
            print("\n=== INTERCEPTED FUNDAMENTAL UPSERT ===")
            if isinstance(payload, dict):
                for key in ("stock_code","stock_price","listed_shares","market_cap","market_snapshot_date","market_data_source","period_end_shares","shares_basis_date","shares_data_source","eps","eps_is_reported","bps","per","pbr","revenue","operating_income","net_income","total_liabilities","total_equity","data_basis_label","issued_shares"):
                    print(f"{key}: {payload.get(key)!r}")
                print("payload keys:", sorted(payload.keys()))
        else:
            print(f"[READ-ONLY] intercepted {operation} on {self._table_name}")

class _WriteInterceptSupabase:
    def __init__(self, real, recorder):
        self._real = real
        self._recorder = recorder
    def table(self, name):
        return _WriteInterceptBuilder(self._real.table(name), name, self._recorder)

def main():
    print("=== STEP 8: REAL SINGLE-STOCK SYNC + WRITE INTERCEPT ===")
    print(f"target: {TARGET_CODE} {TARGET_NAME}")
    real_supabase = collector.supabase
    row_res = (real_supabase.table("Fundamental").select("stock_code,stock_name,sector,wics_sector,holding_company,period_scores").eq("stock_code", TARGET_CODE).limit(1).execute())
    row = (row_res.data or [None])[0]
    if not row:
        print("TARGET_ROW_MISSING")
        return
    print("TARGET_ROW_FOUND")
    writes = []
    collector.supabase = _WriteInterceptSupabase(real_supabase, writes)
    try:
        integration = pipeline.install_market_snapshot_integration()
        print(f"wrapper: {collector.sync_1y_only.__name__}")
        print(f"market map count: {integration['market_snapshot_count']:,}")
        result = collector.sync_1y_only(
            TARGET_CODE, TARGET_NAME,
            sector=row.get("sector"), wics_sector=row.get("wics_sector"),
            holding_company=row.get("holding_company") or False,
            existing_period_scores=row.get("period_scores") or {},
            kospi_mdd_cache=collector.get_kospi_mdd_cache(),
            use_ofs_for_manufacturing=False, force_refresh=False,
        )
        print("\n=== RESULT ===")
        print(f"wrapper return: {result}")
        print(f"intercepted writes: {len(writes)}")
        for table, operation, _ in writes:
            print(f"  - {table}.{operation}")
        print("REAL_SYNC_COMPLETED_WITH_ALL_WRITES_INTERCEPTED")
    except Exception as exc:
        print("\n=== RESULT ===")
        print(f"EXCEPTION: {type(exc).__name__}: {exc}")
        print(f"intercepted writes before exception: {len(writes)}")
    finally:
        collector.supabase = real_supabase

if __name__ == "__main__":
    main()
