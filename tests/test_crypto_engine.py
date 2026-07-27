import os
import tempfile
import time
import unittest
from unittest import mock

from cryptobot import config
from cryptobot.engine import Engine
from cryptobot.state import State
from tests.test_crypto_analyst import (GOOD_TICKER, make, pullback_ltf,
                                       uptrend_htf)


class FakeExchange:
    """Serves canned candles and a settable last price; records every order."""

    def __init__(self, ltf, htf, price):
        self.ltf, self.htf, self.price = ltf, htf, price
        self.orders = []

    def fetch_candles(self, symbol, timeframe, limit):
        return self.htf if timeframe == config.HTF else self.ltf

    def fetch_ticker(self, symbol):
        return dict(GOOD_TICKER, last=self.price)

    def last_price(self, symbol):
        return self.price

    def amount_to_precision(self, symbol, qty):
        return qty

    def create_market_order(self, symbol, side, qty, reduce_only=False):
        raise AssertionError("live order attempted while in paper mode")


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)
        self.state = State(self.path)
        self.state.watchlist = ["BTC/USDT"]
        self.ltf = pullback_ltf()
        self.price = self.ltf[-1].close
        self.exchange = FakeExchange(self.ltf, uptrend_htf(), self.price)
        self.engine = Engine(self.state, self.exchange)
        self.engine.paper.exchange = self.exchange

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)


class TestEntryFlow(EngineTestCase):
    def test_paper_mode_is_the_default(self):
        self.assertEqual(self.engine.mode, "paper")

    def test_tick_opens_a_position_on_an_approved_setup(self):
        events = self.engine.tick()
        self.assertIn("BTC/USDT", self.state.positions)
        self.assertTrue(any("دخول" in e for e in events))

    def test_entry_sizes_within_the_position_cap(self):
        self.engine.tick()
        pos = self.state.positions["BTC/USDT"]
        cap = self.state.paper_balance * config.MAX_POSITION_PCT / 100
        self.assertLessEqual(pos.qty * pos.entry, cap * 1.01)

    def test_entry_fills_worse_than_quoted_price(self):
        self.engine.tick()
        # Paper slippage must work against a buyer, never for them.
        self.assertGreater(self.state.positions["BTC/USDT"].entry, self.price)

    def test_stop_sits_below_entry_after_slippage(self):
        self.engine.tick()
        pos = self.state.positions["BTC/USDT"]
        self.assertLess(pos.stop, pos.entry)
        self.assertGreater(pos.risk_per_unit, 0)

    def test_no_second_position_on_the_same_symbol(self):
        self.engine.tick()
        self.engine.tick()
        self.assertEqual(len(self.state.positions), 1)
        self.assertEqual(self.state.day_trades, 1)

    def test_paused_engine_still_manages_but_does_not_enter(self):
        self.state.paused = True
        self.engine.tick()
        self.assertEqual(self.state.positions, {})

    def test_daily_loss_cap_halts_new_entries(self):
        self.state.day_pnl = -self.state.paper_balance
        events = self.engine.tick()
        self.assertEqual(self.state.positions, {})
        self.assertTrue(any("توقّف الدخول" in e for e in events))

    def test_state_survives_a_restart(self):
        self.engine.tick()
        reloaded = State(self.path)
        self.assertIn("BTC/USDT", reloaded.positions)
        self.assertEqual(reloaded.positions["BTC/USDT"].entry,
                         self.state.positions["BTC/USDT"].entry)


