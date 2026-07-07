import unittest

from options_scanner.indicators import compute_rsi


class TestRSI(unittest.TestCase):
    def test_insufficient_data_returns_none(self):
        self.assertIsNone(compute_rsi([1, 2, 3], period=14))

    def test_exact_minimum_length_computes(self):
        closes = [float(i) for i in range(1, 16)]  # 15 closes, period=14
        self.assertIsNotNone(compute_rsi(closes, period=14))

    def test_strictly_increasing_prices_give_rsi_100(self):
        closes = [float(i) for i in range(1, 30)]
        self.assertEqual(compute_rsi(closes, period=14), 100.0)

    def test_strictly_decreasing_prices_give_rsi_0(self):
        closes = [float(i) for i in range(30, 1, -1)]
        self.assertEqual(compute_rsi(closes, period=14), 0.0)

    def test_flat_prices_give_rsi_100_by_convention(self):
        # no losses at all -> avg_loss == 0 -> defined as 100
        closes = [100.0] * 20
        self.assertEqual(compute_rsi(closes, period=14), 100.0)

    def test_oscillating_prices_are_mid_range(self):
        closes = [100, 102, 99, 101, 98, 103, 97, 104, 96, 105, 95, 106, 94, 107, 93]
        rsi = compute_rsi(closes, period=14)
        self.assertIsNotNone(rsi)
        self.assertTrue(0 < rsi < 100)


if __name__ == "__main__":
    unittest.main()
