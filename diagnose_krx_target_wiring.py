"""Read-only diagnostic for KRX snapshot integration wiring.

Checks:
1. The 32 known raw-present stocks are included in get_1y_update_targets().
2. sync_all_kor_stocks_1y_only resolves the monkey-patched collector.sync_1y_only.
3. The wrapper is not invoked; no DB writes occur.
"""
import collector
from kor_market_pipeline import install_market_snapshot_integration

TARGETS = [
    "001080","001570","001720","002630","018500","020180","021820","021880",
    "030960","032800","033200","050860","060310","067010","082660","092440",
    "093240","097870","099750","169330","189690","190650","226340","289080",
    "334970","395400","417310","448730","900120","900290","950170","950210",
]

def norm(v):
    return str(v or "").strip().zfill(6)

def main():
    print("=== STEP 6: 1Y TARGET + WRAPPER WIRING DIAGNOSTIC ===")
    targets = collector.get_1y_update_targets()
    target_codes = {norm(r.get("stock_code")) for r in targets}
    print(f"1y target count: {len(targets)}")

    missing = [c for c in TARGETS if c not in target_codes]
    present = [c for c in TARGETS if c in target_codes]
    print(f"32 targets included: {len(present)}")
    print(f"32 targets missing: {len(missing)}")
    if missing:
        print("TARGET_MISSING:", ",".join(missing))

    original = collector.sync_1y_only
    integration = install_market_snapshot_integration()
    patched = collector.sync_1y_only
    resolved = collector.sync_all_kor_stocks_1y_only.__globals__.get("sync_1y_only")

    print("\n=== WRAPPER RESOLUTION ===")
    print(f"market map count: {integration['market_snapshot_count']:,}")
    print(f"collector.sync_1y_only after install: {patched.__name__}")
    print(f"sync_all... globals['sync_1y_only']: {getattr(resolved, '__name__', resolved)}")
    print(f"same object: {resolved is patched}")
    print(f"original preserved: {integration['original_sync'] is original}")

    print("\n=== DIAGNOSIS ===")
    if len(missing) == 0 and resolved is patched:
        print("TARGETS_AND_WRAPPER_OK")
        print("The 32 stocks are in the 1y target set and sync_all resolves the KRX wrapper.")
        print("Next point: determine why individual sync_1y_only calls return False/skip or why the upsert path fails.")
    elif missing:
        print("TARGET_FILTERING_ISSUE")
        print("The 32 stocks are not all present in the 1y target set.")
    else:
        print("WRAPPER_WIRING_ISSUE")
        print("sync_all does not resolve the installed KRX wrapper.")

if __name__ == "__main__":
    main()
