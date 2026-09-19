"""Step 9: read-only DART latest-report diagnostic for one Korean stock."""
from __future__ import annotations

import collector

TARGET_CODE = "001080"
TARGET_NAME = "한신공영"


def show_result(label, df):
    print(f"\n--- {label} ---")
    if df is None:
        print("RESULT: None")
        return
    try:
        print(f"shape: {df.shape}")
        print(f"columns: {list(df.columns)}")
        if hasattr(df, "head"):
            print(df.head(5).to_string())
    except Exception as exc:
        print(f"RESULT_PRINT_ERROR: {type(exc).__name__}: {exc}")


def main():
    print("=== STEP 9: DART LATEST REPORT DIAGNOSTIC (READ ONLY) ===")
    print(f"target: {TARGET_CODE} {TARGET_NAME}")

    year, reprt_code = collector.get_latest_available_report()
    print(f"latest_available_report: year={year}, reprt_code={reprt_code}")
    print(f"report_label: {collector.REPORT_CODE_LABEL.get(reprt_code, reprt_code)}")

    # Read-only cache checks.
    try:
        cached = collector._get_cached_raw(
            TARGET_CODE, year, reprt_code, fs_div="BOTH", source="finstate"
        )
        print(f"raw finstate cache: {'HIT' if cached is not None else 'MISS'}")
        if cached is not None:
            print(f"cached shape: {cached.shape}")
    except Exception as exc:
        print(f"raw finstate cache check exception: {type(exc).__name__}: {exc}")

    try:
        metrics_cached = collector._get_cached_report_metrics(
            TARGET_CODE, year, reprt_code, False
        )
        print(f"metrics cache (use_ofs=False): {'HIT' if metrics_cached is not None else 'MISS'}")
    except Exception as exc:
        print(f"metrics cache check exception: {type(exc).__name__}: {exc}")

    # Direct OpenDartReader calls only. These bypass collector cache writes.
    try:
        finstate = collector.dart.finstate(TARGET_CODE, year, reprt_code=reprt_code)
        show_result("DIRECT dart.finstate", finstate)
    except Exception as exc:
        print(f"\n--- DIRECT dart.finstate ---")
        print(f"EXCEPTION: {type(exc).__name__}: {exc}")

    for fs_div in ("CFS", "OFS"):
        try:
            finstate_all = collector.dart.finstate_all(
                TARGET_CODE, year, reprt_code=reprt_code, fs_div=fs_div
            )
            show_result(f"DIRECT dart.finstate_all fs_div={fs_div}", finstate_all)
        except Exception as exc:
            print(f"\n--- DIRECT dart.finstate_all fs_div={fs_div} ---")
            print(f"EXCEPTION: {type(exc).__name__}: {exc}")

    # Company metadata helps distinguish an invalid/unmapped stock code from a report-period issue.
    try:
        company = collector.dart.company(TARGET_CODE)
        show_result("DIRECT dart.company", company)
    except Exception as exc:
        print(f"\n--- DIRECT dart.company ---")
        print(f"EXCEPTION: {type(exc).__name__}: {exc}")

    print("\n=== STEP 9 COMPLETE: NO CACHE/DB WRITES PERFORMED ===")


if __name__ == "__main__":
    main()
