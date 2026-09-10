"""Deep SEC diagnostic for DEC annual-record filtering.

Reads SEC Company Facts but never writes raw SEC JSON to Supabase.
Use: python debug_dec_sec.py
"""
from __future__ import annotations

import os
import requests
from collector_us_fundamental import (
    SEC_FACTS_URL,
    SEC_SUBMISSIONS_URL,
    SEC_USER_AGENT,
    FACT_ALIASES,
    FLOW_FORMS,
    clean_number,
    annual_records,
    build_fact_index,
)

CIK = "0001922446"
TICKER = "DEC"


def diagnose_fact(tag, fact):
    units = fact.get("units") or {}
    raw = accepted = 0
    reasons = {
        "not_list": 0,
        "missing_fy_end_or_bad_form": 0,
        "bad_date": 0,
        "duration_outside_300_380": 0,
        "non_numeric": 0,
    }
    raw_years = []
    accepted_rows = []

    for unit, rows in units.items():
        if not isinstance(rows, list):
            reasons["not_list"] += 1
            continue
        for r in rows:
            raw += 1
            if r.get("fy") and r.get("end") and r.get("form") in FLOW_FORMS:
                raw_years.append(r.get("fy"))
            fy, form, end = r.get("fy"), r.get("form"), r.get("end")
            if not fy or not end or form not in FLOW_FORMS:
                reasons["missing_fy_end_or_bad_form"] += 1
                continue
            start = r.get("start")
            if start:
                try:
                    from datetime import date
                    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
                except ValueError:
                    reasons["bad_date"] += 1
                    continue
                if not 300 <= days <= 380:
                    reasons["duration_outside_300_380"] += 1
                    continue
            value = clean_number(r.get("val"))
            if value is None:
                reasons["non_numeric"] += 1
                continue
            accepted += 1
            accepted_rows.append({
                "fy": int(fy), "end": end, "filed": r.get("filed"),
                "form": form, "frame": r.get("frame"), "unit": unit,
                "val": value,
            })

    return raw, accepted, reasons, raw_years, accepted_rows


def main():
    s = requests.Session()
    s.headers.update({"User-Agent": SEC_USER_AGENT})
    facts = s.get(SEC_FACTS_URL.format(cik=CIK), timeout=30)
    facts.raise_for_status()
    facts = facts.json()
    submissions = s.get(SEC_SUBMISSIONS_URL.format(cik=CIK), timeout=30)
    submissions.raise_for_status()
    submissions = submissions.json()

    print(f"[DEEP SEC] {TICKER} CIK={CIK}")
    print(f"[DEEP SEC] namespaces={list((facts.get('facts') or {}).keys())}")
    print(f"[DEEP SEC] submissions_forms_recent={[(x.get('form'), x.get('filingDate'), x.get('reportDate')) for x in (submissions.get('filings', {}).get('recent') or [])[:20]]}")

    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    all_accepted_years = set()

    for logical, aliases in FACT_ALIASES.items():
        print(f"\n[DEEP SEC] alias={logical}")
        for tag in aliases:
            fact = us_gaap.get(tag)
            if not fact:
                print(f"  {tag}: ABSENT")
                continue
            raw, accepted, reasons, raw_years, accepted_rows = diagnose_fact(tag, fact)
            all_accepted_years.update(r["fy"] for r in accepted_rows)
            print(f"  {tag}: raw_rows={raw} accepted={accepted} raw_annual_fy={sorted(set(raw_years))} accepted_fy={sorted(set(r['fy'] for r in accepted_rows))}")
            print(f"    reject={reasons}")
            for r in accepted_rows:
                print(f"    ACCEPT fy={r['fy']} end={r['end']} filed={r['filed']} form={r['form']} frame={r['frame']} unit={r['unit']} val={r['val']}")

    print(f"\n[DEEP SEC] all accepted years across aliases={sorted(all_accepted_years)}")
    idx = build_fact_index(facts)
    print("[DEEP SEC] selected alias years:")
    for logical, rows in idx.items():
        print(f"  {logical}: {sorted(rows.keys())}")

    # Show every us-gaap tag that has accepted annual rows in older years.
    older = []
    for tag, fact in us_gaap.items():
        rows = annual_records(fact)
        years = sorted(rows.keys())
        if any(y < 2025 for y in years):
            older.append((tag, years))
    print(f"\n[DEEP SEC] us-gaap tags with accepted pre-2025 annual records: count={len(older)}")
    for tag, years in sorted(older):
        print(f"  {tag}: {years}")


if __name__ == "__main__":
    main()
