import unittest

from cryptobot.indicators import (Candle, atr, bollinger, ema, fmt_price, last,
                                  macd, rising_lows, rsi, sma, true_range, vwap)


def series(values):
    """Flat candles from a close series, with a small symmetric range."""
    out = []
    for i, v in enumerate(values):
        out.append(Candle(ts=i * 60000, open=v, high=v * 1.002, low=v * 0.998,
                          close=v, volume=100.0))
    return out


class TestMovingAverages(unittest.TestCase):
    def test_sma_matches_manual(self):
        self.assertEqual(sma([1, 2, 3, 4, 5], 3)[2], 2.0)
        self.assertEqual(sma([1, 2, 3, 4, 5], 3)[4], 4.0)

    def test_sma_undefined_before_period(self):
        self.assertIsNone(sma([1, 2, 3], 3)[1])

    def test_sma_short_series(self):
        self.assertEqual(sma([1, 2], 5), [None, None])

    def test_ema_seeded_with_sma(self):
        self.assertEqual(ema([1, 2, 3], 3)[2], 2.0)

    def test_ema_tracks_a_rally(self):
        values = list(range(1, 51))
        e = ema(values, 10)
        self.assertLess(e[-1], values[-1])       # lags price
        self.assertGreater(e[-1], e[-10])        # but rises with it

    def test_constant_series_has_flat_ema(self):
        self.assertAlmostEqual(last(ema([5.0] * 30, 10)), 5.0)


class TestRSI(unittest.TestCase):
    def test_straight_rally_is_100(self):
        self.assertEqual(last(rsi([float(i) for i in range(1, 40)], 14)), 100.0)

    def test_straight_selloff_is_zero(self):
        self.assertAlmostEqual(last(rsi([float(i) for i in range(40, 1, -1)], 14)), 0.0)

    def test_undefined_before_period(self):
        self.assertIsNone(rsi([1.0, 2.0, 3.0], 14)[-1])

    def test_ranges_between_0_and_100(self):
        values = [100 + (i % 7) - 3 for i in range(60)]
        for v in rsi([float(x) for x in values], 14):
            if v is not None:
                self.assertGreaterEqual(v, 0.0)
                self.assertLessEqual(v, 100.0)


class TestVolatility(unittest.TestCase):
    def test_true_range_uses_previous_close_gap(self):
        candles = [Candle(0, 10, 11, 9, 10, 1), Candle(1, 20, 21, 19, 20, 1)]
        # gap up: |high - prev_close| = 11 beats the 2-wide bar range
        self.assertEqual(true_range(candles)[1], 11.0)

    def test_atr_positive_on_moving_market(self):
        value = last(atr(series([100 + i for i in range(40)]), 14))
        self.assertIsNotNone(value)
        self.assertGreater(value, 0)

    def test_atr_needs_history(self):
        self.assertIsNone(last(atr(series([100.0] * 5), 14)))

    def test_bollinger_bands_straddle_the_mean(self):
        lower, mid, upper = bollinger([100 + (i % 5) for i in range(40)], 20, 2.0)
        self.assertLess(lower[-1], mid[-1])
        self.assertLess(mid[-1], upper[-1])

    def test_bollinger_flat_series_collapses(self):
        lower, mid, upper = bollinger([50.0] * 30, 20, 2.0)
        self.assertAlmostEqual(lower[-1], 50.0)
        self.assertAlmostEqual(upper[-1], 50.0)


class TestMACD(unittest.TestCase):
    def test_histogram_positive_when_the_trend_accelerates(self):
        # A linear ramp gives a constant MACD line and a zero histogram, so
        # acceleration — not direction — is what the histogram measures.
        values = [float(i * i) / 50 for i in range(1, 80)]
        line, sig, hist = macd(values)
        self.assertGreater(hist[-1], 0)

    def test_histogram_negative_when_the_trend_decelerates(self):
        values = [-float(i * i) / 50 for i in range(1, 80)]
        line, sig, hist = macd(values)
        self.assertLess(hist[-1], 0)

    def test_series_stay_input_aligned(self):
        values = [float(i) for i in range(1, 80)]
        line, sig, hist = macd(values)
        self.assertEqual(len(line), len(values))
        self.assertEqual(len(sig), len(values))
        self.assertEqual(len(hist), len(values))


class TestStructureAndVwap(unittest.TestCase):
    def test_vwap_equals_price_on_flat_market(self):
        self.assertAlmostEqual(last(vwap(series([10.0] * 30), 20)), 10.0, places=6)

    def test_vwap_ignores_zero_volume(self):
        candles = series([10.0] * 30)
        for c in candles:
            c.volume = 0.0
        self.assertAlmostEqual(last(vwap(candles, 20)), 10.0, places=6)

    def test_rising_lows_detected_in_uptrend(self):
        # zig-zag upward: each pullback bottoms higher than the last
        closes = []
        base = 100.0
        for _ in range(6):
            closes += [base, base + 3, base + 1, base + 4]
            base += 2
        self.assertTrue(rising_lows(series(closes), order=1))

    def test_rising_lows_false_in_downtrend(self):
        closes = []
        base = 100.0
        for _ in range(6):
            closes += [base, base - 3, base - 1, base - 4]
            base -= 2
        self.assertFalse(rising_lows(series(closes), order=1))


class TestFormatting(unittest.TestCase):
    def test_large_price_two_decimals(self):
        self.assertEqual(fmt_price(61250.0), "61,250.00$")

    def test_micro_price_keeps_significant_digits(self):
        self.assertEqual(fmt_price(0.000012), "0.000012$")


if __name__ == "__main__":
    unittest.main()
