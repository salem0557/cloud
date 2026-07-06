import unittest

from options_scanner.greeks import black_scholes_greeks


class TestGreeks(unittest.TestCase):
    def test_call_delta_near_the_money(self):
        g = black_scholes_greeks(
            spot=100, strike=100, time_to_expiry_years=30 / 365,
            risk_free_rate=0.045, volatility=0.25, option_type="call",
        )
        self.assertTrue(0.45 < g.delta < 0.65)
        self.assertLess(g.theta, 0)

    def test_put_delta_is_negative(self):
        g = black_scholes_greeks(
            spot=100, strike=100, time_to_expiry_years=30 / 365,
            risk_free_rate=0.045, volatility=0.25, option_type="put",
        )
        self.assertTrue(-0.65 < g.delta < -0.35)

    def test_deep_itm_call_delta_near_one(self):
        g = black_scholes_greeks(
            spot=150, strike=100, time_to_expiry_years=30 / 365,
            risk_free_rate=0.045, volatility=0.25, option_type="call",
        )
        self.assertGreater(g.delta, 0.9)

    def test_deep_otm_put_delta_near_zero(self):
        g = black_scholes_greeks(
            spot=150, strike=100, time_to_expiry_years=30 / 365,
            risk_free_rate=0.045, volatility=0.25, option_type="put",
        )
        self.assertGreater(g.delta, -0.1)

    def test_zero_time_returns_zero_greeks(self):
        g = black_scholes_greeks(
            spot=100, strike=100, time_to_expiry_years=0,
            risk_free_rate=0.045, volatility=0.25, option_type="call",
        )
        self.assertEqual(g.delta, 0.0)
        self.assertEqual(g.theta, 0.0)


if __name__ == "__main__":
    unittest.main()
