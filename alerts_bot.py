"""Telegram front-end for the price-extreme alert bot.

Send it a symbol — "BTC", "AAPL", "sol" — and it watches that symbol, messaging
you whenever it prints a new low or high for the hour, day, week, or month.

Run:  ALERTS_BOT_TOKEN=xxx python alerts_bot.py
"""
import asyncio
import logging
import time

from dotenv import load_dotenv

load_dotenv()  # must run before alerts.config reads the environment

from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                      Update)
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from alerts import config, sources
from alerts.engine import Engine, format_break
from alerts.periods import PERIODS, SHORT_LABELS
from alerts.sources import fmt_price
from alerts.store import Store, Watch

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("alerts_bot")

store = Store()
engine = Engine(store)
poll_lock = asyncio.Lock()


# ---------------------------------------------------------------- keyboard

def watch_keyboard(watch: Watch) -> InlineKeyboardMarkup:
    """Toggles for periods and directions, plus mute/delete."""
    sym = watch.asset.symbol
    periods = [
        InlineKeyboardButton(
            f"{'✅' if p in watch.periods else '▫️'} {SHORT_LABELS[p]}",
            callback_data=f"p|{sym}|{p}")
        for p in PERIODS
    ]
    directions = [
        InlineKeyboardButton(
            f"{'✅' if 'low' in watch.directions else '▫️'} 🔻 القيعان",
            callback_data=f"d|{sym}|low"),
        InlineKeyboardButton(
            f"{'✅' if 'high' in watch.directions else '▫️'} 🚀 القمم",
            callback_data=f"d|{sym}|high"),
    ]
    footer = [
        InlineKeyboardButton("🔔 تشغيل" if watch.muted else "🔕 كتم",
                             callback_data=f"m|{sym}|x"),
        InlineKeyboardButton("🗑 حذف", callback_data=f"x|{sym}|x"),
    ]
    return InlineKeyboardMarkup([periods[:2], periods[2:], directions, footer])


def watch_text(watch: Watch, price: float | None = None) -> str:
    asset = watch.asset
    market = "عملة رقمية" if asset.is_crypto else "سهم أمريكي"
    periods = "، ".join(SHORT_LABELS[p] for p in PERIODS if p in watch.periods)
    kinds = []
    if "low" in watch.directions:
        kinds.append("القيعان 🔻")
    if "high" in watch.directions:
        kinds.append("القمم 🚀")
    lines = [f"<b>{asset.display}</b> ({market})"]
    if price:
        lines.append(f"السعر الآن: <b>{fmt_price(price)}$</b>")
    lines.append(f"الفترات: {periods or '— لا شيء، اختر فترة'}")
    lines.append(f"التنبيه على: {'، '.join(kinds) or '— لا شيء'}")
    if watch.muted:
        lines.append("🔕 مكتوم")
    return "\n".join(lines)


# ----------------------------------------------------------------- commands

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 <b>بوت تنبيهات القيعان والقمم</b>\n\n"
        "أرسل اسم أي عملة رقمية أو سهم أمريكي، وسأراقبه وأنبّهك فور تسجيله "
        "<b>أدنى سعر</b> أو <b>أعلى سعر</b> خلال الساعة أو اليوم أو الأسبوع "
        "أو الشهر.\n\n"
        "جرّب الآن: أرسل <code>BTC</code> أو <code>AAPL</code>\n\n"
        "بعد الإضافة تظهر أزرار تختار بها الفترات ونوع التنبيه.\n"
        "الأوامر: /help",
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "<b>الاستخدام</b>\n"
        "أرسل اسم الرمز مباشرة: <code>BTC</code> · <code>ETH</code> · "
        "<code>AAPL</code> · <code>TSLA</code>\n\n"
        "إن كان الرمز مشتركاً بين عملة وسهم:\n"
        "<code>s:LINK</code> ← سهم • <code>c:LINK</code> ← عملة\n\n"
        "<b>الأوامر</b>\n"
        "/list — كل ما تراقبه وأسعاره الآن\n"
        "/price BTC — السعر وقاع/قمة كل فترة\n"
        "/remove BTC — إيقاف المراقبة\n"
        "/clear — حذف الكل\n"
        "/status — حالة البوت وآخر فحص\n\n"
        "<b>كيف يعمل التنبيه</b>\n"
        f"• يفحص الأسعار كل {config.POLL_SECONDS} ثانية\n"
        f"• لا يكرر التنبيه إلا بعد حركة {config.MIN_MOVE_PCT}% "
        f"وبفارق {config.COOLDOWN_MINUTES} دقائق على الأقل\n"
        "• مع بداية كل فترة يعيد حساب القاع والقمة من الشموع الحقيقية، "
        "فلا يفوته شيء إن أُعيد تشغيله\n"
        "• الأسهم تُفحص من ما قبل الافتتاح حتى ما بعد الإغلاق فقط",
        parse_mode=ParseMode.HTML,
    )


