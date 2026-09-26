"""Broad SEC Company Facts inventory for the US canonical financial data layer.

Read-only: this script never writes company financials to Supabase.
It inventories raw accounting facts, conservative logical-field coverage,
derivable metric prerequisites, and recurring concept candidates.

The important distinction is:
  exact mapped coverage = trusted canonical source currently recognized
  fuzzy candidate coverage = source concept worth reviewing, not accepted data
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL") or "https://cnweggechipghcivruie.supabase.co"
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_KEY", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "Fundamental-app contact@example.com")
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
FLOW_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
INSTANT_FORMS = FLOW_FORMS | {"10-Q", "10-Q/A"}

SEC_MIN_REQUEST_INTERVAL = 0.25
_SEC_LAST_REQUEST = 0.0
TODAY = datetime.now(timezone.utc).date()

# These are logical financial facts, not score metrics.  Exact aliases are
# intentionally conservative; fuzzy keywords are used only for discovery.
FIELD_SPECS: dict[str, dict[str, Any]] = {
    "revenue": {
        "kind": "flow",
        "aliases": (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenues", "Revenue", "RevenueFromContractsWithCustomers",
            "SalesRevenueNet", "SalesRevenueGoodsNet",
        ),
        "keywords": ("revenue", "sales"),
        "exclude": ("segment", "proforma", "forecast", "budget", "growth"),
    },
    "operating_income": {
        "kind": "flow",
        "aliases": (
            "OperatingIncomeLoss", "OperatingIncome", "OperatingProfitLoss",
            "IncomeFromOperations", "ProfitLossFromOperatingActivities",
        ),
        "keywords": ("operating income", "operating profit", "income from operations",
                     "profit loss from operating activities", "operating earnings"),
        "exclude": ("segment", "margin", "ratio", "forecast", "budget"),
    },
    "net_income": {
        "kind": "flow",
        "aliases": ("NetIncomeLoss", "ProfitLoss"),
        "keywords": ("net income", "profit loss"),
        "exclude": ("segment", "per share", "eps", "attributable"),
    },
    "assets": {
        "kind": "instant",
        "aliases": ("Assets",),
        "keywords": ("assets",),
        "exclude": ("segment", "assets held for sale"),
    },
    "equity": {
        "kind": "instant",
        "aliases": (
            "EquityAttributableToOwnersOfParent",
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "Equity", "PartnersCapital", "MembersEquity", "ProprietaryCapital",
        ),
        "keywords": ("stockholders equity", "shareholders equity", "equity",
                     "partners capital", "members equity"),
        "exclude": ("segment", "per share", "ratio"),
    },
    "liabilities": {
        "kind": "instant",
        "aliases": ("Liabilities",),
        "keywords": ("liabilities",),
        "exclude": ("segment", "current liabilities"),
    },
    "cash": {
        "kind": "instant",
        "aliases": (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "CashAndCashEquivalents", "CashAndRestrictedCash",
        ),
        "keywords": ("cash and cash equivalents", "cash equivalents"),
        "exclude": ("flow", "segment", "restricted cash flow"),
    },
    "current_assets": {
        "kind": "instant",
        "aliases": ("AssetsCurrent", "CurrentAssets"),
        "keywords": ("current assets",),
        "exclude": ("segment",),
    },
    "current_liabilities": {
        "kind": "instant",
        "aliases": ("LiabilitiesCurrent", "CurrentLiabilities"),
        "keywords": ("current liabilities",),
        "exclude": ("segment",),
    },
    "receivables": {
        "kind": "instant",
        "aliases": (
            "AccountsReceivableNetCurrent", "AccountsReceivableNet",
            "AccountsAndNotesReceivableNetCurrent", "AccountsReceivableGrossCurrent",
            "TradeAndOtherReceivables", "TradeReceivables",
        ),
        "keywords": ("accounts receivable", "trade receivables", "trade and other receivables"),
        "exclude": ("long term", "segment", "financing receivable"),
    },
    "inventory": {
        "kind": "instant",
        "aliases": ("InventoryNet", "InventoryGross", "Inventories"),
        "keywords": ("inventory", "inventories"),
        "exclude": ("segment", "reserve", "turnover"),
    },
    "debt_current": {
        "kind": "instant",
        "aliases": (
            "LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
            "LongTermDebtAndFinanceLeaseObligationsCurrent", "CurrentBorrowings",
            "CurrentPortionOfLongtermBorrowings", "ShortTermBorrowings",
            "ShorttermBorrowings", "FinanceLeaseLiabilityCurrent",
            "ConvertibleDebtCurrent", "DebtCurrent", "NotesPayableCurrent",
            "NotesAndLoansPayableCurrent", "ShortTermBankLoansAndNotesPayable",
            "CommercialPaper", "LineOfCreditCurrent", "RevolvingCreditFacilityCurrent",
            "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
        ),
        "keywords": ("current debt", "current borrowings", "short term borrowings",
                     "current portion", "notes payable current", "commercial paper"),
        "exclude": ("capacity", "maturity", "interest rate", "interest paid",
                    "repayment", "proceeds", "fair value"),
    },
    "debt_noncurrent": {
        "kind": "instant",
        "aliases": (
            "LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
            "LongTermDebtAndFinanceLeaseObligationsNoncurrent", "NoncurrentBorrowings",
            "LongtermBorrowings", "FinanceLeaseLiabilityNoncurrent", "ConvertibleDebtNoncurrent",
            "DebtNoncurrent", "NotesPayableNoncurrent", "NotesPayable",
            "LongTermNotesPayable", "LoansPayable", "LongTermLoansPayable",
            "OtherBorrowings", "UnsecuredDebt", "SecuredDebt", "OtherLongTermDebt",
            "FederalHomeLoanBankAdvancesLongTerm", "LongTermNotesAndLoans",
            "LineOfCreditNoncurrent", "RevolvingCreditFacility",
        ),
        "keywords": ("long term debt", "long term borrowings", "noncurrent borrowings",
                     "notes payable", "loans payable", "senior notes", "debt"),
        "exclude": ("capacity", "maturity", "interest rate", "interest paid",
                    "repayment", "proceeds", "fair value", "net debt", "capitalization"),
    },
    "debt_total": {
        "kind": "instant",
        "aliases": (
            "Borrowings", "DebtLongtermAndShorttermCombinedAmount",
            "DebtAndCapitalLeaseObligations", "LongTermDebtCurrentAndNoncurrent",
            "LongTermDebtAndCapitalLeaseObligations",
            "LongTermDebtAndFinanceLeaseObligations", "DebtAndFinanceLeaseLiabilities",
            "Debt", "TotalDebt",
        ),
        "keywords": ("total debt", "borrowings", "debt"),
        "exclude": ("capacity", "maturity", "interest rate", "interest paid",
                    "repayment", "proceeds", "fair value", "net debt", "capitalization"),
    },
    "interest_expense": {
        "kind": "flow",
        "aliases": (
            "InterestExpenseNonoperating", "InterestExpenseNonOperating",
            "InterestExpenseDebt", "InterestAndDebtExpense", "InterestExpense",
            "InterestExpenseNonoperatingAndOther", "InterestExpenseRelatedParties",
            "InterestExpenseDebtExcludingAmortization", "FinanceCosts",
            "InterestExpenseOnDebtInstrumentsIssued", "InterestExpenseOnBorrowings",
            "InterestExpenseOnOtherFinancialLiabilities", "InterestExpenseOnBankLoansAndOverdrafts",
            "InterestExpenseOnBonds", "InterestExpenseLongTermDebt",
            "InterestExpenseShortTermBorrowings", "InterestExpenseOtherLongTermDebt",
            "InterestExpenseOtherShortTermBorrowings", "InterestCostsIncurred",
            "FinancingInterestExpense", "InterestIncomeExpenseNet",
            "InterestIncomeExpenseNonoperatingNet",
        ),
        "keywords": ("interest expense", "interest cost", "finance cost",
                     "financing cost", "borrowing cost", "debt expense"),
        "exclude": ("interest rate", "interest paid", "interest payable", "interest income",
                    "pension", "defined benefit", "capitalized interest", "tax interest",
                    "derivative", "fair value"),
    },
    "operating_cash_flow": {
        "kind": "flow",
        "aliases": ("NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"),
        "keywords": ("net cash provided by operating activities",
                     "cash flows from operating activities"),
        "exclude": ("segment", "forecast", "proforma"),
    },
    "capex": {
        "kind": "flow",
        "aliases": (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "PaymentsToAcquirePropertyPlantAndEquipmentAndOtherProductiveAssets",
            "PaymentsForAdditionsToPropertyPlantAndEquipment",
            "PaymentsForPropertyPlantAndEquipment",
        ),
        "keywords": ("payments to acquire property", "capital expenditures",
                     "capital expenditure", "property plant equipment"),
        "exclude": ("proceeds", "disposal", "sale", "segment", "depreciation",
                    "accumulated depreciation"),
    },
    "sga": {
        "kind": "flow",
        "aliases": (
            "SellingGeneralAndAdministrativeExpense",
            "SellingGeneralAndAdministrativeExpenseIncludingDepreciationAmortization",
            "GeneralAndAdministrativeExpense", "SellingExpense",
        ),
        "keywords": ("selling general and administrative", "general and administrative expense",
                     "selling expense"),
        "exclude": ("segment", "ratio", "percent"),
    },
    "pretax_income": {
        "kind": "flow",
        "aliases": (
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
            "ProfitLossBeforeTax",
        ),
        "keywords": ("income before income taxes", "profit loss before tax",
                     "pretax income"),
        "exclude": ("segment", "per share"),
    },
    "tax_expense": {
        "kind": "flow",
        "aliases": (
            "IncomeTaxExpenseBenefit", "IncomeTaxExpenseBenefitContinuingOperations",
            "IncomeTaxExpenseBenefitNonoperating", "IncomeTaxesPaid",
        ),
        "keywords": ("income tax expense", "income tax benefit", "tax expense"),
        "exclude": ("paid", "payable", "deferred tax asset", "tax rate"),
    },
    "eps": {
        "kind": "flow",
        "aliases": ("EarningsPerShareDiluted", "EarningsPerShareBasic"),
        "keywords": ("earnings per share", "basic eps", "diluted eps"),
        "exclude": ("weighted average", "numerator", "denominator", "market price",
                    "proforma", "segment"),
    },
    "weighted_avg_diluted_shares": {
        "kind": "flow",
        "aliases": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
        "keywords": ("weighted average number of diluted shares", "diluted weighted average shares"),
        "exclude": ("segment", "stock split"),
    },
    "weighted_avg_basic_shares": {
        "kind": "flow",
        "aliases": ("WeightedAverageNumberOfSharesOutstandingBasic",),
        "keywords": ("weighted average number of shares outstanding", "basic weighted average shares"),
        "exclude": ("diluted", "segment", "stock split"),
    },
    "common_shares_outstanding": {
        "kind": "instant",
        "aliases": (
            "EntityCommonStockSharesOutstanding",
            "CommonStockSharesOutstanding",
        ),
        "keywords": ("common stock shares outstanding", "shares outstanding"),
        "exclude": ("weighted average", "potentially dilutive", "segment"),
    },
    "cash_dividends": {
        "kind": "flow",
        "aliases": (
            "PaymentsOfDividendsCommonStock",
            "PaymentsOfDividends",
            "DividendsCommonStockCash",
            "PaymentsOfDividendsCommon",
        ),
        "keywords": ("payments of dividends", "dividends common stock", "cash dividends"),
        "exclude": ("preferred", "minority interest", "per share", "declared"),
    },
    "depreciation_amortization": {
        "kind": "flow",
        "aliases": (
            "DepreciationDepletionAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization",
            "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment",
        ),
        "keywords": ("depreciation and amortization", "depreciation depletion and amortization"),
        "exclude": ("segment", "per share", "ratio"),
    },
    "stock_based_compensation": {
        "kind": "flow",
        "aliases": (
            "ShareBasedCompensation", "ShareBasedCompensationArrangementByShareBasedPaymentAwardEquityInstrumentsOtherThanOptionsGrantsInPeriodTotal",
        ),
        "keywords": ("share based compensation", "stock based compensation"),
        "exclude": ("segment", "fair value", "weighted average"),
    },
}

CORE_FIELDS = (
    "revenue", "operating_income", "net_income", "assets", "equity", "liabilities",
    "cash", "current_assets", "current_liabilities", "receivables", "inventory",
    "debt", "interest_expense", "operating_cash_flow", "capex", "sga",
    "pretax_income", "tax_expense", "eps", "cash_dividends",
)

DERIVED_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "revenue_growth": {"inputs": ("revenue", "revenue")},
    "eps_growth": {"inputs": ("eps", "eps")},
    "opm": {"inputs": ("operating_income", "revenue")},
    "roic": {"inputs": ("operating_income", "equity", "cash", "debt")},
    "debt_rate": {"inputs": ("liabilities", "equity")},
    "quick_ratio": {"inputs": ("current_assets", "inventory", "current_liabilities")},
    "interest_coverage": {"inputs": ("operating_income", "interest_expense")},
    "ocf_ratio": {"inputs": ("operating_cash_flow", "net_income")},
    "sga_ratio": {"inputs": ("sga", "revenue")},
    "roa": {"inputs": ("net_income", "assets")},
    "debt_capital": {"inputs": ("debt", "equity")},
    "ocf_debt": {"inputs": ("operating_cash_flow", "debt")},
    "fcf_debt": {"inputs": ("operating_cash_flow", "capex", "debt")},
    "dividend_coverage": {"inputs": ("net_income", "cash_dividends")},
    "dividend_payout": {"inputs": ("cash_dividends", "net_income")},
    "net_margin": {"inputs": ("net_income", "revenue")},
    "fcf_margin": {"inputs": ("operating_cash_flow", "capex", "revenue")},
    "current_ratio": {"inputs": ("current_assets", "current_liabilities")},
    "roe": {"inputs": ("net_income", "equity")},
    "net_debt": {"inputs": ("debt", "cash")},
}

def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())

def _valid_date(value: Any):
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except Exception:
        return None

def _valid_row(row: dict[str, Any], kind: str) -> bool:
    end = _valid_date(row.get("end"))
    if not end or end > TODAY:
        return False
    filed = _valid_date(row.get("filed"))
    if filed and filed < end:
        return False
    form = row.get("form") or ""
    if form not in INSTANT_FORMS:
        return False
    start = row.get("start")
    if kind == "instant":
        return not bool(start)
    if not start:
        return False
    start_date = _valid_date(start)
    if not start_date or start_date > end:
        return False
    days = (end - start_date).days
    return 300 <= days <= 380 and form in FLOW_FORMS

def _rows_by_unit(fact: dict[str, Any], kind: str):
    for unit, rows in (fact.get("units") or {}).items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if _valid_row(row, kind):
                yield unit, row

def _candidate_rank(namespace: str, alias_rank: int, row: dict[str, Any]):
    ns_rank = {"us-gaap": 3, "ifrs-full": 2}.get(namespace, 1)
    amendment = 1 if str(row.get("form","")).endswith("/A") else 0
    return (ns_rank, -alias_rank, str(row.get("end") or ""), str(row.get("filed") or ""), amendment)

def _exact_match(facts: dict[str, Any], spec: dict[str, Any]):
    found = []
    wanted = set(spec["aliases"])
    for namespace, nsfacts in (facts or {}).items():
        if not isinstance(nsfacts, dict):
            continue
        for alias_rank, tag in enumerate(spec["aliases"]):
            fact = nsfacts.get(tag)
            if not fact:
                continue
            for unit, row in _rows_by_unit(fact, spec["kind"]):
                found.append((_candidate_rank(namespace, alias_rank, row), namespace, tag, unit, row))
    if not found:
        return None
    found.sort(key=lambda x: x[0], reverse=True)
    rank, namespace, tag, unit, row = found[0]
    return {
        "namespace": namespace,
        "concept": tag,
        "unit": unit,
        "end": row.get("end"),
        "filed": row.get("filed"),
        "form": row.get("form"),
        "value": row.get("val"),
    }

def _logical_presence(exact_results: dict[str, Any]):
    out = dict(exact_results)
    debt = bool(exact_results.get("debt_total") or (
        exact_results.get("debt_current") and exact_results.get("debt_noncurrent")
    ))
    out["debt"] = debt
    # net_income / EPS are canonical alternatives only within their own family.
    return out

def _discover_fuzzy(facts: dict[str, Any], field: str, spec: dict[str, Any]):
    keywords = [_compact(k) for k in spec.get("keywords", ())]
    excludes = [_compact(k) for k in spec.get("exclude", ())]
    exact = set(spec["aliases"])
    candidates = []
    for namespace, nsfacts in (facts or {}).items():
        if namespace in {"dei", "srt", "xbrli", "country", "currency"}:
            continue
        if not isinstance(nsfacts, dict):
            continue
        for tag, fact in nsfacts.items():
            if tag in exact or not isinstance(fact, dict):
                continue
            label = str(fact.get("label") or fact.get("description") or "")
            concept_text = _compact(f"{tag} {label}")
            if not any(k in concept_text for k in keywords):
                continue
            if any(x and x in concept_text for x in excludes):
                continue
            rows = list(_rows_by_unit(fact, spec["kind"]))
            if not rows:
                continue
            # _rows_by_unit yields (unit, row); keep the pair together so
            # discovery never treats the tuple as if it were the SEC row dict.
            best_unit, best_row = max(
                rows,
                key=lambda item: (
                    str(item[1].get("end") or ""),
                    str(item[1].get("filed") or ""),
                ),
            )
            candidates.append({
                "namespace": namespace,
                "concept": tag,
                "label": label,
                "unit": best_unit,
                "end": best_row.get("end"),
                "filed": best_row.get("filed"),
            })
    return candidates

def fetch_json(session: requests.Session, url: str, retries: int = 4):
    global _SEC_LAST_REQUEST
    last_exc = None
    for attempt in range(retries):
        wait = SEC_MIN_REQUEST_INTERVAL - (time.monotonic() - _SEC_LAST_REQUEST)
        if wait > 0:
            time.sleep(wait)
        _SEC_LAST_REQUEST = time.monotonic()
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                delay = min(2 ** attempt, 16)
                retry_after = r.headers.get("Retry-After")
                if retry_after:
                    try:
                        delay = min(max(float(retry_after), 1.0), 30.0)
                    except ValueError:
                        pass
                time.sleep(delay)
                continue
            r.raise_for_status()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= retries - 1:
                raise
            time.sleep(min(2 ** attempt, 16))
    raise RuntimeError(f"SEC request failed after {retries} retries: {last_exc}")

def get_eligible(sb):
    rows = []
    offset = 0
    while True:
        page = (
            sb.table("US_Companies")
            .select("ticker,cik,company_name")
            .eq("is_fundamental_eligible", True)
            .order("ticker")
            .range(offset, offset + 999)
            .execute().data or []
        )
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return rows

def scan_company(session, row):
    ticker = str(row.get("ticker") or "").strip()
    cik = str(row.get("cik") or "").strip().zfill(10)
    url = SEC_FACTS_URL.format(cik=cik)
    facts = fetch_json(session, url)

    exact = {}
    fuzzy = {}
    concept_counter = defaultdict(Counter)
    for field, spec in FIELD_SPECS.items():
        hit = _exact_match(facts.get("facts") or {}, spec)
        exact[field] = hit
        if hit:
            concept_counter[field][f"{hit['namespace']}:{hit['concept']}"] += 1
        else:
            cand = _discover_fuzzy(facts.get("facts") or {}, field, spec)
            fuzzy[field] = cand
            for c in cand:
                concept_counter[field][f"{c['namespace']}:{c['concept']}"] += 1

    logical = _logical_presence(exact)
    logical["debt"] = bool(exact.get("debt_total") or (
        exact.get("debt_current") and exact.get("debt_noncurrent")
    ))

    core_missing = sum(1 for f in CORE_FIELDS if not logical.get(f))
    derived = {}
    for metric, info in DERIVED_REQUIREMENTS.items():
        inputs = info["inputs"]
        # Growth metrics need two fiscal years of the same family; exact current
        # presence is only the first gate and the final report labels these separately.
        if metric in {"revenue_growth", "eps_growth"}:
            derived[metric] = bool(logical.get(inputs[0]))
        else:
            derived[metric] = all(bool(logical.get(x)) for x in inputs)

    return {
        "ticker": ticker,
        "cik": cik,
        "company_name": row.get("company_name"),
        "exact": {k: bool(v) for k, v in exact.items()},
        "exact_detail": {k: v for k, v in exact.items() if v},
        "fuzzy_count": {k: len(v) for k, v in fuzzy.items()},
        "fuzzy_examples": {k: v[:5] for k, v in fuzzy.items() if v},
        "core_missing_count": core_missing,
        "derived_input_ready": derived,
        "error": None,
    }

def run_batch(args):
    if not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY or SUPABASE_KEY is required")
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    universe = get_eligible(sb)
    start = args.batch * args.batch_size
    rows = universe[start:start + args.batch_size]
    session = requests.Session()
    session.headers.update({"User-Agent": SEC_USER_AGENT})

    companies = []
    errors = Counter()
    print(f"[INVENTORY] eligible={len(universe)} batch={args.batch} rows={len(rows)} range={start}-{start+len(rows)-1 if rows else start}")

    for i, row in enumerate(rows, 1):
        try:
            companies.append(scan_company(session, row))
        except Exception as exc:
            errors[type(exc).__name__] += 1
            companies.append({
                "ticker": row.get("ticker"),
                "cik": str(row.get("cik") or ""),
                "company_name": row.get("company_name"),
                "exact": {},
                "exact_detail": {},
                "fuzzy_count": {},
                "fuzzy_examples": {},
                "core_missing_count": None,
                "derived_input_ready": {},
                "error": f"{type(exc).__name__}:{exc}",
            })
        if i % 25 == 0 or i == len(rows):
            print(f"[INVENTORY] batch={args.batch} progress={i}/{len(rows)}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "batch": args.batch,
        "batch_size": args.batch_size,
        "eligible_total": len(universe),
        "requested_rows": len(rows),
        "start": start,
        "errors": dict(errors),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "companies": companies,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"[INVENTORY] wrote={out} companies={len(companies)} errors={sum(errors.values())}")

def merge_reports(paths: list[Path], out_json: Path, out_txt: Path):
    companies = []
    for p in sorted(paths):
        payload = json.loads(p.read_text(encoding="utf-8"))
        companies.extend(payload.get("companies") or [])

    eligible = len(companies)
    successful = [c for c in companies if not c.get("error")]
    errors = [c for c in companies if c.get("error")]
    field_present = Counter()
    field_missing = Counter()
    fuzzy_companies = Counter()
    fuzzy_concepts = defaultdict(Counter)
    missing_dist = Counter()
    derived_ready = Counter()

    for c in companies:
        exact = c.get("exact") or {}
        for f in FIELD_SPECS:
            if exact.get(f):
                field_present[f] += 1
            else:
                field_missing[f] += 1
        for f, n in (c.get("fuzzy_count") or {}).items():
            if n:
                fuzzy_companies[f] += 1
        for f, examples in (c.get("fuzzy_examples") or {}).items():
            for ex in examples:
                fuzzy_concepts[f][f"{ex.get('namespace')}:{ex.get('concept')}"] += 1
        m = c.get("core_missing_count")
        if m is not None:
            missing_dist[str(m if m <= 5 else "5+")] += 1
        for metric, ready in (c.get("derived_input_ready") or {}).items():
            if ready:
                derived_ready[metric] += 1

    # Growth readiness is a current-field gate, not a verified two-year pair.
    # Keep it labeled as input-present to avoid overstating recoverability.
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eligible_companies": eligible,
        "successful_companyfacts_fetch": len(successful),
        "companyfacts_errors": len(errors),
        "field_coverage": {
            f: {
                "present": field_present[f],
                "missing": field_missing[f],
                "coverage_pct": round(field_present[f] / eligible * 100, 2) if eligible else 0,
            }
            for f in FIELD_SPECS
        },
        "core_field_missing_distribution": dict(
            sorted(missing_dist.items(), key=lambda kv: (99 if kv[0] == "5+" else int(kv[0])))
        ),
        "derived_metric_input_ready": {
            m: {
                "ready_companies": derived_ready[m],
                "not_ready_companies": eligible - derived_ready[m],
                "ready_pct": round(derived_ready[m] / eligible * 100, 2) if eligible else 0,
            }
            for m in DERIVED_REQUIREMENTS
        },
        "fuzzy_review_candidates": {
            f: {
                "companies_with_candidate": fuzzy_companies[f],
                "top_concepts": [
                    {"concept": concept, "company_occurrences": count}
                    for concept, count in fuzzy_concepts[f].most_common(25)
                ],
            }
            for f in FIELD_SPECS
        },
        "errors": [
            {"ticker": c.get("ticker"), "error": c.get("error")}
            for c in errors[:100]
        ],
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps({"summary": report, "companies": companies}, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = []
    lines.append("US CANONICAL FIELD INVENTORY")
    lines.append("=" * 72)
    lines.append(f"Eligible companies: {eligible:,}")
    lines.append(f"Successful SEC Company Facts fetch: {len(successful):,}")
    lines.append(f"Company Facts errors: {len(errors):,}")
    lines.append("")
    lines.append("1) CORE SOURCE FIELD COVERAGE (ALL COMPANIES)")
    lines.append("-" * 72)
    for f, data in report["field_coverage"].items():
        lines.append(f"{f:32s} present={data['present']:5d} missing={data['missing']:5d} coverage={data['coverage_pct']:6.2f}%")
    lines.append("")
    lines.append("2) CORE 20-FIELD MISSING DISTRIBUTION")
    lines.append("-" * 72)
    for k, v in report["core_field_missing_distribution"].items():
        lines.append(f"missing {k:>2s}: {v:5d}")
    lines.append("")
    lines.append("3) DERIVED METRIC INPUT READINESS")
    lines.append("-" * 72)
    for m, data in report["derived_metric_input_ready"].items():
        lines.append(f"{m:24s} ready={data['ready_companies']:5d} not_ready={data['not_ready_companies']:5d} ready={data['ready_pct']:6.2f}%")
    lines.append("")
    lines.append("4) FUZZY REVIEW CANDIDATES (NOT ACCEPTED AS DATA)")
    lines.append("-" * 72)
    for f, data in report["fuzzy_review_candidates"].items():
        if not data["companies_with_candidate"]:
            continue
        lines.append(f"[{f}] companies_with_candidate={data['companies_with_candidate']}")
        for x in data["top_concepts"][:10]:
            lines.append(f"  {x['concept']} -> {x['company_occurrences']}")
    lines.append("")
    lines.append("Interpretation")
    lines.append("- Exact coverage is the current conservative canonical mapping.")
    lines.append("- Fuzzy candidates are discovery evidence only; they are not promoted automatically.")
    lines.append("- 'Core missing distribution' counts logical source families, with debt treated as present when total debt exists OR current+noncurrent debt both exist.")
    lines.append("- Growth readiness in this inventory means the current raw field exists; a later two-year pair validation is still required.")
    out_txt.parent.mkdir(parents=True, exist_ok=True)
    out_txt.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))

def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    b = sub.add_parser("batch")
    b.add_argument("--batch", type=int, required=True)
    b.add_argument("--batch-size", type=int, default=500)
    b.add_argument("--out", required=True)
    m = sub.add_parser("merge")
    m.add_argument("--input-dir", required=True)
    m.add_argument("--out-json", required=True)
    m.add_argument("--out-txt", required=True)
    args = parser.parse_args()
    if args.mode == "batch":
        run_batch(args)
    else:
        paths = sorted(Path(args.input_dir).glob("batch-*.json"))
        if not paths:
            raise RuntimeError(f"No batch reports in {args.input_dir}")
        merge_reports(paths, Path(args.out_json), Path(args.out_txt))

if __name__ == "__main__":
    main()
