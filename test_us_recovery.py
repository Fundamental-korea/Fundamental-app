"""Regression tests for unrecovered US fundamental companies.

These tests are network-free and focus on the two recovery paths added after
the 2026-09-20 full recollection:
1) Standard: continue with filing XBRL when SEC Company Facts returns 404.
2) Utility: invoke filing fallback when Company Facts has no core annual years.
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

import collector_us_standard_fallback as standard
import collector_us_utility_v4 as utility
from sec_xbrl_search_v2_3_8 import SECXBRLSearchV2_3_8
from us_utility_filing_fallback import _parse_instance


class _FakeResolver:
    def __init__(self):
        self.search_calls = []

    def search_filing(self, cik, metric, year=None, submissions=None, limit=1):
        self.search_calls.append((str(cik), metric, year))
        candidate = {
            "value": float(year or 2025),
            "end": f"{year or 2025}-12-31",
            "filed": "2026-03-01",
            "form": "10-K",
            "unit": "USD",
            "namespace": "us-gaap",
            "concept": metric,
            "source": "filing-xbrl-inline",
            "fy": year,
        }
        return [type("Candidate", (), {"compact": lambda self, c=candidate: c})()], {
            "target_fy": year,
        }


class _FakeSession:
    pass


class RecoveryRegressionTests(unittest.TestCase):
    def test_standard_company_facts_404_is_recoverable(self):
        facts_url = standard.SEC_FACTS_URL.format(cik="0000000123")
        submissions_url = standard.SEC_SUBMISSIONS_URL.format(cik="0000000123")

        http_error = requests.HTTPError("404")
        response = requests.Response()
        response.status_code = 404
        http_error.response = response

        def fake_fetch(_session, url):
            if url == facts_url:
                raise http_error
            if url == submissions_url:
                return {
                    "name": "Test Issuer",
                    "filings": {
                        "recent": {
                            "form": ["10-K"],
                            "filingDate": ["2026-03-01"],
                            "fy": [2025],
                            "reportDate": ["2025-12-31"],
                        }
                    },
                }
            raise AssertionError(url)

        with patch.object(standard, "fetch_json", side_effect=fake_fetch):
            facts, submissions = standard._load_company_resilient(
                _FakeSession(), "TEST", "123"
            )

        self.assertEqual(facts, {"facts": {}})
        self.assertEqual(submissions["name"], "Test Issuer")

    def test_standard_filing_fallback_used_without_company_facts(self):
        index = {}
        resolver = _FakeResolver()
        submissions = {"filings": {"recent": {"form": ["10-K"]}}}

        standard.augment_index_with_v238(
            index,
            resolver,
            "123",
            2025,
            submissions=submissions,
            company_facts_available=False,
        )

        self.assertIn(2025, index["revenue"])
        self.assertGreater(len(resolver.search_calls), 0)
        self.assertEqual(index["revenue"][2025]["source"], "filing-xbrl-inline")

    def test_utility_filing_parser_keeps_fiscal_year(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <xbrli:xbrl
          xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:us-gaap="http://fasb.org/us-gaap/2025">
          <xbrli:context id="D2025">
            <xbrli:entity><xbrli:identifier scheme="x">X</xbrli:identifier></xbrli:entity>
            <xbrli:period>
              <xbrli:startDate>2025-01-01</xbrli:startDate>
              <xbrli:endDate>2025-12-31</xbrli:endDate>
            </xbrli:period>
          </xbrli:context>
          <xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
          <us-gaap:Revenues contextRef="D2025" unitRef="USD">100</us-gaap:Revenues>
        </xbrli:xbrl>"""
        parsed = _parse_instance(xml)
        row = parsed["Revenues"][0][1]
        self.assertEqual(row["year"], 2025)
        self.assertEqual(row["fy"], 2025)

    def test_utility_build_result_invokes_filing_fallback_without_core_years(self):
        original = utility.augment_with_latest_filing

        fallback_facts = {
            "facts": {
                "filing-xbrl": {
                    "RegulatedAndUnregulatedOperatingRevenue": {
                        "USD": [
                            {"val": 100.0, "end": "2024-12-31", "start": "2024-01-01", "form": "10-K", "filing_annual": True},
                            {"val": 110.0, "end": "2025-12-31", "start": "2025-01-01", "form": "10-K", "filing_annual": True}
                        ]
                    },
                    "OperatingIncomeLoss": {
                        "USD": [
                            {"val": 20.0, "end": "2024-12-31", "start": "2024-01-01", "form": "10-K", "filing_annual": True},
                            {"val": 22.0, "end": "2025-12-31", "start": "2025-01-01", "form": "10-K", "filing_annual": True}
                        ]
                    },
                }
            }
        }

        # Use the real extractor's semantic tags via the fallback hook instead
        # of relying on a live SEC payload.
        def fake_fallback(_session, _cik, _subs, _facts):
            return fallback_facts, {"used": True, "reason": "test"}

        with patch.object(utility, "augment_with_latest_filing", side_effect=fake_fallback):
            with patch.object(utility, "calculate_downturn_defense", return_value=(0.0, {})):
                result = utility.build_result(
                    "TEST", "123", "Test Utility", {}, {}, None, None, _FakeSession()
                )

        self.assertIsNotNone(result)
        self.assertTrue(result["period_scores"])
        self.assertGreaterEqual(result["base_year"], 2025)


if __name__ == "__main__":
    unittest.main()
