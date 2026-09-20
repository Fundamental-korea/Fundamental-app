import unittest

import numpy as np
import pandas as pd

from chart_indicators import compute_adx, compute_all_indicators, generate_adx_commentary


class ADXIndicatorTests(unittest.TestCase):
    def _sample_ohlcv(self, n=120):
        idx = pd.bdate_range("2025-01-01", periods=n)
        base = np.linspace(100, 160, n)
        high = pd.Series(base + 2.0, index=idx)
        low = pd.Series(base - 2.0, index=idx)
        close = pd.Series(base + 0.5, index=idx)
        volume = pd.Series(1_000_000, index=idx)
        return pd.DataFrame(
            {"Open": close - 0.5, "High": high, "Low": low, "Close": close, "Volume": volume},
            index=idx,
        )

    def test_adx_returns_three_bounded_series(self):
        df = self._sample_ohlcv()
        adx, plus_di, minus_di = compute_adx(
            df["High"], df["Low"], df["Close"], window=14
        )

        self.assertEqual(len(adx), len(df))
        self.assertEqual(len(plus_di), len(df))
        self.assertEqual(len(minus_di), len(df))

        for series in (adx, plus_di, minus_di):
            valid = series.dropna()
            self.assertTrue((valid >= 0).all())
            self.assertTrue((valid <= 100).all())

    def test_compute_all_indicators_exposes_adx(self):
        df = self._sample_ohlcv()
        indicators = compute_all_indicators(df)
        for key in ("adx14", "plus_di14", "minus_di14"):
            self.assertIn(key, indicators)
            self.assertEqual(len(indicators[key]), len(df))

    def test_adx_commentary_uses_current_values(self):
        idx = pd.bdate_range("2025-01-01", periods=40)
        adx = pd.Series(35.0, index=idx)
        plus_di = pd.Series(28.0, index=idx)
        minus_di = pd.Series(15.0, index=idx)
        commentary = generate_adx_commentary(adx, plus_di, minus_di)

        self.assertIn("35.0", commentary)
        self.assertIn("상승 방향성", commentary)
        self.assertIn("+DI", commentary)


if __name__ == "__main__":
    unittest.main()
