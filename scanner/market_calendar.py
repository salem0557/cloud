"""NYSE trading calendar helpers: holiday dates, weekend closure window, and
the overnight-session hours — used to pause stock scanning when nothing can
move (saving compute/requests) and to slow the scan pace overnight.

Crypto never closes, so callers only gate the STOCK side of a scan on these.
"""
import datetime as dt
from zoneinfo import ZoneInfo

from dateutil.easter import easter

from . import config

NY = ZoneInfo("America/New_York")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """n-th occurrence of `weekday` (Mon=0..Sun=6) in the given month."""
    d = dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    end = (dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)) \
        - dt.timedelta(days=1)
    offset = (end.weekday() - weekday) % 7
    return end - dt.timedelta(days=offset)


def _observed(d: dt.date) -> dt.date:
    """NYSE shifts a holiday landing on Saturday to Friday, Sunday to Monday."""
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


def year_holidays(year: int) -> set[dt.date]:
    out = {
        _observed(dt.date(year, 1, 1)),      # New Year's Day
        _nth_weekday(year, 1, 0, 3),         # MLK Day
        _nth_weekday(year, 2, 0, 3),         # Washington's Birthday
        easter(year) - dt.timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),           # Memorial Day
        _observed(dt.date(year, 7, 4)),      # Independence Day
        _nth_weekday(year, 9, 0, 1),         # Labor Day
        _nth_weekday(year, 11, 3, 4),        # Thanksgiving
        _observed(dt.date(year, 12, 25)),    # Christmas
    }
    if year >= 2022:
        out.add(_observed(dt.date(year, 6, 19)))  # Juneteenth
    return out


def is_market_holiday(d: dt.date) -> bool:
    return d in year_holidays(d.year)


def is_night_hours(now: dt.datetime | None = None) -> bool:
    """Inside the overnight session window (default 20:00-04:00 ET)."""
    now = (now or dt.datetime.now(NY)).astimezone(NY)
    h = now.hour
    if config.NIGHT_START_HOUR <= config.NIGHT_END_HOUR:
        return config.NIGHT_START_HOUR <= h < config.NIGHT_END_HOUR
    return h >= config.NIGHT_START_HOUR or h < config.NIGHT_END_HOUR


def stocks_scan_paused(now: dt.datetime | None = None) -> bool:
    """True when no US stock can possibly trade: weekend gap (Friday 20:00 ET
    to Sunday 20:00 ET) or a full market holiday date. Crypto is unaffected.
    """
    if not config.WEEKEND_HOLIDAY_PAUSE_ENABLED:
        return False
    now = (now or dt.datetime.now(NY)).astimezone(NY)
    if is_market_holiday(now.date()):
        return True
    wd = now.weekday()  # Monday=0 .. Sunday=6
    if wd == 4 and now.hour >= 20:    # Friday: after-hours session just ended
        return True
    if wd == 5:                      # Saturday: fully closed
        return True
    if wd == 6 and now.hour < 20:     # Sunday: before the overnight session opens
        return True
    return False
