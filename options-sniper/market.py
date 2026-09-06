"""US market-hours guard.

The scheduler ticks continuously, so this decides when the market is actually
open. It follows US daylight saving through the America/New_York zone rather
than a fixed offset, so no seasonal edit is ever needed: the session is always
09:30-16:00 ET, whatever that maps to in Riyadh.
"""
import datetime

import config as C

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                                   # pragma: no cover
    _ET = None

OPEN = datetime.time(9, 30)
CLOSE = datetime.time(16, 0)


def now_et():
    if _ET is None:
        return datetime.datetime.utcnow() - datetime.timedelta(hours=5)
    return datetime.datetime.now(_ET)


def easter(year):
    """Gregorian Easter Sunday. Good Friday is the market holiday, two days
    before. Computed rather than tabulated so the calendar never expires."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f, g = (b + 8) // 25, 0
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime.date(year, month, day)


def _nth_weekday(year, month, weekday, n):
    """The nth given weekday of a month; n = -1 for the last one."""
    first = datetime.date(year, month, 1)
    if n > 0:
        shift = (weekday - first.weekday()) % 7
        return first + datetime.timedelta(days=shift + 7 * (n - 1))
    nxt = datetime.date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - datetime.timedelta(days=1)
    return last - datetime.timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d):
    """A fixed-date holiday on a weekend moves: Saturday back to Friday,
    Sunday forward to Monday."""
    if d.weekday() == 5:
        return d - datetime.timedelta(days=1)
    if d.weekday() == 6:
        return d + datetime.timedelta(days=1)
    return d


def holidays(year):
    """The NYSE full-day closures for a year, by rule.

    There was no holiday calendar at all before this — is_open() checked the
    weekday and the clock and nothing else. On a closed Monday the scanner
    would have run every 15 minutes against Friday's stale candles, and stale
    candles still contain a break: it could have alerted, and opened a paper
    position on a session that never happened.
    """
    e = easter(year)
    return {
        _observed(datetime.date(year, 1, 1)),               # New Year's Day
        _nth_weekday(year, 1, 0, 3),                        # MLK Day
        _nth_weekday(year, 2, 0, 3),                        # Presidents Day
        e - datetime.timedelta(days=2),                     # Good Friday
        _nth_weekday(year, 5, 0, -1),                       # Memorial Day
        _observed(datetime.date(year, 6, 19)),              # Juneteenth
        _observed(datetime.date(year, 7, 4)),               # Independence Day
        _nth_weekday(year, 9, 0, 1),                        # Labor Day
        _nth_weekday(year, 11, 3, 4),                       # Thanksgiving
        _observed(datetime.date(year, 12, 25)),             # Christmas
    }


def early_close(d):
    """13:00 ET on the half-days, else None.

    This matters more than it looks: a 0DTE contract on a half-day expires at
    13:00, so a hard exit written for 15:30 would be an hour and a half after
    the contract stopped existing.
    """
    half = {
        _nth_weekday(d.year, 11, 3, 4) + datetime.timedelta(days=1),  # Black Friday
        datetime.date(d.year, 12, 24),
        datetime.date(d.year, 7, 3),
    }
    if d in half and d.weekday() < 5 and d not in holidays(d.year):
        return datetime.time(13, 0)
    return None


def closes_at(now=None):
    """The bell for this session — 16:00, or 13:00 on a half-day."""
    now = now or now_et()
    return early_close(now.date()) or CLOSE


def is_holiday(now=None):
    now = now or now_et()
    return now.date() in holidays(now.year)


def is_open(now=None):
    now = now or now_et()
    if now.weekday() >= 5:                          # Sat/Sun
        return False
    if is_holiday(now):
        return False
    return OPEN <= now.time() <= closes_at(now)


def minutes_to_close(now=None):
    """Minutes left in the regular session; 0 once it is over."""
    now = now or now_et()
    if not is_open(now):
        return 0
    bell = closes_at(now)
    close = now.replace(hour=bell.hour, minute=bell.minute, second=0,
                        microsecond=0)
    return max(0, int((close - now).total_seconds() // 60))


def past_hard_exit(now=None):
    """True once the 0DTE hard-exit time has passed in the current session."""
    now = now or now_et()
    hh, _, mm = C.ZERO_DTE_HARD_EXIT_ET.partition(":")
    cutoff = datetime.time(int(hh), int(mm))
    bell = closes_at(now)
    if cutoff >= bell:            # half-day: the bell is the hard exit
        cutoff = (datetime.datetime.combine(now.date(), bell)
                  - datetime.timedelta(minutes=30)).time()
    return is_open(now) and now.time() >= cutoff


def reason():
    n = now_et()
    if n.weekday() >= 5:
        return f"weekend ({n:%a %H:%M} ET)"
    if is_holiday(n):
        return f"market holiday ({n:%Y-%m-%d}) — next session is the day after"
    bell = closes_at(n)
    half = " (half day)" if early_close(n.date()) else ""
    return (f"outside 09:30-{bell:%H:%M} ET{half} (now {n:%H:%M} ET)")
