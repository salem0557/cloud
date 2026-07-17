import unittest
from datetime import date

from options_scanner.config import ScreenerConfig
from options_scanner.filters import OptionContract, passes_filters


def make_contract(**overrides) -> OptionContract:
    base = dict(
        ticker="TEST", contract_symbol="TEST250101C00100000", option_type="call",
        expiry=date.today(), dte=21, strike=100.0, spot=100.0,
        bid=1.00, ask=1.05, volume=500, open_interest=1000,
        iv=0.30, delta=0.45, theta=-0.03,
    )
    base.update(overrides)
    return OptionContract(**base)


class TestFilters(unittest.TestCase):
    def setUp(self):
        self.cfg = ScreenerConfig()

    def test_good_contract_passes(self):
        self.assertTrue(passes_filters(make_contract(), self.cfg))

    def test_low_volume_rejected(self):
        self.assertFalse(passes_filters(make_contract(volume=5), self.cfg))

    def test_low_open_interest_rejected(self):
        self.assertFalse(passes_filters(make_contract(open_interest=10), self.cfg))

    def test_wide_spread_rejected(self):
        # ask=1.50 puts the spread exactly at the 40% cap (0.50/1.25), which
        # the inclusive <= ceiling accepts; use a spread clearly above it.
        self.assertFalse(passes_filters(make_contract(bid=1.00, ask=1.60), self.cfg))

    def test_iv_out_of_range_rejected(self):
        self.assertFalse(passes_filters(make_contract(iv=0.05), self.cfg))
        self.assertFalse(passes_filters(make_contract(iv=1.50), self.cfg))

    def test_delta_out_of_range_rejected(self):
        self.assertFalse(passes_filters(make_contract(delta=0.05), self.cfg))
        self.assertFalse(passes_filters(make_contract(delta=0.95), self.cfg))

    def test_put_delta_uses_absolute_value(self):
        self.assertTrue(passes_filters(make_contract(option_type="put", delta=-0.45), self.cfg))

    def test_dte_out_of_range_rejected(self):
        self.assertFalse(passes_filters(make_contract(dte=1), self.cfg))
        self.assertFalse(passes_filters(make_contract(dte=100), self.cfg))

    def test_zero_bid_rejected(self):
        self.assertFalse(passes_filters(make_contract(bid=0), self.cfg))

    def test_high_theta_burn_rejected(self):
        self.assertFalse(passes_filters(make_contract(theta=-0.20), self.cfg))

    def test_theta_filter_disabled_when_none(self):
        cfg = ScreenerConfig(max_theta_pct_of_price=None)
        self.assertTrue(passes_filters(make_contract(theta=-0.20), cfg))


if __name__ == "__main__":
    unittest.main()
