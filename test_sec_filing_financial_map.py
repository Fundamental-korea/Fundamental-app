"""Unit tests for filing-first ROIC/interest classification."""
from sec_filing_financial_map import (
    classify_debt_fact,
    classify_interest_fact,
    classify_filing_rows,
)

def row(concept, namespace="us-gaap", value=100.0, unit="iso4217:USD",
        instant=True, start=None, end="2025-12-31", dimensioned=False):
    return {
        "concept": concept, "namespace": namespace, "label": "",
        "value": value, "unit": unit, "instant": instant,
        "start": start, "end": end, "filed": "2026-02-01",
        "form": "10-K", "dimensioned": dimensioned, "contextRef": "ctx1",
    }

def test_debt_lookalikes_are_excluded():
    assert classify_debt_fact(row("AvailableForSaleSecuritiesDebtSecurities"))[0] is None
    assert classify_debt_fact(row("DebtInstrumentInterestRateStatedPercentage"))[0] is None
    assert classify_debt_fact(row("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo"))[0] is None
    assert classify_debt_fact(row("DebtInstrumentFaceAmount"))[0] is None
    assert classify_debt_fact(row("LineOfCreditFacilityMaximumBorrowingCapacity"))[0] is None
    assert classify_debt_fact(row("DebtInstrumentsHeldAtAmortisedCost"))[0] is None
    assert classify_debt_fact(row("NetDebt"))[0] is None

def test_real_debt_concepts_are_recognized():
    assert classify_debt_fact(row("LongTermLoansPayable"))[0] == "issuer_debt_noncurrent"
    assert classify_debt_fact(row("NotesAndLoansPayable"))[0] == "issuer_debt_noncurrent"
    assert classify_debt_fact(row("UnsecuredDebt"))[0] == "issuer_debt_noncurrent"
    assert classify_debt_fact(row("LongTermDebt"))[0] == "issuer_debt_noncurrent"
    assert classify_debt_fact(row("Borrowings", namespace="ifrs-full"))[0] == "issuer_debt_total"
    assert classify_debt_fact(row("ShorttermBorrowings", namespace="ifrs-full"))[0] == "issuer_debt_current"

def test_custom_debt_concept_is_recognized_only_when_economic_shape_matches():
    assert classify_debt_fact(row("SeniorBorrowings", namespace="acme"))[0] == "custom_issuer_debt"
    assert classify_debt_fact(row("ProceedsFromIssuanceOfDebt", namespace="acme", instant=False, start="2025-01-01"))[0] is None

def test_interest_lookalikes_are_excluded():
    assert classify_interest_fact(row("DebtInstrumentInterestRateStatedPercentage", instant=False, start="2025-01-01"))[0] is None
    assert classify_interest_fact(row("InterestPaidNet", instant=False, start="2025-01-01"))[0] is None
    assert classify_interest_fact(row("DefinedBenefitPlanInterestCost", instant=False, start="2025-01-01"))[0] is None
    assert classify_interest_fact(row("InterestIncome", instant=False, start="2025-01-01"))[0] is None
    assert classify_interest_fact(row("AmortizationOfFinancingCosts", instant=False, start="2025-01-01"))[0] is None
    assert classify_interest_fact(row("UnrecognizedTaxBenefitsIncomeTaxPenaltiesAndInterestExpense", instant=False, start="2025-01-01"))[0] is None
    assert classify_interest_fact(row("OperatingLeaseInterestExpense", instant=False, start="2025-01-01"))[0] is None

def test_real_interest_is_recognized():
    r = row("InterestExpenseNonoperating", instant=False, start="2025-01-01")
    assert classify_interest_fact(r)[0] == "gross_interest_expense"
    r = row("InterestIncomeExpenseNonoperatingNet", instant=False, start="2025-01-01")
    assert classify_interest_fact(r)[0] == "net_interest_expense"
    r = row("InterestExpenseOnBorrowings", namespace="ifrs-full", instant=False, start="2025-01-01")
    assert classify_interest_fact(r)[0] == "interest_expense_component"

def test_direct_total_wins_over_components():
    rows = [
        row("LongTermDebtCurrent", value=20),
        row("LongTermDebtNoncurrent", value=80),
        row("DebtAndCapitalLeaseObligations", value=100),
    ]
    result = classify_filing_rows(rows, target_year=2025)
    assert result["selected_debt"]["basis"] == "reported_total"
    assert result["selected_debt"]["value"] == 100

