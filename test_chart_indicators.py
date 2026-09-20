import unittest

import numpy as np
import pandas as pd

from chart_indicators import (
    compute_adx,
    compute_atr,
    compute_obv,
    compute_mfi,
    compute_rolling_vwap,
    compute_williams_r,
    compute_cci,
    compute_roc,
    compute_parabolic_sar,
    compute_cmf,
    compute_all_indicators,
    generate_adx_commentary,
    generate_atr_commentary,
    generate_obv_commentary,
    generate_mfi_commentary,
    generate_vwap_commentary,
)


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

    def test_new_indicators_are_calculated_and_bounded(self):
        df = self._sample_ohlcv()
        atr = compute_atr(df["High"], df["Low"], df["Close"], window=14)
        obv = compute_obv(df["Close"], df["Volume"])
        mfi = compute_mfi(df["High"], df["Low"], df["Close"], df["Volume"], window=14)
        vwap = compute_rolling_vwap(
            df["High"], df["Low"], df["Close"], df["Volume"], window=20
        )

        self.assertEqual(len(atr), len(df))
        self.assertEqual(len(obv), len(df))
        self.assertEqual(len(mfi), len(df))
        self.assertEqual(len(vwap), len(df))

        self.assertTrue((atr.dropna() >= 0).all())
        self.assertTrue((mfi.dropna() >= 0).all())
        self.assertTrue((mfi.dropna() <= 100).all())
        self.assertTrue((vwap.dropna() > 0).all())

        # 상승 샘플에서는 OBV가 누적되어 마지막 값이 양수여야 함.
        self.assertGreater(float(obv.iloc[-1]), 0.0)

        williams = compute_williams_r(df["High"], df["Low"], df["Close"], window=14)
        cci = compute_cci(df["High"], df["Low"], df["Close"], window=20)
        roc = compute_roc(df["Close"], window=12)
        psar = compute_parabolic_sar(df["High"], df["Low"], step=0.02, max_step=0.20)
        cmf = compute_cmf(df["High"], df["Low"], df["Close"], df["Volume"], window=20)

        for series in (williams, cci, roc, psar, cmf):
            self.assertEqual(len(series), len(df))
            self.assertGreater(series.dropna().shape[0], 0)

        self.assertTrue((williams.dropna() <= 0).all())
        self.assertTrue((williams.dropna() >= -100).all())
        self.assertTrue((cmf.dropna() <= 1).all())
        self.assertTrue((cmf.dropna() >= -1).all())
        self.assertTrue((psar.dropna() > 0).all())

    def test_new_indicator_commentaries_use_current_values(self):
        idx = pd.bdate_range("2025-01-01", periods=40)
        atr = pd.Series(5.0, index=idx)
        close = pd.Series(100.0, index=idx)
        obv = pd.Series(np.arange(40) * 1_000_000.0, index=idx)
        mfi = pd.Series(85.0, index=idx)
        vwap = pd.Series(98.0, index=idx)

        self.assertIn("5.00", generate_atr_commentary(atr, close))
        self.assertIn("OBV", generate_obv_commentary(obv))
        self.assertIn("85.0", generate_mfi_commentary(mfi))
        self.assertIn("98.00", generate_vwap_commentary(close, vwap))

    def test_compute_all_indicators_exposes_adx(self):
        df = self._sample_ohlcv()
        indicators = compute_all_indicators(df)
        for key in ("adx14", "plus_di14", "minus_di14", "williams_r", "cci", "roc", "psar", "cmf"):
            self.assertIn(key, indicators)
            self.assertEqual(len(indicators[key]), len(df))

    def test_new_commentaries_use_current_values(self):
        from chart_indicators import (
            generate_williams_r_commentary,
            generate_cci_commentary,
            generate_roc_commentary,
            generate_psar_commentary,
            generate_cmf_commentary,
        )
        idx = pd.bdate_range("2025-01-01", periods=40)
        close = pd.Series(100.0, index=idx)
        williams = pd.Series(-15.0, index=idx)
        cci = pd.Series(125.0, index=idx)
        roc = pd.Series(6.5, index=idx)
        psar = pd.Series(98.0, index=idx)
        cmf = pd.Series(0.25, index=idx)

        self.assertIn("-15.0", generate_williams_r_commentary(williams))
        self.assertIn("125.0", generate_cci_commentary(cci))
        self.assertIn("6.50", generate_roc_commentary(roc))
        self.assertIn("98.00", generate_psar_commentary(close, psar))
        self.assertIn("0.250", generate_cmf_commentary(cmf))

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
