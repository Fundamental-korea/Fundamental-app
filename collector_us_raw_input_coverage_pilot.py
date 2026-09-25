"""Controlled SEC Company Facts coverage pilot for the US data-collection pipeline.

Read-only. No database writes.

Purpose:
- establish whether the canonical raw inputs used by the collector are actually
  present in SEC Company Facts for a representative set of the current universe;
- measure coverage separately by scoring profile and venue;
- record namespace / alias provenance and missing raw families before attempting
  a full-universe backfill.

The pilot intentionally does NOT calculate scores or change eligibility.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
SEC_TICKER_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

PAGE_SIZE = 1000
PER_BUCKET = 25
REQUEST_INTERVAL = 0.30

OUT = Path("artifacts")
OUT.mkdir(exist_ok=True)

RAW_FAMILIES = {
    "revenue": {
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet",
        },
        "ifrs-full": {"Revenue", "RevenueFromContractsWithCustomers"},
    },
    "operating_income": {
        "us-gaap": {"OperatingIncomeLoss", "OperatingIncome", "OperatingProfitLoss", "IncomeFromOperations"},
        "ifrs-full": {"ProfitLossFromOperatingActivities", "OperatingIncomeLoss"},
    },
    "net_income": {
        "us-gaap": {"NetIncomeLoss", "ProfitLoss"},
        "ifrs-full": {"ProfitLoss", "ProfitLossAttributableToOwnersOfParent"},
    },
    "assets": {"us-gaap": {"Assets"}, "ifrs-full": {"Assets"}},
    "equity": {
        "us-gaap": {
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "Equity", "PartnersCapital", "MembersEquity",
            "EquityAttributableToOwnersOfParent",
        },
        "ifrs-full": {"EquityAttributableToOwnersOfParent", "Equity"},
    },
    "liabilities": {"us-gaap": {"Liabilities"}, "ifrs-full": {"Liabilities"}},
    "current_assets": {"us-gaap": {"AssetsCurrent"}, "ifrs-full": {"CurrentAssets"}},
    "current_liabilities": {"us-gaap": {"LiabilitiesCurrent"}, "ifrs-full": {"CurrentLiabilities"}},
    "cash": {
        "us-gaap": {
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        },
        "ifrs-full": {"CashAndCashEquivalents"},
    },
    "inventory": {"us-gaap": {"InventoryNet", "InventoryGross"}, "ifrs-full": {"Inventories"}},
    "receivables": {
        "us-gaap": {
            "AccountsReceivableNetCurrent", "AccountsReceivableNet",
            "AccountsAndNotesReceivableNetCurrent", "AccountsReceivableGrossCurrent",
        },
        "ifrs-full": {"TradeAndOtherReceivables", "TradeReceivables"},
    },
    "debt": {
        "us-gaap": {
            "LongTermDebtCurrent",
            "LongTermDebtAndCapitalLeaseObligationsCurrent",
            "LongTermDebtAndFinanceLeaseObligationsCurrent",
            "CurrentBorrowings",
            "CurrentPortionOfLongtermBorrowings",
            "ShortTermBorrowings",
            "FinanceLeaseLiabilityCurrent",
            "ConvertibleDebtCurrent",
            "DebtCurrent", "NotesPayableCurrent", "NotesAndLoansPayableCurrent",
            "ShortTermBankLoansAndNotesPayable", "CommercialPaper",
            "LineOfCreditCurrent", "RevolvingCreditFacilityCurrent",
            "LongTermDebtNoncurrent", "LongTermDebt",
            "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
            "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
            "NoncurrentBorrowings", "LongtermBorrowings",
            "FinanceLeaseLiabilityNoncurrent", "ConvertibleDebtNoncurrent",
            "DebtNoncurrent", "NotesPayableNoncurrent", "NotesPayable",
            "LongTermNotesPayable", "LoansPayable", "LongTermLoansPayable",
            "OtherBorrowings", "UnsecuredDebt", "SecuredDebt", "OtherLongTermDebt",
            "Borrowings", "DebtLongtermAndShorttermCombinedAmount",
            "DebtAndCapitalLeaseObligations", "Debt",
        },
        "ifrs-full": {
            "CurrentBorrowings", "CurrentPortionOfLongtermBorrowings",
            "ShorttermBorrowings", "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
            "LongtermBorrowings", "NoncurrentBorrowings",
            "LongTermDebtNoncurrent", "LongTermDebt",
            "LongTermNotesPayable", "FinanceLeaseLiabilityNoncurrent",
            "Borrowings", "LoansAndBorrowings",
        },
    },
    "interest_expense": {
        "us-gaap": {
            "InterestExpenseNonoperating", "InterestExpenseNonOperating",
            "InterestExpenseDebt", "InterestExpenseNonoperatingAndOther",
            "InterestAndDebtExpense", "InterestExpense",
            "InterestExpenseOnDebtInstrumentsIssued", "InterestExpenseOnBorrowings",
            "InterestExpenseOnOtherFinancialLiabilities",
            "InterestExpenseOnBankLoansAndOverdrafts", "InterestExpenseOnBonds",
            "InterestExpenseLongTermDebt", "InterestExpenseShortTermBorrowings",
            "InterestExpenseOtherLongTermDebt", "InterestExpenseOtherShortTermBorrowings",
            "InterestExpenseSubordinatedNotesAndDebentures",
            "InterestCostsIncurred", "FinancingInterestExpense",
        },
        "ifrs-full": {
            "FinanceCosts", "InterestExpense", "InterestExpenseOnBorrowings",
            "InterestExpenseOnDebtInstrumentsIssued",
            "InterestExpenseOnOtherFinancialLiabilities",
            "InterestExpenseOnBankLoansAndOverdrafts", "InterestExpenseOnBonds",
            "InterestExpenseLongTermDebt", "InterestCostsIncurred",
        },
    },
    "operating_cash_flow": {
        "us-gaap": {"NetCashProvidedByUsedInOperatingActivities"},
        "ifrs-full": {"CashFlowsFromUsedInOperatingActivities"},
    },
    "sga": {
        "us-gaap": {
            "SellingGeneralAndAdministrativeExpense",
            "SellingGeneralAndAdministrativeExpenseIncludingDepreciationAmortization",
            "GeneralAndAdministrativeExpense", "SellingExpense",
        },
        "ifrs-full": {"SellingGeneralAndAdministrativeExpense"},
    },
    "eps": {
        "us-gaap": {"EarningsPerShareDiluted", "EarningsPerShareBasic"},
        "ifrs-full": {"EarningsPerShareDiluted", "EarningsPerShareBasic"},
    },
}

PROFILE_REQUIRED = {
    "standard": ["revenue", "operating_income", "net_income", "assets", "equity",
                 "liabilities", "current_assets", "current_liabilities", "cash",
                 "receivables", "debt", "interest_expense", "operating_cash_flow", "sga", "eps"],
    "utility": ["revenue", "operating_income", "net_income", "assets", "equity",
                "liabilities", "cash", "debt", "interest_expense", "operating_cash_flow", "eps"],
    "financial": ["revenue", "operating_income", "net_income", "assets", "equity",
                  "liabilities", "cash", "interest_expense", "operating_cash_flow", "eps"],
    "reit": ["revenue", "operating_income", "net_income", "assets", "equity",
             "liabilities", "cash", "debt", "interest_expense", "eps"],
    "bdc": ["revenue", "operating_income", "net_income", "assets", "equity",
            "liabilities", "cash", "debt", "interest_expense", "eps"],
    "defense": ["revenue", "operating_income", "net_income", "assets", "equity",
                "liabilities", "cash", "debt", "interest_expense", "operating_cash_flow", "sga", "eps"],
}

def norm(value):
    return (value or "").strip().upper()

def cik10(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(10) if digits else ""

def ticker_norm(value):
    return re.sub(r"\s+", "", norm(value))

def stable_score(value):
    return int(hashlib.sha256(value.encode()).hexdigest()[:12], 16)

def fetch_all(sb):
    rows = []
    offset = 0
    while True:
        page = (
            sb.table("US_Companies")
            .select("ticker,cik,company_name,exchange,company_type,scoring_profile,is_fundamental_eligible")
            .eq("is_fundamental_eligible", True)
            .order("ticker")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute().data or []
        )
        if not page:
            break
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return rows

class SecClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": SEC_USER_AGENT, "Accept-Encoding": "gzip, deflate"})
        self.last = 0.0

    def get(self, url):
        wait = REQUEST_INTERVAL - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        last_exc = None
        for attempt in range(5):
            try:
                r = self.s.get(url, timeout=60)
                self.last = time.monotonic()
                if r.status_code == 200:
                    return r.json(), None
                if r.status_code == 404:
                    return None, "SEC_404"
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(min(2 ** attempt, 16))
                    continue
                r.raise_for_status()
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(min(2 ** attempt, 16))
        return None, f"REQUEST_ERROR:{type(last_exc).__name__ if last_exc else 'unknown'}"

def fact_inventory(doc):
    out = {}
    for namespace, facts in ((doc or {}).get("facts") or {}).items():
        ns = out.setdefault(namespace, set())
        for concept in facts:
            ns.add(concept)
    return out

def classify_venue(sec_item):
    ex = norm(sec_item.get("exchange"))
    if ex == "OTC":
        return "OTC"
    if ex:
        return "EXCHANGE_LISTED"
    return "NO_SEC_EXCHANGE"

def main():
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    companies = fetch_all(sb)
    sec = SecClient()
    tickers_doc, err = sec.get(SEC_TICKER_EXCHANGE_URL)
    if err:
        raise RuntimeError(f"Could not fetch SEC ticker/exchange file: {err}")

    fields = tickers_doc.get("fields") or []
    idx = {name: i for i, name in enumerate(fields)}
    sec_by_pair = {}
    sec_by_ticker = {}
    for raw in tickers_doc.get("data") or []:
        item = {k: raw[i] if i < len(raw) else None for k, i in idx.items()}
        key = (ticker_norm(item.get("ticker")), cik10(item.get("cik")))
        sec_by_pair[key] = item
        sec_by_ticker.setdefault(ticker_norm(item.get("ticker")), []).append(item)

    # Deterministic stratified sample by scoring profile x SEC venue.
    buckets = defaultdict(list)
    for row in companies:
        pair = sec_by_pair.get((ticker_norm(row.get("ticker")), cik10(row.get("cik"))))
        venue = classify_venue(pair or {})
        profile = row.get("scoring_profile") or "standard"
        buckets[(profile, venue)].append(row)

    sampled = []
    sample_plan = {}
    for key, rows in sorted(buckets.items()):
        rows = sorted(rows, key=lambda r: stable_score(r["ticker"]))
        take = min(PER_BUCKET, len(rows))
        sample_plan[f"{key[0]}|{key[1]}"] = take
        sampled.extend(rows[:take])

    results = []
    source_errors = Counter()
    family_present = Counter()
    profile_results = defaultdict(lambda: {"sample": 0, "facts_ok": 0, "facts_404": 0, "families_present": Counter(), "required_all_present": 0})
    venue_results = defaultdict(lambda: {"sample": 0, "facts_ok": 0, "facts_404": 0, "families_present": Counter()})

    print(f"=== US RAW INPUT COVERAGE PILOT v1 ===")
    print(f"eligible={len(companies)} sampled={len(sampled)} buckets={len(buckets)}")

    for i, row in enumerate(sampled, 1):
        ticker = row["ticker"]
        cik = cik10(row["cik"])
        pair = sec_by_pair.get((ticker_norm(ticker), cik))
        venue = classify_venue(pair or {})
        profile = row.get("scoring_profile") or "standard"

        doc, error = sec.get(SEC_FACTS_URL.format(cik=cik))
        item = {
            "ticker": ticker,
            "cik": cik,
            "company_name": row.get("company_name"),
            "scoring_profile": profile,
            "company_type": row.get("company_type"),
            "sec_venue": venue,
            "sec_exchange": (pair or {}).get("exchange"),
            "sec_pair_match": pair is not None,
            "facts_status": "OK" if not error else error,
        }

        profile_results[profile]["sample"] += 1
        venue_results[venue]["sample"] += 1

        if error:
            source_errors[error] += 1
            if error == "SEC_404":
                profile_results[profile]["facts_404"] += 1
                venue_results[venue]["facts_404"] += 1
            results.append(item)
            continue

        profile_results[profile]["facts_ok"] += 1
        venue_results[venue]["facts_ok"] += 1

        inventory = fact_inventory(doc)
        families = {}
        matched_tags = {}
        for family, by_ns in RAW_FAMILIES.items():
            matched = []
            for ns, aliases in by_ns.items():
                present = sorted(aliases & inventory.get(ns, set()))
                matched.extend(f"{ns}:{tag}" for tag in present)
            families[family] = bool(matched)
            matched_tags[family] = matched
            if families[family]:
                family_present[family] += 1
                profile_results[profile]["families_present"][family] += 1
                venue_results[venue]["families_present"][family] += 1

        required = PROFILE_REQUIRED.get(profile, PROFILE_REQUIRED["standard"])
        required_all = all(families.get(f, False) for f in required)
        if required_all:
            profile_results[profile]["required_all_present"] += 1

        item["families_present"] = families
        item["matched_tags"] = matched_tags
        item["namespaces"] = sorted(inventory)
        item["fact_concept_count"] = sum(len(v) for v in inventory.values())
        results.append(item)

        if i % 25 == 0:
            print(f"[{i}/{len(sampled)}] ticker={ticker} profile={profile} venue={venue}")

    profile_summary = {}
    for profile, d in profile_results.items():
        profile_summary[profile] = {
            "sample": d["sample"],
            "facts_ok": d["facts_ok"],
            "facts_404": d["facts_404"],
            "required_all_present": d["required_all_present"],
            "family_coverage": {
                f: {
                    "present": d["families_present"].get(f, 0),
                    "pct_of_ok": round(100 * d["families_present"].get(f, 0) / d["facts_ok"], 1) if d["facts_ok"] else 0,
                }
                for f in PROFILE_REQUIRED.get(profile, PROFILE_REQUIRED["standard"])
            },
        }

    venue_summary = {}
    for venue, d in venue_results.items():
        venue_summary[venue] = {
            "sample": d["sample"],
            "facts_ok": d["facts_ok"],
            "facts_404": d["facts_404"],
            "family_coverage": {
                f: {
                    "present": d["families_present"].get(f, 0),
                    "pct_of_ok": round(100 * d["families_present"].get(f, 0) / d["facts_ok"], 1) if d["facts_ok"] else 0,
                }
                for f in RAW_FAMILIES
            },
        }

    report = {
        "summary": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "eligible": len(companies),
            "sampled": len(sampled),
            "bucket_count": len(buckets),
            "per_bucket": PER_BUCKET,
            "source": "SEC Company Facts",
            "source_errors": dict(source_errors),
            "family_coverage_all_ok": {
                f: {"present": n, "pct": round(100 * n / max(1, sum(1 for r in results if r.get("facts_status") == "OK")), 1)}
                for f, n in family_present.items()
            },
            "profile_summary": profile_summary,
            "venue_summary": venue_summary,
            "sample_plan": sample_plan,
        },
        "companies": results,
    }

    out = OUT / "us_raw_input_coverage_pilot_v1.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n[PROFILE]")
    for profile, d in profile_summary.items():
        print(profile, d)
    print("\n[VENUE]")
    for venue, d in venue_summary.items():
        print(venue, d)
    print(f"\n[REPORT] {out}")

if __name__ == "__main__":
    main()
