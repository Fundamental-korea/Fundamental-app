import unittest

import us_scoring


class USScoringStructureTest(unittest.TestCase):
    def test_profile_weights_sum_to_100(self):
        for profile, weights in us_scoring.PROFILE_METRICS.items():
            self.assertEqual(sum(weights.values()), 100, profile)

    def test_profile_bands_match_metrics(self):
        for profile, weights in us_scoring.PROFILE_METRICS.items():
            self.assertEqual(set(weights), set(us_scoring.PROFILE_BANDS[profile]), profile)
            for metric, bands in us_scoring.PROFILE_BANDS[profile].items():
                self.assertEqual(len(bands), 10, f"{profile}:{metric}")
                self.assertEqual([score for _, score in bands], list(range(10, 0, -1)))

    def test_standard_boundary_values(self):
        cases = {
            "revenue_growth": (12, 8),
            "eps_growth": (15, 8),
            "opm": (20, 8),
            "roic": (12, 8),
            "debt_rate": (75, 8),
            "quick_ratio": (1.2, 8),
            "interest_coverage": (8, 8),
            "ocf_ratio": (1.1, 8),
            "sga_ratio": (16, 8),
            "downturn_defense": (5, 8),
        }
        for metric, (value, expected) in cases.items():
            self.assertEqual(
                us_scoring.calculate_metric_score_us(metric, value, "standard"),
                expected,
                metric,
            )

    def test_standard_all_eight_scores_to_80(self):
        metrics = {
            "revenue_growth": 12,
            "eps_growth": 15,
            "opm": 20,
            "roic": 12,
            "debt_rate": 75,
            "quick_ratio": 1.2,
            "interest_coverage": 8,
            "ocf_ratio": 1.1,
            "sga_ratio": 16,
            "downturn_defense": 5,
        }
        result = us_scoring.calculate_us_score(metrics, "standard")
        self.assertEqual(result["total_score"], 80.0)
        self.assertEqual(result["coverage_pct"], 100.0)
        self.assertEqual(result["grade"], "S")

    def test_missing_metric_uses_coverage_cap(self):
        metrics = {
            "revenue_growth": 12,
            "eps_growth": 15,
            "opm": 20,
            "roic": 12,
            "debt_rate": 75,
            "quick_ratio": 1.2,
            "interest_coverage": None,
            "ocf_ratio": 1.1,
            "sga_ratio": 16,
            "downturn_defense": 5,
        }
        result = us_scoring.calculate_us_score(metrics, "standard")
        self.assertEqual(result["coverage_pct"], 95.0)
        self.assertEqual(result["score_cap"], 100.0)
        self.assertEqual(result["total_score"], 80.0)
        self.assertEqual(result["missing_metric_count"], 1)

    def test_lower_better_metrics(self):
        self.assertEqual(us_scoring.calculate_metric_score_us("debt_rate", 30, "standard"), 10)
        self.assertEqual(us_scoring.calculate_metric_score_us("debt_rate", 800, "standard"), 1)
        self.assertEqual(us_scoring.calculate_metric_score_us("sga_ratio", 8, "standard"), 10)
        self.assertEqual(us_scoring.calculate_metric_score_us("sga_ratio", 80, "standard"), 1)

    def test_defense_is_standard_bands_with_reweighted_metrics(self):
        self.assertIs(
            us_scoring.PROFILE_BANDS["defense"]["opm"],
            us_scoring.STANDARD_BANDS["opm"],
        )
        self.assertEqual(
            us_scoring.PROFILE_METRICS["defense"]["downturn_defense"],
            18,
        )
        self.assertEqual(
            us_scoring.calculate_metric_score_us("opm", 20, "defense"),
            us_scoring.calculate_metric_score_us("opm", 20, "standard"),
        )

    def test_profiles_are_deterministic(self):
        samples = {
            "financial": {
                "revenue_growth": 7,
                "eps_growth": 10,
                "roa": 2.0,
                "downturn_defense": 5,
            },
            "reit": {
                "revenue_growth": 5,
                "eps_growth": 5,
                "roa": 4,
                "debt_rate": 60,
                "ocf_ratio": 1.1,
                "interest_coverage": 6,
                "downturn_defense": 5,
            },
            "bdc": {
                "eps_growth": 10,
                "roa": 5,
                "debt_rate": 90,
                "ocf_ratio": 1.1,
                "interest_coverage": 6,
                "downturn_defense": 5,
            },
            "defense": {
                "revenue_growth": 7,
                "eps_growth": 10,
                "opm": 20,
                "roic": 12,
                "debt_rate": 100,
                "quick_ratio": 1.2,
                "interest_coverage": 7,
                "ocf_ratio": 1.1,
                "sga_ratio": 12,
                "downturn_defense": 5,
            },
            "utility": {
                "revenue_growth": 10,
                "eps_growth": 10,
                "opm": 25,
                "roa": 5,
                "debt_capital": 45,
                "ocf_debt": 15,
                "fcf_debt": 6,
                "interest_coverage": 5,
                "dividend_coverage": 3.5,
                "dividend_payout": 60,
                "downturn_defense": 10,
            },
        }
        for profile, metrics in samples.items():
            first = us_scoring.calculate_us_score(metrics, profile)
            second = us_scoring.calculate_us_score(metrics, profile)
            self.assertEqual(first, second)
            self.assertGreaterEqual(first["total_score"], 0)
            self.assertLessEqual(first["total_score"], 100)


    def test_score_confidence_levels(self):
        self.assertEqual(us_scoring.score_confidence_level(100.0), "high")
        self.assertEqual(us_scoring.score_confidence_level(90.0), "high")
        self.assertEqual(us_scoring.score_confidence_level(75.0), "medium")
        self.assertEqual(us_scoring.score_confidence_level(60.0), "low")
        self.assertEqual(us_scoring.score_confidence_level(59.9), "insufficient")

    def test_score_returns_confidence_metadata(self):
        metrics = {
            "revenue_growth": 12,
            "eps_growth": 15,
            "opm": 20,
            "roic": None,
            "debt_rate": 75,
            "quick_ratio": 1.2,
            "interest_coverage": 8,
            "ocf_ratio": 1.1,
            "sga_ratio": 16,
            "downturn_defense": 5,
        }
        result = us_scoring.calculate_us_score(metrics, "standard")
        self.assertEqual(result["coverage_pct"], 85.0)
        self.assertEqual(result["confidence_level"], "medium")
        self.assertEqual(result["score_cap"], 92.0)

    def test_extreme_flags_do_not_change_score(self):
        base = {
            "revenue_growth": 5,
            "eps_growth": 5,
            "opm": 10,
            "roic": 8,
            "debt_rate": 50,
            "quick_ratio": 2.0,
            "interest_coverage": 8,
            "ocf_ratio": 1.1,
            "sga_ratio": 12,
            "downturn_defense": 2,
        }
        extreme = dict(base)
        extreme["quick_ratio"] = 25.0
        normal = us_scoring.calculate_us_score(base, "standard")
        flagged = us_scoring.calculate_us_score(extreme, "standard")
        self.assertTrue(flagged["metric_scores"]["quick_ratio"]["is_extreme"])
        self.assertEqual(
            normal["metric_scores"]["quick_ratio"]["score"],
            flagged["metric_scores"]["quick_ratio"]["score"],
        )


if __name__ == "__main__":
    unittest.main()
