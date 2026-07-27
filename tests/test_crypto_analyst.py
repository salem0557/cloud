import unittest
from unittest import mock

from cryptobot import analyst, config
from cryptobot.indicators import Candle

GOOD_TICKER = {"bid": 100.0, "ask": 100.02, "quoteVolume": 5e8}


def make(closes, vols=None, spread=0.0008) -> list[Candle]:
    return [Candle(i * 60000, c, c * (1 + spread), c * (1 - spread), c,
                   (vols[i] if vols else 100.0))
            for i, c in enumerate(closes)]


def uptrend_htf(n: int = 260) -> list[Candle]:
    return make([100 * (1.004 ** i) for i in range(n)])


def downtrend_htf(n: int = 260) -> list[Candle]:
    return make([100 * (0.996 ** i) for i in range(n)])


def flat_htf(n: int = 260) -> list[Candle]:
    return make([100.0 + (i % 3) * 0.01 for i in range(n)])


def pullback_ltf(vol_surge: float = 3.0) -> list[Candle]:
    """The setup the analyst is built to find: an established uptrend, a
    six-bar pullback, then a three-bar reclaim on rising volume."""
    closes = []
    price = 200.0
    for i in range(185):
        price *= 1.0015
        closes.append(price * (1.003 if i % 4 in (1, 2) else 0.997))
    peak = closes[-1]
    for k in range(1, 7):
        closes.append(peak * (1 - 0.003 * k))
    trough = closes[-1]
    for k in range(1, 4):
        closes.append(trough * (1 + 0.004 * k))
    vols = [100.0] * len(closes)
    vols[-1] = 100.0 * vol_surge
    return make(closes, vols)


class TestApprovedSetup(unittest.TestCase):
    def setUp(self):
        self.v = analyst.analyze("BTC/USDT", pullback_ltf(), uptrend_htf(), GOOD_TICKER)

    def test_setup_is_approved(self):
        self.assertTrue(self.v.ok, self.v.blockers)
        self.assertEqual(self.v.side, "long")
        self.assertEqual(self.v.blockers, [])

    def test_score_clears_the_threshold(self):
        self.assertGreaterEqual(self.v.score, config.MIN_SCORE)

    def test_trade_plan_is_ordered_correctly(self):
        self.assertLess(self.v.stop, self.v.entry)
        self.assertLess(self.v.entry, self.v.tp1)
        self.assertLess(self.v.tp1, self.v.tp2)

    def test_targets_are_r_multiples_of_the_stop_distance(self):
        r = self.v.entry - self.v.stop
        self.assertAlmostEqual(self.v.tp1, self.v.entry + config.TP1_R * r, places=6)
        self.assertAlmostEqual(self.v.tp2, self.v.entry + config.TP2_R * r, places=6)

    def test_reward_ratio_is_net_of_fees(self):
        gross = (self.v.tp2 - self.v.entry) / (self.v.entry - self.v.stop)
        self.assertLess(self.v.rr, gross)
        self.assertGreaterEqual(self.v.rr, config.MIN_RR)

    def test_formatted_output_mentions_the_symbol(self):
        text = analyst.format_verdict(self.v)
        self.assertIn("BTC/USDT", text)
        self.assertIn("وقف الخسارة", text)


