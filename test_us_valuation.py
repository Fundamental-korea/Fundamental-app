import unittest

from us_valuation import build_valuation_snapshot


class TestUSValuation(unittest.TestCase):
    def test_nci_safe_bps_market_cap_and_reported_annual_eps(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "StockholdersEquity": {"units": {"USD": [{"val": 900, "start": None, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
                    "EarningsPerShareDiluted": {"units": {"USD/shares": [
                        {"val": 1.20, "start": "2026-04-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"},
                        {"val": 4.80, "start": "2025-07-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-K"},
                    ]}},
                    "EntityCommonStockSharesOutstanding": {"units": {"shares": [
                        {"val": 100, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"},
                        {"val": 110, "end": "2026-08-15", "filed": "2026-08-15", "form": "10-Q"},
                    ]}},
                }
            }
        }
        result = build_valuation_snapshot(facts, "2026-06-30", {"price": 10, "current_shares": 110})
        self.assertEqual(result["eps"], 4.80)
        self.assertEqual(result["bps"], 9.0)
        self.assertEqual(result["market_cap"], 1100.0)
        self.assertAlmostEqual(result["per"], 10 / 4.8)
        self.assertAlmostEqual(result["pbr"], 10 / 9.0)
        self.assertEqual(result["eps_basis"], "reported-diluted")

    def test_generic_eps_fallback_rejects_share_denominators_and_helpers(self):
        facts = {
            "facts": {
                "custom": {
                    "WeightedAverageNumberOfShareOutstandingBasicAndDiluted": {
                        "label": "Weighted average number of shares outstanding basic and diluted",
                        "units": {"shares": [{"val": 100, "start": "2025-07-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-K"}]},
                    },
                    "AdjustmentsToReconcileProfitLossAttributableToOwnersOfParentToNumeratorUsedInCalculatingBasicEarningsPerShare": {
                        "label": "Adjustments to reconcile profit or loss to numerator used in calculating basic earnings per share",
                        "units": {"USD": [{"val": 5000, "start": "2025-07-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-K"}]},
                    },
                    "RedemptionPremium": {
                        "label": "Redemption premium",
                        "units": {"USD": [{"val": 7000, "start": "2025-07-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-K"}]},
                    },
                    "CustomReportedEarningsPerShareDiluted": {
                        "label": "Earnings per share, diluted",
                        "units": {"USD/shares": [{"val": 2.50, "start": "2025-07-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-K"}]},
                    },
                }
            }
        }
        result = build_valuation_snapshot(facts, "2026-06-30", {"price": 10, "current_shares": 110})
        self.assertEqual(result["eps"], 2.50)
        self.assertEqual(result["eps_source"], "CustomReportedEarningsPerShareDiluted")
        self.assertEqual(result["eps_unit"], "USD/shares")
        self.assertEqual(result["eps_basis"], "reported-diluted-fallback")

    def test_subsequent_dei_share_fallback_is_limited_to_60_days(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "StockholdersEquity": {"units": {"USD": [{"val": 1000, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
                    "EarningsPerShareDiluted": {
                        "units": {"USD/shares": [{"val": 3.0, "start": "2025-07-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-K"}]}
                    },
                },
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {"shares": [
                            {"val": 100, "end": "2026-09-15", "filed": "2026-09-15", "form": "10-Q"},
                            {"val": 110, "end": "2026-08-15", "filed": "2026-09-01", "form": "10-Q"},
                        ]}
                    },
                }
            }
        }
        result = build_valuation_snapshot(facts, "2026-06-30", {"price": 12, "current_shares": 100})
        self.assertEqual(result["period_end_shares_outstanding"], 110.0)
        self.assertEqual(result["period_end_shares_days_after_fiscal_end"], 46)

    def test_nci_inclusive_equity_is_reduced(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {"units": {"USD": [{"val": 1200, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
                    "NoncontrollingInterestInConsolidatedEntity": {"units": {"USD": [{"val": 200, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
                    "EntityCommonStockSharesOutstanding": {"units": {"shares": [{"val": 100, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
                    "EarningsPerShareDiluted": {"units": {"USD/shares": [{"val": 3.0, "start": "2025-07-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-K"}] }},
                }
            }
        }
        result = build_valuation_snapshot(facts, "2026-06-30", {"price": 12, "current_shares": 100})
        self.assertEqual(result["bps"], 10.0)
        self.assertEqual(result["bps_equity_basis"], "parent-attributable")
        self.assertEqual(result["bps_nci_source_tag"], "NoncontrollingInterestInConsolidatedEntity")


if __name__ == "__main__":
    unittest.main()