def test_components_sum_when_total_absent():
    rows = [
        row("LongTermDebtCurrent", value=20),
        row("LongTermDebtNoncurrent", value=80),
    ]
    result = classify_filing_rows(rows, target_year=2025)
    assert result["selected_debt"]["basis"] == "current_plus_noncurrent"
    assert result["selected_debt"]["value"] == 100

def test_explicit_zero_interest_is_confirmed_zero():
    r = row("InterestExpenseNonoperating", value=0, instant=False, start="2025-01-01")
    result = classify_filing_rows([r], target_year=2025)
    assert result["interest_status"] == "ZERO_CONFIRMED"

def test_roic_core_inputs_come_from_filing():
    rows = [
        row("StockholdersEquity", value=500),
        row("CashAndCashEquivalentsAtCarryingValue", value=100),
        row("LongTermDebt", value=200),
        row("OperatingIncomeLoss", value=150, instant=False, start="2025-01-01"),
    ]
    result = classify_filing_rows(rows, target_year=2025)
    assert result["roic_inputs"]["equity"]["value"] == 500
    assert result["roic_inputs"]["cash"]["value"] == 100
    assert result["roic_inputs"]["operating_income"]["value"] == 150

def test_combined_debt_and_capital_lease_is_total_debt():
    r = row("DebtAndCapitalLeaseObligations", value=123)
    result = classify_filing_rows([r], target_year=2025)
    assert result["selected_debt"]["basis"] == "reported_total"
    assert result["selected_debt"]["value"] == 123

def test_debt_must_match_equity_basis():
    rows = [
        row("StockholdersEquity", value=500, end="2025-12-31"),
        row("CashAndCashEquivalentsAtCarryingValue", value=100, end="2025-12-31"),
        row("OperatingIncomeLoss", value=150, instant=False, start="2025-01-01", end="2025-12-31"),
        row("LongTermDebt", value=200, end="2025-09-30"),
    ]
    result = classify_filing_rows(rows, target_year=2025)
    assert result["selected_debt"] is None

def test_interest_must_match_operating_income_basis():
    rows = [
        row("StockholdersEquity", value=500),
        row("CashAndCashEquivalentsAtCarryingValue", value=100),
        row("LongTermDebt", value=200),
        row("OperatingIncomeLoss", value=150, instant=False, start="2025-01-01", end="2025-12-31"),
        row("InterestExpense", value=20, instant=False, start="2025-01-01", end="2025-09-30"),
    ]
    result = classify_filing_rows(rows, target_year=2025)
    assert result["selected_interest"] is None


def test_interest_components_aggregate_when_direct_total_is_absent():
    rows = [
        row("InterestExpenseLongTermDebt", value=100, instant=False, start="2025-01-01"),
        row("InterestExpenseShortTermBorrowings", value=20, instant=False, start="2025-01-01"),
    ]
    result = classify_filing_rows(rows, target_year=2025)
    assert result["selected_interest"]["basis"] == "aggregated_interest_components"
    assert result["selected_interest"]["value"] == 120


def test_direct_component_aggregate_wins_over_child_component():
    rows = [
        row("InterestExpenseOnDebtInstrumentsIssued", namespace="ifrs-full", value=100, instant=False, start="2025-01-01"),
        row("InterestExpenseOnBonds", namespace="ifrs-full", value=80, instant=False, start="2025-01-01"),
    ]
    result = classify_filing_rows(rows, target_year=2025)
    assert result["selected_interest"]["basis"] == "reported_component_aggregate"
    assert result["selected_interest"]["value"] == 100


def test_lease_inclusive_debt_totals_are_not_lease_only():
    assert classify_debt_fact(row("DebtAndCapitalLeaseObligations"))[0] == "issuer_debt_total"
    assert classify_debt_fact(row("LongTermDebtAndCapitalLeaseObligations"))[0] == "issuer_debt_total"


def test_borrowing_capacity_is_not_debt():
    assert classify_debt_fact(row("AuthorizedShortTermBorrowings"))[0] is None
    assert classify_debt_fact(row("AmountOfTotalBorrowingCapacity"))[0] is None


def test_custom_debt_bucket_is_inferred():
    assert classify_debt_fact(row("ShortTermBorrowingsOutstanding", namespace="acme"))[0] == "custom_issuer_debt_current"
    assert classify_debt_fact(row("LongTermBorrowingsOutstanding", namespace="acme"))[0] == "custom_issuer_debt_noncurrent"
