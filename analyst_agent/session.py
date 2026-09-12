"""US market session state — which session the last candle belongs to.

A US-only analyst has to say whether it is reading a live tape or yesterday's
close: the same RSI means something different at 10:15 ET than it does on a
Saturday. The NYSE holiday calendar is reused from the scanner package rather
than duplicated.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

try:
    from scanner.market_calendar import is_market_holiday
except Exception:  # pragma: no cover - analyst_agent must run standalone
    def is_market_holiday(_d: dt.date) -> bool:
        return False

NY = ZoneInfo("America/New_York")

PRE_OPEN = dt.time(4, 0)
OPEN = dt.time(9, 30)
CLOSE = dt.time(16, 0)
POST_CLOSE = dt.time(20, 0)

LABELS = {
    "regular": "الجلسة الرسمية مفتوحة",
    "pre": "ما قبل الافتتاح (بري ماركت)",
    "after": "ما بعد الإغلاق (أفتر أورز)",
    "closed": "السوق مغلق",
    "weekend": "نهاية الأسبوع — السوق مغلق",
    "holiday": "عطلة رسمية في السوق الأمريكي",
}


def state(now: dt.datetime | None = None) -> dict:
    """Session state plus the minutes to the next open/close."""
    now = (now or dt.datetime.now(dt.timezone.utc)).astimezone(NY)
    today = now.date()
    clock = now.time()

    if now.weekday() >= 5:
        phase = "weekend"
    elif is_market_holiday(today):
        phase = "holiday"
    elif OPEN <= clock < CLOSE:
        phase = "regular"
    elif PRE_OPEN <= clock < OPEN:
        phase = "pre"
    elif CLOSE <= clock < POST_CLOSE:
        phase = "after"
    else:
        phase = "closed"

    out = {
        "phase": phase,
        "phase_ar": LABELS[phase],
        "now_et": now.strftime("%Y-%m-%d %H:%M ET"),
        "is_open": phase == "regular",
    }
    if phase == "regular":
        close_at = dt.datetime.combine(today, CLOSE, tzinfo=NY)
        out["minutes_to_close"] = int((close_at - now).total_seconds() // 60)
    elif phase in ("pre", "closed") and clock < OPEN:
        open_at = dt.datetime.combine(today, OPEN, tzinfo=NY)
        out["minutes_to_open"] = int((open_at - now).total_seconds() // 60)
    return out


def bar_freshness(last_bar: dt.datetime | None, minutes_per_bar: int) -> dict:
    """How stale the last candle is, in its own bar units.

    A read on a candle three bars old is a different claim from a read on the
    one forming right now, so the answer has to know the difference.
    """
    if last_bar is None:
        return {}
    now = dt.datetime.now(dt.timezone.utc)
    stamp = last_bar
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=NY)
    age_minutes = max(0, int((now - stamp.astimezone(dt.timezone.utc)).total_seconds() // 60))
    bars_old = age_minutes / max(minutes_per_bar, 1)
    out = {"last_bar_age_minutes": age_minutes, "last_bar_age_in_bars": round(bars_old, 1)}
    if bars_old > 3:
        out["stale_note"] = ("آخر شمعة متأخرة عن الوقت الحالي — البيانات ليست لحظية، "
                             "تأكد من السعر قبل أي تنفيذ")
    return out
