"""SEC filing-level financial map for ROIC / interest coverage.

This module is deliberately economic-first:
1. inspect the latest annual Inline XBRL filing;
2. classify actual issuer liability/debt and interest concepts;
3. exclude look-alikes such as debt securities held as assets, interest rates,
   interest paid, pension interest cost, and debt maturity/activity facts;
4. select one coherent debt basis and one coherent annual interest expense basis.

No database writes. Raw filing rows stay in memory.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date
import math
import re
from typing import Any, Iterable

from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8
from sec_xbrl_search_v2_3_4 import _annual_duration, _date

KNOWN_TAXONOMIES = {"us-gaap", "ifrs-full", "srt", "dei", "xbrli", "country", "currency"}

# Explicit stock concepts that represent issuer borrowing/debt. These are
# intentionally narrower than keyword search.
STANDARD_DEBT_TOTAL = {
    "LongTermDebt",
    "LongTermDebtCurrentAndNoncurrent",
    "Debt",
    "TotalDebt",
    "DebtAndCapitalLeaseObligations",
    "DebtAndFinanceLeaseLiabilities",
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebtAndFinanceLeaseObligations",
    "DebtLongtermAndShorttermCombinedAmount",
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
}
STANDARD_DEBT_NONCURRENT = {
    "LongTermDebtNoncurrent",
    "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
    "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
    "DebtAndCapitalLeaseObligationsNoncurrent",
    "DebtAndFinanceLeaseLiabilitiesNoncurrent",
    "NoncurrentBorrowings",
    "LongtermBorrowings",
    "Borrowings",
    "LoansPayable",
    "NotesPayableNoncurrent",
    "LongTermNotesPayable",
    "ConvertibleDebtNoncurrent",
    "UnsecuredDebt",
    "UnsecuredLongTermDebt",
    "SecuredDebt",
    "OtherLongTermDebt",
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
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
    "DebtAndCapitalLeaseObligations",
    "DebtAndCapitalLeaseObligationsCurrent",
    "DebtAndCapitalLeaseObligationsNoncurrent",
}
OPERATING_LEASE_CONCEPTS = {
    "OperatingLeaseLiabilityCurrent",
    "OperatingLeaseLiabilityNoncurrent",
    "OperatingLeaseLiability",
    "OperatingLeaseLiabilityMaturingInNextTwelveMonths",
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
)

STANDARD_INTEREST_GROSS = {
    "InterestExpense",
    "InterestExpenseNonoperating",
    "InterestExpenseNonOperating",
    "InterestExpenseDebt",
    "InterestExpenseNonoperatingNetOfTax",
    "InterestAndDebtExpense",
    "InterestExpenseNonoperatingAndOther",
    "InterestExpenseNonOperatingAndOther",
    "InterestExpenseDebtExcludingAmortization",
    "FinanceCosts",
}
STANDARD_INTEREST_NET = {
    "InterestIncomeExpenseNet",
    "InterestIncomeExpenseNonoperatingNet",
    "InterestExpenseNonOperatingNet",
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
)

ACTIVITY_EXCLUSIONS = (
    "proceeds",
    "repayment",
    "repayments",
    "maturities",
    "redemption",
    "extinguishment",
    "refinanced",
    "increase",
    "decrease",
    "fairvalue",
)

DEBT_KEYWORDS = (
    "debt", "borrow", "borrowing", "loan", "notes payable", "note payable",
    "credit facility", "revolving", "revolver", "term loan",
    "senior note", "convertible debt", "unsecured debt", "secured debt",
)
LEASE_KEYWORDS = ("finance lease", "capital lease", "lease liability", "lease liabilities")
INTEREST_KEYWORDS = ("interest expense", "interest cost", "finance costs", "financing costs", "debt expense")


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


def _looks_annual(row: dict[str, Any], target_year: int | None) -> bool:
    end = _date(row.get("end"))
    if target_year is not None and (not end or end.year != int(target_year)):
        return False
    if not row.get("start"):
        return False
    return _annual_duration(row.get("start"), row.get("end"))


def _looks_instant(row: dict[str, Any], target_year: int | None) -> bool:
    end = _date(row.get("end"))
    return bool(row.get("instant")) and bool(end) and (
        target_year is None or end.year == int(target_year)
    )


def _is_bad_debt_concept(concept: str, label: str) -> bool:
    text = _compact(concept) + " " + _compact(label)
    return any(token in text for token in DEBT_EXCLUSIONS)


def classify_debt_fact(row: dict[str, Any]) -> tuple[str | None, str, str]:
    """Return category, confidence, reason for a possible debt/lease fact."""
    concept = _local(row.get("concept"))
    label = row.get("label") or ""
    compact = _compact(concept + " " + label)
    namespace = row.get("namespace") or ""

    if _is_bad_debt_concept(concept, label):
        return None, "exclude", "debt/investment/activity/look-alike concept"

    if concept in OPERATING_LEASE_CONCEPTS or "operatingleaseliability" in compact:
        return "operating_lease_liability", "high", "explicit operating lease liability"

    if concept in FINANCE_LEASE_CONCEPTS or "financeleaseliability" in compact or "capitalleaseobligation" in compact:
        return "finance_lease_liability", "high", "explicit finance/capital lease liability"

    if concept in STANDARD_DEBT_TOTAL:
        return "issuer_debt_total", "high", "canonical issuer debt total"

    if concept in STANDARD_DEBT_CURRENT:
        return "issuer_debt_current", "high", "canonical current issuer borrowing/debt"

    if concept in STANDARD_DEBT_NONCURRENT:
        return "issuer_debt_noncurrent", "high", "canonical noncurrent issuer borrowing/debt"

    if concept in STANDARD_DEBT_CARRYING:
        return "debt_carrying_amount_candidate", "medium", "debt instrument carrying amount; reconcile before use"

    if namespace in {"us-gaap", "ifrs-full"} and any(k in compact for k in (
        "debt", "borrowings", "borrowing", "loanspayable", "notespayable",
        "creditfacility", "revolvingcreditfacility", "termloan",
    )):
        if any(token in compact for token in ACTIVITY_EXCLUSIONS):
            return None, "exclude", "debt activity/maturity fact"
        return "issuer_debt_other", "medium", "standard-taxonomy debt-like stock concept"

    if namespace not in KNOWN_TAXONOMIES and any(k in compact for k in (
        "debt", "borrowings", "borrowing", "loanspayable", "notespayable",
        "creditfacility", "revolvingcreditfacility", "termloan",
    )):
        if any(token in compact for token in ACTIVITY_EXCLUSIONS):
            return None, "exclude", "custom debt activity fact"
        return "custom_issuer_debt", "medium", "custom taxonomy debt-like concept"

    return None, "none", ""


def classify_interest_fact(row: dict[str, Any]) -> tuple[str | None, str, str]:
    concept = _local(row.get("concept"))
    label = row.get("label") or ""
    compact = _compact(concept + " " + label)
    namespace = row.get("namespace") or ""

    if any(token in compact for token in INTEREST_EXCLUSIONS):
        return None, "exclude", "interest look-alike (rate, paid, payable, income, pension, capitalized)"

    if concept in STANDARD_INTEREST_GROSS:
        return "gross_interest_expense", "high", "canonical gross interest expense / finance cost"

    if concept in STANDARD_INTEREST_NET:
        return "net_interest_expense", "medium", "canonical net interest expense fallback"

    if namespace in {"us-gaap", "ifrs-full"} and any(
        _compact(k) in compact for k in INTEREST_KEYWORDS
    ):
        if any(token in compact for token in ACTIVITY_EXCLUSIONS):
            return None, "exclude", "interest-related activity/metadata fact"
        return "gross_interest_expense_other", "medium", "standard-taxonomy interest expense-like concept"

    if namespace not in KNOWN_TAXONOMIES and any(
        _compact(k) in compact for k in INTEREST_KEYWORDS
    ):
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


def classify_filing_rows(rows: Iterable[dict[str, Any]], target_year: int | None = None) -> dict[str, Any]:
    """Classify annual filing rows and select coherent debt/interest bases."""
    debt: list[FinancialFact] = []
    interest: list[FinancialFact] = []
    excluded: list[dict[str, Any]] = []

    for row in rows:
        category, confidence, reason = classify_debt_fact(row)
        if category and _looks_instant(row, target_year):
            fact = _to_fact(row, category, confidence, reason)
            if fact:
                debt.append(fact)
        elif confidence == "exclude" and _local(row.get("concept")):
            excluded.append({
                "concept": _local(row.get("concept")),
                "label": row.get("label") or "",
                "value": row.get("value"),
                "category": "debt_excluded",
                "reason": reason,
            })

        category, confidence, reason = classify_interest_fact(row)
        if category and _looks_annual(row, target_year):
            fact = _to_fact(row, category, confidence, reason)
            if fact:
                interest.append(fact)

    # Prefer exact totals over components. Components are used only when both
    # current and noncurrent pieces form a coherent balance-sheet pair.
    total = [x for x in debt if x.category == "issuer_debt_total"]
    current = [x for x in debt if x.category in {"issuer_debt_current", "finance_lease_liability"}]
    noncurrent = [x for x in debt if x.category in {"issuer_debt_noncurrent", "finance_lease_liability"}]
    other = [x for x in debt if x.category in {"issuer_debt_other", "custom_issuer_debt"}]
    carrying = [x for x in debt if x.category == "debt_carrying_amount_candidate"]

    selected_debt: dict[str, Any] | None = None
    if total:
        # Same-end direct total; choose the latest filed / highest confidence.
        chosen = sorted(total, key=lambda x: (x.end, x.filed or ""), reverse=True)[0]
        selected_debt = {
            "value": chosen.value,
            "basis": "reported_total",
            "category": chosen.category,
            "concept": chosen.concept,
            "namespace": chosen.namespace,
            "confidence": chosen.confidence,
            "components": [asdict(chosen)],
        }
    else:
        # Require one current and one noncurrent component when both exist.
        # A sole explicit borrowing remains usable but is marked partial.
        cur = sorted(current, key=lambda x: (x.end, x.filed or ""), reverse=True)
        ncur = sorted(noncurrent, key=lambda x: (x.end, x.filed or ""), reverse=True)
        if cur and ncur:
            c = cur[0]
            n = ncur[0]
            if c.end == n.end:
                selected_debt = {
                    "value": c.value + n.value,
                    "basis": "current_plus_noncurrent",
                    "category": "issuer_debt_components",
                    "concept": f"{c.concept}+{n.concept}",
                    "namespace": c.namespace if c.namespace == n.namespace else f"{c.namespace}+{n.namespace}",
                    "confidence": "high" if c.confidence == n.confidence == "high" else "medium",
                    "components": [asdict(c), asdict(n)],
                }
        if selected_debt is None and other:
            chosen = sorted(other, key=lambda x: (x.confidence != "high", x.end, x.filed or ""), reverse=False)[0]
            selected_debt = {
                "value": chosen.value,
                "basis": "reported_other_debt",
                "category": chosen.category,
                "concept": chosen.concept,
                "namespace": chosen.namespace,
                "confidence": chosen.confidence,
                "components": [asdict(chosen)],
            }
        if selected_debt is None and len(carrying) == 1:
            chosen = carrying[0]
            selected_debt = {
                "value": chosen.value,
                "basis": "carrying_amount_candidate",
                "category": chosen.category,
                "concept": chosen.concept,
                "namespace": chosen.namespace,
                "confidence": "medium",
                "components": [asdict(chosen)],
            }

    # Interest: gross exact > gross other > net.
    gross = [x for x in interest if x.category == "gross_interest_expense"]
    gross_other = [x for x in interest if x.category in {"gross_interest_expense_other", "custom_interest_expense"}]
    net = [x for x in interest if x.category == "net_interest_expense"]

    selected_interest: dict[str, Any] | None = None
    if gross:
        chosen = sorted(gross, key=lambda x: (x.end, x.filed or ""), reverse=True)[0]
        selected_interest = {
            "value": abs(chosen.value),
            "basis": "reported_gross_interest_expense",
            "category": chosen.category,
            "concept": chosen.concept,
            "namespace": chosen.namespace,
            "confidence": chosen.confidence,
            "components": [asdict(chosen)],
        }
    elif gross_other:
        chosen = sorted(gross_other, key=lambda x: (x.confidence != "high", x.end, x.filed or ""))[0]
        selected_interest = {
            "value": abs(chosen.value),
            "basis": "other_interest_expense",
            "category": chosen.category,
            "concept": chosen.concept,
            "namespace": chosen.namespace,
            "confidence": chosen.confidence,
            "components": [asdict(chosen)],
        }
    elif net:
        chosen = sorted(net, key=lambda x: (x.end, x.filed or ""), reverse=True)[0]
        selected_interest = {
            "value": abs(chosen.value),
            "basis": "reported_net_interest_expense",
            "category": chosen.category,
            "concept": chosen.concept,
            "namespace": chosen.namespace,
            "confidence": chosen.confidence,
            "components": [asdict(chosen)],
        }

    lease_only = bool(debt) and all(x.category in {"finance_lease_liability", "operating_lease_liability"} for x in debt)
    debt_status = (
        "FOUND_STANDARD" if selected_debt and selected_debt["namespace"] in {"us-gaap", "ifrs-full"}
        else "FOUND_CUSTOM" if selected_debt
        else "LEASE_ONLY" if lease_only
        else "UNRESOLVED"
    )

    if selected_interest:
        interest_status = {
            "gross_interest_expense": "FOUND_GROSS",
            "gross_interest_expense_other": "FOUND_GROSS",
            "custom_interest_expense": "FOUND_CUSTOM",
            "net_interest_expense": "FOUND_NET_ONLY",
        }.get(selected_interest["category"], "FOUND")
    elif any(
        _compact(x.label + " " + x.concept).find("interestincome") >= 0
        for x in []
    ):
        interest_status = "INTEREST_INCOME_ONLY"
    else:
        interest_status = "UNRESOLVED"

    return {
        "debt_status": debt_status,
        "interest_status": interest_status,
        "selected_debt": selected_debt,
        "selected_interest": selected_interest,
        "debt_candidates": [asdict(x) for x in debt],
        "interest_candidates": [asdict(x) for x in interest],
        "excluded_count": len(excluded),
        "excluded_examples": excluded[:100],
    }


def filing_map(cik: str | int, resolver: SECXBRLSearchV2_3_8, year: int | None = None) -> dict[str, Any]:
    """Fetch latest annual filing Inline XBRL and return the financial map."""
    submissions = resolver.submissions(cik)
    rows, meta = resolver._inline_filing_rows(cik, submissions)
    if year is None:
        end_dates = [_date(r.get("end")) for r in rows if r.get("end")]
        year = max((d.year for d in end_dates), default=None)
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


__all__ = [
    "FinancialFact",
    "classify_debt_fact",
    "classify_interest_fact",
    "classify_filing_rows",
    "filing_map",
]