class TestHardRefusals(unittest.TestCase):
    def test_insufficient_history_refused(self):
        v = analyst.analyze("X/USDT", make([100.0] * 10), uptrend_htf(), GOOD_TICKER)
        self.assertFalse(v.ok)
        self.assertIn("بيانات غير كافية", v.blockers[0])

    def test_wide_spread_refused(self):
        ticker = dict(GOOD_TICKER, bid=100.0, ask=101.0)   # 1% spread
        v = analyst.analyze("X/USDT", pullback_ltf(), uptrend_htf(), ticker)
        self.assertFalse(v.ok)
        self.assertTrue(any("الفارق السعري" in b for b in v.blockers))

    def test_thin_market_refused(self):
        ticker = dict(GOOD_TICKER, quoteVolume=1000.0)
        v = analyst.analyze("X/USDT", pullback_ltf(), uptrend_htf(), ticker)
        self.assertFalse(v.ok)
        self.assertTrue(any("سيولة" in b for b in v.blockers))

    def test_dead_market_refused(self):
        # No range at all: ATR is ~0, below the tradeable floor.
        v = analyst.analyze("X/USDT", make([100.0] * 200, spread=0.0),
                            uptrend_htf(), GOOD_TICKER)
        self.assertFalse(v.ok)
        self.assertTrue(any("ميت" in b for b in v.blockers))

    def test_vertical_pump_refused_as_chasing(self):
        closes = [200 * (1.0015 ** i) for i in range(190)]
        closes += [closes[-1] * (1 + 0.02 * k) for k in range(1, 6)]  # blow-off top
        v = analyst.analyze("X/USDT", make(closes), uptrend_htf(), GOOD_TICKER)
        self.assertFalse(v.ok)
        self.assertTrue(any("ممتد" in b for b in v.blockers))

    def test_downtrend_refused_while_shorts_are_disabled(self):
        with mock.patch.object(config, "ALLOW_SHORT", False):
            v = analyst.analyze("X/USDT", pullback_ltf(), downtrend_htf(), GOOD_TICKER)
        self.assertFalse(v.ok)
        self.assertTrue(any("البيع المكشوف" in b for b in v.blockers))

    def test_choppy_higher_timeframe_refused(self):
        v = analyst.analyze("X/USDT", pullback_ltf(), flat_htf(), GOOD_TICKER)
        self.assertFalse(v.ok)
        self.assertTrue(any("اتجاه واضح" in b for b in v.blockers))

    def test_missing_ticker_does_not_crash(self):
        v = analyst.analyze("X/USDT", pullback_ltf(), uptrend_htf(), None)
        self.assertIsInstance(v.ok, bool)


class TestScoring(unittest.TestCase):
    def test_weights_total_one_hundred(self):
        v = analyst.analyze("X/USDT", pullback_ltf(), uptrend_htf(), GOOD_TICKER)
        self.assertAlmostEqual(sum(c.weight for c in v.checks), 100.0)

    def test_score_equals_the_sum_of_passed_weights(self):
        v = analyst.analyze("X/USDT", pullback_ltf(), uptrend_htf(), GOOD_TICKER)
        self.assertAlmostEqual(v.score, sum(c.weight for c in v.passed_checks))

    def test_volume_check_fails_without_a_surge(self):
        v = analyst.analyze("X/USDT", pullback_ltf(vol_surge=1.0), uptrend_htf(),
                            GOOD_TICKER)
        volume_check = next(c for c in v.checks if c.name == "حجم مؤكِّد")
        self.assertFalse(volume_check.passed)

    def test_raising_the_threshold_rejects_a_borderline_setup(self):
        with mock.patch.object(config, "MIN_SCORE", 99.0):
            v = analyst.analyze("X/USDT", pullback_ltf(), uptrend_htf(), GOOD_TICKER)
        self.assertFalse(v.ok)
        self.assertTrue(any("القوة" in b for b in v.blockers))

    def test_demanding_reward_ratio_rejects_the_setup(self):
        with mock.patch.object(config, "MIN_RR", 99.0):
            v = analyst.analyze("X/USDT", pullback_ltf(), uptrend_htf(), GOOD_TICKER)
        self.assertFalse(v.ok)
        self.assertTrue(any("العائد/المخاطرة" in b for b in v.blockers))


class TestShortSide(unittest.TestCase):
    def test_downtrend_produces_a_short_plan_when_enabled(self):
        closes = []
        price = 200.0
        for i in range(185):
            price *= 0.9985
            closes.append(price * (0.997 if i % 4 in (1, 2) else 1.003))
        trough = closes[-1]
        for k in range(1, 7):
            closes.append(trough * (1 + 0.003 * k))
        peak = closes[-1]
        for k in range(1, 4):
            closes.append(peak * (1 - 0.004 * k))
        vols = [100.0] * len(closes)
        vols[-1] = 300.0
        with mock.patch.object(config, "ALLOW_SHORT", True), \
             mock.patch.object(config, "MARKET_TYPE", "swap"):
            v = analyst.analyze("X/USDT", make(closes, vols), downtrend_htf(),
                                GOOD_TICKER)
        self.assertEqual(v.side, "short")
        self.assertGreater(v.stop, v.entry)
        self.assertLess(v.tp1, v.entry)
        self.assertLess(v.tp2, v.tp1)


if __name__ == "__main__":
    unittest.main()
