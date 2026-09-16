"""Targeted MDA foreign-parser validation with expanded label aliases.

No database writes. This wrapper imports the existing foreign parser, expands
only the aliases known to appear in MDA's June 30, 2026 interim filing, and
prints the normalized Standard metrics.
"""

from __future__ import annotations

import requests

import collector_us_foreign_fallback as parser

MDA_URL = "https://www.sec.gov/Archives/edgar/data/1857047/000110465926092383/tm2621766d1_ex99-1.htm"


def main() -> None:
    parser.LABEL_ALIASES["sga"] = list(dict.fromkeys(
        parser.LABEL_ALIASES.get("sga", [])
        + [
            "selling, general and administration",
            "selling general and administration",
        ]
    ))

    parser.LABEL_ALIASES["operating_cash_flow"] = list(dict.fromkeys(
        parser.LABEL_ALIASES.get("operating_cash_flow", [])
        + [
            "net cash generated (used) in operating activities",
            "net cash generated (used in) operating activities",
            "net cash generated / (used) in operating activities",
        ]
    ))

    parser.LABEL_ALIASES["equity"] = list(dict.fromkeys(
        parser.LABEL_ALIASES.get("equity", [])
        + [
            "total equity attributable to shareholders",
            "total equity attributable to owners",
            "equity attributable to shareholders",
            "equity attributable to owners of the company",
        ]
    ))

    parser.LABEL_ALIASES["current_assets"] = list(dict.fromkeys(
        parser.LABEL_ALIASES.get("current_assets", [])
        + [
            "current assets",
            "total current asset",
        ]
    ))

    parser.LABEL_ALIASES["current_liabilities"] = list(dict.fromkeys(
        parser.LABEL_ALIASES.get("current_liabilities", [])
        + [
            "current liabilities",
            "total current liability",
        ]
    ))

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Fundamental-app contact@example.com",
    })

    response = session.get(MDA_URL, timeout=30)
    response.raise_for_status()
    html = response.text

    print(f"[HTTP] status={response.status_code} bytes={len(response.content)}")

    result = parser.normalize_foreign_result(
        html,
        ticker="MDA",
        fiscal_end="2026-06-30",
        form="6-K",
    )

    print("[NORMALIZED]")
    print(result)

    print("[STANDARD]")
    standard = parser.build_standard_metric_payload(result)
    print(standard)


if __name__ == "__main__":
    main()
