"""US equity-options session calendar: holidays, early closes, DST-aware times.

Equity options trade 09:30-16:00 New York time, and 09:30-13:00 on NYSE early
close days. zoneinfo handles daylight saving, so nothing here hard-codes an
offset; Riyadh (UTC+3, no DST) is only used for display.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from dateutil.easter import easter

NY = ZoneInfo("America/New_York")
RIYADH = ZoneInfo("Asia/Riyadh")
UTC = dt.timezone.utc

OPEN = dt.time(9, 30)
CLOSE = dt.time(16, 0)
EARLY_CLOSE = dt.time(13, 0)

# Owner overrides from the settings file (ISO dates), for exchange notices the
# rules below cannot know about (e.g. a national day of mourning).
_extra_holidays: set[dt.date] = set()
_extra_early_closes: set[dt.date] = set()


def set_overrides(holidays=(), early_closes=()) -> None:
    _extra_holidays.clear()
    _extra_holidays.update(dt.date.fromisoformat(d) for d in holidays)
    _extra_early_closes.clear()
    _extra_early_closes.update(dt.date.fromisoformat(d) for d in early_closes)


def _nth_weekday(year, month, weekday, n):
    d = dt.date(year, month, 1)
    return d + dt.timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    end = (dt.date(year + 1, 1, 1) if month == 12 else dt.date(year, month + 1, 1)) \
        - dt.timedelta(days=1)
    return end - dt.timedelta(days=(end.weekday() - weekday) % 7)


def _observed(d):
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


def year_holidays(year: int) -> set[dt.date]:
    out = {
        _nth_weekday(year, 1, 0, 3),          # MLK Day
        _nth_weekday(year, 2, 0, 3),          # Washington's Birthday
        easter(year) - dt.timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),            # Memorial Day
        _observed(dt.date(year, 6, 19)),      # Juneteenth
        _observed(dt.date(year, 7, 4)),       # Independence Day
        _nth_weekday(year, 9, 0, 1),          # Labor Day
        _nth_weekday(year, 11, 3, 4),         # Thanksgiving
        _observed(dt.date(year, 12, 25)),     # Christmas
    }
    # NYSE does not move New Year's Day back into the previous year.
    new_year = dt.date(year, 1, 1)
    if new_year.weekday() != 5:
        out.add(_observed(new_year))
    return out


def is_trading_day(d: dt.date) -> bool:
    if d.weekday() >= 5 or d in _extra_holidays:
        return False
    return d not in year_holidays(d.year)


def is_early_close(d: dt.date) -> bool:
    if not is_trading_day(d):
        return False
    if d in _extra_early_closes:
        return True
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    return d in {dt.date(d.year, 7, 3), dt.date(d.year, 12, 24),
                 thanksgiving + dt.timedelta(days=1)}


def session_bounds(d: dt.date) -> tuple[dt.datetime, dt.datetime] | None:
    """(open, close) as aware UTC datetimes, or None on a non-trading day."""
    if not is_trading_day(d):
        return None
    close = EARLY_CLOSE if is_early_close(d) else CLOSE
    return (dt.datetime.combine(d, OPEN, NY).astimezone(UTC),
            dt.datetime.combine(d, close, NY).astimezone(UTC))


def is_open(now: dt.datetime) -> bool:
    bounds = session_bounds(now.astimezone(NY).date())
    return bool(bounds and bounds[0] <= now < bounds[1])


def ny_date(now: dt.datetime) -> dt.date:
    return now.astimezone(NY).date()


def next_open(now: dt.datetime) -> dt.datetime:
    d = ny_date(now)
    for _ in range(15):
        bounds = session_bounds(d)
        if bounds and now < bounds[0]:
            return bounds[0]
        d += dt.timedelta(days=1)
    raise RuntimeError("no session within 15 days")


def market_time(t: dt.datetime) -> dt.datetime:
    """The latest moment at or before t when the market was open."""
    if is_open(t):
        return t
    d = ny_date(t)
    for _ in range(15):
        bounds = session_bounds(d)
        if bounds and bounds[1] <= t:
            return bounds[1]
        d -= dt.timedelta(days=1)
    return t


def sessions_between(start: dt.datetime, end: dt.datetime) -> list[dt.date]:
    """Trading sessions that overlap [start, end)."""
    out, d = [], ny_date(start)
    while d <= ny_date(end):
        bounds = session_bounds(d)
        if bounds and bounds[0] < end and start < bounds[1]:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def fmt_both(t: dt.datetime | None) -> str:
    """'10:31 نيويورك (17:31 الرياض) 2026-10-12' — both clocks, never ambiguous."""
    if t is None:
        return "—"
    ny, ry = t.astimezone(NY), t.astimezone(RIYADH)
    return f"{ny:%Y-%m-%d %H:%M} نيويورك ({ry:%H:%M} الرياض)"
