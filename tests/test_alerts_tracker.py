import unittest

from alerts.tracker import Track, check, reset

MIN_MOVE = 0.05      # percent
COOLDOWN = 300.0     # seconds


def fresh(low=100.0, high=110.0, now=1000.0) -> Track:
    return reset(Track(symbol="BTC/USDT", period="day"), "2026-07-27",
                 low, high, now)


class TestBreakDetection(unittest.TestCase):
    def test_new_low_is_reported(self):
        track = fresh()
        brk = check(track, 95.0, MIN_MOVE, COOLDOWN, now=2000.0)
        self.assertIsNotNone(brk)
        self.assertEqual(brk.kind, "low")
        self.assertEqual(brk.price, 95.0)
        self.assertEqual(brk.previous, 100.0)
        self.assertAlmostEqual(brk.pct, -5.0)

    def test_new_high_is_reported(self):
        brk = check(fresh(), 120.0, MIN_MOVE, COOLDOWN, now=2000.0)
        self.assertEqual(brk.kind, "high")
        self.assertAlmostEqual(brk.pct, 120 / 110 * 100 - 100)

    def test_price_inside_the_range_is_silent(self):
        self.assertIsNone(check(fresh(), 105.0, MIN_MOVE, COOLDOWN, now=2000.0))

    def test_touching_the_extreme_is_not_a_break(self):
        self.assertIsNone(check(fresh(), 100.0, MIN_MOVE, COOLDOWN, now=2000.0))
        self.assertIsNone(check(fresh(), 110.0, MIN_MOVE, COOLDOWN, now=2000.0))

    def test_extreme_is_stored_even_when_the_alert_is_suppressed(self):
        # The bot must never later announce a low that is no longer the low.
        track = fresh()
        check(track, 95.0, MIN_MOVE, COOLDOWN, now=2000.0)
        check(track, 94.99, MIN_MOVE, COOLDOWN, now=2100.0)   # too small to send
        self.assertEqual(track.low, 94.99)

    def test_unready_track_reports_nothing(self):
        self.assertIsNone(check(Track("BTC/USDT", "day"), 50.0, MIN_MOVE, COOLDOWN))

    def test_zero_price_ignored(self):
        self.assertIsNone(check(fresh(), 0.0, MIN_MOVE, COOLDOWN, now=2000.0))


class TestNoiseControl(unittest.TestCase):
    def test_tiny_new_low_does_not_alert_twice(self):
        track = fresh()
        self.assertIsNotNone(check(track, 95.0, MIN_MOVE, COOLDOWN, now=2000.0))
        # 0.01% lower: a real new low, but not worth a second message
        self.assertIsNone(check(track, 94.99, MIN_MOVE, COOLDOWN, now=9000.0))

    def test_meaningful_new_low_alerts_again(self):
        track = fresh()
        check(track, 95.0, MIN_MOVE, COOLDOWN, now=2000.0)
        brk = check(track, 93.0, MIN_MOVE, COOLDOWN, now=9000.0)
        self.assertIsNotNone(brk)
        self.assertEqual(brk.previous, 95.0)

    def test_slow_drift_eventually_alerts(self):
        # Each step is below the threshold on its own; measuring against the
        # last *announced* price lets them accumulate instead of vanishing.
        track = fresh()
        check(track, 95.0, MIN_MOVE, COOLDOWN, now=2000.0)
        alerts = 0
        price = 95.0
        for i in range(10):
            price -= 0.02                     # ~0.02% per step
            if check(track, price, MIN_MOVE, COOLDOWN, now=3000.0 + i * 600):
                alerts += 1
        self.assertGreaterEqual(alerts, 1)

    def test_cooldown_blocks_a_rapid_second_alert(self):
        track = fresh()
        check(track, 95.0, MIN_MOVE, COOLDOWN, now=2000.0)
        self.assertIsNone(check(track, 90.0, MIN_MOVE, COOLDOWN, now=2100.0))

    def test_cooldown_expires(self):
        track = fresh()
        check(track, 95.0, MIN_MOVE, COOLDOWN, now=2000.0)
        self.assertIsNotNone(check(track, 90.0, MIN_MOVE, COOLDOWN, now=2400.0))

    def test_lows_and_highs_have_independent_cooldowns(self):
        track = fresh()
        check(track, 95.0, MIN_MOVE, COOLDOWN, now=2000.0)
        # A high right after a low is a different event and must not be muted.
        self.assertIsNotNone(check(track, 120.0, MIN_MOVE, COOLDOWN, now=2050.0))


class TestPeriodReset(unittest.TestCase):
    def test_reset_clears_the_alert_memory(self):
        track = fresh()
        check(track, 95.0, MIN_MOVE, COOLDOWN, now=2000.0)
        reset(track, "2026-07-28", 200.0, 210.0, now=3000.0)
        self.assertEqual(track.alert_price, {})
        self.assertEqual(track.low, 200.0)
        self.assertEqual(track.high, 210.0)

    def test_first_break_of_a_new_period_alerts_immediately(self):
        track = fresh()
        check(track, 95.0, MIN_MOVE, COOLDOWN, now=2000.0)
        reset(track, "2026-07-28", 200.0, 210.0, now=2010.0)
        # No cooldown carried over — a new day starts clean.
        self.assertIsNotNone(check(track, 199.0, MIN_MOVE, COOLDOWN, now=2020.0))

    def test_round_trip_serialization(self):
        track = fresh()
        check(track, 95.0, MIN_MOVE, COOLDOWN, now=2000.0)
        restored = Track.from_dict(track.to_dict())
        self.assertEqual(restored.to_dict(), track.to_dict())
        self.assertTrue(restored.ready)


if __name__ == "__main__":
    unittest.main()
