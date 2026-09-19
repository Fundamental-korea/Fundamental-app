"""Read-only Step 8: run the real 001080 sync path and intercept all Supabase upserts."""
from __future__ import annotations
import collector
import kor_market_pipeline as pipeline

TARGET_CODE = "001080"
TARGET_NAME = "한신공영"

class _NoWriteResult:
    data = []

class _NoWriteTable:
    def __init__(self, real, name):
        self.real = real
        self.name = name
    def select(self, *a, **k): self.real = self.real.select(*a, **k); return self
    def eq(self, *a, **k): self.real = self.real.eq(*a, **k); return self
    def neq(self, *a, **k): self.real = self.real.neq(*a, **k); return self
    def not_(self, *a, **k): self.real = self.real.not_(*a, **k); return self
    def is_(self, *a, **k): self.real = self.real.is_(*a, **k); return self
    def limit(self, *a, **k): self.real = self.real.limit(*a, **k); return self
    def range(self, *a, **k): self.real = self.real.range(*a, **k); return self
    def order(self, *a, **k): self.real = self.real.order(*a, **k); return self
    def single(self, *a, **k): self.real = self.real.single(*a, **k); return self
    def maybe_single(self, *a, **k): self.real = self.real.maybe_single(*a, **k); return self
    def execute(self): return self.real.execute()
    def upsert(self, payload, *a, **k):
        if self.name == "Fundamental":
            print("\n=== INTERCEPTED FUNDAMENTAL UPSERT ===")
            if isinstance(payload, dict):
                for key in (
                    "stock_code","stock_price","listed_shares","market_cap",
                    "market_snapshot_date","market_data_source",
                    "period_end_shares","shares_basis_date","shares_data_source",
                    "eps","eps_is_reported","bps","per","pbr",
                    "revenue","operating_income","net_income","total_liabilities",
                    "total_equity","data_basis_label","issued_shares",
                ):
                    print(f"{key}: {payload.get(key)!r}")
                print("payload keys:", sorted(payload.keys()))
            else:
                print(f"payload type: {type(payload).__name__}")
        else:
            print(f"[READ-ONLY] intercepted upsert on {self.name}")
        return self
    def insert(self, payload, *a, **k):
        print(f"[READ-ONLY] intercepted insert on {self.name}")
        return self
    def update(self, payload, *a, **k):
        print(f"[READ-ONLY] intercepted update on {self.name}")
        return self

class _NoWriteSupabase:
    def __init__(self, real): self.real = real
    def table(self, name): return _NoWriteTable(self.real.table(name), name)

def main():
    print("=== STEP 8: REAL SINGLE-STOCK SYNC + WRITE INTERCEPT ===")
    print(f"target: {TARGET_CODE} {TARGET_NAME}")

    real_supabase = collector.supabase
    row_res = (real_supabase.table("Fundamental")
        .select("stock_code,stock_name,sector,wics_sector,holding_company,period_scores")
        .eq("stock_code", TARGET_CODE).limit(1).execute())
    row = (row_res.data or [None])[0]
    if not row:
        print("TARGET_ROW_MISSING"); return

    print("TARGET_ROW_FOUND")
    collector.supabase = _NoWriteSupabase(real_supabase)
    try:
        # Install wrapper around the REAL collector.sync_1y_only.
        integration = pipeline.install_market_snapshot_integration()
        print(f"wrapper: {collector.sync_1y_only.__name__}")
        print(f"market map count: {integration['market_snapshot_count']:,}")

        result = collector.sync_1y_only(
            TARGET_CODE, TARGET_NAME,
            sector=row.get("sector"), wics_sector=row.get("wics_sector"),
            holding_company=row.get("holding_company") or False,
            existing_period_scores=row.get("period_scores") or {},
            kospi_mdd_cache=collector.get_kospi_mdd_cache(),
            use_ofs_for_manufacturing=False,
            force_refresh=False,
        )
        print("\n=== RESULT ===")
        print(f"wrapper return: {result}")
        print("REAL_SYNC_COMPLETED_WITH_ALL_WRITES_INTERCEPTED")
    except Exception as exc:
        print("\n=== RESULT ===")
        print(f"EXCEPTION: {type(exc).__name__}: {exc}")
    finally:
        collector.supabase = real_supabase

if __name__ == "__main__":
    main()
