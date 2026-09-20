"""Read-only DART benchmark diagnostic for Naver-style TTM valuation.

No database writes. Prints the relevant interim/annual income statement rows and both
standalone (thstrm_amount) and accumulated (thstrm_add_amount) fields.
"""

from __future__ import annotations

import os
import re
import OpenDartReader

BENCHMARKS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "005380": "현대차",
    "035420": "NAVER",
    "105560": "KB금융",
}

REPORTS = [
    (2026, "11012"),
    (2026, "11013"),
    (2025, "11014"),
    (2025, "11012"),
    (2025, "11011"),
]


def main():
    dart = OpenDartReader(os.environ["DART_API_KEY"])
    print("=== Korean DART Naver-similarity benchmark diagnostic ===")
    for code, name in BENCHMARKS.items():
        print(f"\n### {code} {name}")
        for year, report_code in REPORTS:
            try:
                df = dart.finstate_all(code, year, reprt_code=report_code, fs_div="CFS")
            except Exception as exc:
                print(f"[{year} {report_code}] ERROR: {exc}")
                continue

            if df is None or df.empty:
                print(f"[{year} {report_code}] EMPTY")
                continue

            print(f"[{year} {report_code}] rows={len(df)}")
            names = df["account_nm"].astype(str)
            normalized = names.str.replace(r"\s+", "", regex=True)
            mask = (
                normalized.str.contains("당기순이익", na=False, regex=False)
                | normalized.str.contains("기본주당이익", na=False, regex=False)
                | normalized.str.contains("지배기업의소유주", na=False, regex=False)
                | normalized.str.contains("지배기업소유주", na=False, regex=False)
            )
            view = df.loc[mask, [
                "sj_div", "account_nm", "thstrm_nm", "thstrm_amount",
                "thstrm_add_amount", "frmtrm_nm", "frmtrm_amount", "frmtrm_add_amount"
            ]].copy()
            if view.empty:
                print("  NO RELEVANT ROWS")
            else:
                for _, row in view.iterrows():
                    print(
                        "  | ".join([
                            str(row.get("sj_div")),
                            str(row.get("account_nm")),
                            f"current={row.get('thstrm_amount')}",
                            f"current_acc={row.get('thstrm_add_amount')}",
                            f"prior={row.get('frmtrm_amount')}",
                            f"prior_acc={row.get('frmtrm_add_amount')}",
                        ])
                    )


if __name__ == "__main__":
    main()
