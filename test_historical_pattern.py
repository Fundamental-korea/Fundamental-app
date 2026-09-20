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
    classify_current_condition,
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
        self.assertGreaterEqual(stats["down_probability_ci_low"], 0.0)
        self.assertLessEqual(stats["down_probability_ci_high"], 100.0)

    def test_horizons_are_fixed_trading_day_offsets(self):
        self.assertEqual(HORIZONS, (5, 20, 60))

    def test_wilson_interval_stays_inside_zero_to_hundred(self):
        low, high = _wilson_interval(8, 10)
        self.assertGreaterEqual(low, 0.0)
        self.assertLessEqual(high, 100.0)
        self.assertLess(low, high)

    def test_overbought_condition_switches_primary_direction_to_down(self):
        idx = pd.bdate_range("2025-01-01", periods=80)
        close = pd.Series(np.linspace(100, 125, len(idx)), index=idx)
        rsi = pd.Series(75.0, index=idx)
        stoch_k = pd.Series(92.0, index=idx)
        stoch_d = pd.Series(90.0, index=idx)
        sma20 = pd.Series(112.0, index=idx)
        sma60 = pd.Series(105.0, index=idx)
        bb_mid = pd.Series(112.0, index=idx)
        bb_upper = pd.Series(120.0, index=idx)
        bb_lower = pd.Series(100.0, index=idx)
        macd_hist = pd.Series(np.linspace(3.0, 1.0, len(idx)), index=idx)
        indicators = {
            "rsi14": rsi,
            "stoch_k": stoch_k,
            "stoch_d": stoch_d,
            "sma20": sma20,
            "sma60": sma60,
            "bb_mid": bb_mid,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "macd_hist": macd_hist,
        }
        condition = classify_current_condition(
            pd.DataFrame({"Close": close}, index=idx),
            indicators,
        )
        self.assertTrue(condition["state"].startswith("overbought"))
        self.assertEqual(condition["primary_direction"], "down")

    def test_oversold_condition_switches_primary_direction_to_up(self):
        idx = pd.bdate_range("2025-01-01", periods=80)
        close = pd.Series(np.linspace(125, 100, len(idx)), index=idx)
        rsi = pd.Series(25.0, index=idx)
        stoch_k = pd.Series(8.0, index=idx)
        stoch_d = pd.Series(10.0, index=idx)
        sma20 = pd.Series(115.0, index=idx)
        sma60 = pd.Series(120.0, index=idx)
        bb_mid = pd.Series(115.0, index=idx)
        bb_upper = pd.Series(130.0, index=idx)
        bb_lower = pd.Series(110.0, index=idx)
        macd_hist = pd.Series(np.linspace(-1.0, -3.0, len(idx)), index=idx)
        indicators = {
            "rsi14": rsi,
            "stoch_k": stoch_k,
            "stoch_d": stoch_d,
            "sma20": sma20,
            "sma60": sma60,
            "bb_mid": bb_mid,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "macd_hist": macd_hist,
        }
        condition = classify_current_condition(
            pd.DataFrame({"Close": close}, index=idx),
            indicators,
        )
        self.assertTrue(condition["state"].startswith("oversold"))
        self.assertEqual(condition["primary_direction"], "up")


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
