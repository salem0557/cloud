"""Regular US equity market hours (9:30-16:00 America/New_York, Mon-Fri).

This intentionally ignores exchange holidays (Thanksgiving, Christmas,
etc.) - a full holiday calendar needs an extra dependency/data file that
isn't worth it just to decide whether to keep a background scan running.
Worst case: the app stays up (or briefly wakes up and shuts back down)
on a handful of holidays each year.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


def is_market_open(now: datetime = None) -> bool:
    now = (now or datetime.now(MARKET_TZ)).astimezone(MARKET_TZ)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return MARKET_OPEN <= now.time() < MARKET_CLOSE
