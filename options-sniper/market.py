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


def et_minute(ts):
    """'HH:MM' in New York, from an ISO timestamp in whatever zone UW sends.

    UW serves these in UTC. Every clock rule in this project is written in
    Eastern -- the 15:30 hard exit, SESSION_WINDOWS, the time-of-day buckets --
    so slicing the string straight out of the payload compares an Eastern
    number against a UTC one, and every rule lands four hours from where it was
    meant to. Nothing in the read path ever converted, so the error was
    invisible: the numbers all looked like plausible session times.
    """
    if not ts:
        return ""
    if "T" not in ts:
        # A bare "HH:MM:SS" carries no date and no zone, so there is nothing
        # to convert; pass it through rather than guess a day.
        return ts[:5]
    try:
        t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts.split("T", 1)[1][:5]
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    et = t.astimezone(_ET) if _ET else t - datetime.timedelta(hours=4)
    return et.strftime("%H:%M")


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


def early_close(when):
    """Accepts a datetime OR a date, for the same reason is_holiday does."""
    d = when.date() if hasattr(when, "date") else when
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


def is_holiday(when=None):
    """Accepts a datetime OR a date. Both, because the two live side by side.

    It took a datetime and called .date() on it, while early_close() next to
    it takes a date. Every caller happened to pass the right one, which is
    luck rather than design: the next one to pass a date gets an
    AttributeError on a market-calendar check, and a calendar check that
    raises inside the scheduler is a session with no scan.
    """
    when = when or now_et()
    day = when.date() if hasattr(when, "date") else when
    return day in holidays(day.year)


def after_bell(now=None):
    """True on a trading day once the bell has gone.

    A session can only be summarised after it ends, so the daily reports test
    this rather than `not is_open()` — which is also true all night, all
    weekend, and every holiday.
    """
    now = now or now_et()
    if now.weekday() >= 5 or is_holiday(now):
        return False
    return now.time() >= closes_at(now)


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
