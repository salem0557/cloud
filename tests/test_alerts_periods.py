import datetime as dt
import unittest

from alerts.periods import (NY, PERIODS, UTC, candle_interval, market_tz,
                            period_key, period_start)


def moment(y, m, d, h=0, mi=0, tz=UTC) -> dt.datetime:
    return dt.datetime(y, m, d, h, mi, tzinfo=tz)


class TestPeriodStart(unittest.TestCase):
    def test_hour_truncates_minutes(self):
        self.assertEqual(period_start(moment(2026, 7, 27, 14, 37), "hour"),
                         moment(2026, 7, 27, 14, 0))

    def test_day_truncates_to_midnight(self):
        self.assertEqual(period_start(moment(2026, 7, 27, 14, 37), "day"),
                         moment(2026, 7, 27))

    def test_week_starts_on_monday(self):
        # 2026-07-27 is a Monday; the whole week maps back to it
        for day in range(27, 32):
            self.assertEqual(period_start(moment(2026, 7, day, 9), "week"),
                             moment(2026, 7, 27))

    def test_week_of_a_sunday_looks_back_six_days(self):
        self.assertEqual(period_start(moment(2026, 8, 2, 23), "week"),
                         moment(2026, 7, 27))

    def test_month_starts_on_the_first(self):
        self.assertEqual(period_start(moment(2026, 7, 27, 14), "month"),
                         moment(2026, 7, 1))

    def test_week_can_cross_a_month_boundary(self):
        self.assertEqual(period_start(moment(2026, 9, 2), "week"),
                         moment(2026, 8, 31))

    def test_unknown_period_rejected(self):
        with self.assertRaises(ValueError):
            period_start(moment(2026, 7, 27), "decade")

    def test_start_is_idempotent(self):
        for period in PERIODS:
            once = period_start(moment(2026, 7, 27, 14, 37), period)
            self.assertEqual(period_start(once, period), once)


class TestPeriodKey(unittest.TestCase):
    def test_key_is_stable_within_a_period(self):
        a = period_key(moment(2026, 7, 27, 14, 0), "hour")
        b = period_key(moment(2026, 7, 27, 14, 59), "hour")
        self.assertEqual(a, b)

    def test_key_changes_on_rollover(self):
        a = period_key(moment(2026, 7, 27, 14, 59), "hour")
        b = period_key(moment(2026, 7, 27, 15, 0), "hour")
        self.assertNotEqual(a, b)

    def test_day_key_changes_at_midnight(self):
        self.assertNotEqual(period_key(moment(2026, 7, 27, 23, 59), "day"),
                            period_key(moment(2026, 7, 28, 0, 0), "day"))

    def test_month_key_changes_on_the_first(self):
        self.assertNotEqual(period_key(moment(2026, 7, 31, 23), "month"),
                            period_key(moment(2026, 8, 1, 0), "month"))

    def test_every_period_produces_a_distinct_key(self):
        now = moment(2026, 7, 27, 14, 30)
        keys = {period_key(now, p) for p in PERIODS}
        self.assertEqual(len(keys), len(PERIODS))


class TestTimezone(unittest.TestCase):
    def test_crypto_uses_utc_and_stocks_use_new_york(self):
        self.assertEqual(market_tz("crypto"), UTC)
        self.assertEqual(market_tz("stock"), NY)

    def test_stock_day_does_not_roll_over_during_post_market(self):
        # 19:00 ET is 23:00 UTC — a UTC day boundary here would cut the
        # session in half. In NY it is still the same trading day.
        evening = moment(2026, 7, 27, 19, 0, tz=NY)
        late = moment(2026, 7, 27, 23, 30, tz=NY)
        self.assertEqual(period_key(evening, "day"), period_key(late, "day"))


class TestIntervals(unittest.TestCase):
    def test_every_period_has_an_interval(self):
        for period in PERIODS:
            self.assertTrue(candle_interval(period))

    def test_shorter_periods_use_finer_candles(self):
        self.assertEqual(candle_interval("hour"), "1m")
        self.assertNotEqual(candle_interval("month"), "1m")


if __name__ == "__main__":
    unittest.main()
