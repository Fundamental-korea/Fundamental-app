"""Targeted SEC/raw recovery for current 1-3 missing US score metrics.

Only companies with exactly one missing upstream raw prerequisite for at least
one active 1y metric are selected. The current Annual raw layer is the source
of truth for determining the gap; SEC annual Inline-XBRL is used only to recover
the missing source values.

Existing Annual raw values are never overwritten. After a raw recovery, the
existing deterministic scorer is run in-memory for the same ticker so only newly
computable score metrics are added.
"""

from __future__ import annotations

import argparse
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from supabase import create_client

import collector_us_deterministic_recovery as deterministic
from collector_us_gt1b_filing_recovery import (
    annual_filing_candidates,
    build_rows,
    fetch_filing_rows,
)
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "Fundamental-app contact@example.com"
)

PAGE_SIZE = 500
TICKER_BATCH = 100
DB_UPSERT_BATCH = 20

PROFILE_TARGET_METRICS = {
    "standard": {
        "opm", "roic", "debt_rate", "ocf_ratio", "sga_ratio",
        "quick_ratio", "interest_coverage", "eps_growth", "revenue_growth",
    },
    "defense": {
        "opm", "roic", "debt_rate", "ocf_ratio", "sga_ratio",
        "quick_ratio", "interest_coverage", "eps_growth", "revenue_growth",
    },
    "financial": {
        "roa", "eps_growth", "revenue_growth",
    },
    "reit": {
        "roa", "debt_rate", "ocf_ratio", "interest_coverage",
        "eps_growth", "revenue_growth",
    },
    "bdc": {
        "roa", "debt_rate", "ocf_ratio", "interest_coverage",
        "eps_growth",
    },
}


