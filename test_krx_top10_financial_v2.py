"""Read-only KRX top-10 financial validation.

Purpose:
- Diagnose DART financial data independently from current-price/share-count retrieval.
- Never abort the whole test because one company's DART stock-total report is missing.
- Never write to Supabase.
- Samsung Electronics / SK hynix are excluded because already validated.

Important distinction:
EPS comes from the latest DART financial report and must not depend on the
DART "주식총수" report. BPS needs an equity/share basis, so missing share-count
data is reported explicitly as a share-basis issue instead of being mislabeled
as an EPS failure.
"""

from kor_market_pipeline import fetch_market_snapshot_map
import collector


EXCLUDE_CODES = {"005930", "000660"}
PREFERRED_SUFFIXES = ("2우B", "1우", "2우", "3우", "우B", "우C", "우")


def load_company_rows(codes=None):
    """Load only target rows. Avoid scanning the large period_scores JSON column."""
    if codes:
        res = (
            collector.supabase.table("Fundamental")
            .select("stock_code,stock_name,sector,wics_sector,holding_company")
            .in_("stock_code", list(codes))
            .execute()
        )
        return res.data or []
    return []


def resolve_issuer(code, name, rows):
    name = str(name or "").strip()
    base = None
    for suffix in PREFERRED_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            base = name[:-len(suffix)].strip()
            break
    if not base:
        return code, name, False

    for row in rows:
        if str(row.get("stock_name") or "").strip() == base:
            issuer = str(row.get("stock_code") or "").zfill(6)
            if issuer and issuer != code:
                return issuer, base, True
    return code, name, False


def get_fresh_stock_total(code, years):
    """Bypass Report_Metrics_Cache and ask DART directly for each candidate year."""
    attempts = []
    for year in years:
        try:
            df = collector.dart.report(code, "주식총수", year)
            issued, distributed = collector.extract_issued_shares(df)
            attempts.append((year, issued, distributed, df))
            if issued and issued > 0:
                return year, issued, distributed, attempts
        except Exception as e:
            print(f"  🔎 DART stock-total exception ({code}, {year}): {e}")
            attempts.append((year, None, None, str(e)))
    return None, None, None, attempts


