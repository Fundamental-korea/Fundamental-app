"""Standalone diagnostic for SEC Inline XBRL parsing.

Read-only: downloads one annual SEC HTML filing, parses it in memory, and
prints structural counts/samples. No project/DB imports are used.
Raw SEC content is never persisted.
"""
from __future__ import annotations

import sys
from collections import Counter

import requests
from lxml import html

from sec_xbrl_inline import _attr, _local, _num, _first_text

SEC_HEADERS = {
    "User-Agent": "Fundamental-korea SEC XBRL diagnostic contact@example.com",
    "Accept-Encoding": "gzip, deflate",
}
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}


def get_json(url: str):
    r = requests.get(url, headers=SEC_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def get_text(url: str) -> str:
    r = requests.get(url, headers=SEC_HEADERS, timeout=60)
    r.raise_for_status()
    return r.text


def ticker_to_cik(ticker: str) -> str:
    data = get_json("https://www.sec.gov/files/company_tickers.json")
    wanted = ticker.upper()
    for row in data.values():
        if str(row.get("ticker", "")).upper() == wanted:
            return str(int(row["cik_str"])).zfill(10)
    raise RuntimeError(f"Ticker not found in SEC company_tickers.json: {ticker}")


def latest_annual_filing(cik: str):
    sub = get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    recent = sub.get("filings", {}).get("recent", {})
    rows = []
    for i, form in enumerate(recent.get("form", [])):
        if form not in ANNUAL_FORMS:
            continue
        rows.append({
            "form": form,
            "accession": recent["accessionNumber"][i],
            "doc": recent["primaryDocument"][i],
            "filed": recent["filingDate"][i],
            "fy": recent.get("fy", [None] * len(recent.get("form", [])))[i],
            "reportDate": recent.get("reportDate", [None] * len(recent.get("form", [])))[i],
        })
    if not rows:
        raise RuntimeError(f"No annual filing found for CIK {cik}")
    rows.sort(key=lambda x: x["filed"], reverse=True)
    return rows[0]


def context_summary(ctx):
    if ctx is None:
        return {}
    return {
        "id": _attr(ctx, "id"),
        "instant": _first_text(ctx, "instant"),
        "start": _first_text(ctx, "startDate"),
        "end": _first_text(ctx, "endDate"),
    }


def main(ticker: str = "HON"):
    cik = ticker_to_cik(ticker)
    filing = latest_annual_filing(cik)
    accession = filing["accession"]
    doc = filing["doc"]
    filed = filing["filed"]
    report_date = filing.get("reportDate")
    compact = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact}/{doc}"
    text = get_text(url)
    root = html.fromstring(text.encode("utf-8"))

    print(f"ticker={ticker} cik={int(cik)} accession={accession} doc={doc} filed={filed} fy={filing.get('fy')}")
    print(f"report_date={report_date}")
    print(f"url={url}")
    print(f"root_tag={root.tag}")

    tags = Counter(_local(e.tag) for e in root.iter() if isinstance(e.tag, str))
    print(f"context_count={tags['context']}")
    print(f"unit_count={tags['unit']}")
    print(f"nonfraction_count={tags['nonfraction']}")
    print(f"nonfractionrelated_count={sum(v for k, v in tags.items() if 'nonfraction' in k)}")

    print("top_tags:")
    for name, count in tags.most_common(25):
        print(f"  {name}: {count}")

    contexts = [e for e in root.iter() if _local(e.tag) == "context"]
    context_map = {_attr(e, "id"): e for e in contexts}
    print(f"contexts_parsed={len(contexts)}")
    for e in contexts[:3]:
        print("CONTEXT", context_summary(e))

    facts = [e for e in root.iter() if _local(e.tag) == "nonfraction"]
    print(f"nonfraction_samples={min(10, len(facts))}")
    for e in facts[:10]:
        raw_text = " ".join(e.itertext()).strip()
        print("FACT", {
            "tag": e.tag,
            "name": _attr(e, "name"),
            "contextRef": _attr(e, "contextRef"),
            "unitRef": _attr(e, "unitRef"),
            "scale": _attr(e, "scale"),
            "sign": _attr(e, "sign"),
            "nil": _attr(e, "nil"),
            "text": raw_text[:120],
            "num": _num(raw_text, _attr(e, "scale"), _attr(e, "sign")),
        })

    # Diagnostic 1: any instant fact on the filing report date with a large
    # balance-sheet-like value. This deliberately does not require "liabil"
    # to appear in the concept name, because issuers often use custom concepts.
    print("\nINSTANT_BALANCE_DIAGNOSTIC")
    candidates = []
    for e in facts:
        cref = _attr(e, "contextRef")
        ctx = context_map.get(cref)
        instant = _first_text(ctx, "instant") if ctx is not None else None
        if not instant or (report_date and instant != report_date):
            continue
        raw_text = " ".join(e.itertext()).strip()
        value = _num(raw_text, _attr(e, "scale"), _attr(e, "sign"))
        if value is None or abs(value) < 5_000_000_000:
            continue
        name = _attr(e, "name") or ""
        candidates.append((abs(value), name, value, cref, instant, e))
    candidates.sort(reverse=True)
    print(f"large_instant_fact_count={len(candidates)}")
    for _, name, value, cref, instant, e in candidates[:100]:
        # Include the nearest textual table/row context so the human-readable
        # label can be identified even when the XBRL concept is custom.
        parent = e.getparent()
        ancestor_text = ""
        depth = 0
        while parent is not None and depth < 5:
            txt = " ".join(parent.itertext()).strip()
            if len(txt) > len(ancestor_text):
                ancestor_text = txt
            parent = parent.getparent()
            depth += 1
        print("BALANCE_FACT", {
            "name": name,
            "value": value,
            "contextRef": cref,
            "instant": instant,
            "ancestor_text": ancestor_text[:300],
        })

    # Diagnostic 2: all facts whose concept name itself contains liability.
    # Kept separately to distinguish "no such concept" from semantic filtering.
    print("\nLIABILITY_NAME_DIAGNOSTIC")
    named = []
    for e in facts:
        name = _attr(e, "name") or ""
        if "liabil" not in name.lower():
            continue
        cref = _attr(e, "contextRef")
        ctx = context_map.get(cref)
        instant = _first_text(ctx, "instant") if ctx is not None else None
        end_date = _first_text(ctx, "endDate") if ctx is not None else None
        raw_text = " ".join(e.itertext()).strip()
        value = _num(raw_text, _attr(e, "scale"), _attr(e, "sign"))
        if value is None:
            continue
        named.append((abs(value), name, value, cref, instant, end_date))
    named.sort(reverse=True)
    print(f"liability_name_fact_count={len(named)}")
    for _, name, value, cref, instant, end_date in named[:50]:
        print("LIABILITY_NAME", {
            "name": name,
            "value": value,
            "contextRef": cref,
            "instant": instant,
            "end": end_date,
        })


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "HON")
