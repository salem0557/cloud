import time
import unittest

from cryptobot import config, trader
from cryptobot.trader import Position


def long_position(**kw) -> Position:
    base = dict(symbol="BTC/USDT", side="long", qty=1.0, initial_qty=1.0,
                entry=100.0, stop=98.0, tp1=102.0, tp2=104.0,
                risk_per_unit=2.0, opened_at=time.time())
    base.update(kw)
    return Position(**base)


def short_position(**kw) -> Position:
    base = dict(symbol="BTC/USDT", side="short", qty=1.0, initial_qty=1.0,
                entry=100.0, stop=102.0, tp1=98.0, tp2=96.0,
                risk_per_unit=2.0, opened_at=time.time())
    base.update(kw)
    return Position(**base)


class TestExitRules(unittest.TestCase):
    def test_stop_closes_everything(self):
        actions = trader.manage(long_position(), price=97.9, atr_val=1.0)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0].kind, "close")
        self.assertEqual(actions[0].qty, 1.0)

    def test_stop_wins_over_target_on_the_same_bar(self):
        # Price shown beyond both levels: the pessimistic read must be taken.
        pos = long_position(stop=98.0, tp2=104.0)
        actions = trader.manage(pos, price=97.0, atr_val=1.0)
        self.assertEqual(actions[0].kind, "close")
        self.assertIn("وقف", actions[0].reason)

    def test_tp1_banks_half_and_moves_stop_to_breakeven(self):
        pos = long_position()
        actions = trader.manage(pos, price=102.5, atr_val=1.0)
        kinds = [a.kind for a in actions]
        self.assertEqual(kinds, ["partial", "move_stop"])
        self.assertAlmostEqual(actions[0].qty, config.TP1_FRACTION)
        self.assertEqual(actions[1].new_stop, pos.entry)

    def test_tp2_closes_the_remainder(self):
        pos = long_position(qty=0.5, tp1_done=True)
        actions = trader.manage(pos, price=104.5, atr_val=1.0)
        self.assertEqual(actions[0].kind, "close")
        self.assertEqual(actions[0].qty, 0.5)

    def test_no_action_while_the_trade_develops(self):
        self.assertEqual(trader.manage(long_position(), price=100.5, atr_val=1.0), [])

    def test_stale_trade_is_cut_after_the_clock(self):
        old = time.time() - (config.MAX_HOLD_MINUTES + 5) * 60
        actions = trader.manage(long_position(opened_at=old), price=100.1, atr_val=1.0)
        self.assertTrue(any(a.kind == "close" for a in actions))

    def test_winning_trade_survives_the_clock(self):
        old = time.time() - (config.MAX_HOLD_MINUTES + 5) * 60
        pos = long_position(opened_at=old)
        price = pos.entry + config.STALE_EXIT_R * pos.risk_per_unit + 0.1
        self.assertEqual([a for a in trader.manage(pos, price, 1.0)
                          if a.kind == "close"], [])

    def test_momentum_flip_ignored_before_tp1(self):
        actions = trader.manage(long_position(), price=100.5, atr_val=1.0,
                                momentum_flip=True)
        self.assertEqual(actions, [])

    def test_momentum_flip_closes_after_tp1(self):
        pos = long_position(qty=0.5, tp1_done=True, stop=100.0)
        actions = trader.manage(pos, price=101.0, atr_val=0.0, momentum_flip=True)
        self.assertTrue(any(a.kind == "close" for a in actions))


class TestTrailing(unittest.TestCase):
    def test_no_trail_before_tp1(self):
        self.assertIsNone(update := trader.update_trail(long_position(), 103.0, 1.0))

    def test_trail_follows_the_high_water_mark(self):
        pos = long_position(tp1_done=True, stop=100.0, best_price=100.0)
        new_stop = trader.update_trail(pos, price=110.0, atr_val=2.0)
        self.assertAlmostEqual(new_stop, 108.0)

    def test_trail_never_moves_backwards(self):
        pos = long_position(tp1_done=True, stop=108.0, best_price=110.0)
        self.assertIsNone(trader.update_trail(pos, price=105.0, atr_val=2.0))

    def test_short_trail_moves_down_only(self):
        pos = short_position(tp1_done=True, stop=100.0, best_price=100.0)
        self.assertAlmostEqual(trader.update_trail(pos, 90.0, 2.0), 92.0)
        pos.stop = 92.0
        self.assertIsNone(trader.update_trail(pos, 95.0, 2.0))


class TestShortSide(unittest.TestCase):
    def test_short_stop_triggers_above_entry(self):
        actions = trader.manage(short_position(), price=102.5, atr_val=1.0)
        self.assertEqual(actions[0].kind, "close")

    def test_short_tp1_triggers_below_entry(self):
        actions = trader.manage(short_position(), price=97.5, atr_val=1.0)
        self.assertEqual(actions[0].kind, "partial")

    def test_short_pnl_is_positive_when_price_falls(self):
        self.assertGreater(short_position().unrealized(95.0), 0)


class TestBookkeeping(unittest.TestCase):
    def test_partial_close_reduces_quantity(self):
        pos = long_position()
        trade = trader.apply_close(pos, price=102.0, qty=0.5, reason="هدف")
        self.assertAlmostEqual(pos.qty, 0.5)
        self.assertAlmostEqual(trade.pnl, 1.0)
        self.assertAlmostEqual(trade.r, 1.0)

    def test_fees_reduce_realized_pnl(self):
        pos = long_position()
        trade = trader.apply_close(pos, price=102.0, qty=1.0, reason="هدف", fee=0.4)
        self.assertAlmostEqual(trade.pnl, 1.6)

    def test_cannot_close_more_than_is_open(self):
        pos = long_position(qty=0.4)
        trade = trader.apply_close(pos, price=102.0, qty=1.0, reason="هدف")
        self.assertAlmostEqual(trade.qty, 0.4)
        self.assertEqual(pos.qty, 0.0)

    def test_loss_is_recorded_negative(self):
        pos = long_position()
        self.assertLess(trader.apply_close(pos, 98.0, 1.0, "وقف").pnl, 0)

    def test_r_multiple_tracks_the_stop_distance(self):
        pos = long_position()
        self.assertAlmostEqual(pos.r_multiple(104.0), 2.0)
        self.assertAlmostEqual(pos.r_multiple(98.0), -1.0)

    def test_round_trip_serialization(self):
        pos = long_position(tp1_done=True, fees=0.3)
        restored = Position.from_dict(pos.to_dict())
        self.assertEqual(restored.to_dict(), pos.to_dict())


if __name__ == "__main__":
    unittest.main()
