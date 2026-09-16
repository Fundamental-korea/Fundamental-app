"""Inspect SEC submissions for a small set of Standard snapshot-gap companies.

This is diagnostic only: no database writes and no Company Facts requests.
It answers one question: does SEC submissions show relevant financial filings?
"""

from __future__ import annotations

import argparse
import os
import time

import requests

SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

DEFAULT_CASES = {
    "DPUI": ("Discount Print USA, Inc.", "0001791929"),
    "EAXR": ("Ealixir, Inc.", "0000832370"),
    "FITY": ("Fifty 1 Labs, Inc.", "0001285828"),
    "GOGR": ("Go Green Global Technologies Corp.", "0001378866"),
    "GSAC": ("GelStat Corp.", "0000890725"),
    "INKW": ("Greene Concepts, Inc.", "0001585380"),
    "NTRX": ("Entrex Carbon Market, Inc.", "0001363598"),
    "PCGC": ("Hydro Power Technologies, Inc.", "0001365359"),
    "SRMX": ("Saddle Ranch Media, Inc.", "0000841533"),
    "XCRT": ("Xcelerate, Inc.", "0001138586"),
}

RELEVANT_FORMS = {
    "10-K", "10-K/A", "10-Q", "10-Q/A",
    "20-F", "20-F/A", "40-F", "40-F/A", "6-K",
}
DOMESTIC_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A"}
FOREIGN_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}


def fetch_json(session: requests.Session, url: str):
    for attempt in range(4):
        response = session.get(url, timeout=30)
        if response.status_code == 200:
            return response.json()
        if response.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue
        response.raise_for_status()
    raise RuntimeError(f"SEC request failed: {url}")


def rows_from_submissions(data):
    recent = (data.get("filings") or {}).get("recent") or {}
    keys = ["form", "filingDate", "reportDate", "accessionNumber", "primaryDocument", "primaryDocDescription"]
    arrays = {k: recent.get(k, []) or [] for k in keys}
    count = len(arrays["form"])
    rows = []
    for i in range(count):
        row = {k: arrays[k][i] if i < len(arrays[k]) else None for k in keys}
        if row["form"] in RELEVANT_FORMS:
            rows.append(row)
    rows.sort(key=lambda r: (r.get("filingDate") or "", r.get("accessionNumber") or ""), reverse=True)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickers", default=",".join(DEFAULT_CASES))
    args = parser.parse_args()

    tickers = [x.strip().upper() for x in args.tickers.split(",") if x.strip()]
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    print("ticker|company|cik|relevant_count|domestic_count|foreign_count|latest_relevant|latest_filed|latest_report|latest_accession|latest_primary")

    for ticker in tickers:
        company, cik = DEFAULT_CASES.get(ticker, ("", ""))
        if not cik:
            print(f"{ticker}|UNKNOWN||0|0|0|ERROR|missing mapping")
            continue
        try:
            data = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik.zfill(10)))
            rows = rows_from_submissions(data)
            domestic = sum(r["form"] in DOMESTIC_FORMS for r in rows)
            foreign = sum(r["form"] in FOREIGN_FORMS for r in rows)
            latest = rows[0] if rows else {}
            print("|".join([
                ticker,
                company,
                cik,
                str(len(rows)),
                str(domestic),
                str(foreign),
                latest.get("form", "NONE") or "NONE",
                latest.get("filingDate", "") or "",
                latest.get("reportDate", "") or "",
                latest.get("accessionNumber", "") or "",
                latest.get("primaryDocument", "") or "",
            ]))
        except Exception as exc:
            print(f"{ticker}|{company}|{cik}|ERROR|{type(exc).__name__}:{exc}")
        time.sleep(0.12)


if __name__ == "__main__":
    main()
