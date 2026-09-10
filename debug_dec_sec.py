"""Deep SEC diagnostic for DEC annual-record filtering.

Reads SEC Company Facts but never writes raw SEC JSON to Supabase.
Use: python debug_dec_sec.py
"""
from __future__ import annotations

import requests
from collector_us_fundamental import (
    SEC_FACTS_URL,
    SEC_SUBMISSIONS_URL,
    SEC_USER_AGENT,
    FACT_ALIASES,
    IFRS_FACT_ALIASES,
    FLOW_FORMS,
    clean_number,
    annual_records,
    build_fact_index,
)

CIK = "0001922446"
TICKER = "DEC"


def diagnose_fact(fact):
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
            fy, form, end = r.get("fy"), r.get("form"), r.get("end")
            if fy and end and form in FLOW_FORMS:
                raw_years.append(r.get("fy"))
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
                "fy": int(fy), "year": int(end[:4]), "end": end, "filed": r.get("filed"),
                "form": form, "frame": r.get("frame"), "unit": unit, "val": value,
            })

    return raw, accepted, reasons, raw_years, accepted_rows


def print_namespace(facts, namespace, alias_map):
    namespace_facts = (facts.get("facts") or {}).get(namespace) or {}
    all_accepted_years = set()
    print(f"\n[DEEP SEC] namespace={namespace} tags={len(namespace_facts)}")
    for logical, aliases in alias_map.items():
        print(f"[DEEP SEC] alias={logical} namespace={namespace}")
        for tag in aliases:
            fact = namespace_facts.get(tag)
            if not fact:
                print(f"    {tag}: ABSENT")
                continue
            raw, accepted, reasons, raw_years, accepted_rows = diagnose_fact(fact)
            all_accepted_years.update(r["year"] for r in accepted_rows)
            print(f"    {tag}: raw_rows={raw} accepted={accepted} accepted_years={sorted(set(r['year'] for r in accepted_rows))}")
            print(f"      reject={reasons}")
            for r in accepted_rows:
                print(f"      ACCEPT year={r['year']} fy={r['fy']} end={r['end']} filed={r['filed']} form={r['form']} frame={r['frame']} unit={r['unit']} val={r['val']}")
    print(f"[DEEP SEC] {namespace} accepted years across aliases={sorted(all_accepted_years)}")


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
    recent = (submissions.get('filings', {}).get('recent') or {})
    if isinstance(recent, dict):
        forms = recent.get('form') or []
        filing_dates = recent.get('filingDate') or []
        report_dates = recent.get('reportDate') or []
        recent_rows = list(zip(forms, filing_dates, report_dates))[:20]
    else:
        recent_rows = []
    print(f"[DEEP SEC] submissions_forms_recent={recent_rows}")

    print_namespace(facts, "us-gaap", FACT_ALIASES)
    print_namespace(facts, "ifrs-full", IFRS_FACT_ALIASES)

    idx = build_fact_index(facts)
    print("\n[DEEP SEC] normalized selected metric years:")
    for logical, rows in idx.items():
        print(f"  {logical}: {sorted(rows.keys())}")
        for year in sorted(rows.keys()):
            row = rows[year]
            print(f"    {year}: namespace={row.get('namespace')} tag={row.get('tag')} val={row.get('val')}")

    older = []
    for namespace in ("us-gaap", "ifrs-full"):
        for tag, fact in ((facts.get("facts") or {}).get(namespace) or {}).items():
            rows = annual_records(fact)
            years = sorted(rows.keys())
            if any(y < 2025 for y in years):
                older.append((namespace, tag, years))
    print(f"\n[DEEP SEC] supported namespace tags with accepted pre-2025 annual records: count={len(older)}")
    for namespace, tag, years in sorted(older):
        print(f"  {namespace}:{tag}: {years}")


if __name__ == "__main__":
    main()
