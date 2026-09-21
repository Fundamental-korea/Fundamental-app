"""Final read-only validation for the US fundamental pipeline."""
from __future__ import annotations

import os

from supabase import create_client

URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")


def count_query(sb, query):
    return sb.rpc("exec_sql", {"query": query}).execute()


def main():
    if not KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(URL, KEY)

    # Supabase does not expose arbitrary SQL over REST by default, so use
    # ordinary table queries for deterministic checks.
    for n in range(0, 11):
        q = (
            sb.table("US_Fundamental")
            .select("ticker", count="exact", head=True)
            .eq("data_unavailable", False)
            .eq("missing_metric_count", n)
            .execute()
        )
        print(f"missing={n}: {q.count}")

    unavailable = (
        sb.table("US_Fundamental")
        .select("ticker", count="exact", head=True)
        .eq("data_unavailable", True)
        .execute()
    )
    print(f"data_unavailable=true: {unavailable.count}")

    # CIK persistence is mandatory for future SEC recovery.
    missing_cik = (
        sb.table("US_Fundamental")
        .select("ticker", count="exact", head=True)
        .is_("cik", "null")
        .eq("data_unavailable", False)
        .execute()
    )
    print(f"usable rows missing CIK: {missing_cik.count}")

    # Snapshot timestamp/period coverage.
    missing_snapshot = (
        sb.table("US_Fundamental")
        .select("ticker", count="exact", head=True)
        .eq("data_unavailable", False)
        .is_("snapshot_fiscal_end", "null")
        .execute()
    )
    print(f"usable rows missing snapshot_fiscal_end: {missing_snapshot.count}")

    # Representative, human-auditable rows.
    sample = ["AAPL", "MSFT", "NVDA", "JPM", "O", "RTX", "GSBD", "ARCC"]
    rows = (
        sb.table("US_Fundamental")
        .select("ticker,total_score,grade,missing_metric_count,data_reliability,snapshot_fiscal_end,snapshot_form,period_scores")
        .in_("ticker", sample)
        .execute()
        .data
        or []
    )
    by_ticker = {row["ticker"]: row for row in rows}
    for ticker in sample:
        row = by_ticker.get(ticker)
        if not row:
            print(f"sample {ticker}: NOT FOUND")
            continue
        avg = (
            ((row.get("period_scores") or {}).get("1y") or {})
            .get("avg") or {}
        )
        metrics = avg.get("metric_scores") or {}
        print(
            f"sample {ticker}: "
            f"score={row.get('total_score')} "
            f"grade={row.get('grade')} "
            f"missing={row.get('missing_metric_count')} "
            f"reliability={row.get('data_reliability')} "
            f"roic={(metrics.get('roic') or {}).get('value')} "
            f"interest_coverage={(metrics.get('interest_coverage') or {}).get('value')} "
            f"snapshot={row.get('snapshot_fiscal_end')} "
            f"form={row.get('snapshot_form')}"
        )

    print("[VALIDATION] completed read-only checks.")


if __name__ == "__main__":
    main()
