import time
import unittest

from cryptobot import config, risk
from cryptobot.analyst import Verdict
from cryptobot.trader import Position


def verdict(entry=100.0, stop=99.0) -> Verdict:
    return Verdict(symbol="BTC/USDT", side="long", ok=True, entry=entry, stop=stop)


def position(symbol="ETH/USDT") -> Position:
    return Position(symbol=symbol, side="long", qty=1.0, initial_qty=1.0,
                    entry=100.0, stop=99.0, tp1=101.0, tp2=102.0,
                    risk_per_unit=1.0, opened_at=time.time())


class TestSizing(unittest.TestCase):
    def test_risk_is_a_fixed_fraction_of_equity(self):
        # Stop far enough away that the notional cap is not the binding rule.
        s = risk.size_position(verdict(100.0, 95.0), equity=10_000.0)
        self.assertTrue(s.ok)
        expected = 10_000 * config.RISK_PER_TRADE_PCT / 100
        self.assertAlmostEqual(s.risk_amount, expected, places=6)

    def test_tighter_stop_buys_more_units_for_the_same_risk(self):
        wide = risk.size_position(verdict(100.0, 94.0), equity=10_000.0)
        tight = risk.size_position(verdict(100.0, 96.0), equity=10_000.0)
        self.assertGreater(tight.qty, wide.qty)
        self.assertAlmostEqual(wide.risk_amount, tight.risk_amount, places=6)

    def test_notional_cap_binds_before_risk_on_a_tight_scalp_stop(self):
        # Documents the real interaction: a 1%-away stop hits the position cap
        # first, so the trade risks *less* than RISK_PER_TRADE_PCT, never more.
        s = risk.size_position(verdict(100.0, 99.0), equity=10_000.0)
        self.assertTrue(s.ok)
        self.assertAlmostEqual(s.notional, 10_000 * config.MAX_POSITION_PCT / 100,
                               places=6)
        self.assertLess(s.risk_amount, 10_000 * config.RISK_PER_TRADE_PCT / 100)

    def test_notional_capped_regardless_of_stop_tightness(self):
        # A 0.05% stop would otherwise size into a position many times equity.
        s = risk.size_position(verdict(100.0, 99.95), equity=10_000.0)
        cap = 10_000 * config.MAX_POSITION_PCT / 100
        self.assertLessEqual(s.notional, cap + 1e-6)

    def test_capped_position_also_caps_the_risk_taken(self):
        s = risk.size_position(verdict(100.0, 99.95), equity=10_000.0)
        self.assertLess(s.risk_amount, 10_000 * config.RISK_PER_TRADE_PCT / 100)

    def test_dust_account_rejected(self):
        s = risk.size_position(verdict(100.0, 99.0), equity=1.0)
        self.assertFalse(s.ok)
        self.assertIn("الحد الأدنى", s.reason)

    def test_zero_distance_stop_rejected(self):
        s = risk.size_position(verdict(100.0, 100.0), equity=10_000.0)
        self.assertFalse(s.ok)

    def test_inverted_stop_still_sizes_off_absolute_distance(self):
        s = risk.size_position(Verdict(symbol="X", side="short", entry=100.0,
                                       stop=101.0), equity=10_000.0)
        self.assertTrue(s.ok)


class TestGates(unittest.TestCase):
    def setUp(self):
        self.now = time.time()

    def test_clean_slate_passes(self):
        self.assertEqual(
            risk.check_gates(1000.0, [], 0.0, 0, "BTC/USDT", {}, self.now), "")

    def test_duplicate_symbol_blocked(self):
        reason = risk.check_gates(1000.0, [position("BTC/USDT")], 0.0, 0,
                                  "BTC/USDT", {}, self.now)
        self.assertIn("مركز مفتوح", reason)

    def test_max_open_positions_blocked(self):
        open_ = [position(f"C{i}/USDT") for i in range(config.MAX_OPEN_POSITIONS)]
        reason = risk.check_gates(1000.0, open_, 0.0, 0, "BTC/USDT", {}, self.now)
        self.assertIn("مراكز المفتوحة", reason)

    def test_daily_loss_cap_blocks_new_entries(self):
        loss = -1000.0 * config.DAILY_MAX_LOSS_PCT / 100
        reason = risk.check_gates(1000.0, [], loss, 0, "BTC/USDT", {}, self.now)
        self.assertIn("الحد اليومي", reason)

    def test_just_inside_the_daily_cap_still_trades(self):
        loss = -1000.0 * config.DAILY_MAX_LOSS_PCT / 100 + 0.01
        self.assertEqual(
            risk.check_gates(1000.0, [], loss, 0, "BTC/USDT", {}, self.now), "")

    def test_trade_count_cap_blocks(self):
        reason = risk.check_gates(1000.0, [], 0.0, config.MAX_TRADES_PER_DAY,
                                  "BTC/USDT", {}, self.now)
        self.assertIn("صفقات اليوم", reason)

    def test_cooldown_blocks_then_expires(self):
        cooldowns = {"BTC/USDT": self.now + 600}
        self.assertIn("تهدئة", risk.check_gates(1000.0, [], 0.0, 0, "BTC/USDT",
                                                cooldowns, self.now))
        self.assertEqual("", risk.check_gates(1000.0, [], 0.0, 0, "BTC/USDT",
                                              cooldowns, self.now + 601))

    def test_empty_account_blocked(self):
        self.assertIn("رصيد", risk.check_gates(0.0, [], 0.0, 0, "BTC/USDT", {},
                                               self.now))


if __name__ == "__main__":
    unittest.main()