async def on_symbol(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Any plain text is treated as a symbol to watch."""
    text = (update.message.text or "").strip()
    if not text or text.startswith("/"):
        return
    chat_id = update.effective_chat.id

    if store.count(chat_id) >= config.MAX_WATCHES_PER_CHAT:
        await update.message.reply_text(
            f"وصلت للحد الأقصى ({config.MAX_WATCHES_PER_CHAT} رمز). "
            f"احذف رمزاً بـ /remove أولاً.")
        return

    msg = await update.message.reply_text(f"🔎 يبحث عن «{text}»…")
    asset = await asyncio.to_thread(sources.resolve, text)
    if asset is None:
        await msg.edit_text(
            f"لم أجد «{text}» — تأكد من الرمز.\n"
            f"أمثلة: <code>BTC</code> · <code>ETH</code> · <code>AAPL</code>\n"
            f"للأسهم المتشابهة مع العملات اكتب <code>s:الرمز</code>",
            parse_mode=ParseMode.HTML)
        return

    watch = store.add(chat_id, asset)
    store.save()
    try:
        price = await asyncio.to_thread(sources.price, asset)
    except Exception:
        price = None
    await msg.edit_text("✅ <b>تمت الإضافة</b>\n\n" + watch_text(watch, price),
                        parse_mode=ParseMode.HTML,
                        reply_markup=watch_keyboard(watch))


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    chat_id = query.message.chat_id
    action, symbol, value = query.data.split("|", 2)
    watch = store.get(chat_id, symbol)
    if watch is None:
        await query.answer("لم يعد مراقباً")
        await query.edit_message_text("🗑 حُذف.")
        return

    if action == "p":
        watch.periods.symmetric_difference_update({value})
        note = f"{SHORT_LABELS[value]}: {'مفعّل' if value in watch.periods else 'موقف'}"
    elif action == "d":
        watch.directions.symmetric_difference_update({value})
        note = "تم التحديث"
    elif action == "m":
        watch.muted = not watch.muted
        note = "🔕 مكتوم" if watch.muted else "🔔 يعمل"
    elif action == "x":
        store.remove(chat_id, symbol)
        store.save()
        await query.answer("حُذف")
        await query.edit_message_text(f"🗑 توقفت مراقبة <b>{watch.asset.display}</b>.",
                                      parse_mode=ParseMode.HTML)
        return
    else:
        await query.answer()
        return

    store.prune_tracks()
    store.save()
    await query.answer(note)
    await query.edit_message_text(watch_text(watch), parse_mode=ParseMode.HTML,
                                  reply_markup=watch_keyboard(watch))


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    watches = store.list_watches(chat_id)
    if not watches:
        await update.message.reply_text(
            "لا تراقب أي رمز بعد. أرسل اسم عملة أو سهم للبدء.")
        return
    lines = [f"<b>تراقب {len(watches)} رمزاً</b>\n"]
    for watch in watches:
        try:
            price = await asyncio.to_thread(sources.price, watch.asset)
            price_text = f"{fmt_price(price)}$"
        except Exception:
            price_text = "—"
        periods = "،".join(SHORT_LABELS[p] for p in PERIODS if p in watch.periods)
        bell = "🔕" if watch.muted else "🔔"
        lines.append(f"{bell} <b>{watch.asset.display}</b> — {price_text} "
                     f"({periods or 'بلا فترات'})")
    lines.append("\nلتعديل رمز أرسل اسمه من جديد.")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("مثال: /price BTC")
        return
    asset = await asyncio.to_thread(sources.resolve, " ".join(ctx.args))
    if asset is None:
        await update.message.reply_text("لم أجد هذا الرمز.")
        return
    msg = await update.message.reply_text(f"⏳ يجلب بيانات {asset.display}…")
    try:
        price = await asyncio.to_thread(sources.price, asset)
    except Exception as exc:
        await msg.edit_text(f"تعذّر جلب السعر: {exc}")
        return
    lines = [f"<b>{asset.display}</b> — <b>{fmt_price(price)}$</b>\n"]
    for period in PERIODS:
        try:
            low, high = await asyncio.to_thread(sources.extremes, asset, period)
        except Exception:
            continue
        # Where the current price sits inside the period's range, 0-100%.
        span = high - low
        pos = ((price - low) / span * 100) if span > 0 else 100.0
        lines.append(f"<b>{SHORT_LABELS[period]}</b>: قاع {fmt_price(low)}$ · "
                     f"قمة {fmt_price(high)}$ · الموقع {pos:.0f}%")
    await msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("مثال: /remove BTC")
        return
    chat_id = update.effective_chat.id
    asset = await asyncio.to_thread(sources.resolve, " ".join(ctx.args))
    symbol = asset.symbol if asset else " ".join(ctx.args).upper()
    if store.remove(chat_id, symbol):
        store.save()
        await update.message.reply_text(f"🗑 توقفت مراقبة {symbol}.")
    else:
        await update.message.reply_text("هذا الرمز غير موجود في قائمتك.")


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    count = store.count(chat_id)
    store.watches.pop(chat_id, None)
    store.prune_tracks()
    store.save()
    await update.message.reply_text(f"🗑 حُذفت {count} من قائمتك.")


async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    active = store.active()
    ago = int(time.time() - engine.last_poll) if engine.last_poll else None
    lines = [
        "<b>حالة البوت</b>\n",
        f"الرموز المراقَبة (الكل): {len(active)}",
        f"رموزك أنت: {store.count(update.effective_chat.id)}",
        f"دورة الفحص: كل {config.POLL_SECONDS} ثانية",
        f"آخر فحص: {f'قبل {ago} ثانية' if ago is not None else 'لم يبدأ بعد'}",
        f"سوق الأسهم: {'مفتوح' if sources.stock_market_open() else 'مغلق'}",
    ]
    if engine.last_error:
        lines.append(f"\n⚠️ آخر خطأ: {engine.last_error}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# --------------------------------------------------------------- background

async def poll_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if poll_lock.locked():
        log.info("previous poll still running; skipping")
        return
    async with poll_lock:
        alerts = await asyncio.to_thread(engine.poll)
    for chat_id, brk, asset in alerts:
        try:
            await ctx.bot.send_message(chat_id, format_break(brk, asset),
                                       parse_mode=ParseMode.HTML)
        except Exception as exc:
            log.warning("could not alert %s: %s", chat_id, exc)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("list", "قائمة ما تراقبه"),
        BotCommand("price", "السعر والقيعان والقمم"),
        BotCommand("remove", "إيقاف مراقبة رمز"),
        BotCommand("clear", "حذف الكل"),
        BotCommand("status", "حالة البوت"),
        BotCommand("help", "المساعدة"),
    ])
    log.info("watching %d symbols across %d chats",
             len(store.active()), len(store.watches))


def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("ALERTS_BOT_TOKEN غير مضبوط")

    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_symbol))

    app.job_queue.run_repeating(poll_job, interval=config.POLL_SECONDS, first=5)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
