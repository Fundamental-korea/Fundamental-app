import unittest

from us_valuation import build_valuation_snapshot


class TestUSValuation(unittest.TestCase):
    def test_nci_safe_bps_and_reported_eps(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "StockholdersEquity": {"units": {"USD": [{"val": 900, "start": None, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
                    "NetIncomeLoss": {"units": {"USD": [{"val": 120, "start": "2026-04-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
                    "EarningsPerShareDiluted": {"units": {"USD/shares": [{"val": 1.20, "start": "2026-04-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}] }},
                    "EntityCommonStockSharesOutstanding": {"units": {"shares": [
                        {"val": 100, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"},
                        {"val": 110, "end": "2026-08-15", "filed": "2026-08-15", "form": "10-Q"},
                    ]}},
                }
            }
        }
        result = build_valuation_snapshot(facts, "2026-06-30", {"price": 10})
        self.assertEqual(result["eps"], 1.20)
        self.assertEqual(result["bps"], 9.0)
        self.assertEqual(result["market_cap"], 1100.0)
        self.assertAlmostEqual(result["per"], 10 / 1.2)
        self.assertAlmostEqual(result["pbr"], 10 / 9.0)

    def test_nci_inclusive_equity_is_reduced(self):
        facts = {
            "facts": {
                "us-gaap": {
                    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": {"units": {"USD": [{"val": 1200, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
                    "NoncontrollingInterestInConsolidatedEntity": {"units": {"USD": [{"val": 200, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
                    "EntityCommonStockSharesOutstanding": {"units": {"shares": [{"val": 100, "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}]}},
                    "EarningsPerShareDiluted": {"units": {"USD/shares": [{"val": 3.0, "start": "2026-04-01", "end": "2026-06-30", "filed": "2026-08-01", "form": "10-Q"}] }},
                }
            }
        }
        result = build_valuation_snapshot(facts, "2026-06-30", {"price": 12})
        self.assertEqual(result["bps"], 10.0)
        self.assertEqual(result["bps_equity_basis"], "parent-attributable")
        self.assertEqual(result["bps_nci_source_tag"], "NoncontrollingInterestInConsolidatedEntity")


if __name__ == "__main__":
    unittest.main()
