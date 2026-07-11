"""حارس المراكز المفتوحة: يعيد تسعير كل مركز /track-ed (bot.py's hourly
JobQueue job، خلال ساعات السوق فقط عبر market_calendar.market_is_open)
ويطلق تنبيهات لمرة واحدة لكل شرط -- أعلام alerted_* بجدول positions تمنع
تكرار نفس التنبيه كل ساعة.

مستقل تماماً عن signals_db's `signals` table (سجل /review's التاريخي):
هذا عن مراكز حقيقية أخبر عنها عضو يدوياً عبر /track -- البوت ما عنده أي
تكامل مع وسيط ولا يكتشف عملية شراء بنفسه، فكل ما يعرفه هو ما أُدخل يدوياً.

check_positions_for_alerts() لا يرسل أي رسالة تيليجرام بنفسه -- يرجع قائمة
تنبيهات جاهزة، وbot.py's job callback هو من يرسلها فعلياً؛ هذا يبقي المنطق
قابلاً للاختبار بمعزل عن Telegram.
"""
import asyncio
import datetime as dt
import logging

from . import config, options
from . import signals_db as db
from .utils import fmt_price

log = logging.getLogger(__name__)


async def reprice(row) -> float | None:
    """Current premium for one tracked position, or None if the contract
    can't be found (expired, delisted, no live quote)."""
    return await asyncio.to_thread(
        options.fetch_contract_premium, row["symbol"], row["strike"], row["expiry"], True)


def format_position_line(row, current_price: float | None) -> str:
    entry = row["entry_price"]
    if current_price is not None:
        pl_pct = (current_price - entry) / entry * 100
        price_part = f"{fmt_price(current_price)} ({pl_pct:+.0f}%)"
    else:
        price_part = "تعذر جلب السعر الحالي"
    return (f"*{row['symbol']}* {row['strike']:.2f}$ {row['expiry']} — "
           f"دخول {fmt_price(entry)} → حالياً {price_part}")


def _days_remaining(expiry: str, today: dt.date) -> int:
    return (dt.date.fromisoformat(expiry) - today).days


def _alert(row, message: str) -> dict:
    return {"chat_id": row["chat_id"], "position_id": row["id"], "symbol": row["symbol"],
           "message": message}


async def check_positions_for_alerts() -> list[dict]:
    """Evaluates every OPEN position (across every chat) against the four
    alert thresholds and returns whichever fired for the FIRST time this
    cycle -- [{chat_id, position_id, symbol, message}, ...]. Marks the
    corresponding alerted_* flag immediately so a later cycle never
    re-fires the same alert for the same position."""
    rows = await asyncio.to_thread(db.fetch_open_positions)
    alerts: list[dict] = []
    today = dt.date.today()

    for row in rows:
        current = await reprice(row)

        if current is not None:
            pl_pct = (current - row["entry_price"]) / row["entry_price"] * 100

            if pl_pct <= config.POSITION_STOPLOSS_PCT and not row["alerted_stoploss"]:
                alerts.append(_alert(row,
                    f"🔴 *{row['symbol']}* {row['strike']:.2f}$ {row['expiry']} — "
                    f"نزل {pl_pct:.0f}% من سعر شرائك ({fmt_price(row['entry_price'])} → "
                    f"{fmt_price(current)})\nتنبيه: فعّل وقف الخسارة."))
                await asyncio.to_thread(db.mark_alerted, row["id"], "alerted_stoploss")

            if pl_pct >= config.POSITION_PROFIT_PCT and not row["alerted_profit"]:
                alerts.append(_alert(row,
                    f"🟢 *{row['symbol']}* {row['strike']:.2f}$ {row['expiry']} — "
                    f"ربح {pl_pct:.0f}% ({fmt_price(row['entry_price'])} → {fmt_price(current)})\n"
                    f"تنبيه: جني أرباح - بِع النص أو الكل."))
                await asyncio.to_thread(db.mark_alerted, row["id"], "alerted_profit")

            tracked_date = dt.datetime.fromtimestamp(row["tracked_ts"]).date()
            elapsed = (today - tracked_date).days
            if (elapsed >= row["original_dte"] / 2 and current < row["entry_price"]
                    and not row["alerted_timestop"]):
                alerts.append(_alert(row,
                    f"🟡 *{row['symbol']}* {row['strike']:.2f}$ {row['expiry']} — "
                    f"عدّى نص المدة الأصلية ({row['original_dte']} يوم) وهو خاسر "
                    f"({fmt_price(row['entry_price'])} → {fmt_price(current)})\n"
                    f"تنبيه: الوقف الزمني - راجع المركز."))
                await asyncio.to_thread(db.mark_alerted, row["id"], "alerted_timestop")

        days_left = _days_remaining(row["expiry"], today)
        if 0 <= days_left <= config.POSITION_THETA_WARNING_DAYS and not row["alerted_theta"]:
            alerts.append(_alert(row,
                f"⏰ *{row['symbol']}* {row['strike']:.2f}$ {row['expiry']} — "
                f"باقي {days_left} يوم على الانتهاء\nتحذير: دخلت منطقة تسارع الـ theta."))
            await asyncio.to_thread(db.mark_alerted, row["id"], "alerted_theta")

    return alerts
