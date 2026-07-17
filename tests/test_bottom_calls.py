"""اختبارات فلتر عقود استراتيجية القاع (scanner/options.py) — دوال نقية."""
from scanner.options import bottom_call_matches, moneyness_label


def _contract(**overrides):
    c = {"strike": 9.0, "days": 90, "premium": 2.5}
    c.update(overrides)
    return c


SPOT = 10.0
ARGS = dict(max_premium=3.0, min_days=30, max_days=365, atm_tol=0.02)


def test_itm_cheap_mid_dte_passes():
    assert bottom_call_matches(_contract(), SPOT, **ARGS)


def test_atm_within_tolerance_passes():
    assert bottom_call_matches(_contract(strike=10.15), SPOT, **ARGS)


def test_otm_strike_rejected():
    assert not bottom_call_matches(_contract(strike=10.5), SPOT, **ARGS)


def test_expensive_premium_rejected():
    assert not bottom_call_matches(_contract(premium=3.05), SPOT, **ARGS)


def test_dte_window_enforced():
    assert not bottom_call_matches(_contract(days=29), SPOT, **ARGS)
    assert not bottom_call_matches(_contract(days=366), SPOT, **ARGS)
    assert bottom_call_matches(_contract(days=30), SPOT, **ARGS)
    assert bottom_call_matches(_contract(days=365), SPOT, **ARGS)


def test_moneyness_label():
    assert moneyness_label(9.0, SPOT, 0.02) == "ITM"
    assert moneyness_label(9.9, SPOT, 0.02) == "ATM"
    assert moneyness_label(10.1, SPOT, 0.02) == "ATM"
