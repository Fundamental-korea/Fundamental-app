"""Backfill persisted US market valuation snapshots.

Unlike the older Standard-only valuation runner, this runner applies the same
SEC/yfinance valuation helper to every usable eligible US_Fundamental row whose
snapshot exists and whose valuation block is missing or incomplete.
Scores and fundamental metrics are preserved.
"""
from __future__ import annotations

import argparse
import os
import time

from supabase import create_client

from collector_us_fundamental import SUPABASE_KEY, SUPABASE_URL
from collector_us_valuation_only import collect_valuation_one, is_safe_reported_eps_label

PAGE_SIZE = 100


CORE_VALUATION_KEYS = (
    "price",
    "eps",
    "bps",
    "current_shares_outstanding",
    "period_end_shares_outstanding",
)


def _eps_source_looks_safe(valuation):
    source = str(valuation.get("eps_source") or "").strip()
    basis = str(valuation.get("eps_basis") or "").strip().lower()
    if not source or valuation.get("eps") is None:
        return False
    if source in {"EarningsPerShareDiluted", "EarningsPerShareBasic"}:
        return True
    if source == "filing:xbrl-reported-eps":
        return is_safe_reported_eps_label(valuation.get("eps_report_label"))
    if not basis.startswith("reported-"):
        return False
    lower = source.lower()
    return not any(
        token in lower
        for token in (
            "weightedaverage",
            "sharesoutstanding",
            "antidilutive",
            "dilutivesecurities",
            "adjustmentstoreconcile",
            "redemptionpremium",
            "financingcost",
            "proformaweightedaverage",
        )
    )


def _valuation_core_complete(valuation):
    if not isinstance(valuation, dict):
        return False
    for key in CORE_VALUATION_KEYS:
        if key == "bps" and valuation.get("bps_status") == "not-applicable-preferred-security":
            continue
        if valuation.get(key) is None:
            return False
    if not _eps_source_looks_safe(valuation):
        return False
    try:
        current_shares = float(valuation.get("current_shares_outstanding"))
        period_shares = float(valuation.get("period_end_shares_outstanding"))
    except (TypeError, ValueError):
        return False
    return current_shares > 0 and period_shares > 0


def load_targets(sb, start_after=""):

    rows = []
    offset = 0
    while True:
        page = (
            sb.table("US_Fundamental")
            .select("ticker,cik,company_name,snapshot,data_unavailable")
            .eq("data_unavailable", False)
            .not_.is_("snapshot", "null")
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        if not page:
            break
        for row in page:
            snapshot = row.get("snapshot") or {}
            if snapshot.get("fiscal_end"):
                valuation = snapshot.get("valuation")
                # Rebuild when the block is absent or any core valuation output
                # is missing. This lets failed/incomplete prior rows self-heal.
                if not _valuation_core_complete(valuation):
                    ticker = str(row.get("ticker") or "").strip()
                    if start_after and ticker <= start_after:
                        continue
                    rows.append(row)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0.15)
    parser.add_argument(
        "--start-after",
        default="",
        help="Resume after this ticker (exclusive). Use the last processed ticker from the prior batch.",
    )
    args = parser.parse_args()

    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows = load_targets(sb, start_after=args.start_after.strip().upper())
    if args.limit:
        rows = rows[:max(0, args.limit)]

    print(
        f"[US VALUATION] targets={len(rows)} start_after={args.start_after.strip().upper() or '-'}",
        flush=True,
    )

    import requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": os.environ.get(
            "SEC_USER_AGENT",
            "Fundamental-app contact@example.com",
        ),
        "Accept-Encoding": "gzip, deflate",
    })

    success = failed = 0
    for i, row in enumerate(rows, 1):
        try:
            # collect_valuation_one preserves the supplied snapshot and writes
            # valuation/market blocks only.
            snapshot, reason = collect_valuation_one(
                session,
                {
                    "ticker": row["ticker"],
                    "cik": row["cik"],
                    "company_name": row.get("company_name") or row["ticker"],
                    "snapshot": row["snapshot"],
                },
            )
            if snapshot is None:
                print(
                    f"[{i}/{len(rows)}] {row['ticker']}: SKIPPED {reason}",
                    flush=True,
                )
                continue

            (
                sb.table("US_Fundamental")
                .update({"snapshot": snapshot})
                .eq("ticker", row["ticker"])
                .execute()
            )
            valuation = snapshot.get("valuation") or {}
            print(
                f"[{i}/{len(rows)}] {row['ticker']}: "
                f"price={valuation.get('price')} "
                f"EPS={valuation.get('eps')} "
                f"BPS={valuation.get('bps')} "
                f"PER={valuation.get('per')} "
                f"PBR={valuation.get('pbr')} "
                f"shares={valuation.get('current_shares_outstanding')}",
                flush=True,
            )
            success += 1
        except Exception as exc:
            failed += 1
            print(
                f"[{i}/{len(rows)}] {row['ticker']}: FAILED {exc}",
                flush=True,
            )

        if args.sleep > 0:
            time.sleep(args.sleep)

    print(
        f"[US VALUATION] Completed targets={len(rows)} "
        f"success={success} failed={failed}",
        flush=True,
    )


if __name__ == "__main__":
    main()
