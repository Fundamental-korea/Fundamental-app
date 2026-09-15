"""Diagnose domestic Standard companies with missing fiscal snapshots.

No database writes. First classifies all snapshot-missing Standard companies
using SEC submissions, then only analyzes those whose latest relevant filing
is domestic under the project's existing classification rule.
"""

import os
import time
from datetime import datetime

import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY") or ""
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
RELEVANT_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "6-K"}
FOREIGN_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}
STANDARD_SECTORS = {"technology", "healthcare", "consumer", "industrials", "energy", "materials", "communication"}
PAGE_SIZE = 1000
SNAPSHOT_FORMS = {"10-Q", "10-Q/A", "10-K", "10-K/A"}
INSTANT_METRICS = {"assets", "equity", "liabilities", "current_assets", "current_liabilities", "cash", "receivables", "inventory"}
FLOW_METRICS = {"revenue", "operating_income", "net_income", "interest_expense", "operating_cash_flow", "sga", "eps"}
FACT_ALIASES = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "assets": ["Assets"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "liabilities": ["Liabilities"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "inventory": ["InventoryNet", "InventoryGross"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "receivables": ["AccountsReceivableNetCurrent", "AccountsReceivableNet", "AccountsAndNotesReceivableNetCurrent", "AccountsReceivableGrossCurrent"],
    "interest_expense": ["InterestExpenseNonOperating", "InterestExpenseDebt", "InterestExpenseNonOperatingNet", "InterestExpenseNonOperatingAndOther"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "sga": ["SellingGeneralAndAdministrativeExpense", "SellingGeneralAndAdministrativeExpenseIncludingDepreciationAmortization"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic"],
}


def fetch_json(session, url):
    for attempt in range(4):
        response = session.get(url, timeout=30)
        if response.status_code == 200:
            return response.json()
        if response.status_code in (429, 500, 502, 503, 504):
            time.sleep(1.5 * (attempt + 1))
            continue
        response.raise_for_status()
    raise RuntimeError(f"SEC request failed: {url}")


def paginated_query(query_builder):
    rows = []
    offset = 0
    while True:
        page = query_builder(offset, offset + PAGE_SIZE - 1).execute().data or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows


def load_standard_missing(sb):
    def build_companies(offset, end):
        return (
            sb.table("US_Companies")
            .select("ticker,sector_common")
            .eq("is_fundamental_eligible", True)
            .in_("sector_common", list(STANDARD_SECTORS))
            .order("ticker")
            .range(offset, end)
        )

    standard = {r["ticker"] for r in paginated_query(build_companies) if r.get("ticker")}

    def build_fundamental(offset, end):
        return (
            sb.table("US_Fundamental")
            .select("ticker,company_name,cik,snapshot_filed,data_unavailable,data_reliability")
            .is_("snapshot_filed", "null")
            .order("ticker")
            .range(offset, end)
        )

    missing = paginated_query(build_fundamental)
    return [r for r in missing if r.get("ticker") in standard]


def latest_relevant_filing(submissions):
    recent = submissions.get("filings", {}).get("recent", {}) or {}
    rows = []
    for form, filed, accession, primary in zip(
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("accessionNumber", []),
        recent.get("primaryDocument", []),
    ):
        if form not in RELEVANT_FORMS:
            continue
        rows.append({"form": form, "filed": filed, "accession": accession, "primary": primary})
    rows.sort(key=lambda x: (x.get("filed") or "", x.get("accession") or ""), reverse=True)
    return rows[0] if rows else None


def parse_date(value):
    try:
        return datetime.fromisoformat(value).date()
    except Exception:
        return None


def companyfacts_summary(facts):
    facts_root = facts.get("facts") or {}
    namespaces = sorted(facts_root.keys())
    tag_summary = {namespace: len(facts_root.get(namespace) or {}) for namespace in namespaces}
    available_metrics = []
    max_dates = []
    future_rows = []

    for metric in sorted(INSTANT_METRICS | FLOW_METRICS):
        matched = []
        for tag in FACT_ALIASES.get(metric, []):
            for namespace in namespaces:
                fact = (facts_root.get(namespace) or {}).get(tag)
                if not fact:
                    continue
                matched.append(f"{namespace}:{tag}")
                for unit_rows in (fact.get("units") or {}).values():
                    if not isinstance(unit_rows, list):
                        continue
                    for row in unit_rows:
                        form = row.get("form")
                        end = row.get("end")
                        if form not in SNAPSHOT_FORMS or not end:
                            continue
                        max_dates.append(end)
                        filed = row.get("filed") or ""
                        if filed and parse_date(end) and parse_date(filed) and parse_date(end) > parse_date(filed):
                            future_rows.append((metric, namespace, tag, end, filed, form))
        if matched:
            available_metrics.append(metric)

    return {
        "namespaces": namespaces,
        "tag_counts": tag_summary,
        "available_metrics": available_metrics,
        "max_snapshot_end": max(max_dates) if max_dates else None,
        "future_end_count": len(future_rows),
        "future_examples": future_rows[:5],
    }


def main():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")

    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    rows = load_standard_missing(sb)
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    print(f"[INPUT] snapshot-missing Standard rows={len(rows)}")

    # Preserve the project's existing classification rule:
    # 20-F/40-F = foreign; everything else in RELEVANT_FORMS = domestic.
    domestic_rows = []
    classification_counts = {"domestic": 0, "foreign": 0, "no_relevant_filing": 0, "error": 0}
    classification_errors = []
    for row in rows:
        ticker = row.get("ticker") or ""
        cik_raw = str(row.get("cik") or "").strip()
        try:
            if not cik_raw:
                classification_counts["error"] += 1
                classification_errors.append((ticker, "missing_cik"))
                continue
            submissions = fetch_json(session, SEC_SUBMISSIONS_URL.format(cik=cik_raw.zfill(10)))
            filing = latest_relevant_filing(submissions)
            if not filing:
                classification_counts["no_relevant_filing"] += 1
                continue
            kind = "foreign" if filing["form"] in FOREIGN_FORMS else "domestic"
            classification_counts[kind] += 1
            if kind == "domestic":
                domestic_rows.append((row, filing))
        except Exception as exc:
            classification_counts["error"] += 1
            classification_errors.append((ticker, f"{type(exc).__name__}:{exc}"))
        time.sleep(0.12)

    print(
        "[CLASSIFICATION] "
        f"domestic={classification_counts['domestic']} "
        f"foreign={classification_counts['foreign']} "
        f"no_relevant_filing={classification_counts['no_relevant_filing']} "
        f"error={classification_counts['error']}"
    )

    print("ticker|company|cik|filing|filed|facts_namespaces|metrics|candidate_end|future_end_rows|diagnosis")
    counts = {}
    for row, filing in domestic_rows:
        ticker = row.get("ticker") or ""
        company = row.get("company_name") or ""
        cik = str(row.get("cik") or "").strip().zfill(10)
        try:
            facts = fetch_json(session, SEC_FACTS_URL.format(cik=cik))
            summary = companyfacts_summary(facts)
            ns = ",".join(summary["namespaces"]) or "-"
            metrics = ",".join(summary["available_metrics"]) or "-"
            candidate_end = summary["max_snapshot_end"] or "-"

            if not summary["namespaces"]:
                diagnosis = "companyfacts_empty"
            elif not summary["available_metrics"]:
                diagnosis = "no_supported_tags"
            elif candidate_end != "-":
                end_date = parse_date(candidate_end)
                filed_date = parse_date(filing["filed"])
                if end_date and filed_date and end_date > filed_date:
                    diagnosis = "future_end_candidate"
                else:
                    diagnosis = "supported_facts_but_builder_missing_snapshot"
            else:
                diagnosis = "no_snapshot_form_rows"

            counts[diagnosis] = counts.get(diagnosis, 0) + 1
            print("|".join([
                ticker, company, cik, filing["form"], filing["filed"],
                ns, metrics, candidate_end, str(summary["future_end_count"]), diagnosis,
            ]))
        except Exception as exc:
            diagnosis = f"error:{type(exc).__name__}"
            counts[diagnosis] = counts.get(diagnosis, 0) + 1
            print(f"{ticker}|{company}|{cik}|ERROR|||||{diagnosis}:{exc}")
        time.sleep(0.25)

    print("[SUMMARY] " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
