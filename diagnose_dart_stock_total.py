"""Read-only DART stock-total diagnostic for three validation stocks.

This script intentionally performs NO Supabase writes. It prints the raw
OpenDartReader stock-total dataframe and the exact fields used by
extract_issued_shares(), so we can distinguish a DART/OpenDartReader source
value from a transformation bug in our pipeline.
"""

import collector


TARGETS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "229640": "LS에코에너지",
}


if __name__ == "__main__":
    year = collector.get_latest_annual_year()
    print("\n=== DART STOCK-TOTAL RAW DIAGNOSTIC (READ ONLY) ===")
    print(f"annual year requested by collector: {year}")

    for code, name in TARGETS.items():
        print(f"\n--- {name} ({code}) ---")
        df = collector.fetch_stock_total_count_info(code, year)

        if df is None:
            print("RAW RESULT: None")
            continue

        print(f"shape={df.shape}")
        print(f"columns={list(df.columns)}")
        print("\nRAW DATAFRAME:")
        print(df.to_string(index=False))

        print("\nDTYPES:")
        print(df.dtypes.to_string())

        wanted = [
            c for c in [
                "se",
                "stlm_dt",
                "istc_totqy",
                "tesstk_co",
                "distb_stock_co",
                "isu_stock_totqy",
                "now_to_isu_stock_totqy",
                "now_to_dcrs_stock_totqy",
            ]
            if c in df.columns
        ]

        if wanted:
            print("\nKEY FIELDS:")
            print(df[wanted].to_string(index=False))

        issued, distributed = collector.extract_issued_shares(df)
        print(f"\nextract_issued_shares -> issued={issued}, distributed={distributed}")

    print("\nREAD-ONLY DIAGNOSTIC COMPLETE")
