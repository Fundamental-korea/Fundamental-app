"""Full-universe filing-first forensic census for ROIC / interest coverage.

Unlike the earlier diagnostic, this collector treats the latest annual filing's
Inline XBRL as the primary source for the economic inputs. It processes every
currently-missing company, regardless of sector or scoring profile.

It is strictly read-only with respect to Supabase: no US_Fundamental writes.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone

from supabase import create_client
from requests import Session

import collector_us_fundamental as base
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8
from sec_filing_financial_map import filing_map

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")

PAGE_SIZE = 200

ROIC_PROFILES = {"standard", "defense"}
INTEREST_PROFILES = {"standard", "reit", "bdc", "defense", "utility"}


def load_targets(sb):
    fundamentals = []
    offset = 0
    while True:
        page = (
            sb.table("US_Fundamental")
            .select("ticker,period_scores,base_year,data_unavailable")
            .eq("data_unavailable", False)
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        if not page:
            break
        fundamentals.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    missing = []
    for row in fundamentals:
        avg = (((row.get("period_scores") or {}).get("1y") or {}).get("avg") or {})
        scores = avg.get("metric_scores") or {}
        roic_missing = (scores.get("roic") or {}).get("value") is None
        interest_missing = (scores.get("interest_coverage") or {}).get("value") is None
        if roic_missing or interest_missing:
            missing.append({
                "ticker": row["ticker"],
                "base_year_db": row.get("base_year"),
                "missing_roic": roic_missing,
                "missing_interest": interest_missing,
            })

    tickers = [x["ticker"] for x in missing]
    meta = {}
    for i in range(0, len(tickers), PAGE_SIZE):
        batch = tickers[i:i + PAGE_SIZE]
        rows = (
            sb.table("US_Companies")
            .select("ticker,cik,company_name,sector_common,company_type,scoring_profile,is_fundamental_eligible")
            .in_("ticker", batch)
            .execute()
            .data
            or []
        )
        for row in rows:
            meta[row["ticker"]] = row

    out = []
    for row in missing:
        m = meta.get(row["ticker"])
        if not m or not m.get("is_fundamental_eligible"):
            continue
        out.append({**row, "_meta": m})
    return out


def _fact_value(obj, key):
    item = obj.get(key) if isinstance(obj, dict) else None
    return item.get("value") if isinstance(item, dict) else None


def _fact_unit(obj, key):
    item = obj.get(key) if isinstance(obj, dict) else None
    return item.get("unit") if isinstance(item, dict) else None


def _selected_concept(obj, key):
    item = obj.get(key) if isinstance(obj, dict) else None
    return item.get("concept") if isinstance(item, dict) else None


def analyze_result(rec, mapped):
    meta = rec["_meta"]
    roic_inputs = mapped.get("roic_inputs") or {}
    debt = mapped.get("selected_debt")
    interest = mapped.get("selected_interest")
    equity = roic_inputs.get("equity")
    cash = roic_inputs.get("cash")
    opinc = roic_inputs.get("operating_income")

    roic_ready = all(x is not None for x in (equity, cash, opinc, debt))
    roic_reason = "CALCULABLE_FROM_FILING" if roic_ready else "UNRESOLVED"

    invested_capital = None
    if roic_ready:
        invested_capital = equity["value"] + debt["value"] - cash["value"]
        if invested_capital <= 0:
            roic_ready = False
            roic_reason = "NON_POSITIVE_INVESTED_CAPITAL"

    interest_ready = all(x is not None for x in (opinc, interest))
    interest_reason = "CALCULABLE_FROM_FILING" if interest_ready else "UNRESOLVED"
    interest_coverage = None
    if interest_ready and interest["value"] not in (None, 0):
        interest_coverage = opinc["value"] / interest["value"]

    profile = meta.get("scoring_profile")
    score_relevant_roic = bool(rec["missing_roic"] and profile in ROIC_PROFILES)
    score_relevant_interest = bool(rec["missing_interest"] and profile in INTEREST_PROFILES)

    return {
        "ticker": rec["ticker"],
        "cik": meta["cik"],
        "company_name": meta.get("company_name"),
        "sector": meta.get("sector_common"),
        "company_type": meta.get("company_type"),
        "scoring_profile": profile,
        "base_year_db": rec.get("base_year_db"),
        "filing_target_year": (mapped.get("filing") or {}).get("target_year"),
        "missing_roic": rec["missing_roic"],
        "missing_interest": rec["missing_interest"],
        "score_relevant_roic": score_relevant_roic,
        "score_relevant_interest": score_relevant_interest,
        "roic_reason": roic_reason,
        "interest_reason": interest_reason,
        "debt_status": mapped.get("debt_status"),
        "interest_status": mapped.get("interest_status"),
        "filing": mapped.get("filing"),
        "roic_inputs": {
            "equity": equity,
            "cash": cash,
            "operating_income": opinc,
            "debt": debt,
            "invested_capital": invested_capital,
        },
        "selected_interest": interest,
        "interest_coverage_preview": interest_coverage,
        "candidate_concept_counts": mapped.get("candidate_concept_counts") or {},
        "unclassified_like_counts": mapped.get("unclassified_like_counts") or {},
        "unclassified_like_examples": mapped.get("unclassified_like_examples") or {},
    }


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--output", default="us_roic_interest_filing_forensics_v2.json")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    targets = load_targets(sb)
    targets = targets[max(0, args.start):]
    if args.limit is not None:
        targets = targets[:max(0, args.limit)]

    session = Session()
    session.headers.update({
        "User-Agent": SEC_USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    })
    resolver = SECXBRLSearchV2_3_8(user_agent=SEC_USER_AGENT, session=session)

    company_results = []
    counters = {
        "debt_status": Counter(),
        "interest_status": Counter(),
        "roic_reason": Counter(),
        "interest_reason": Counter(),
        "score_relevant_roic": Counter(),
        "score_relevant_interest": Counter(),
        "profiles": Counter(),
        "sectors": Counter(),
    }
    debt_concepts = Counter()
    interest_concepts = Counter()
    unclassified_debt = Counter()
    unclassified_interest = Counter()
    failures = 0

    print(
        f"[FILING-FORENSICS-V2] targets={len(targets)} "
        f"start={args.start} limit={args.limit}",
        flush=True,
    )

    for i, rec in enumerate(targets, 1):
        meta = rec["_meta"]
        try:
            mapped = filing_map(meta["cik"], resolver, year=rec.get("base_year_db"))
            result = analyze_result(rec, mapped)
            company_results.append(result)

            counters["debt_status"][result["debt_status"] or "UNKNOWN"] += 1
            counters["interest_status"][result["interest_status"] or "UNKNOWN"] += 1
            counters["roic_reason"][result["roic_reason"]] += 1
            counters["interest_reason"][result["interest_reason"]] += 1
            counters["score_relevant_roic"][str(result["score_relevant_roic"])] += 1
            counters["score_relevant_interest"][str(result["score_relevant_interest"])] += 1
            counters["profiles"][result["scoring_profile"] or "unknown"] += 1
            counters["sectors"][result["sector"] or "unknown"] += 1

            for concept, count in (result["candidate_concept_counts"].get("debt") or {}).items():
                debt_concepts[concept] += 1
            for concept, count in (result["candidate_concept_counts"].get("interest") or {}).items():
                interest_concepts[concept] += 1
            for concept, count in (result["unclassified_like_counts"].get("debt") or {}).items():
                unclassified_debt[concept] += 1
            for concept, count in (result["unclassified_like_counts"].get("interest") or {}).items():
                unclassified_interest[concept] += 1

            if i % 25 == 0:
                print(
                    f"[{i}/{len(targets)}] "
                    f"debt={result['debt_status']} interest={result['interest_status']} "
                    f"roic={result['roic_reason']} ic={result['interest_reason']}",
                    flush=True,
                )

        except Exception as exc:
            failures += 1
            company_results.append({
                "ticker": rec["ticker"],
                "cik": meta["cik"],
                "company_name": meta.get("company_name"),
                "sector": meta.get("sector_common"),
                "scoring_profile": meta.get("scoring_profile"),
                "missing_roic": rec["missing_roic"],
                "missing_interest": rec["missing_interest"],
                "score_relevant_roic": bool(rec["missing_roic"] and meta.get("scoring_profile") in ROIC_PROFILES),
                "score_relevant_interest": bool(rec["missing_interest"] and meta.get("scoring_profile") in INTEREST_PROFILES),
                "error": str(exc),
            })
            print(f"[{i}/{len(targets)}] {rec['ticker']}: FAILED {exc}", flush=True)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "targets": len(targets),
            "sector_restriction": None,
            "scoring_profile_restriction": None,
            "primary_source": "latest annual SEC Inline XBRL filing",
            "db_writes": False,
            "roic_score_relevant_profiles": sorted(ROIC_PROFILES),
            "interest_score_relevant_profiles": sorted(INTEREST_PROFILES),
        },
        "counts": {
            "processed": len(company_results),
            "failures": failures,
            "debt_status": dict(counters["debt_status"]),
            "interest_status": dict(counters["interest_status"]),
            "roic_reason": dict(counters["roic_reason"]),
            "interest_reason": dict(counters["interest_reason"]),
            "score_relevant_roic": dict(counters["score_relevant_roic"]),
            "score_relevant_interest": dict(counters["score_relevant_interest"]),
            "profiles": dict(counters["profiles"]),
            "sectors": dict(counters["sectors"]),
        },
        "concept_frequency_unique_companies": {
            "debt": debt_concepts.most_common(150),
            "interest": interest_concepts.most_common(150),
        },
        "unclassified_like_frequency_unique_companies": {
            "debt": unclassified_debt.most_common(150),
            "interest": unclassified_interest.most_common(150),
        },
    }

    result = {
        "summary": summary,
        "companies": company_results,
    }

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    txt_path = os.path.splitext(args.output)[0] + ".txt"
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write("US FILING-FIRST ROIC / INTEREST FORENSICS V2\n")
        fh.write("=" * 72 + "\n")
        fh.write(json.dumps(summary, ensure_ascii=False, indent=2))
        fh.write("\n\nTop issuer-debt concepts\n")
        for k, v in debt_concepts.most_common(75):
            fh.write(f"  {k}: {v}\n")
        fh.write("\nTop interest concepts\n")
        for k, v in interest_concepts.most_common(75):
            fh.write(f"  {k}: {v}\n")
        fh.write("\nTop unclassified debt-like concepts\n")
        for k, v in unclassified_debt.most_common(75):
            fh.write(f"  {k}: {v}\n")
        fh.write("\nTop unclassified interest-like concepts\n")
        for k, v in unclassified_interest.most_common(75):
            fh.write(f"  {k}: {v}\n")

    print(
        f"[DONE] processed={len(company_results)} failures={failures} "
        f"json={args.output} txt={txt_path}",
        flush=True,
    )


if __name__ == "__main__":
    run()
