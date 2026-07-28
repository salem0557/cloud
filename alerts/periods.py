"""Period boundaries: what "this hour / today / this week / this month" mean.

Crypto trades round the clock, so its periods run on UTC. A US stock's day is
the New York day — using UTC there would roll "today" over at 7pm ET, in the
middle of post-market trading.
"""
import datetime as dt
from zoneinfo import ZoneInfo

UTC = ZoneInfo("UTC")
NY = ZoneInfo("America/New_York")

PERIODS = ("hour", "day", "week", "month")

LABELS = {
    "hour": "هذه الساعة",
    "day": "اليوم",
    "week": "هذا الأسبوع",
    "month": "هذا الشهر",
}

SHORT_LABELS = {"hour": "ساعة", "day": "يوم", "week": "أسبوع", "month": "شهر"}


def market_tz(market: str) -> ZoneInfo:
    return NY if market == "stock" else UTC


def period_start(now: dt.datetime, period: str) -> dt.datetime:
    """Start of the period containing `now`, in `now`'s own timezone."""
    if period == "hour":
        return now.replace(minute=0, second=0, microsecond=0)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "day":
        return midnight
    if period == "week":                      # weeks start Monday
        return midnight - dt.timedelta(days=now.weekday())
    if period == "month":
        return midnight.replace(day=1)
    raise ValueError(f"unknown period: {period}")


def period_key(now: dt.datetime, period: str) -> str:
    """Stable identifier for the current period; a change means it rolled over."""
    start = period_start(now, period)
    fmt = {"hour": "%Y-%m-%dT%H", "day": "%Y-%m-%d",
           "week": "%Y-W%V", "month": "%Y-%m"}[period]
    return start.strftime(fmt)


def now_in(market: str) -> dt.datetime:
    return dt.datetime.now(market_tz(market))


def lookback_ms(period: str) -> int:
    """How far back to fetch candles so the whole current period is covered.

    Generous on purpose: a month period asks for 32 days, so the first candle
    always precedes the period start no matter when the bot starts up.
    """
    hours = {"hour": 2, "day": 26, "week": 8 * 24, "month": 32 * 24}[period]
    return hours * 3600 * 1000


def candle_interval(period: str) -> str:
    """Candle size used to reconstruct a period's extremes.

    Finer than needed for short periods, coarse enough for long ones to stay
    inside a single exchange request.
    """
    return {"hour": "1m", "day": "5m", "week": "30m", "month": "2h"}[period]
