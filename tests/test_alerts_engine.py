import os
import tempfile
import unittest

from alerts import config
from alerts.engine import Engine, format_break
from alerts.sources import Asset
from alerts.store import Store

BTC = Asset("BTC/USDT", "BTC", "crypto")
AAPL = Asset("AAPL", "AAPL", "stock")


class FakeSource:
    """Stands in for the ccxt/yfinance adapters; every price is settable."""

    def __init__(self):
        self.prices = {"BTC/USDT": 100.0, "AAPL": 200.0}
        self.ranges = {"BTC/USDT": (95.0, 105.0), "AAPL": (195.0, 205.0)}
        self.market_open = True
        self.price_calls = 0
        self.fail_price = set()

    def price(self, asset):
        self.price_calls += 1
        if asset.symbol in self.fail_price:
            raise ConnectionError("source down")
        return self.prices[asset.symbol]

    def extremes(self, asset, period):
        return self.ranges[asset.symbol]

    def stock_market_open(self, now=None):
        return self.market_open


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        os.unlink(self.path)
        self.store = Store(self.path)
        self.source = FakeSource()
        self.engine = Engine(self.store, self.source)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def watch(self, chat_id=1, asset=BTC, periods=("day",), directions=("low", "high")):
        self.store.add(chat_id, asset, periods=periods, directions=directions)

    def warm_up(self):
        """First poll only establishes the baseline; it never alerts."""
        return self.engine.poll()


class TestAlerting(EngineTestCase):
    def test_first_poll_is_silent(self):
        self.watch()
        self.assertEqual(self.warm_up(), [])

    def test_new_low_alerts_the_watcher(self):
        self.watch()
        self.warm_up()
        self.source.prices["BTC/USDT"] = 90.0
        alerts = self.engine.poll()
        self.assertEqual(len(alerts), 1)
        chat_id, brk, asset = alerts[0]
        self.assertEqual(chat_id, 1)
        self.assertEqual(brk.kind, "low")
        self.assertEqual(asset.display, "BTC")

    def test_new_high_alerts_the_watcher(self):
        self.watch()
        self.warm_up()
        self.source.prices["BTC/USDT"] = 120.0
        self.assertEqual(self.engine.poll()[0][1].kind, "high")

    def test_price_inside_the_range_is_silent(self):
        self.watch()
        self.warm_up()
        self.source.prices["BTC/USDT"] = 101.0
        self.assertEqual(self.engine.poll(), [])

    def test_baseline_includes_the_live_price(self):
        # Price already below the candle low at startup: that is the real low,
        # so the next tick must not report it as a fresh break.
        self.watch()
        self.source.prices["BTC/USDT"] = 80.0
        self.warm_up()
        self.assertEqual(self.engine.poll(), [])

    def test_direction_filter_respected(self):
        self.watch(directions=("high",))
        self.warm_up()
        self.source.prices["BTC/USDT"] = 90.0
        self.assertEqual(self.engine.poll(), [])

    def test_period_filter_respected(self):
        self.watch(periods=("hour",))
        self.warm_up()
        self.source.prices["BTC/USDT"] = 90.0
        alerts = self.engine.poll()
        self.assertTrue(all(brk.period == "hour" for _, brk, _ in alerts))

    def test_muted_watch_receives_nothing(self):
        self.watch()
        self.warm_up()
        self.store.get(1, "BTC/USDT").muted = True
        self.source.prices["BTC/USDT"] = 90.0
        self.assertEqual(self.engine.poll(), [])

    def test_one_break_alerts_every_subscriber(self):
        self.watch(chat_id=1)
        self.watch(chat_id=2)
        self.watch(chat_id=3)
        self.warm_up()
        self.source.prices["BTC/USDT"] = 90.0
        alerts = self.engine.poll()
        self.assertEqual({chat_id for chat_id, _, _ in alerts}, {1, 2, 3})

    def test_shared_symbol_is_fetched_once_per_cycle(self):
        self.watch(chat_id=1)
        self.watch(chat_id=2)
        self.source.price_calls = 0
        self.engine.poll()
        self.assertEqual(self.source.price_calls, 1)

    def test_multiple_periods_alert_separately(self):
        self.watch(periods=("hour", "day", "month"))
        self.warm_up()
        self.source.prices["BTC/USDT"] = 50.0
        alerts = self.engine.poll()
        self.assertEqual({brk.period for _, brk, _ in alerts},
                         {"hour", "day", "month"})


