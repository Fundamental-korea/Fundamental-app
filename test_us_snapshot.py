import unittest

from collector_us_fundamental import annual_metrics, build_fact_index, build_latest_snapshot
from collector_us_standard_fallback import _snapshot_from_index
from sec_xbrl_search_v2_2 import INSTANT_METRICS, EXACT_CONCEPTS


class USSnapshotAndMetricTests(unittest.TestCase):
    def _companyfacts(self, namespace="us-gaap"):
        facts = {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [{
                "val": 1000, "fy": 2025, "fp": "FY", "form": "10-K",
                "filed": "2026-02-15", "start": "2025-01-01", "end": "2025-12-31",
            }]}},
            "OperatingIncomeLoss": {"units": {"USD": [{
                "val": 150, "fy": 2025, "fp": "FY", "form": "10-K",
                "filed": "2026-02-15", "start": "2025-01-01", "end": "2025-12-31",
            }]}},
            "Assets": {"units": {"USD": [{
                "val": 2000, "fy": 2025, "fp": "FY", "form": "10-K",
                "filed": "2026-02-15", "end": "2025-12-31",
            }]}},
            "StockholdersEquity": {"units": {"USD": [{
                "val": 800, "fy": 2025, "fp": "FY", "form": "10-K",
                "filed": "2026-02-15", "end": "2025-12-31",
            }]}},
            "LongTermDebtAndFinanceLeaseObligationsNoncurrent": {"units": {"USD": [{
                "val": 500, "fy": 2025, "fp": "FY", "form": "10-K",
                "filed": "2026-02-15", "end": "2025-12-31",
            }]}},
            "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [{
                "val": 100, "fy": 2025, "fp": "FY", "form": "10-K",
                "filed": "2026-02-15", "end": "2025-12-31",
            }]}},
        }
        return {"facts": {namespace: facts}}


    def test_annual_instant_fact_without_fy_is_kept(self):
        facts = {
            "Assets": {"units": {"USD": [{
                "val": 3000, "form": "10-K", "filed": "2026-02-15",
                "end": "2025-12-31"
            }]}}
        }
        index = build_fact_index({"facts": {"us-gaap": facts}})
        self.assertEqual(index["assets"][2025]["val"], 3000)

    def test_snapshot_uses_company_facts(self):
        snapshot = build_latest_snapshot(self._companyfacts())
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["fiscal_end"], "2025-12-31")
        self.assertEqual(snapshot["form"], "10-K")
        self.assertEqual(snapshot["instant"]["assets"]["source"], "sec-company-facts")

    def test_snapshot_uses_filing_xbrl_namespace(self):
        snapshot = build_latest_snapshot(self._companyfacts("filing-xbrl"))
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["instant"]["assets"]["source"], "sec-filing-xbrl")

    def test_expanded_debt_tags_enable_roic(self):
        index = build_fact_index(self._companyfacts())
        metrics = annual_metrics(index, 2025)
        self.assertIsNotNone(metrics["roic"])
        self.assertAlmostEqual(metrics["roic"], 9.75, places=2)

    def test_resolver_supports_debt_metrics(self):
        self.assertIn("debt_current", INSTANT_METRICS)
        self.assertIn("debt_noncurrent", INSTANT_METRICS)
        self.assertIn("debt_total", INSTANT_METRICS)
        self.assertIn("LongTermDebtCurrent", EXACT_CONCEPTS["debt_current"])
        self.assertIn("LongTermDebtNoncurrent", EXACT_CONCEPTS["debt_noncurrent"])
        self.assertIn("LongTermDebt", EXACT_CONCEPTS["debt_total"])

    def test_standard_fallback_snapshot_from_index(self):
        index = {
            "revenue": {2025: {"val": 1000, "end": "2025-12-31", "filed": "2026-02-15", "form": "10-K", "unit": "USD", "namespace": "filing-xbrl", "tag": "Revenue"}},
            "assets": {2025: {"val": 2000, "end": "2025-12-31", "filed": "2026-02-15", "form": "10-K", "unit": "USD", "namespace": "filing-xbrl", "tag": "Assets"}},
        }
        submissions = {"filings": {"recent": {
            "form": ["10-K"], "filingDate": ["2026-02-15"], "reportDate": ["2025-12-31"], "fy": [2025]
        }}}
        snapshot = _snapshot_from_index(index, submissions, 2025)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["fiscal_end"], "2025-12-31")
        self.assertEqual(snapshot["basis"], "fallback filing XBRL")


if __name__ == "__main__":
    unittest.main()
