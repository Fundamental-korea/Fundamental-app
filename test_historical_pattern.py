import unittest
import numpy as np
import pandas as pd

from historical_pattern import (
    HORIZONS,
    LOOKBACK_YEARS,
    MIN_SIMILARITY,
    _similarity,
    _outcome_stats,
    _select_matches,
    _wilson_interval,
)


class HistoricalPatternTests(unittest.TestCase):
    def test_similarity_is_deterministic_and_high_for_same_state(self):
        current = pd.Series({
            "rsi": 55.0,
            "rsi_change5": 2.0,
            "return20": 0.04,
        })
        same = current.copy()
        self.assertEqual(_similarity(current, same, "rsi"), 100.0)

    def test_outcome_stats_counts_real_forward_returns(self):
        close = pd.Series([100, 102, 101, 103, 104, 105, 110, 108, 111, 115, 120])
        matches = [(0, 80.0), (1, 70.0)]
        stats = _outcome_stats(close, matches, 5)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["samples"], 2)
        self.assertAlmostEqual(stats["up_probability"], 100.0)
        self.assertGreater(stats["median_return"], 0.0)

    def test_horizons_are_fixed_trading_day_offsets(self):
        self.assertEqual(HORIZONS, (5, 20, 60))

    def test_wilson_interval_stays_inside_zero_to_hundred(self):
        low, high = _wilson_interval(8, 10)
        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(high, 100.0)
        self.assertLess(low, high)

    def test_match_selection_has_no_fixed_100_case_cap(self):
        idx = pd.bdate_range("2020-01-01", periods=800)
        features = pd.DataFrame(
            {"rsi": 50.0, "rsi_change5": 0.0, "return20": 0.0},
            index=idx,
        )
        matches = _select_matches(features, "rsi")
        self.assertGreater(len(matches), 100)
        self.assertGreaterEqual(min(sim for _, sim in matches), MIN_SIMILARITY)
        self.assertEqual(LOOKBACK_YEARS, 10)


if __name__ == "__main__":
    unittest.main()