def fetch_fundamentals(sb) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    columns = (
        "ticker,cik,company_name,base_year,total_score,grade,data_unavailable,"
        "data_reliability,missing_metric_count,period_scores"
    )
    while True:
        page = (
            sb.table("US_Fundamental")
            .select(columns)
            .eq("data_unavailable", False)
            .gte("missing_metric_count", 1)
            .lte("missing_metric_count", 3)
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def fetch_company_meta(sb) -> dict[str, dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = (
            sb.table("US_Companies")
            .select("ticker,scoring_profile,company_type,exchange,is_active")
            .eq("is_fundamental_eligible", True)
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return {r["ticker"]: r for r in rows}


def fetch_annual(sb, tickers: list[str]) -> dict[str, list[dict[str, Any]]]:
    annual: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for start in range(0, len(tickers), TICKER_BATCH):
        batch = tickers[start:start + TICKER_BATCH]
        offset = 0
        while True:
            page = (
                sb.table("US_Fundamental_Annual")
                .select(
                    "ticker,fiscal_year,period_end,filed,form,fiscal_period,"
                    "source_kind,source_accession,source_document,canonical,"
                    "provenance,completeness,recovery_status,recovery_notes,updated_at"
                )
                .in_("ticker", batch)
                .order("ticker")
                .order("fiscal_year")
                .range(offset, offset + PAGE_SIZE - 1)
                .execute()
                .data
                or []
            )
            for row in page:
                annual[row["ticker"]].append(row)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        print(
            f"[TARGETED] annual_fetch="
            f"{min(start + TICKER_BATCH, len(tickers))}/{len(tickers)}",
            flush=True,
        )
    return dict(annual)


def present(canonical: dict[str, Any], field: str) -> bool:
    return canonical.get(field) is not None


def debt_present(canonical: dict[str, Any]) -> bool:
    if canonical.get("debt_total") is not None:
        return True
    if canonical.get("debt") is not None:
        return True
    return (
        canonical.get("debt_current") is not None
        and canonical.get("debt_noncurrent") is not None
    )


def sga_present(canonical: dict[str, Any]) -> bool:
    return (
        canonical.get("sga") is not None
        or canonical.get("general_and_administrative_expense") is not None
        or canonical.get("selling_expense") is not None
    )


def eps_present(canonical: dict[str, Any]) -> bool:
    if canonical.get("eps") is not None:
        return True
    ni = canonical.get("net_income")
    shares = (
        canonical.get("weighted_avg_diluted_shares")
        or canonical.get("weighted_avg_basic_shares")
    )
    return ni is not None and shares not in (None, 0)


def quick_ratio_present(canonical: dict[str, Any]) -> bool:
    current_liabilities = canonical.get("current_liabilities")
    if current_liabilities is None:
        return False
    current_assets = canonical.get("current_assets")
    if current_assets is not None:
        return True
    cash = canonical.get("cash")
    receivables = canonical.get("receivables")
    return cash is not None or receivables is not None


def interest_present(canonical: dict[str, Any]) -> bool:
    return (
        canonical.get("interest_expense") is not None
        or canonical.get("interest_expense_net") is not None
    )


def missing_prerequisites(
    metric: str,
    current: dict[str, Any],
    previous: dict[str, Any],
) -> list[str]:
    # These prerequisites intentionally mirror collector_us_fundamental.annual_metrics()
    # and the deterministic scorer's canonical_to_index() derivations.
    checks = {
        "opm": (
            ("revenue", present(current, "revenue")),
            ("operating_income", present(current, "operating_income")),
        ),
        "roic": (
            ("operating_income", present(current, "operating_income")),
            ("cash", present(current, "cash")),
            ("equity", present(current, "equity")),
            ("debt", debt_present(current)),
        ),
        "debt_rate": (
            ("liabilities", present(current, "liabilities")),
            ("equity", present(current, "equity")),
        ),
        "ocf_ratio": (
            ("operating_cash_flow", present(current, "operating_cash_flow")),
            ("net_income", present(current, "net_income")),
        ),
        "sga_ratio": (
            ("sga", sga_present(current)),
            ("revenue", present(current, "revenue")),
        ),
        "quick_ratio": (
            ("quick_assets", quick_ratio_present(current)),
        ),
        "interest_coverage": (
            ("operating_income", present(current, "operating_income")),
            ("interest_expense", interest_present(current)),
        ),
        "eps_growth": (
            ("eps_current", eps_present(current)),
            ("eps_previous", eps_present(previous)),
        ),
        "revenue_growth": (
            ("revenue_current", present(current, "revenue")),
            ("revenue_previous", present(previous, "revenue")),
        ),
        "roa": (
            ("net_income", present(current, "net_income")),
            ("assets", present(current, "assets")),
        ),
    }
    return [field for field, ok in checks.get(metric, ()) if not ok]


def size_proxy(canonical: dict[str, Any]) -> float:
    values = []
    for field in (
        "assets",
        "equity",
        "revenue",
        "net_income",
        "operating_cash_flow",
    ):
        value = canonical.get(field)
        try:
            number = abs(float(value))
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number)
    return max(values, default=0.0)


def build_target_queue(
    fundamentals: list[dict[str, Any]],
    meta: dict[str, dict[str, Any]],
    annual: dict[str, list[dict[str, Any]]],
    limit: int,
) -> list[dict[str, Any]]:
    queue_by_ticker: dict[str, dict[str, Any]] = {}

    for row in fundamentals:
        ticker = row.get("ticker")
        m = meta.get(ticker)
        if not ticker or not m:
            continue

        profile = (m.get("scoring_profile") or "standard").lower()
        if profile not in deterministic.PROFILES:
            continue

        avg = (
            (((row.get("period_scores") or {}).get("1y") or {}).get("avg") or {})
            .get("metric_scores")
            or {}
        )
        base_year = row.get("base_year")
        try:
            base_year = int(base_year)
        except (TypeError, ValueError):
            continue

        by_year = {
            int(r["fiscal_year"]): r
            for r in (annual.get(ticker) or [])
            if r.get("fiscal_year") is not None
        }
        current = (by_year.get(base_year) or {}).get("canonical") or {}
        previous = (by_year.get(base_year - 1) or {}).get("canonical") or {}

        recoverable_metrics: dict[str, str] = {}
        active_metrics = PROFILE_TARGET_METRICS.get(profile, set())
        for metric in active_metrics:
            entry = avg.get(metric) or {}
            if entry.get("value") is not None:
                continue
            missing = missing_prerequisites(metric, current, previous)
            if len(missing) == 1:
                recoverable_metrics[metric] = missing[0]

        if not recoverable_metrics:
            continue

        ticker_size = size_proxy(current)
        existing = queue_by_ticker.get(ticker)
        if existing is None:
            queue_by_ticker[ticker] = {
                "ticker": ticker,
                "cik": row.get("cik"),
                "company_name": row.get("company_name"),
                "base_year": base_year,
                "scoring_profile": profile,
                "company_type": m.get("company_type"),
                "exchange": m.get("exchange"),
                "missing_metric_count": row.get("missing_metric_count"),
                "total_score": row.get("total_score"),
                "size_proxy": ticker_size,
                "targets": recoverable_metrics,
            }
        else:
            existing["targets"].update(recoverable_metrics)

    queue = list(queue_by_ticker.values())
    queue.sort(
        key=lambda x: (
            int(x.get("missing_metric_count") or 99),
            -float(x.get("size_proxy") or 0.0),
            -float(x.get("total_score") or 0.0),
            x["ticker"],
        )
    )
    return queue[:limit]


def merge_filing_rows(
    existing_rows: list[dict[str, Any]],
    filing_rows: list[dict[str, Any]],
    targets: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_year = {
        int(row["fiscal_year"]): dict(row)
        for row in existing_rows
        if row.get("fiscal_year") is not None
    }
    changed_rows: list[dict[str, Any]] = []

    for src in filing_rows:
        year = int(src["fiscal_year"])
        old = by_year.get(year)

        if old is None:
            merged = dict(src)
            merged["recovery_status"] = "targeted_filing_recovery"
            merged["recovery_notes"] = {
                **(merged.get("recovery_notes") or {}),
                "target_metrics": sorted(targets),
                "method": "missing-source-only-merge",
            }
            changed_rows.append(merged)
            by_year[year] = merged
            continue

        old_can = dict(old.get("canonical") or {})
        old_prov = dict(old.get("provenance") or {})
        new_can = src.get("canonical") or {}
        new_prov = src.get("provenance") or {}
        changed = False

        for field, value in new_can.items():
            if value is None:
                continue
            if old_can.get(field) is None:
                old_can[field] = value
                if new_prov.get(field) is not None:
                    old_prov[field] = new_prov[field]
                changed = True

        if not changed:
            continue

        old["canonical"] = old_can
        old["provenance"] = old_prov
        old["completeness"] = dict(
            src.get("completeness") or old.get("completeness") or {}
        )
        old["recovery_status"] = "targeted_filing_recovery"
        old["recovery_notes"] = {
            **(old.get("recovery_notes") or {}),
            "target_metrics": sorted(targets),
            "source_accession": src.get("source_accession"),
            "source_document": src.get("source_document"),
            "method": "missing-source-only-merge",
        }
        old["updated_at"] = datetime.now(timezone.utc).isoformat()
        changed_rows.append(old)

    merged_rows = sorted(
        by_year.values(), key=lambda r: int(r["fiscal_year"])
    )
    return merged_rows, changed_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--out-dir", default="artifacts/us_targeted_raw_recovery"
    )
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    fundamentals = fetch_fundamentals(sb)
    meta = fetch_company_meta(sb)

    candidate_tickers = [
        r["ticker"] for r in fundamentals if r.get("ticker") in meta
    ]
    annual = fetch_annual(sb, candidate_tickers)

    full_queue = build_target_queue(
        fundamentals, meta, annual, limit=1_000_000
    )
    queue = full_queue[args.offset:args.offset + args.limit]

    print(
        f"[TARGETED] current_1_3_rows={len(fundamentals)} "
        f"candidate_queue={len(full_queue)} selected={len(queue)} "
        f"offset={args.offset}",
        flush=True,
    )

    if not queue:
        print("[TARGETED] no target companies remain")
        return

    old_by_ticker = {r["ticker"]: r for r in fundamentals}
    annual_by_ticker = {t: list(annual.get(t) or []) for t in old_by_ticker}

    resolver = SECXBRLSearchV2_3_8(user_agent=SEC_USER_AGENT)

    recovered: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for i, company in enumerate(queue, 1):
        ticker = company["ticker"]
        cik = str(company.get("cik") or "").strip()
        targets = set(company["targets"].keys())

        try:
            submissions = resolver.submissions(cik)
            candidates = annual_filing_candidates(
                resolver, submissions, max_candidates=8
            )
            if not candidates:
                raise RuntimeError("no annual SEC filing candidate")

            accepted = False
            attempts = []

            for candidate in candidates:
                try:
                    filing_rows, filing_meta = fetch_filing_rows(
                        resolver, cik, candidate
                    )
                except Exception as exc:
                    attempts.append({
                        "accession": candidate.get("accession"),
                        "form": candidate.get("form"),
                        "filed": candidate.get("filed"),
                        "error": f"{type(exc).__name__}:{exc}",
                    })
                    continue

                if not filing_rows:
                    attempts.append({
                        "accession": candidate.get("accession"),
                        "form": candidate.get("form"),
                        "filed": candidate.get("filed"),
                        "error": "no_xbrl_rows",
                    })
                    continue

                builder_company = {
                    "ticker": ticker,
                    "cik": cik,
                    "company_name": company.get("company_name"),
                }
                built = build_rows(
                    builder_company, submissions, filing_rows, filing_meta
                )
                merged_rows, changed_rows = merge_filing_rows(
                    annual_by_ticker.get(ticker) or [],
                    built,
                    targets,
                )

                old_fundamental = old_by_ticker.get(ticker)
                if old_fundamental is None:
                    raise RuntimeError(
                        "fundamental row disappeared from initial snapshot"
                    )

                profile = (
                    meta[ticker].get("scoring_profile") or "standard"
                ).lower()
                score_result = deterministic.recover_one(
                    sb,
                    old_fundamental,
                    merged_rows,
                    profile,
                )

                old_avg = (
                    (((old_fundamental.get("period_scores") or {}).get("1y") or {})
                     .get("avg") or {})
                    .get("metric_scores")
                    or {}
                )
                new_avg = (
                    (((score_result or {}).get("period_scores") or {}).get("1y") or {})
                    .get("avg") or {}
                )
                new_metric_scores = new_avg.get("metric_scores") or {}
                newly_available_targets = sorted(
                    metric
                    for metric in targets
                    if (old_avg.get(metric) or {}).get("value") is None
                    and (new_metric_scores.get(metric) or {}).get("value") is not None
                )

                if not newly_available_targets:
                    if changed_rows:
                        for start in range(0, len(changed_rows), DB_UPSERT_BATCH):
                            sb.table("US_Fundamental_Annual").upsert(
                                changed_rows[start:start + DB_UPSERT_BATCH],
                                on_conflict="ticker,fiscal_year",
                            ).execute()
                        annual_by_ticker[ticker] = merged_rows
                    attempts.append({
                        "accession": candidate.get("accession"),
                        "form": candidate.get("form"),
                        "filed": candidate.get("filed"),
                        "error": "filing_parsed_but_target_metric_still_unavailable",
                        "targets": sorted(targets),
                    })
                    continue

                for start in range(0, len(changed_rows), DB_UPSERT_BATCH):
                    sb.table("US_Fundamental_Annual").upsert(
                        changed_rows[start:start + DB_UPSERT_BATCH],
                        on_conflict="ticker,fiscal_year",
                    ).execute()

                score_result["filing_recovery"] = {
                    **(old_fundamental.get("filing_recovery") or {}),
                    "targeted_raw_recovery": {
                        "source_kind": "sec_annual_inline_xbrl",
                        "accession": filing_meta.get("accession"),
                        "document": filing_meta.get("primary_document"),
                        "target_metrics": sorted(targets),
                        "newly_available_metrics": newly_available_targets,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                }
                sb.table("US_Fundamental").upsert(
                    score_result, on_conflict="ticker"
                ).execute()

                annual_by_ticker[ticker] = merged_rows
                recovered.append({
                    "ticker": ticker,
                    "targets": sorted(targets),
                    "newly_available_metrics": newly_available_targets,
                    "changed_annual_rows": len(changed_rows),
                    "score_updated": True,
                    "accession": filing_meta.get("accession"),
                    "form": filing_meta.get("form"),
                    "parser": filing_meta.get("reason"),
                })
                accepted = True
                break

            if not accepted:
                unchanged.append({
                    "ticker": ticker,
                    "targets": sorted(targets),
                    "reason": "no_target_source_recovered",
                    "attempts": attempts,
                })

        except Exception as exc:
            failures.append({
                "ticker": ticker,
                "targets": sorted(targets),
                "error": f"{type(exc).__name__}:{exc}",
            })

        if i % 25 == 0 or i == len(queue):
            print(
                f"[TARGETED] progress={i}/{len(queue)} "
                f"recovered={len(recovered)} unchanged={len(unchanged)} "
                f"failed={len(failures)}",
                flush=True,
            )

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    import json

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "current_1_3_rows": len(fundamentals),
        "candidate_queue_size": len(full_queue),
        "offset": args.offset,
        "limit": args.limit,
        "selected": len(queue),
        "recovered": len(recovered),
        "unchanged": len(unchanged),
        "failures": len(failures),
        "recovered_detail": recovered,
        "unchanged_detail": unchanged,
        "failure_detail": failures,
    }

    with open(
        os.path.join(out_dir, "us_targeted_raw_recovery_result.json"),
        "w",
        encoding="utf-8",
    ) as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)

    print(
        json.dumps(
            {
                k: result[k]
                for k in (
                    "current_1_3_rows",
                    "candidate_queue_size",
                    "offset",
                    "limit",
                    "selected",
                    "recovered",
                    "unchanged",
                    "failures",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
