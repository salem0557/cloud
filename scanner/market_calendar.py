"""NYSE trading calendar helpers: holiday dates and the regular trading
session window — used to pause scanning entirely outside market hours
(saving compute/requests) since no US stock can move then.
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


def is_regular_session(now: dt.datetime | None = None) -> bool:
    """Inside the official trading session, 9:30-16:00 ET, on a weekday."""
    now = (now or dt.datetime.now(NY)).astimezone(NY)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now <= close_t


def scan_paused(now: dt.datetime | None = None) -> bool:
    """True whenever the bot should not be scanning: a full weekend, a
    market holiday, or (when MARKET_HOURS_ONLY_ENABLED) any time outside
    the regular 9:30-16:00 ET session.
    """
    if not config.WEEKEND_HOLIDAY_PAUSE_ENABLED:
        return False
    now = (now or dt.datetime.now(NY)).astimezone(NY)
    if now.weekday() >= 5:
        return True
    if is_market_holiday(now.date()):
        return True
    if config.MARKET_HOURS_ONLY_ENABLED and not is_regular_session(now):
        return True
    return False
