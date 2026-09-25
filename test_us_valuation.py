import unittest

from collector_us_valuation_only import parse_reported_eps_from_report
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
                    "CommonStockSharesOutstanding": {"units": {"shares": [
                        {"val": 100, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"},
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

    def test_eps_filing_parser_rejects_supporting_rows_and_accepts_direct_row(self):
        bad = """
        <table>
          <tr><td>Net Income per Share - Schedule of Antidilutive Securities Excluded from Computation of Earnings Per Share (Details) - shares shares in Millions</td><td>12</td></tr>
          <tr><td>Supplemental Information (Earnings Per Share) (Details)</td><td>12</td></tr>
          <tr><td>EARNINGS PER SHARE - Narrative (Details)</td><td>12</td></tr>
        </table>
        """
        self.assertIsNone(parse_reported_eps_from_report(bad))

        good = """
        <table>
          <tr><td>Earnings Per Share Attributable to Ordinary Equity Holders of the Parent (Details)</td><td>$ 2.50</td></tr>
        </table>
        """
        result = parse_reported_eps_from_report(good)
        self.assertEqual(result["value"], 2.50)
        self.assertEqual(result["currency"], "USD")

    def test_filing_eps_currency_parser_handles_local_currency_and_avoids_substring_match(self):
        cad = """
        <table>
          <tr><td>Diluted earnings per share (CAD per share)</td><td>2.50</td></tr>
        </table>
        """
        result = parse_reported_eps_from_report(cad)
        self.assertEqual(result["value"], 2.50)
        self.assertEqual(result["currency"], "CAD")

        plain = """
        <table>
          <tr><td>Earnings per share</td><td>2.50</td></tr>
        </table>
        """
        result = parse_reported_eps_from_report(plain)
        self.assertIsNone(result["currency"])

    def test_currency_mismatch_blocks_per_and_pbr(self):
        facts = {
            "facts": {
                "ifrs-full": {
                    "EquityAttributableToOwnersOfParent": {
                        "units": {"CLP": [{"val": 1000, "end": "2026-06-30", "form": "20-F", "filed": "2026-08-01"}]}
                    },
                    "EarningsPerShareDiluted": {
                        "units": {"CLP/shares": [{"val": 50, "start": "2025-07-01", "end": "2026-06-30", "form": "20-F", "filed": "2026-08-01"}]}
                    },
                },
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {"shares": [{"val": 100, "end": "2026-06-30", "form": "20-F", "filed": "2026-08-01"}]}
                    }
                }
            }
        }
        result = build_valuation_snapshot(
            facts,
            "2026-06-30",
            {"price": 20, "current_shares": 100, "currency": "USD"},
            security_ticker="TEST",
        )
        self.assertEqual(result["eps_currency"], "CLP")
        self.assertEqual(result["bps_currency"], "CLP")
        self.assertIsNone(result["per"])
        self.assertEqual(result["per_status"], "currency-mismatch")
        self.assertIsNone(result["pbr"])
        self.assertEqual(result["pbr_status"], "currency-mismatch")

    def test_preferred_security_does_not_compute_common_bps(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "StockholdersEquity": {
                        "units": {"USD": [{"val": 18_000_000_000, "end": "2026-06-30", "form": "10-Q", "filed": "2026-08-01"}]}
                    },
                    "EarningsPerShareDiluted": {
                        "units": {"USD/shares": [{"val": 3.0, "start": "2025-07-01", "end": "2026-06-30", "form": "10-K", "filed": "2026-08-01"}]}
                    },
                    "CommonStockSharesOutstanding": {
                        "units": {"shares": [{"val": 203_805, "end": "2026-06-30", "form": "10-Q", "filed": "2026-08-01"}]}
                    },
                }
            }
        }
        result = build_valuation_snapshot(
            facts,
            "2026-06-30",
            {"price": 23.5, "current_shares": 100_000_000},
            security_ticker="ATH-PA",
        )
        self.assertIsNone(result["bps"])
        self.assertEqual(result["bps_status"], "not-applicable-preferred-security")
        self.assertIsNone(result["pbr"])

    def test_nci_inclusive_equity_is_reduced(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {"units": {"USD": [{"val": 1200, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
                    "NoncontrollingInterestInConsolidatedEntity": {"units": {"USD": [{"val": 200, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
                    "CommonStockSharesOutstanding": {"units": {"shares": [{"val": 100, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
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