class TestExitFlow(EngineTestCase):
    def test_stop_hit_closes_and_books_a_loss(self):
        self.engine.tick()
        pos = self.state.positions["BTC/USDT"]
        self.exchange.price = pos.stop * 0.999
        events = self.engine.manage_open()
        self.assertEqual(self.state.positions, {})
        self.assertEqual(len(self.state.history), 1)
        self.assertLess(self.state.history[0]["pnl"], 0)
        self.assertTrue(any("إغلاق" in e for e in events))

    def test_a_loss_puts_the_symbol_on_cooldown(self):
        self.engine.tick()
        self.exchange.price = self.state.positions["BTC/USDT"].stop * 0.999
        self.engine.manage_open()
        self.assertGreater(self.state.cooldowns.get("BTC/USDT", 0), time.time())

    def test_cooldown_blocks_immediate_re_entry(self):
        self.engine.tick()
        self.exchange.price = self.state.positions["BTC/USDT"].stop * 0.999
        self.engine.manage_open()
        self.exchange.price = self.price
        self.engine.tick()
        self.assertEqual(self.state.positions, {})

    def test_tp1_banks_part_and_keeps_the_rest(self):
        self.engine.tick()
        pos = self.state.positions["BTC/USDT"]
        opened_qty = pos.qty
        self.exchange.price = pos.tp1 * 1.001
        self.engine.manage_open()
        self.assertIn("BTC/USDT", self.state.positions)
        self.assertTrue(pos.tp1_done)
        self.assertLess(pos.qty, opened_qty)
        self.assertEqual(pos.stop, pos.entry)          # moved to breakeven
        self.assertGreater(self.state.history[0]["pnl"], 0)

    def test_tp2_closes_the_position_in_profit(self):
        self.engine.tick()
        pos = self.state.positions["BTC/USDT"]
        self.exchange.price = pos.tp2 * 1.001
        self.engine.manage_open()
        self.assertEqual(self.state.positions, {})
        self.assertGreater(self.state.day_pnl, 0)

    def test_profit_is_added_to_the_paper_balance(self):
        start = self.state.paper_balance
        self.engine.tick()
        pos = self.state.positions["BTC/USDT"]
        self.exchange.price = pos.tp2 * 1.001
        self.engine.manage_open()
        self.assertGreater(self.state.paper_balance, start)

    def test_close_all_flattens_everything(self):
        self.engine.tick()
        self.engine.close_all()
        self.assertEqual(self.state.positions, {})
        self.assertEqual(len(self.state.history), 1)


class TestSafety(EngineTestCase):
    def test_engine_never_places_a_live_order_in_paper_mode(self):
        # FakeExchange.create_market_order raises; a clean tick proves the
        # paper broker handled the fill instead.
        self.engine.tick()
        self.exchange.price = self.state.positions["BTC/USDT"].tp2 * 1.001
        self.engine.manage_open()

    def test_live_mode_requires_both_switches(self):
        with mock.patch.object(config, "LIVE_TRADING", True), \
             mock.patch.object(config, "LIVE_CONFIRM", "nope"):
            self.assertEqual(self.engine.mode, "paper")
        with mock.patch.object(config, "LIVE_TRADING", True), \
             mock.patch.object(config, "LIVE_CONFIRM", config.LIVE_CONFIRM_PHRASE):
            self.assertEqual(self.engine.mode, "live")

    def test_tick_survives_an_exchange_outage(self):
        def boom(*a, **k):
            raise ConnectionError("exchange down")
        self.exchange.fetch_candles = boom
        self.exchange.last_price = boom
        self.assertEqual(self.engine.tick(), [])   # logged, not raised

    def test_bad_symbol_does_not_stop_the_scan(self):
        self.state.watchlist = ["BAD/USDT", "BTC/USDT"]
        original = self.exchange.fetch_candles

        def selective(symbol, timeframe, limit):
            if symbol == "BAD/USDT":
                raise ValueError("no such market")
            return original(symbol, timeframe, limit)
        self.exchange.fetch_candles = selective
        self.engine.tick()
        self.assertIn("BTC/USDT", self.state.positions)


class TestDailyRollover(EngineTestCase):
    def test_new_day_resets_counters(self):
        self.state.paused = True          # isolate the rollover from entries
        self.state.day = "2000-01-01"
        self.state.day_pnl = -50.0
        self.state.day_trades = 9
        events = self.engine.tick()
        self.assertEqual(self.state.day_pnl, 0.0)
        self.assertEqual(self.state.day_trades, 0)
        self.assertTrue(any("يوم تداول جديد" in e for e in events))

    def test_same_day_keeps_counters(self):
        self.state.paused = True
        self.state.day_pnl = -50.0
        self.state.day_trades = 9
        self.engine.tick()
        self.assertEqual(self.state.day_pnl, -50.0)
        self.assertEqual(self.state.day_trades, 9)


if __name__ == "__main__":
    unittest.main()
