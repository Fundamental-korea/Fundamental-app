"""Full-universe forensic census for US ROIC / interest-coverage gaps.

Read-only diagnostic. It does NOT modify US_Fundamental.

Phase 1: census every currently-missing ROIC / interest-coverage row across the
whole eligible US universe. Company Facts are used to determine the exact
missing dependency and to inventory standardized XBRL concepts.

Phase 2: run Inline-XBRL forensic inspection on a bounded, stratified sample
from ALL sectors/profiles/reason classes. This is intentionally not limited to
the Standard scoring profile. The sample is used to discover filing-specific
and custom concepts before changing recovery logic.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone

import requests
from supabase import create_client

import collector_us_fundamental as base
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")

PAGE_SIZE = 200
META_BATCH = 200
DEFAULT_INLINE_PER_REASON = 15
DEFAULT_INLINE_MAX = 240

ROIC_KEYS = ("equity", "cash", "debt_current", "debt_noncurrent", "debt_total", "operating_income")
INTEREST_KEYS = ("operating_income", "interest_expense")

DEBT_WORDS = (
    "debt", "borrow", "borrowing", "loan", "note", "notes payable",
    "commercial paper", "credit facility", "revolving", "revolver",
    "term loan", "senior note", "convertible debt",
)
LEASE_WORDS = ("lease liability", "lease liabilities", "finance lease", "capital lease", "right of use")
INTEREST_WORDS = ("interest", "finance cost", "financing cost", "debt expense")
OPERATING_LIABILITY_WORDS = (
    "accounts payable", "account payable", "accrued", "contract liability",
    "deferred revenue", "deferred income", "pension liability",
    "employee benefit liability", "other operating liabilities",
)
TOTAL_LIABILITY_WORDS = ("total liabilities", "liabilities and stockholders", "liabilities and equity")


def sec_json(session, url):
    for attempt in range(6):
        try:
            response = session.get(url, timeout=45)
            if response.status_code == 200:
                return response.json()
            if response.status_code in (429, 500, 502, 503, 504):
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = None
                time.sleep(min(max(delay, 1.0), 30.0) if delay is not None else min(2.0 ** attempt, 16.0))
                continue
            response.raise_for_status()
        except requests.RequestException:
            if attempt == 5:
                raise
            time.sleep(min(2.0 ** attempt, 16.0))
    raise RuntimeError(f"SEC request failed: {url}")


def load_targets(sb):
    """Return every non-unavailable US_Fundamental row missing 1Y ROIC and/or interest coverage."""
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
        roic = (scores.get("roic") or {}).get("value")
        interest = (scores.get("interest_coverage") or {}).get("value")
        if roic is None or interest is None:
            missing.append({
                **row,
                "missing_roic": roic is None,
                "missing_interest": interest is None,
            })

    tickers = [r["ticker"] for r in missing]
    meta = {}
    for i in range(0, len(tickers), META_BATCH):
        batch = tickers[i:i + META_BATCH]
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


def concept_bucket(concept: str, label: str) -> str | None:
    text = f"{concept or ''} {label or ''}".lower()
    if any(x in text for x in TOTAL_LIABILITY_WORDS):
        return "total_liability_excluded"
    if any(x in text for x in LEASE_WORDS):
        if "operating lease" in text:
            return "operating_lease"
        return "lease_liability"
    if any(x in text for x in INTEREST_WORDS):
        if "income" in text and "expense" not in text and "cost" not in text:
            return "interest_income_or_other"
        if "net" in text or "income expense" in text:
            return "net_interest_or_interest_income_expense"
        return "interest_expense_or_financing_cost"
    if any(x in text for x in DEBT_WORDS):
        return "interest_bearing_debt_candidate"
    if any(x in text for x in OPERATING_LIABILITY_WORDS):
        return "operating_liability"
    return None


def relevant_companyfact_concepts(facts, latest_year):
    rows = []
    root = facts.get("facts") or {}
    for namespace, fact_map in root.items():
        if not isinstance(fact_map, dict):
            continue
        for concept, body in fact_map.items():
            if not isinstance(body, dict):
                continue
            label = body.get("label") or ""
            bucket = concept_bucket(concept, label)
            if bucket is None:
                continue

            latest = []
            for unit, unit_rows in (body.get("units") or {}).items():
                if not isinstance(unit_rows, list):
                    continue
                for r in unit_rows:
                    end = r.get("end")
                    if not end or not str(end).startswith(str(latest_year)):
                        continue
                    if r.get("form") not in base.SNAPSHOT_FORMS:
                        continue
                    value = r.get("val")
                    if value is None:
                        continue
                    latest.append({
                        "unit": unit,
                        "val": value,
                        "start": r.get("start"),
                        "end": end,
                        "fy": r.get("fy"),
                        "fp": r.get("fp"),
                        "form": r.get("form"),
                        "filed": r.get("filed"),
                    })
            rows.append({
                "namespace": namespace,
                "concept": concept,
                "label": label,
                "bucket": bucket,
                "has_latest_value": bool(latest),
                "latest_values": latest[-3:],
            })
    return rows


def classify_companyfacts(facts, latest_year, missing_roic, missing_interest):
    idx = base.build_fact_index(facts)
    detail = {}
    if missing_roic:
        vals = {m: base.latest_annual_value(idx, m, latest_year) for m in ROIC_KEYS}
        op = vals["operating_income"]
        eq = vals["equity"]
        debt = (
            vals["debt_current"] + vals["debt_noncurrent"]
            if vals["debt_current"] is not None and vals["debt_noncurrent"] is not None
            else vals["debt_total"]
        )
        cash = vals["cash"]
        if op is None:
            reason = "NO_OPERATING_INCOME"
        elif eq is None:
            reason = "NO_EQUITY"
        elif debt is None:
            reason = "NO_DEBT_FACT"
        else:
            invested = eq + debt - (cash or 0.0)
            reason = "NON_POSITIVE_INVESTED_CAPITAL" if invested <= 0 else "CALCULABLE_FROM_COMPANY_FACTS"
            vals["invested_capital"] = invested
        detail["roic"] = {"reason": reason, "inputs": vals}

    if missing_interest:
        op = base.latest_annual_value(idx, "operating_income", latest_year)
        interest = base.latest_annual_value(idx, "interest_expense", latest_year)
        if op is None and interest is None:
            reason = "NO_OPERATING_INCOME_AND_INTEREST_EXPENSE"
        elif op is None:
            reason = "NO_OPERATING_INCOME"
        elif interest is None:
            reason = "NO_INTEREST_EXPENSE_FACT"
        elif interest == 0:
            reason = "ZERO_INTEREST_EXPENSE"
        else:
            reason = "CALCULABLE_FROM_COMPANY_FACTS"
        detail["interest"] = {
            "reason": reason,
            "inputs": {"operating_income": op, "interest_expense": interest},
        }

    return idx, detail


def latest_flow_year(index):
    years = sorted(
        set(index.get("revenue", {}))
        | set(index.get("operating_income", {}))
        | set(index.get("net_income", {}))
    )
    return max(years) if years else None


def inline_interest_or_debt_rows(rows, target_year):
    out = []
    for r in rows:
        concept = r.get("concept") or ""
        label = r.get("label") or ""
        bucket = concept_bucket(concept, label)
        if bucket is None:
            continue
        end = r.get("end")
        if target_year is not None and end and str(end)[:4] != str(target_year):
            continue
        out.append({
            "namespace": r.get("namespace"),
            "concept": concept,
            "label": label,
            "bucket": bucket,
            "value": r.get("value"),
            "unit": r.get("unit"),
            "start": r.get("start"),
            "end": end,
            "fy": r.get("fy"),
            "form": r.get("form"),
            "filed": r.get("filed"),
            "instant": bool(r.get("instant")),
            "dimensioned": bool(r.get("dimensioned")),
            "contextRef": r.get("contextRef"),
        })
    return out


def choose_inline_sample(records, per_reason, max_total):
    groups = defaultdict(list)
    for rec in records:
        reasons = []
        if rec.get("missing_roic"):
            reasons.append(("roic", rec.get("roic_reason") or "UNKNOWN"))
        if rec.get("missing_interest"):
            reasons.append(("interest", rec.get("interest_reason") or "UNKNOWN"))
        for kind, reason in reasons:
            groups[(kind, reason)].append(rec)

    chosen = []
    for key in sorted(groups):
        rows = sorted(groups[key], key=lambda x: x["ticker"])
        chosen.extend(rows[:per_reason])

    # De-duplicate companies and cap total size while retaining group ordering.
    unique = []
    seen = set()
    for rec in chosen:
        if rec["ticker"] in seen:
            continue
        seen.add(rec["ticker"])
        unique.append(rec)
    return unique[:max_total]


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--inline-per-reason", type=int, default=DEFAULT_INLINE_PER_REASON)
    parser.add_argument("--inline-max", type=int, default=DEFAULT_INLINE_MAX)
    parser.add_argument("--output", default="us_roic_interest_forensics_full.json")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    targets = load_targets(sb)
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"})
    resolver = SECXBRLSearchV2_3_8(user_agent=SEC_USER_AGENT, session=session)

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "universe": "all eligible US_Fundamental rows missing 1Y ROIC and/or interest_coverage",
            "sector_restriction": None,
            "scoring_profile_restriction": None,
            "db_writes": False,
        },
        "summary": {},
        "company_census": [],
        "companyfact_concepts": {},
        "inline_sample": [],
        "inline_concepts": {},
        "resolver_candidates": {},
    }

    census_reason_counts = {"roic": Counter(), "interest": Counter()}
    concept_counts = {"roic": Counter(), "interest": Counter(), "all": Counter()}
    concept_examples = defaultdict(list)

    print(f"[FORENSICS] targets={len(targets)}", flush=True)

    for n, rec in enumerate(targets, 1):
        m = rec["_meta"]
        try:
            facts = sec_json(
                session,
                base.SEC_FACTS_URL.format(cik=str(m["cik"]).zfill(10)),
            )
            submissions = sec_json(
                session,
                base.SEC_SUBMISSIONS_URL.format(cik=str(m["cik"]).zfill(10)),
            )
            idx = base.build_fact_index(facts)
            year = latest_flow_year(idx)
            if year is None:
                rec["roic_reason"] = "NO_SEC_FLOW_YEAR" if rec["missing_roic"] else None
                rec["interest_reason"] = "NO_SEC_FLOW_YEAR" if rec["missing_interest"] else None
            else:
                _, detail = classify_companyfacts(
                    facts, year, rec["missing_roic"], rec["missing_interest"]
                )
                rec["base_year"] = year
                rec["roic_reason"] = (detail.get("roic") or {}).get("reason")
                rec["interest_reason"] = (detail.get("interest") or {}).get("reason")
                rec["roic_inputs"] = (detail.get("roic") or {}).get("inputs")
                rec["interest_inputs"] = (detail.get("interest") or {}).get("inputs")

                cf = relevant_companyfact_concepts(facts, year)
                rec["companyfact_relevant_concept_count"] = len(cf)
                for item in cf:
                    concept = f"{item['namespace']}:{item['concept']}"
                    concept_counts["all"][concept] += 1
                    if rec["missing_roic"]:
                        concept_counts["roic"][concept] += int(item["has_latest_value"])
                    if rec["missing_interest"]:
                        concept_counts["interest"][concept] += int(item["has_latest_value"])
                    if item["has_latest_value"] and len(concept_examples[concept]) < 3:
                        concept_examples[concept].append({
                            "ticker": rec["ticker"],
                            "year": year,
                            "bucket": item["bucket"],
                            "label": item["label"],
                            "latest_values": item["latest_values"],
                        })

            census = {
                "ticker": rec["ticker"],
                "cik": m["cik"],
                "company_name": m.get("company_name"),
                "sector": m.get("sector_common"),
                "company_type": m.get("company_type"),
                "scoring_profile": m.get("scoring_profile"),
                "base_year": rec.get("base_year"),
                "missing_roic": rec["missing_roic"],
                "missing_interest": rec["missing_interest"],
                "roic_reason": rec.get("roic_reason"),
                "interest_reason": rec.get("interest_reason"),
                "roic_inputs": rec.get("roic_inputs"),
                "interest_inputs": rec.get("interest_inputs"),
                "companyfact_relevant_concept_count": rec.get("companyfact_relevant_concept_count", 0),
            }
            result["company_census"].append(census)

            if rec.get("missing_roic"):
                census_reason_counts["roic"][rec.get("roic_reason") or "UNKNOWN"] += 1
            if rec.get("missing_interest"):
                census_reason_counts["interest"][rec.get("interest_reason") or "UNKNOWN"] += 1

            if n % 100 == 0:
                print(
                    f"[CENSUS] {n}/{len(targets)} "
                    f"ROIC={sum(census_reason_counts['roic'].values())} "
                    f"Interest={sum(census_reason_counts['interest'].values())}",
                    flush=True,
                )
        except Exception as exc:
            result["company_census"].append({
                "ticker": rec["ticker"],
                "cik": m["cik"],
                "company_name": m.get("company_name"),
                "sector": m.get("sector_common"),
                "scoring_profile": m.get("scoring_profile"),
                "missing_roic": rec["missing_roic"],
                "missing_interest": rec["missing_interest"],
                "roic_reason": "FETCH_OR_PARSE_ERROR" if rec["missing_roic"] else None,
                "interest_reason": "FETCH_OR_PARSE_ERROR" if rec["missing_interest"] else None,
                "error": str(exc),
            })

    # Compact aggregate concept map.
    for concept, count in concept_counts["all"].most_common():
        result["companyfact_concepts"][concept] = {
            "observed_in_targets": count,
            "roic_latest_value_count": concept_counts["roic"].get(concept, 0),
            "interest_latest_value_count": concept_counts["interest"].get(concept, 0),
            "examples": concept_examples.get(concept, []),
        }

    # Stratified inline inspection across the entire census, not just Standard.
    sample_source = []
    census_by_ticker = {r["ticker"]: r for r in result["company_census"]}
    for rec in targets:
        c = census_by_ticker.get(rec["ticker"])
        if c and (c.get("roic_reason") or c.get("interest_reason")):
            sample_source.append({**rec, **c, "_meta": rec["_meta"]})

    inline_sample = choose_inline_sample(
        sample_source,
        per_reason=max(0, args.inline_per_reason),
        max_total=max(0, args.inline_max),
    )
    print(f"[INLINE] sample={len(inline_sample)}", flush=True)

    inline_bucket_counts = Counter()
    inline_concept_counts = Counter()
    inline_examples = defaultdict(list)
    resolver_counts = Counter()

    for i, rec in enumerate(inline_sample, 1):
        m = rec["_meta"]
        item = {
            "ticker": rec["ticker"],
            "cik": m["cik"],
            "company_name": m.get("company_name"),
            "sector": m.get("sector_common"),
            "company_type": m.get("company_type"),
            "scoring_profile": m.get("scoring_profile"),
            "base_year": rec.get("base_year"),
            "roic_reason": rec.get("roic_reason"),
            "interest_reason": rec.get("interest_reason"),
            "relevant_inline_concepts": [],
            "resolver": {},
        }
        try:
            facts = sec_json(session, base.SEC_FACTS_URL.format(cik=str(m["cik"]).zfill(10)))
            submissions = sec_json(session, base.SEC_SUBMISSIONS_URL.format(cik=str(m["cik"]).zfill(10)))
            idx = base.build_fact_index(facts)
            year = rec.get("base_year") or latest_flow_year(idx)
            resolver.prime_company(m["cik"], facts, submissions)
            rows, inline_meta = resolver._inline_filing_rows(m["cik"], submissions)
            relevant = inline_interest_or_debt_rows(rows, year)
            item["relevant_inline_concepts"] = relevant

            for r in relevant:
                key = f"{r.get('namespace')}:{r.get('concept')}"
                inline_concept_counts[key] += 1
                inline_bucket_counts[r["bucket"]] += 1
                if len(inline_examples[key]) < 3:
                    inline_examples[key].append({
                        "ticker": rec["ticker"],
                        "year": year,
                        "bucket": r["bucket"],
                        "label": r["label"],
                        "value": r["value"],
                        "unit": r["unit"],
                        "form": r["form"],
                        "filed": r["filed"],
                        "instant": r["instant"],
                        "dimensioned": r["dimensioned"],
                    })

            needed = []
            if rec.get("missing_roic"):
                needed.append("roic")
            if rec.get("missing_interest"):
                needed.append("interest_expense")
            for metric in needed:
                try:
                    rr = resolver.resolve(m["cik"], metric, year=year, limit=5)
                    best = rr.get("best") if isinstance(rr, dict) else None
                    if best:
                        item["resolver"][metric] = {
                            "best_concept": best.get("concept"),
                            "value": best.get("value"),
                            "source": best.get("source"),
                            "reason": best.get("reason"),
                            "form": best.get("form"),
                            "end": best.get("end"),
                        }
                        resolver_counts[f"{metric}:FOUND"] += 1
                    else:
                        item["resolver"][metric] = {"best_concept": None}
                        resolver_counts[f"{metric}:NOT_FOUND"] += 1
                except Exception as exc:
                    item["resolver"][metric] = {"error": str(exc)}
                    resolver_counts[f"{metric}:ERROR"] += 1

            item["inline_meta"] = {
                "annual_accession": inline_meta.get("accession"),
                "document": inline_meta.get("document"),
                "filed": inline_meta.get("filed"),
            }
        except Exception as exc:
            item["error"] = str(exc)

        result["inline_sample"].append(item)
        if i % 25 == 0:
            print(f"[INLINE] {i}/{len(inline_sample)}", flush=True)

    for key, count in inline_concept_counts.most_common():
        result["inline_concepts"][key] = {
            "observed_in_sample": count,
            "bucket": next(
                (
                    x["bucket"]
                    for rec in result["inline_sample"]
                    for x in rec.get("relevant_inline_concepts", [])
                    if f"{x.get('namespace')}:{x.get('concept')}" == key
                ),
                None,
            ),
            "examples": inline_examples.get(key, []),
        }

    result["summary"] = {
        "total_missing_rows": len(targets),
        "roic_missing_rows": sum(1 for r in targets if r["missing_roic"]),
        "interest_missing_rows": sum(1 for r in targets if r["missing_interest"]),
        "both_missing_rows": sum(1 for r in targets if r["missing_roic"] and r["missing_interest"]),
        "roic_reason_counts": dict(census_reason_counts["roic"]),
        "interest_reason_counts": dict(census_reason_counts["interest"]),
        "inline_sample_size": len(inline_sample),
        "inline_bucket_counts": dict(inline_bucket_counts),
        "resolver_counts": dict(resolver_counts),
        "top_companyfact_concepts": result["companyfact_concepts"] and list(
            result["companyfact_concepts"].items()
        )[:40],
        "top_inline_concepts": list(result["inline_concepts"].items())[:40],
    }

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    # Small human-readable companion report.
    report_path = os.path.splitext(args.output)[0] + ".txt"
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("US ROIC / INTEREST FULL-UNIVERSE FORENSICS\n")
        fh.write("=" * 72 + "\n")
        fh.write(f"generated_at: {result['generated_at']}\n")
        fh.write(f"total_missing_rows: {len(targets)}\n")
        fh.write(f"roic_missing_rows: {result['summary']['roic_missing_rows']}\n")
        fh.write(f"interest_missing_rows: {result['summary']['interest_missing_rows']}\n")
        fh.write(f"both_missing_rows: {result['summary']['both_missing_rows']}\n\n")
        fh.write("ROIC reason counts\n")
        for k, v in sorted(census_reason_counts["roic"].items(), key=lambda x: (-x[1], x[0])):
            fh.write(f"  {k}: {v}\n")
        fh.write("\nInterest reason counts\n")
        for k, v in sorted(census_reason_counts["interest"].items(), key=lambda x: (-x[1], x[0])):
            fh.write(f"  {k}: {v}\n")
        fh.write("\nInline buckets\n")
        for k, v in inline_bucket_counts.most_common():
            fh.write(f"  {k}: {v}\n")
        fh.write("\nTop inline concepts\n")
        for k, v in inline_concept_counts.most_common(50):
            fh.write(f"  {k}: {v}\n")

    print(
        f"[DONE] census={len(targets)} inline={len(inline_sample)} "
        f"wrote={args.output} report={report_path}",
        flush=True,
    )


if __name__ == "__main__":
    run()
