from types import SimpleNamespace
from unittest.mock import patch

import collector_us_fundamental as collector


def candidate(concept, value, unit="USD/share", score=100, namespace="us-gaap", label=None):
    return SimpleNamespace(
        concept=concept,
        value=value,
        unit=unit,
        score=score,
        namespace=namespace,
        label=label or concept,
        start="2025-01-01",
        end="2025-12-31",
        filed="2026-02-01",
    )


def test_select_eps_pair_prefers_diluted_and_same_unit():
    current = [
        candidate("NetIncomeLoss", 1000, label="Net income"),
        candidate("EarningsPerShareBasic", 2.0, score=140, label="Basic earnings per share"),
        candidate("EarningsPerShareDiluted", 1.8, score=150, label="Diluted earnings per share"),
        candidate("EarningsPerShareDilutedADS", 18.0, unit="USD/ADS", score=160, label="Diluted earnings per share per ADS"),
    ]
    prior = [
        candidate("EarningsPerShareBasic", 1.0, score=140, label="Basic earnings per share"),
        candidate("EarningsPerShareDiluted", 0.9, score=150, label="Diluted earnings per share"),
        candidate("NetIncomeLoss", 900, label="Net income"),
    ]

    result = collector.select_eps_pair(current, prior)

    assert result is not None
    assert result["current"]["concept"] == "EarningsPerShareDiluted"
    assert result["prior"]["concept"] == "EarningsPerShareDiluted"
    assert result["current"]["unit"] == result["prior"]["unit"] == "USD/share"


def test_select_eps_pair_rejects_numerator_and_weighted_average_lookalikes():
    current = [
        candidate("EarningsPerShareBasicNumerator", 1000, score=200, label="Earnings per share numerator"),
        candidate("WeightedAverageNumberOfSharesOutstandingBasic", 500, score=200, label="Weighted average shares"),
    ]
    prior = [
        candidate("EarningsPerShareBasicNumerator", 900, score=200, label="Earnings per share numerator"),
        candidate("WeightedAverageNumberOfSharesOutstandingBasic", 450, score=200, label="Weighted average shares"),
    ]

    assert collector.select_eps_pair(current, prior) is None


def test_annual_metrics_accepts_filing_overrides_for_roic_interest_and_eps():
    index = {
        "revenue": {2025: {"val": 1000}},
        "operating_income": {2025: {"val": 100}},
        "net_income": {2025: {"val": 70}},
        "assets": {2025: {"val": 2000}},
        "equity": {2025: {"val": 500}},
        "liabilities": {2025: {"val": 900}},
        "current_assets": {2025: {"val": 700}},
        "current_liabilities": {2025: {"val": 400}},
        "cash": {2025: {"val": 100}},
        "inventory": {2025: {"val": 200}},
        "interest_expense": {2025: {"val": 20}},
        "operating_cash_flow": {2025: {"val": 90}},
        "sga": {2025: {"val": 100}},
        "eps": {},
        "debt_total": {},
        "debt_current": {},
        "debt_noncurrent": {},
    }

    metrics = collector.annual_metrics(
        index,
        2025,
        annual_overrides={
            2025: {
                "equity": 600,
                "cash": 120,
                "debt": 300,
                "operating_income": 150,
                "interest_expense": 30,
                "eps": 3.0,
            }
        },
    )

    assert metrics["opm"] == 15.0
    assert round(metrics["roic"], 6) == round((150 * 0.78) / (600 + 300 - 120) * 100, 6)
    assert metrics["interest_coverage"] == 5.0
    assert metrics["eps"] == 3.0


def test_period_metrics_pair_recovers_eps_growth_from_overrides():
    index = {
        "revenue": {
            2024: {"val": 900},
            2025: {"val": 1000},
        },
        "operating_income": {
            2024: {"val": 90},
            2025: {"val": 100},
        },
        "net_income": {
            2024: {"val": 60},
            2025: {"val": 70},
        },
        "assets": {2024: {"val": 1800}, 2025: {"val": 2000}},
        "equity": {2024: {"val": 500}, 2025: {"val": 600}},
        "liabilities": {2024: {"val": 800}, 2025: {"val": 900}},
        "current_assets": {2024: {"val": 650}, 2025: {"val": 700}},
        "current_liabilities": {2024: {"val": 380}, 2025: {"val": 400}},
        "cash": {2024: {"val": 100}, 2025: {"val": 120}},
        "inventory": {2024: {"val": 180}, 2025: {"val": 200}},
        "interest_expense": {2024: {"val": 20}, 2025: {"val": 25}},
        "operating_cash_flow": {2024: {"val": 70}, 2025: {"val": 90}},
        "sga": {2024: {"val": 90}, 2025: {"val": 100}},
        "eps": {},
        "debt_total": {2024: {"val": 250}, 2025: {"val": 300}},
        "debt_current": {},
        "debt_noncurrent": {},
    }

    result = collector.period_metrics_pair(
        index,
        2025,
        1,
        annual_overrides={
            2024: {"eps": 1.0},
            2025: {"eps": 2.0},
        },
    )

    assert result["worst_metrics"]["eps_growth"] == 100.0
    assert result["avg_metrics"]["eps_growth"] == 100.0


def test_sanitize_growth_keeps_legitimate_large_eps_growth():
    assert collector.sanitize_growth(800.0) == 800.0


def test_recover_critical_filing_metrics_uses_cached_filing_and_eps_pair():
    mapped = {
        "roic_inputs": {
            "equity": {"value": 600, "unit": "iso4217:USD", "end": "2025-12-31"},
            "cash": {"value": 120, "unit": "iso4217:USD", "end": "2025-12-31"},
            "operating_income": {"value": 150, "unit": "iso4217:USD", "end": "2025-12-31"},
        },
        "selected_debt": {
            "value": 300,
            "unit": "iso4217:USD",
            "end": "2025-12-31",
            "concept": "LongTermDebt",
        },
        "selected_interest": {
            "value": 30,
            "unit": "iso4217:USD",
            "end": "2025-12-31",
            "concept": "InterestExpenseNonoperating",
        },
        "filing": {"target_year": 2025},
        "debt_status": "FOUND_STANDARD",
        "interest_status": "FOUND_GROSS",
    }

    class FakeResolver:
        def __init__(self):
            self.calls = []

        def search_filing(self, cik, metric, year=None, limit=50):
            self.calls.append((cik, metric, year))
            if year == 2025:
                return [candidate("EarningsPerShareDiluted", 3.0)], {}
            return [candidate("EarningsPerShareDiluted", 1.5)], {}

    resolver = FakeResolver()
    cache = {}

    with patch.object(collector, "filing_map", return_value=mapped):
        first = collector.recover_critical_filing_metrics(
            "123",
            2025,
            "standard",
            resolver,
            cache=cache,
            need_roic=True,
            need_interest=True,
            need_eps_growth=True,
        )
        second = collector.recover_critical_filing_metrics(
            "123",
            2025,
            "standard",
            resolver,
            cache=cache,
            need_roic=True,
            need_interest=True,
            need_eps_growth=True,
        )

    assert first is second
    assert first["annual_overrides"][2025]["equity"] == 600
    assert first["annual_overrides"][2025]["debt"] == 300
    assert first["annual_overrides"][2025]["interest_expense"] == 30
    assert first["annual_overrides"][2025]["eps"] == 3.0
    assert first["annual_overrides"][2024]["eps"] == 1.5
    assert len(resolver.calls) == 2
