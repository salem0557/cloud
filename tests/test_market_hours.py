import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from options_scanner.market_hours import is_market_open

ET = ZoneInfo("America/New_York")


class TestMarketHours(unittest.TestCase):
    def test_open_during_regular_session(self):
        # Wednesday, 2024-01-10, 12:00 ET
        self.assertTrue(is_market_open(datetime(2024, 1, 10, 12, 0, tzinfo=ET)))

    def test_closed_before_open(self):
        self.assertFalse(is_market_open(datetime(2024, 1, 10, 9, 0, tzinfo=ET)))

    def test_closed_after_close(self):
        self.assertFalse(is_market_open(datetime(2024, 1, 10, 16, 30, tzinfo=ET)))

    def test_open_at_exact_open_time(self):
        self.assertTrue(is_market_open(datetime(2024, 1, 10, 9, 30, tzinfo=ET)))

    def test_closed_at_exact_close_time(self):
        self.assertFalse(is_market_open(datetime(2024, 1, 10, 16, 0, tzinfo=ET)))

    def test_closed_on_saturday(self):
        # 2024-01-13 is a Saturday
        self.assertFalse(is_market_open(datetime(2024, 1, 13, 12, 0, tzinfo=ET)))

    def test_closed_on_sunday(self):
        # 2024-01-14 is a Sunday
        self.assertFalse(is_market_open(datetime(2024, 1, 14, 12, 0, tzinfo=ET)))

    def test_converts_other_timezones(self):
        # 12:00 UTC = 07:00 ET in January (EST, UTC-5) - before open
        self.assertFalse(is_market_open(datetime(2024, 1, 10, 12, 0, tzinfo=ZoneInfo("UTC"))))
        # 15:00 UTC = 10:00 ET in January - open
        self.assertTrue(is_market_open(datetime(2024, 1, 10, 15, 0, tzinfo=ZoneInfo("UTC"))))


if __name__ == "__main__":
    unittest.main()