def main():
    snapshots = fetch_market_snapshot_map()
    if not snapshots:
        raise SystemExit("KRX snapshot cache is empty.")

    ranked = sorted(
        ((code, s) for code, s in snapshots.items() if s.get("market_cap") is not None),
        key=lambda x: x[1]["market_cap"],
        reverse=True,
    )
    top10 = ranked[:10]
    targets = [(r, c, s) for r, (c, s) in enumerate(top10, 1) if c not in EXCLUDE_CODES]

    if len(targets) != 8:
        raise SystemExit(f"Expected 8 targets, got {len(targets)}.")

    rows = load_company_rows([c for c, _ in top10])
    row_map = {str(r.get("stock_code") or "").zfill(6): r for r in rows}
    print("\n=== READ-ONLY KRX TOP-10 DART DIAGNOSTIC ===")
    print("DB WRITE: NO")
    print("RULE: EPS is validated independently of DART stock-total availability.\n")

    hard_failures = []
    share_basis_issues = []

    for rank, code, snap in targets:
        dbrow = row_map.get(code)
        if not dbrow:
            hard_failures.append(f"{code}: Fundamental row missing")
            print(f"\nFAIL rank={rank} {code}: Fundamental row missing")
            continue

        issuer_code, issuer_name, preferred = resolve_issuer(code, dbrow.get("stock_name"), rows)
        print(f"\n--- Rank {rank}: {dbrow.get('stock_name')} ({code}) ---")
        if preferred:
            print(f"  preferred -> DART issuer {issuer_name} ({issuer_code})")

        try:
            latest = collector.fetch_latest_report_metrics(
                issuer_code, use_ofs_for_manufacturing=False, force_refresh=True
            )
        except Exception as e:
            hard_failures.append(f"{code}: DART latest-report exception: {e}")
            print(f"  ❌ DART latest-report exception: {e}")
            continue
        if latest is None:
            hard_failures.append(f"{code}: latest DART financial report unavailable")
            print("  FAIL: latest DART financial report unavailable")
            continue

        eps = latest.get("reported_eps")
        eps_source = "DART reported EPS"
        if eps is None and code == "009150":
            for fs_div in ("CFS", "OFS"):
                try:
                    raw = collector._dart_finstate_all_cached(issuer_code, latest["_report_year"], latest["_report_code"], fs_div)
                    if raw is not None and not raw.empty:
                        candidates = raw[raw["account_nm"].astype(str).str.contains("주당|EPS|earnings per share", case=False, na=False, regex=True)]
                        print(f"  🔎 {fs_div} EPS account candidates: {candidates[['account_nm','thstrm_amount']].to_dict('records')[:20]}")
                except Exception as e:
                    print(f"  🔎 {fs_div} EPS-account diagnostic failed: {e}")

        if eps is None:
            # EPS fallback is allowed only if DART explicitly supplies a usable
            # stock-total denominator. Do not silently use KRX current shares.
            stock_year, issued, distributed, attempts = get_fresh_stock_total(
                issuer_code, [collector.get_latest_annual_year(),
                              collector.get_latest_annual_year() - 1,
                              collector.get_latest_annual_year() - 2]
            )
            denom = distributed or issued
            if denom and latest.get("net_income") is not None:
                eps = latest["net_income"] / denom
                eps_source = f"DART net income / DART shares ({stock_year})"
            else:
                eps_source = "unavailable"

        equity = latest.get("equity_for_bps", latest.get("total_equity"))
        stock_year, issued, distributed, attempts = get_fresh_stock_total(
            issuer_code, [collector.get_latest_annual_year(),
                          collector.get_latest_annual_year() - 1,
                          collector.get_latest_annual_year() - 2]
        )

        bps = (equity / issued) if equity is not None and issued and issued > 0 else None
        bps_source = f"DART equity / DART issued shares ({stock_year})" if bps is not None else "unavailable"

        price = snap.get("stock_price")
        per = round(price / eps, 2) if price is not None and eps not in (None, 0) else None
        pbr = round(price / bps, 2) if price is not None and bps is not None and bps > 0 else None

        print(f"  report={latest['_report_year']} {collector.REPORT_CODE_LABEL.get(latest['_report_code'], latest['_report_code'])}")
        print(f"  EPS={eps} [{eps_source}]")
        print(f"  equity_for_bps={equity}")
        print(f"  DART shares={issued} (source year={stock_year})")
        print(f"  BPS={bps} [{bps_source}]")
        print(f"  KRX price={price} | listed_shares={snap.get('listed_shares')} | market_cap={snap.get('market_cap')}")
        print(f"  PER={per} | PBR={pbr}")

        if eps is None:
            hard_failures.append(f"{code}: EPS unavailable from DART")
        if bps is None:
            share_basis_issues.append(
                f"{code}: DART stock-total unavailable; BPS cannot be validated on a period-end share basis"
            )
        if per is None and eps is not None:
            hard_failures.append(f"{code}: PER calculation failed")
        if pbr is None and bps is not None:
            hard_failures.append(f"{code}: PBR calculation failed")

        if eps is not None:
            print("  ✅ EPS OK (independent of stock-total report)")
        if bps is not None:
            print("  ✅ BPS OK (DART period-end share basis)")
        else:
            print("  ⚠️ BPS NOT VALIDATED: share-count source unavailable")
        if per is not None:
            print("  ✅ PER OK")
        if pbr is not None:
            print("  ✅ PBR OK")

        # For diagnosis, show whether the three direct DART stock-total attempts
        # all failed, without dumping raw data.
        if issued is None:
            print("  🔎 DART stock-total attempts:", [
                (a[0], "OK" if a[1] else "NO DATA") for a in attempts
            ])

    print("\n=== DIAGNOSTIC SUMMARY ===")
    print(f"Targets processed: {len(targets)} / {len(targets)}")
    print(f"Hard financial failures: {len(hard_failures)}")
    for x in hard_failures:
        print("  ❌", x)
    print(f"Share-basis issues: {len(share_basis_issues)}")
    for x in share_basis_issues:
        print("  ⚠️", x)

    if hard_failures:
        raise SystemExit("Financial validation failed; do not roll out.")
    if share_basis_issues:
        print("\nRESULT: DART financials are healthy, but share-basis gaps remain.")
        print("Do not treat those gaps as EPS failures; fix/define the BPS share-basis fallback before full rollout.")
        raise SystemExit(2)

    print("\nALL 8 TOP-MARKET-CAP FINANCIAL VALIDATIONS PASSED")


if __name__ == "__main__":
    main()
