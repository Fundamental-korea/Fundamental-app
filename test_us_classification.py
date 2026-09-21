import unittest

from us_classification import classify_company


class USClassificationSafetyTest(unittest.TestCase):
    def test_non_reit_real_estate_is_typed_without_special_scoring(self):
        result = classify_company(
            "TESTRE",
            "Test Real Estate Company",
            "6512",
            "Operators of Nonresidential Buildings",
        )
        self.assertEqual(result["sector_common"], "real_estate")
        self.assertEqual(result["company_type"], "real_estate_company")
        self.assertEqual(result["scoring_profile"], "standard")

    def test_obvious_digital_asset_sic6199_does_not_become_financial_profile(self):
        result = classify_company(
            "TESTBC",
            "Test Blockchain Holdings Inc.",
            "6199",
            "Finance Services",
        )
        self.assertEqual(result["sector_common"], "other")
        self.assertEqual(result["scoring_profile"], "standard")

    def test_generic_sic6199_is_not_reclassified_without_keyword(self):
        result = classify_company(
            "TESTGF",
            "Test Generic Finance Services Inc.",
            "6199",
            "Finance Services",
        )
        self.assertEqual(result["sector_common"], "financials")


if __name__ == "__main__":
    unittest.main()
