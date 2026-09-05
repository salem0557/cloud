"""US market-hours guard.

The scheduler ticks continuously, so this decides when the market is actually
open. It follows US daylight saving through the America/New_York zone rather
than a fixed offset, so no seasonal edit is ever needed: the session is always
09:30-16:00 ET, whatever that maps to in Riyadh.
"""
import datetime

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


def is_open(now=None):
    now = now or now_et()
    if now.weekday() >= 5:                          # Sat/Sun
        return False
    return OPEN <= now.time() <= CLOSE


def reason():
    n = now_et()
    if n.weekday() >= 5:
        return f"weekend ({n:%a %H:%M} ET)"
    return f"outside 09:30-16:00 ET (now {n:%H:%M} ET)"