class TestStocks(EngineTestCase):
    def test_stock_skipped_when_the_market_is_closed(self):
        self.watch(asset=AAPL)
        self.source.market_open = False
        self.engine.poll()
        self.assertEqual(self.source.price_calls, 0)

    def test_stock_polled_when_the_market_is_open(self):
        self.watch(asset=AAPL)
        self.engine.poll()
        self.source.prices["AAPL"] = 150.0
        self.assertEqual(len(self.engine.poll()), 1)

    def test_crypto_polled_around_the_clock(self):
        self.watch(asset=BTC)
        self.source.market_open = False
        self.engine.poll()
        self.assertEqual(self.source.price_calls, 1)


class TestResilience(EngineTestCase):
    def test_source_failure_does_not_raise(self):
        self.watch()
        self.source.fail_price.add("BTC/USDT")
        self.assertEqual(self.engine.poll(), [])
        self.assertIn("BTC/USDT", self.engine.errors)

    def test_one_bad_symbol_does_not_block_the_others(self):
        self.watch(asset=BTC)
        self.watch(asset=AAPL)
        self.warm_up()
        self.source.fail_price.add("BTC/USDT")
        self.source.prices["AAPL"] = 150.0
        alerts = self.engine.poll()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0][2].display, "AAPL")

    def test_recovery_clears_the_error_counter(self):
        self.watch()
        self.source.fail_price.add("BTC/USDT")
        self.engine.poll()
        self.source.fail_price.clear()
        self.engine.poll()
        self.assertNotIn("BTC/USDT", self.engine.errors)

    def test_state_survives_a_restart(self):
        self.watch()
        self.warm_up()
        reloaded = Store(self.path)
        self.assertIn("BTC/USDT", reloaded.watches[1])
        engine = Engine(reloaded, self.source)
        self.source.prices["BTC/USDT"] = 90.0
        self.assertEqual(len(engine.poll()), 1)   # baseline was not lost

    def test_no_watches_means_no_work(self):
        self.assertEqual(self.engine.poll(), [])
        self.assertEqual(self.source.price_calls, 0)


class TestStoreHousekeeping(EngineTestCase):
    def test_removing_a_watch_drops_its_tracking_state(self):
        self.watch()
        self.warm_up()
        self.assertTrue(self.store.tracks)
        self.store.remove(1, "BTC/USDT")
        self.assertEqual(self.store.tracks, {})

    def test_tracking_state_kept_while_another_chat_watches(self):
        self.watch(chat_id=1)
        self.watch(chat_id=2)
        self.warm_up()
        self.store.remove(1, "BTC/USDT")
        self.assertTrue(self.store.tracks)

    def test_adding_the_same_symbol_twice_merges_periods(self):
        self.store.add(1, BTC, periods=("day",))
        self.store.add(1, BTC, periods=("hour",))
        self.assertEqual(self.store.get(1, "BTC/USDT").periods, {"day", "hour"})
        self.assertEqual(self.store.count(1), 1)

    def test_watch_with_no_periods_is_not_polled(self):
        self.watch()
        self.store.get(1, "BTC/USDT").periods.clear()
        self.engine.poll()
        self.assertEqual(self.source.price_calls, 0)


class TestMessage(EngineTestCase):
    def test_low_message_names_symbol_price_and_period(self):
        self.watch()
        self.warm_up()
        self.source.prices["BTC/USDT"] = 90.0
        _, brk, asset = self.engine.poll()[0]
        text = format_break(brk, asset)
        self.assertIn("BTC", text)
        self.assertIn("90", text)
        self.assertIn("أدنى سعر", text)

    def test_high_message_reads_as_a_high(self):
        self.watch()
        self.warm_up()
        self.source.prices["BTC/USDT"] = 130.0
        _, brk, asset = self.engine.poll()[0]
        self.assertIn("أعلى سعر", format_break(brk, asset))


if __name__ == "__main__":
    unittest.main()
