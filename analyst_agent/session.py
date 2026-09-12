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
    "crypto_24h": "سوق الكريبتو — يعمل 24 ساعة طوال الأسبوع",
    "fx_open": "سوق العملات مفتوح (24 ساعة حتى إغلاق الجمعة)",
    "fx_closed": "سوق العملات مغلق (يفتح مساء الأحد بتوقيت نيويورك)",
    "futures_open": "سوق العقود الآجلة مفتوح (شبه 24 ساعة)",
    "futures_closed": "سوق العقود الآجلة في فترة التوقف اليومية",
}


def asset_class(symbol: str | None) -> str:
    """crypto / fx / futures / us_equity — each keeps different hours."""
    if not symbol:
        return "us_equity"
    upper = symbol.upper()
    if upper.endswith("-USD") or upper.endswith("-USDT"):
        return "crypto"
    if upper.endswith("=X"):
        return "fx"
    if upper.endswith("=F"):
        return "futures"
    return "us_equity"


def state_for(symbol: str | None, now: dt.datetime | None = None) -> dict:
    """Session state for the asset actually being analysed.

    Saying "market closed" about Bitcoin at 2am would be wrong and would make
    the whole read look careless — crypto never closes, FX closes only for the
    weekend, and futures only for the daily break.
    """
    kind = asset_class(symbol)
    if kind == "us_equity":
        return state(now)

    moment = (now or dt.datetime.now(dt.timezone.utc)).astimezone(NY)
    weekday, clock = moment.weekday(), moment.time()
    if kind == "crypto":
        phase = "crypto_24h"
    elif kind == "fx":
        # Opens Sunday 17:00 ET, closes Friday 17:00 ET.
        closed = (weekday == 5
                  or (weekday == 6 and clock < dt.time(17, 0))
                  or (weekday == 4 and clock >= dt.time(17, 0)))
        phase = "fx_closed" if closed else "fx_open"
    else:
        # Futures: Sunday 18:00 ET to Friday 17:00 ET, with a daily 17:00-18:00 break.
        closed = (weekday == 5
                  or (weekday == 6 and clock < dt.time(18, 0))
                  or (weekday == 4 and clock >= dt.time(17, 0))
                  or (dt.time(17, 0) <= clock < dt.time(18, 0)))
        phase = "futures_closed" if closed else "futures_open"
    return {
        "phase": phase,
        "phase_ar": LABELS[phase],
        "now_et": moment.strftime("%Y-%m-%d %H:%M ET"),
        "is_open": phase in ("crypto_24h", "fx_open", "futures_open"),
        "asset_class": kind,
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
        "asset_class": "us_equity",
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
