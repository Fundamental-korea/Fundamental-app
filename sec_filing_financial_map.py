"""SEC filing-first financial map for US fundamental data.

Economic-first rules:
- The latest annual filing's Inline XBRL is the primary source.
- Standard concepts are preferred, but issuer custom concepts are eligible when
  their economic meaning clearly represents issuer debt or interest expense.
- Debt investment assets, interest rates, interest paid, interest income,
  pension/defined-benefit interest cost, and debt activity/maturity facts are
  explicitly excluded.
- Instant balance-sheet facts and annual duration facts are handled separately.
- No database writes and no synthetic values unless explicitly derived from
  the same filing context.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import math
import re
from typing import Any, Iterable

from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8
from sec_xbrl_search_v2_3_4 import _annual_duration, _date

KNOWN_TAXONOMIES = {"us-gaap", "ifrs-full", "srt", "dei", "xbrli", "country", "currency"}

CORE_EQUITY = {
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "Equity",
    "EquityAttributableToOwnersOfParent",
    "ProprietaryCapital",
    "PartnersCapital",
    "MembersEquity",
}
CORE_NCI = {
    "MinorityInterest",
    "NoncontrollingInterestInConsolidatedEntity",
    "NoncontrollingInterestInConsolidatedEntityIncludingPortionAttributableToRedeemableNoncontrollingInterest",
}
CORE_CASH = {
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "CashAndCashEquivalents",
    "CashAndRestrictedCash",
}
CORE_OPERATING_INCOME = {
    "OperatingIncomeLoss",
    "OperatingIncome",
    "OperatingProfitLoss",
    "IncomeFromOperations",
    "ProfitLossFromOperatingActivities",
}
CORE_PRETAX = {
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
    "ProfitLossBeforeTax",
}
CORE_OTHER_NONOPERATING = {
    "OtherNonoperatingIncomeExpense",
    "OtherNonoperatingIncome",
    "OtherNonoperatingExpense",
    "NonoperatingIncomeExpense",
    "OtherIncomeExpenseNet",
}

STANDARD_DEBT_TOTAL = {
    "LongTermDebtCurrentAndNoncurrent",
    "Debt",
    "TotalDebt",
    "DebtAndFinanceLeaseLiabilities",
    "DebtAndCapitalLeaseObligations",
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebtAndFinanceLeaseObligations",
    "DebtLongtermAndShorttermCombinedAmount",
    "Borrowings",
}
STANDARD_DEBT_CURRENT = {
    "LongTermDebtCurrent",
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "LongTermDebtAndFinanceLeaseObligationsCurrent",
    "DebtAndCapitalLeaseObligationsCurrent",
    "DebtAndFinanceLeaseLiabilitiesCurrent",
    "CurrentBorrowings",
    "CurrentPortionOfLongtermBorrowings",
    "ShortTermBorrowings",
    "OtherShortTermBorrowings",
    "ConvertibleDebtCurrent",
    "ConvertibleNotesPayableCurrent",
    "NotesPayableCurrent",
    "NotesAndLoansPayableCurrent",
    "CommercialPaper",
    "DebtCurrent",
    "LineOfCreditCurrent",
    "RevolvingCreditFacilityCurrent",
    "FederalHomeLoanBankAdvancesShortTerm",
    "ShorttermBorrowings",
    "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings",
}
STANDARD_DEBT_NONCURRENT = {
    "LongTermDebt",
    "LongTermDebtNoncurrent",
    "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
    "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
    "DebtAndCapitalLeaseObligationsNoncurrent",
    "DebtAndFinanceLeaseLiabilitiesNoncurrent",
    "NoncurrentBorrowings",
    "LongtermBorrowings",
    "LoansPayable",
    "LongTermLoansPayable",
    "NotesPayableNoncurrent",
    "LongTermNotesPayable",
    "NotesAndLoansPayable",
    "ConvertibleDebtNoncurrent",
    "ConvertibleNotes",
    "ConvertibleNotesPayable",
    "SeniorNotes",
    "SeniorNotesPayable",
    "SeniorSecuredNotes",
    "SeniorUnsecuredNotes",
    "SubordinatedNotes",
    "DebtObligations",
    "DebtLiabilities",
    "OtherDebt",
    "OtherDebtNoncurrent",
    "OtherBorrowings",
    "UnsecuredDebt",
    "UnsecuredLongTermDebt",
    "SecuredDebt",
    "OtherLongTermDebt",
    "FederalHomeLoanBankAdvancesLongTerm",
    "FederalHomeLoanBankAdvances",
    "LongTermNotesAndLoans",
    "DebtNoncurrent",
    "LineOfCreditNoncurrent",
    "RevolvingCreditFacility",
}
STANDARD_DEBT_CARRYING = {
    "DebtInstrumentCarryingAmount",
    "LongTermDebtCarryingAmount",
}

FINANCE_LEASE_CONCEPTS = {
    "FinanceLeaseLiabilityCurrent",
    "FinanceLeaseLiabilityNoncurrent",
    "FinanceLeaseLiability",
    "FinanceLeaseObligation",
    "CapitalLeaseObligation",
    "CapitalLeaseObligationsCurrent",
    "CapitalLeaseObligationsNoncurrent",
}
OPERATING_LEASE_CONCEPTS = {
    "OperatingLeaseLiabilityCurrent",
    "OperatingLeaseLiabilityNoncurrent",
    "OperatingLeaseLiability",
}

DEBT_EXCLUSIONS = (
    "availableforsale",
    "tradingsecurities",
    "debtsecurities",
    "securitiesdebt",
    "financingreceivable",
    "receivable",
    "debtinstrumentinterestrate",
    "debtweightedaveragerate",
    "weightedaverageinterestrate",
    "interestrate",
    "maturitiesrepayments",
    "proceedsfromissuance",
    "repayments",
    "repayment",
    "extinguishment",
    "redemption",
    "fairvalue",
    "unamortized",
    "discountpremium",
    "interestpayable",
    "interestpaid",
    "interestincome",
    "interestcostscapitalized",
    "definedbenefitplan",
    "faceamount",
    "maximumborrowingcapacity",
    "remainingborrowingcapacity",
    "availablecredit",
    "undrawnborrowing",
    "debtinstrumentheld",
    "debtinstrumentsheld",
    "securitiesheld",
    "netdebt",
    "capitalization",
    "conversionprice",
    "conversionratio",
    "conversionfeature",
    "borrowingcapacity",
    "borrowinglimit",
    "borrowinglimits",
    "shorttermborrowinglimit",
    "maximumindebtedness",
    "authorizedborrowings",
    "authorizedshorttermborrowings",
    "undrawnborrowingfacilities",
    "unusedborrowingcapacity",
    "unusedborrowingfacilities",
    "availableborrowingcapacity",
    "amountoftotalborrowingcapacity",
)

STANDARD_INTEREST_GROSS = {
    "InterestExpense",
    "InterestExpenseNonoperating",
    "InterestExpenseNonOperating",
    "InterestExpenseDebt",
    "InterestAndDebtExpense",
    "InterestExpenseNonoperatingAndOther",
    "InterestExpenseRelatedParties",
    "InterestExpenseNonOperatingAndOther",
    "InterestExpenseDebtExcludingAmortization",
    "FinanceCosts",
}
STANDARD_INTEREST_NET = {
    "InterestIncomeExpenseNet",
    "InterestIncomeExpenseNonoperatingNet",
    "InterestExpenseNonOperatingNet",
}

STANDARD_INTEREST_COMPONENTS = {
    "InterestExpenseOnDebtInstrumentsIssued",
    "InterestExpenseOnBorrowings",
    "InterestExpenseOnOtherFinancialLiabilities",
    "InterestExpenseOnBankLoansAndOverdrafts",
    "InterestExpenseOnBonds",
    "InterestExpenseLongTermDebt",
    "InterestExpenseShortTermBorrowings",
    "InterestExpenseOtherLongTermDebt",
    "InterestExpenseOtherShortTermBorrowings",
    "InterestExpenseSubordinatedNotesAndDebentures",
    "InterestExpenseFederalHomeLoanBankAndFederalReserveBankAdvancesLongTerm",
    "InterestExpenseFederalHomeLoanBankAndFederalReserveBankAdvancesShortTerm",
}

INTEREST_COMPONENT_AGGREGATES = {
    "InterestExpenseOnDebtInstrumentsIssued",
    "InterestExpenseOnBorrowings",
}

INTEREST_ADDITIVE_BUCKETS = {
    "InterestExpenseLongTermDebt": "long_term",
    "InterestExpenseOtherLongTermDebt": "long_term",
    "InterestExpenseFederalHomeLoanBankAndFederalReserveBankAdvancesLongTerm": "long_term",
    "InterestExpenseOnBonds": "long_term",
    "InterestExpenseShortTermBorrowings": "short_term",
    "InterestExpenseOtherShortTermBorrowings": "short_term",
    "InterestExpenseFederalHomeLoanBankAndFederalReserveBankAdvancesShortTerm": "short_term",
}
INTEREST_EXCLUSIONS = (
    "interestrate",
    "interestpaid",
    "interestpayable",
    "interestincome",
    "dividendinterestincome",
    "definedbenefitplan",
    "pension",
    "capitalizedinterest",
    "interestcostscapitalized",
    "financeleaseinterestexpense",
    "interestexpenseonleaseliabilities",
    "operatingleaseinterestexpense",
    "amortizationoffinancingcosts",
    "financingfees",
    "debtissuancecosts",
    "unrecognizedtaxbenefits",
    "taxpenalty",
    "taxinterest",
    "interestontax",
    "noninterestexpense",
    "netoftax",
    "fairvalue",
    "derivative",
)
ACTIVITY_EXCLUSIONS = (
    "proceeds",
    "repayment",
    "repayments",
    "maturities",
    "redemption",
    "extinguishment",
    "refinanced",
    "payments",
    "increase",
    "decrease",
)

@dataclass(frozen=True)
class FinancialFact:
    namespace: str
    concept: str
    label: str
    value: float
    unit: str | None
    start: str | None
    end: str
    filed: str | None
    form: str | None
    instant: bool
    dimensioned: bool
    context_ref: str | None
    category: str
    confidence: str
    reason: str

def _local(concept: str | None) -> str:
    if not concept:
        return ""
    return str(concept).rsplit("}", 1)[-1].rsplit(":", 1)[-1]

def _compact(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())

def _finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None

def _target_end(row: dict[str, Any], target_year: int | None) -> bool:
    end = _date(row.get("end"))
    return bool(end) and (target_year is None or end.year == int(target_year))

def _annual_duration_row(row: dict[str, Any], target_year: int | None) -> bool:
    return _target_end(row, target_year) and bool(row.get("start")) and _annual_duration(row.get("start"), row.get("end"))

def _instant_row(row: dict[str, Any], target_year: int | None) -> bool:
    return _target_end(row, target_year) and bool(row.get("instant")) and not bool(row.get("start"))

def _is_currency(unit: str | None) -> bool:
    if not unit:
        return False
    u = unit.lower()
    return "iso4217:" in u or u in {"usd", "cny", "eur", "gbp", "jpy", "cad", "aud"}

def _is_bad_debt(concept: str, label: str) -> bool:
    text = _compact(concept + " " + label)
    return any(token in text for token in DEBT_EXCLUSIONS)

def classify_debt_fact(row: dict[str, Any]) -> tuple[str | None, str, str]:
    concept = _local(row.get("concept"))
    label = row.get("label") or ""
    compact = _compact(concept + " " + label)
    namespace = row.get("namespace") or ""

    if _is_bad_debt(concept, label):
        return None, "exclude", "debt investment, metadata, maturity/activity, interest, or other look-alike"

    if concept in STANDARD_DEBT_TOTAL:
        return "issuer_debt_total", "high", "canonical issuer debt total"

    if concept in STANDARD_DEBT_CURRENT:
        return "issuer_debt_current", "high", "canonical current issuer borrowing/debt"

    if concept in STANDARD_DEBT_NONCURRENT:
        return "issuer_debt_noncurrent", "high", "canonical noncurrent issuer borrowing/debt"

    if concept in OPERATING_LEASE_CONCEPTS or "operatingleaseliability" in compact:
        return "operating_lease_liability", "high", "explicit operating lease liability"

    if concept in FINANCE_LEASE_CONCEPTS or "financeleaseliability" in compact or "capitalleaseobligation" in compact:
        return "finance_lease_liability", "high", "explicit finance/capital lease liability"

    if concept in STANDARD_DEBT_CARRYING:
        return "debt_carrying_amount_candidate", "medium", "debt instrument carrying amount; reconcile before use"

    debt_terms = (
        "debt", "borrowings", "borrowing", "loanspayable", "notespayable",
        "seniornotes", "subordinatednotes", "convertnotepayable", "convertiblenotes",
        "debtobligations", "debtliabilities", "otherdebt", "otherborrowings",
        "creditfacility", "revolvingcreditfacility", "termloan",
        "bankloans", "loansreceived", "loanpayable", "longtermnotesandloans",
        "federalhomeloanbankadvances",
    )

    if namespace in {"us-gaap", "ifrs-full"} and any(k in compact for k in debt_terms):
        if any(token in compact for token in ACTIVITY_EXCLUSIONS):
            return None, "exclude", "debt activity/maturity fact"
        if any(token in compact for token in ("current", "shortterm", "currentportion", "withinoneyear")):
            return "issuer_debt_current", "medium", "standard-taxonomy current debt-like concept"
        if any(token in compact for token in ("noncurrent", "longterm")):
            return "issuer_debt_noncurrent", "medium", "standard-taxonomy noncurrent debt-like concept"
        return "issuer_debt_other", "medium", "standard-taxonomy debt-like concept"

    if namespace not in KNOWN_TAXONOMIES and any(k in compact for k in debt_terms):
        if any(token in compact for token in ACTIVITY_EXCLUSIONS):
            return None, "exclude", "custom debt activity/metadata fact"
        if any(token in compact for token in ("current", "shortterm", "currentportion", "withinoneyear")):
            return "custom_issuer_debt_current", "medium", "custom taxonomy current debt-like concept"
        if any(token in compact for token in ("noncurrent", "longterm")):
            return "custom_issuer_debt_noncurrent", "medium", "custom taxonomy noncurrent debt-like concept"
        return "custom_issuer_debt", "medium", "custom taxonomy issuer debt-like concept"

    return None, "none", ""

def classify_interest_fact(row: dict[str, Any]) -> tuple[str | None, str, str]:
    concept = _local(row.get("concept"))
    label = row.get("label") or ""
    compact = _compact(concept + " " + label)
    namespace = row.get("namespace") or ""

    if concept in STANDARD_INTEREST_GROSS:
        return "gross_interest_expense", "high", "canonical gross interest expense / finance cost"

    if concept in STANDARD_INTEREST_NET:
        return "net_interest_expense", "medium", "canonical net interest expense fallback"

    if concept in STANDARD_INTEREST_COMPONENTS:
        return "interest_expense_component", "medium", "debt-linked interest expense component"

    if any(token in compact for token in INTEREST_EXCLUSIONS):
        return None, "exclude", "interest rate/paid/payable/income/pension/capitalized/look-alike"

    if namespace in {"us-gaap", "ifrs-full"} and any(_compact(k) in compact for k in (
        "interest expense", "interest cost", "finance cost", "finance costs", "financing cost",
        "financing costs", "borrowing costs", "debt expense"
    )):
        if any(token in compact for token in ACTIVITY_EXCLUSIONS):
            return None, "exclude", "interest activity/metadata fact"
        return "gross_interest_expense_other", "medium", "standard-taxonomy interest expense-like concept"

    if namespace not in KNOWN_TAXONOMIES:
        custom_component = (
            "interestexpenseon" in compact
            or (
                "intereston" in compact
                and any(term in compact for term in (
                    "loan", "loans", "borrow", "borrowing", "debt", "bond", "note"
                ))
            )
        )
        custom_expense = any(_compact(k) in compact for k in (
            "interest expense", "interest cost", "finance costs", "financing costs", "debt expense"
        ))
        if custom_component:
            if any(token in compact for token in ACTIVITY_EXCLUSIONS):
                return None, "exclude", "custom interest activity/metadata fact"
            return "custom_interest_expense_component", "medium", "custom borrowing-linked interest expense component"
        if custom_expense:
            if any(token in compact for token in ACTIVITY_EXCLUSIONS):
                return None, "exclude", "custom interest activity/metadata fact"
            return "custom_interest_expense", "medium", "custom taxonomy interest expense-like concept"

    return None, "none", ""

def _to_fact(row: dict[str, Any], category: str, confidence: str, reason: str) -> FinancialFact | None:
    value = _finite(row.get("value"))
    end = row.get("end")
    if value is None or not end:
        return None
    return FinancialFact(
        namespace=row.get("namespace") or "",
        concept=_local(row.get("concept")),
        label=row.get("label") or "",
        value=value,
        unit=row.get("unit"),
        start=row.get("start"),
        end=end,
        filed=row.get("filed"),
        form=row.get("form"),
        instant=bool(row.get("instant")),
        dimensioned=bool(row.get("dimensioned")),
        context_ref=row.get("contextRef"),
        category=category,
        confidence=confidence,
        reason=reason,
    )

def _core_fact(row: dict[str, Any], concepts: set[str], target_year: int | None) -> bool:
    return (
        _instant_row(row, target_year)
        and not row.get("dimensioned")
        and _local(row.get("concept")) in concepts
        and _finite(row.get("value")) is not None
        and _is_currency(row.get("unit"))
    )

def _select_core(rows: list[dict[str, Any]], concepts: set[str], target_year: int | None, prefer=("USD",)) -> FinancialFact | None:
    candidates = [r for r in rows if _core_fact(r, concepts, target_year)]
    if not candidates:
        return None
    pref = {x.lower() for x in prefer}
    candidates.sort(key=lambda r: (
        1 if (r.get("unit") or "").lower().endswith(":usd") or (r.get("unit") or "").lower() in pref else 0,
        r.get("filed") or "",
    ), reverse=True)
    concept_rank = {name: i for i, name in enumerate(concepts)}
    candidates.sort(key=lambda r: (concept_rank.get(_local(r.get("concept")), 999),), reverse=False)
    return _to_fact(candidates[0], "core", "high", "canonical filing concept")

def _same_context(rows: list[dict[str, Any]], concepts: set[str], target_year: int | None) -> list[dict[str, Any]]:
    return [
        r for r in rows
        if _instant_row(r, target_year)
        and not r.get("dimensioned")
        and _local(r.get("concept")) in concepts
        and _finite(r.get("value")) is not None
    ]

def _select_equity(rows: list[dict[str, Any]], target_year: int | None) -> FinancialFact | None:
    candidates = _same_context(rows, CORE_EQUITY, target_year)
    if not candidates:
        return None
    rank = {name: i for i, name in enumerate([
        "EquityAttributableToOwnersOfParent", "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "Equity", "ProprietaryCapital", "PartnersCapital", "MembersEquity",
    ])}
    candidates.sort(key=lambda r: (rank.get(_local(r.get("concept")), 999), -(1 if _is_currency(r.get("unit")) else 0), -(1 if "USD" in str(r.get("unit")).upper() else 0)))
    chosen = candidates[0]
    fact = _to_fact(chosen, "core_equity", "high", "canonical filing equity concept")
    if not fact:
        return None
    if "IncludingPortionAttributableToNoncontrollingInterest" in fact.concept:
        nci = _select_core(rows, CORE_NCI, target_year)
        if nci and nci.end == fact.end and nci.unit == fact.unit:
            return FinancialFact(
                **{**asdict(fact), "value": fact.value - nci.value,
                   "confidence": "high",
                   "reason": "parent-attributable equity: consolidated equity minus NCI"}
            )
    return fact

def _select_cash(rows: list[dict[str, Any]], target_year: int | None) -> FinancialFact | None:
    candidates = _same_context(rows, CORE_CASH, target_year)
    if not candidates:
        return None
    rank = {name: i for i, name in enumerate([
        "CashAndCashEquivalentsAtCarryingValue",
        "CashAndCashEquivalents",
        "CashAndRestrictedCash",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ])}
    candidates.sort(key=lambda r: (rank.get(_local(r.get("concept")), 999), -(1 if "USD" in str(r.get("unit")).upper() else 0)))
    return _to_fact(candidates[0], "core_cash", "high" if rank.get(_local(candidates[0].get("concept")), 99) < 2 else "medium", "canonical filing cash concept")

def _select_operating_income(rows: list[dict[str, Any]], target_year: int | None) -> FinancialFact | None:
    candidates = [
        r for r in rows
        if _annual_duration_row(r, target_year)
        and not r.get("dimensioned")
        and _local(r.get("concept")) in CORE_OPERATING_INCOME
        and _finite(r.get("value")) is not None
        and _is_currency(r.get("unit"))
    ]
    if not candidates:
        return None
    rank = {name: i for i, name in enumerate([
        "OperatingIncomeLoss", "OperatingIncome", "OperatingProfitLoss",
        "IncomeFromOperations", "ProfitLossFromOperatingActivities",
    ])}
    candidates.sort(key=lambda r: (rank.get(_local(r.get("concept")), 999), -(1 if "USD" in str(r.get("unit")).upper() else 0), r.get("filed") or ""), reverse=False)
    return _to_fact(candidates[0], "operating_income", "high", "canonical annual operating income concept")

def _select_annual_flow(rows: list[dict[str, Any]], concepts: set[str], target_year: int | None) -> list[FinancialFact]:
    out = []
    for r in rows:
        if not (_annual_duration_row(r, target_year) and not r.get("dimensioned")):
            continue
        if _local(r.get("concept")) not in concepts:
            continue
        if not _finite(r.get("value")) or not _is_currency(r.get("unit")):
            continue
        fact = _to_fact(r, "flow_support", "high", "same-filing annual support fact")
        if fact:
            out.append(fact)
    return out

def _compatible_instant(fact: FinancialFact | None, anchor: FinancialFact | None) -> bool:
    if fact is None or anchor is None:
        return True
    return fact.end == anchor.end and fact.unit == anchor.unit


def _compatible_flow(fact: FinancialFact | None, anchor: FinancialFact | None) -> bool:
    if fact is None or anchor is None:
        return True
    if fact.end != anchor.end or fact.unit != anchor.unit:
        return False
    # Same fiscal-period end and currency is required.  Start dates can differ
    # by a few days for 52/53-week fiscal calendars, so they are not required
    # to be byte-for-byte identical here.
    return True


def _select_component_interest(components: list[FinancialFact]) -> dict[str, Any] | None:
    """Select a debt-linked interest component when no direct gross total exists."""
    if not components:
        return None
    unique = {}
    for fact in components:
        key = (fact.concept, fact.value, fact.start, fact.end, fact.unit, fact.context_ref)
        unique[key] = fact
    items = list(unique.values())

    aggregate = [x for x in items if x.concept in INTEREST_COMPONENT_AGGREGATES]
    if aggregate:
        priority = {
            "InterestExpenseOnDebtInstrumentsIssued": 0,
            "InterestExpenseOnBorrowings": 1,
        }
        chosen = sorted(aggregate, key=lambda x: (priority.get(x.concept, 99), x.filed or ""))[0]
        return {
            "value": abs(chosen.value),
            "basis": "reported_component_aggregate",
            "category": "interest_component_expense",
            "concept": chosen.concept,
            "namespace": chosen.namespace,
            "confidence": "high",
            "unit": chosen.unit,
            "components": [asdict(chosen)],
        }

    if len(items) == 1:
        chosen = items[0]
        return {
            "value": abs(chosen.value),
            "basis": "reported_interest_component",
            "category": "interest_component_expense",
            "concept": chosen.concept,
            "namespace": chosen.namespace,
            "confidence": chosen.confidence,
            "unit": chosen.unit,
            "components": [asdict(chosen)],
        }

    groups: dict[tuple[Any, ...], list[FinancialFact]] = {}
    for fact in items:
        key = (fact.end, fact.unit, fact.start, fact.context_ref or "")
        groups.setdefault(key, []).append(fact)

    candidates = []
    for group in groups.values():
        bucketed = {}
        for fact in group:
            bucket = INTEREST_ADDITIVE_BUCKETS.get(fact.concept)
            if bucket is None:
                continue
            previous = bucketed.get(bucket)
            if previous is None or (fact.filed or "") > (previous.filed or ""):
                bucketed[bucket] = fact
        if {"long_term", "short_term"}.issubset(bucketed):
            selected = [bucketed["long_term"], bucketed["short_term"]]
            candidates.append({
                "value": sum(abs(x.value) for x in selected),
                "basis": "aggregated_interest_components",
                "category": "interest_component_expense",
                "concept": "+".join(x.concept for x in selected),
                "namespace": (
                    selected[0].namespace if selected[0].namespace == selected[1].namespace
                    else f"{selected[0].namespace}+{selected[1].namespace}"
                ),
                "confidence": "high",
                "unit": selected[0].unit,
                "components": [asdict(x) for x in selected],
            })

    if candidates:
        return candidates[0]

    chosen = sorted(items, key=lambda x: (x.confidence != "high", x.filed or ""))[0]
    return {
        "value": abs(chosen.value),
        "basis": "reported_interest_component",
        "category": "interest_component_expense",
        "concept": chosen.concept,
        "namespace": chosen.namespace,
        "confidence": chosen.confidence,
        "unit": chosen.unit,
        "components": [asdict(chosen)],
    }


def classify_filing_rows(rows: Iterable[dict[str, Any]], target_year: int | None = None) -> dict[str, Any]:
    rows = list(rows)
    debt: list[FinancialFact] = []
    interest: list[FinancialFact] = []
    unclassified_debt_like: list[dict[str, Any]] = []
    unclassified_interest_like: list[dict[str, Any]] = []
    for row in rows:
        concept = _local(row.get("concept"))
        label = row.get("label") or ""
        compact = _compact(concept + " " + label)
        category, confidence, reason = classify_debt_fact(row)
        if category and _instant_row(row, target_year) and not row.get("dimensioned") and _is_currency(row.get("unit")):
            fact = _to_fact(row, category, confidence, reason)
            if fact:
                debt.append(fact)
        elif (
            _instant_row(row, target_year)
            and not row.get("dimensioned")
            and _is_currency(row.get("unit"))
            and any(k in compact for k in (
                "debt", "borrow", "borrowing", "loan", "notespayable",
                "seniornotes", "subordinatednotes", "convertiblenotes",
                "creditfacility", "revolvingcreditfacility", "termloan",
            ))
            and not _is_bad_debt(concept, label)
        ):
            unclassified_debt_like.append({
                "namespace": row.get("namespace") or "",
                "concept": concept,
                "label": label,
                "value": row.get("value"),
                "unit": row.get("unit"),
                "end": row.get("end"),
                "contextRef": row.get("contextRef"),
            })
        category, confidence, reason = classify_interest_fact(row)
        if category and _annual_duration_row(row, target_year) and not row.get("dimensioned") and _is_currency(row.get("unit")):
            fact = _to_fact(row, category, confidence, reason)
            if fact:
                interest.append(fact)
        elif (
            _annual_duration_row(row, target_year)
            and not row.get("dimensioned")
            and _is_currency(row.get("unit"))
            and any(k in compact for k in (
                "interestexpense", "interestcost", "financecost",
                "financingcost", "borrowingcost", "debtexpense",
            ))
            and not any(token in compact for token in INTEREST_EXCLUSIONS)
        ):
            unclassified_interest_like.append({
                "namespace": row.get("namespace") or "",
                "concept": concept,
                "label": label,
                "value": row.get("value"),
                "unit": row.get("unit"),
                "start": row.get("start"),
                "end": row.get("end"),
                "contextRef": row.get("contextRef"),
            })

    equity = _select_equity(rows, target_year)
    cash = _select_cash(rows, target_year)
    operating_income = _select_operating_income(rows, target_year)

    # ROIC capital components must be measured on the same balance-sheet date
    # and in the same reporting currency.
    if equity and cash and not _compatible_instant(cash, equity):
        cash = None

    debt_eligible = [x for x in debt if _compatible_instant(x, equity)]
    total = [x for x in debt_eligible if x.category == "issuer_debt_total"]
    current = [x for x in debt_eligible if x.category == "issuer_debt_current"]
    noncurrent = [x for x in debt_eligible if x.category == "issuer_debt_noncurrent"]
    other = [x for x in debt_eligible if x.category in {"issuer_debt_other", "custom_issuer_debt"}]
    carrying = [x for x in debt_eligible if x.category == "debt_carrying_amount_candidate"]

    selected_debt = None
    if total:
        chosen = sorted(total, key=lambda x: (0 if x.confidence == "high" else 1, x.filed or ""))[0]
        selected_debt = {
            "value": chosen.value, "basis": "reported_total", "category": chosen.category,
            "concept": chosen.concept, "namespace": chosen.namespace, "confidence": chosen.confidence,
            "unit": chosen.unit, "components": [asdict(chosen)],
        }
    else:
        pairs = [(c, n) for c in current for n in noncurrent if c.end == n.end and c.unit == n.unit]
        if pairs:
            c, n = sorted(pairs, key=lambda pair: (pair[0].filed or "", pair[1].filed or ""), reverse=True)[0]
            selected_debt = {
                "value": c.value + n.value, "basis": "current_plus_noncurrent",
                "category": "issuer_debt_components",
                "concept": f"{c.concept}+{n.concept}",
                "namespace": c.namespace if c.namespace == n.namespace else f"{c.namespace}+{n.namespace}",
                "confidence": "high" if c.confidence == n.confidence == "high" else "medium",
                "unit": c.unit, "components": [asdict(c), asdict(n)],
            }
        elif other:
            chosen = sorted(other, key=lambda x: (x.confidence != "high", x.filed or ""))[0]
            selected_debt = {
                "value": chosen.value, "basis": "reported_other_debt",
                "category": chosen.category, "concept": chosen.concept,
                "namespace": chosen.namespace, "confidence": chosen.confidence,
                "unit": chosen.unit, "components": [asdict(chosen)],
            }
        elif len(carrying) == 1:
            chosen = carrying[0]
            selected_debt = {
                "value": chosen.value, "basis": "carrying_amount_candidate",
                "category": chosen.category, "concept": chosen.concept,
                "namespace": chosen.namespace, "confidence": "medium",
                "unit": chosen.unit, "components": [asdict(chosen)],
            }

    interest_eligible = [x for x in interest if _compatible_flow(x, operating_income)]
    gross = [x for x in interest_eligible if x.category == "gross_interest_expense"]
    gross_other = [x for x in interest_eligible if x.category in {"gross_interest_expense_other", "custom_interest_expense"}]
    net = [x for x in interest_eligible if x.category == "net_interest_expense"]

    selected_interest = None
    if gross:
        chosen = sorted(
            gross,
            key=lambda x: (
                x.concept not in {
                    "InterestExpenseNonoperating",
                    "InterestExpenseDebt",
                    "InterestExpense",
                    "FinanceCosts",
                },
                x.filed or "",
            ),
        )[0]
        selected_interest = {
            "value": abs(chosen.value),
            "basis": "reported_gross_interest_expense",
            "category": chosen.category,
            "concept": chosen.concept,
            "namespace": chosen.namespace,
            "confidence": chosen.confidence,
            "unit": chosen.unit,
            "components": [asdict(chosen)],
        }
        if chosen.value == 0:
            selected_interest["zero_reported"] = True
    elif gross_other:
        chosen = sorted(
            gross_other,
            key=lambda x: (x.confidence != "high", x.filed or ""),
        )[0]
        selected_interest = {
            "value": abs(chosen.value),
            "basis": "other_interest_expense",
            "category": chosen.category,
            "concept": chosen.concept,
            "namespace": chosen.namespace,
            "confidence": chosen.confidence,
            "unit": chosen.unit,
            "components": [asdict(chosen)],
        }
    else:
        component_selected = _select_component_interest(
            [
                x for x in interest_eligible
                if x.category in {
                    "interest_expense_component",
                    "custom_interest_expense_component",
                }
            ]
        )
        if component_selected is not None:
            selected_interest = component_selected
        elif net:
            chosen = sorted(
                net,
                key=lambda x: (x.end, x.filed or ""),
                reverse=True,
            )[0]
            selected_interest = {
                "value": abs(chosen.value),
                "basis": "reported_net_interest_expense",
                "category": chosen.category,
                "concept": chosen.concept,
                "namespace": chosen.namespace,
                "confidence": chosen.confidence,
                "unit": chosen.unit,
                "components": [asdict(chosen)],
            }
    lease_only = bool(debt) and not selected_debt and all(x.category in {"finance_lease_liability", "operating_lease_liability"} for x in debt)
    debt_status = (
        "FOUND_STANDARD" if selected_debt and any(c["namespace"] in {"us-gaap", "ifrs-full"} for c in selected_debt["components"])
        else "FOUND_CUSTOM" if selected_debt
        else "LEASE_ONLY" if lease_only
        else "UNRESOLVED"
    )
    interest_status = "UNRESOLVED"
    if selected_interest:
        if selected_interest.get("zero_reported"):
            interest_status = "ZERO_CONFIRMED"
        elif selected_interest["category"] == "net_interest_expense":
            interest_status = "FOUND_NET_ONLY"
        elif selected_interest["category"] in {"interest_component_expense", "custom_interest_expense_component"}:
            interest_status = "FOUND_COMPONENTS"
        elif selected_interest["category"] == "custom_interest_expense":
            interest_status = "FOUND_CUSTOM"
        else:
            interest_status = "FOUND_GROSS"

    pretax = _select_annual_flow(rows, CORE_PRETAX, target_year)
    other_nonop = _select_annual_flow(rows, CORE_OTHER_NONOPERATING, target_year)

    return {
        "roic_inputs": {
            "equity": asdict(equity) if equity else None,
            "cash": asdict(cash) if cash else None,
            "operating_income": asdict(operating_income) if operating_income else None,
        },
        "support_flows": {
            "pretax": [asdict(x) for x in pretax[:10]],
            "other_nonoperating": [asdict(x) for x in other_nonop[:10]],
        },
        "debt_status": debt_status,
        "interest_status": interest_status,
        "selected_debt": selected_debt,
        "selected_interest": selected_interest,
        "debt_candidates": [asdict(x) for x in debt],
        "interest_candidates": [asdict(x) for x in interest],
        "candidate_concept_counts": {
            "debt": dict(Counter(x.concept for x in debt)),
            "interest": dict(Counter(x.concept for x in interest)),
        },
        "unclassified_like_counts": {
            "debt": dict(Counter(x["concept"] for x in unclassified_debt_like)),
            "interest": dict(Counter(x["concept"] for x in unclassified_interest_like)),
        },
        "unclassified_like_examples": {
            "debt": unclassified_debt_like[:50],
            "interest": unclassified_interest_like[:50],
        },
    }

def filing_map(cik: str | int, resolver: SECXBRLSearchV2_3_8, year: int | None = None) -> dict[str, Any]:
    submissions = resolver.submissions(cik)
    rows, meta = resolver._inline_filing_rows(cik, submissions)
    if year is None:
        annual_fy = resolver._latest_annual_fy(submissions)
        if annual_fy is not None:
            year = annual_fy
        else:
            dates = [_date(r.get("end")) for r in rows if r.get("end")]
            year = max((d.year for d in dates), default=None)
    result = classify_filing_rows(rows, target_year=year)
    result["filing"] = {
        "accession": meta.get("accession"),
        "primary_document": meta.get("primary_document") or meta.get("document"),
        "filed": meta.get("filed"),
        "target_year": year,
        "concept_count": len({r.get("concept") for r in rows if r.get("concept")}),
        "row_count": len(rows),
    }
    return result

__all__ = ["FinancialFact","classify_debt_fact","classify_interest_fact","classify_filing_rows","filing_map"]
