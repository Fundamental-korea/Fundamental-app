"""Top-10 market-cap DART + KRX validation.

Selects the 10 largest Korean stocks from the current KRX snapshot, then
excludes Samsung Electronics (005930) and SK hynix (000660), which were already
validated. The remaining rank-3..10 names are refreshed from DART and overlaid
with the KRX market snapshot.

This is a live DB test for only eight stocks. It must never be expanded to the
full universe here.
"""

from kor_market_pipeline import _recalculate_valuation, fetch_market_snapshot_map
import collector


EXCLUDE_CODES = {"005930", "000660"}

# KRX는 우선주를 별도 증권으로 취급하지만, DART 재무제표는 발행회사(보통주)의
# 재무정보를 기준으로 조회해야 하는 경우가 있다. 예: 삼성전자우(005935) -> 삼성전자(005930).
# DART 공식 고유번호 목록도 상장회사 종목코드를 회사 식별자와 별도로 제공하므로,
# KRX security code를 그대로 DART issuer code로 가정하지 않는다.
PREFERRED_SUFFIXES = ("2우B", "1우", "2우", "3우", "우B", "우C", "우")


def _load_all_company_rows():
    rows = []
    start = 0
    page_size = 1000
    while True:
        res = (
            collector.supabase.table("Fundamental")
            .select("stock_code,stock_name,sector,wics_sector,holding_company,period_scores")
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return rows


def _resolve_dart_financial_base_code(code, name, company_rows):
    """Return the issuer/common-stock code used for DART financials, if this KRX
    security appears to be a preferred share. Return None when no mapping is needed."""
    name = str(name or "").strip()
    base_name = None
    for suffix in PREFERRED_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            base_name = name[: -len(suffix)].strip()
            break
    if not base_name:
        return None

    for candidate in company_rows:
        if str(candidate.get("stock_name") or "").strip() == base_name:
            candidate_code = str(candidate.get("stock_code") or "").zfill(6)
            if candidate_code and candidate_code != code:
                return candidate_code
    return None


def _load_row(code):
    res = (
        collector.supabase.table("Fundamental")
        .select("stock_code,stock_name,sector,wics_sector,holding_company,period_scores")
        .eq("stock_code", code)
        .limit(1)
        .execute()
    )
    return (res.data or [None])[0]


if __name__ == "__main__":
    snapshots = fetch_market_snapshot_map()
    if not snapshots:
        raise SystemExit("KRX snapshot cache is empty; refusing to run live financial test.")

    ranked = sorted(
        (
            (code, snap)
            for code, snap in snapshots.items()
            if snap.get("market_cap") is not None
        ),
        key=lambda item: item[1]["market_cap"],
        reverse=True,
    )

    top10 = ranked[:10]
    targets = [(rank, code, snap) for rank, (code, snap) in enumerate(top10, start=1)
               if code not in EXCLUDE_CODES]

    if len(targets) != 8:
        raise SystemExit(
            f"Expected 8 test targets after excluding Samsung Electronics and SK hynix; "
            f"got {len(targets)}."
        )

    print("\n=== KRX MARKET-CAP TOP 10 (CURRENT SNAPSHOT) ===")
    for rank, (code, snap) in enumerate(top10, start=1):
        print(
            f"{rank:>2}. {code} | {snap.get('stock_name', code)} | "
            f"market_cap={snap.get('market_cap'):,}"
        )

    kospi_mdd_cache = collector.get_kospi_mdd_cache()
    print("\n=== DART + KRX FINANCIAL TEST (TOP 10 EXCLUDING SAMSUNG + SK HYNIX) ===")

    for rank, code, snapshot in targets:
        row = _load_row(code)
        if not row:
            raise SystemExit(
                f"No Fundamental row for rank {rank} ({code}); refusing to run."
            )

        name = row.get("stock_name") or code
        print(f"\n--- Rank {rank}: {name} ({code}) ---")

        company_rows = _load_all_company_rows()
        dart_code = _resolve_dart_financial_base_code(code, name, company_rows)
        financial_row = row
        financial_code = code
        financial_name = name

        if dart_code:
            financial_row = next(
                (candidate for candidate in company_rows
                 if str(candidate.get("stock_code") or "").zfill(6) == dart_code),
                None,
            )
            if not financial_row:
                raise SystemExit(
                    f"Preferred-share issuer mapping failed for {name} ({code}) -> {dart_code}."
                )
            financial_name = financial_row.get("stock_name") or dart_code
            financial_code = dart_code
            print(
                f"  ↳ KRX preferred share detected: {code} {name} "
                f"-> DART financial issuer: {dart_code} {financial_name}"
            )

        ok = collector.sync_1y_only(
            financial_code,
            financial_name,
            sector=financial_row.get("sector"),
            wics_sector=financial_row.get("wics_sector"),
            holding_company=financial_row.get("holding_company") or False,
            existing_period_scores=financial_row.get("period_scores") or {},
            kospi_mdd_cache=kospi_mdd_cache,
            force_refresh=True,
        )
        if not ok:
            raise SystemExit(
                f"DART 1y refresh failed for {name} ({code}); "
                f"financial issuer={financial_name} ({financial_code})."
            )

        after = (
            collector.supabase.table("Fundamental")
            .select(
                "stock_price,eps,bps,issued_shares,period_end_shares,"
                "per,pbr,data_basis_label"
            )
            .eq("stock_code", financial_code)
            .limit(1)
            .execute()
        )
        saved = (after.data or [None])[0]
        if not saved:
            raise SystemExit(
                f"Financial verification read failed for {financial_name} ({financial_code}) "
                f"while testing {name} ({code})."
            )

        per, pbr = _recalculate_valuation(
            snapshot.get("stock_price"), saved.get("eps"), saved.get("bps")
        )

        payload = {
            "stock_price": snapshot["stock_price"],
            "listed_shares": snapshot["listed_shares"],
            "market_cap": snapshot["market_cap"],
            "market_snapshot_date": snapshot["market_snapshot_date"],
            "market_data_source": snapshot["market_data_source"],
            # EPS/BPS는 issuer(보통주 회사)의 DART 재무정보를 사용하지만,
            # price/listed_shares/market_cap은 반드시 테스트 대상 KRX 증권 자체 값을 사용한다.
            "eps": saved.get("eps"),
            "bps": saved.get("bps"),
            "per": per,
            "pbr": pbr,
            "period_end_shares": saved.get("period_end_shares") or saved.get("issued_shares"),
            "shares_data_source": "DART_STOCK_TOTAL_ISSUER",
            "data_basis_label": saved.get("data_basis_label"),
        }
        collector.supabase.table("Fundamental").update(payload).eq("stock_code", code).execute()

        verify = (
            collector.supabase.table("Fundamental")
            .select(
                "stock_code,stock_name,stock_price,eps,bps,issued_shares,"
                "period_end_shares,listed_shares,market_cap,market_snapshot_date,"
                "market_data_source,per,pbr,data_basis_label"
            )
            .eq("stock_code", code)
            .limit(1)
            .execute()
        )
        saved = (verify.data or [None])[0]
        if not saved:
            raise SystemExit(f"Final verification read failed for {name} ({code}).")

        if saved.get("eps") is None:
            raise SystemExit(f"EPS is still NULL for {name} ({code}); stop before rollout.")
        if saved.get("bps") is None or float(saved.get("bps") or 0) <= 0:
            raise SystemExit(f"BPS is invalid for {name} ({code}): {saved.get('bps')}")
        if saved.get("per") is None:
            raise SystemExit(f"PER is still NULL for {name} ({code}); stop before rollout.")
        if saved.get("pbr") is None:
            raise SystemExit(f"PBR is still NULL for {name} ({code}); stop before rollout.")

        print(
            f"OK rank={rank} {name} ({code}) | "
            f"price={saved.get('stock_price'):,} | "
            f"EPS={saved.get('eps')} | BPS={saved.get('bps')} | "
            f"PER={saved.get('per')} | PBR={saved.get('pbr')} | "
            f"listed_shares={saved.get('listed_shares'):,} | "
            f"period_end_shares={saved.get('period_end_shares'):,}"
        )

    print("\nALL 8 TOP-MARKET-CAP DART + KRX FINANCIAL TESTS PASSED")
