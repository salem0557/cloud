"""US market-hours guard.

GitHub Actions cron only understands UTC and cannot follow US daylight saving,
so the workflows run over a window wide enough for both EST and EDT and this
check ends the run early outside real trading hours. Cheaper than a wrong alert
on a stale quote.
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
