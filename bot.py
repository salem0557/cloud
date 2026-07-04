"""Telegram bot: hourly scanner for US stocks.

Filters (a stock is reported when >= FILTERS_REQUIRED of them match):
  1. Price at the lower Bollinger Band
  2. RSI < 30 (oversold)
  3. Price at a support zone
  4. Falling wedge pattern

Run:  TELEGRAM_BOT_TOKEN=xxx python bot.py
"""
import asyncio
import datetime as dt
import logging
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()  # must run before scanner.config reads the environment

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from scanner import config, engine, universe
from scanner.indicators import FILTERS, fmt_price
from scanner.state import State

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

NY = ZoneInfo("America/New_York")
MSG_LIMIT = 3800  # keep below Telegram's 4096-char cap

state = State()
scan_lock = asyncio.Lock()


def market_is_open(now: dt.datetime | None = None) -> bool:
    now = (now or dt.datetime.now(NY)).astimezone(NY)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=10, second=0, microsecond=0)
    return open_t <= now <= close_t


# ------------------------------------------------------------- formatting

KIND_HEADERS = {
    "stock": "⚡ إشارة فورية — 📈 أسهم أمريكية",
    "crypto": "⚡ إشارة فورية — 🪙 عملات رقمية",
}


def format_match(m) -> str:
    icon = "🪙 " if m.is_crypto else ""
    lines = [f"{icon}*{m.display_symbol}* — {m.score}/4 — {fmt_price(m.price)}"]
    for key, (name, _) in FILTERS.items():
        mark = "✅" if key in m.matched else "❌"
        lines.append(f"  {mark} {name}: {m.details.get(key, '-')}")
    return "\n".join(lines)


def build_messages(header: str, matches) -> list[str]:
    """Assemble alert text, split into Telegram-sized chunks."""
    chunks, current = [], header
    for m in matches:
        block = "\n\n" + format_match(m)
        if len(current) + len(block) > MSG_LIMIT:
            chunks.append(current)
            current = format_match(m)
        else:
            current += block
    chunks.append(current)
    return chunks


async def broadcast(app: Application, text: str):
    for chat_id in list(state.subscribers):
        try:
            await app.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            log.exception("Send to %s failed", chat_id)


# ------------------------------------------------------------------ scans

async def do_scan(app: Application, only_changes: bool, notify_empty: bool):
    """Scan batch by batch, pushing each matching stock the moment it's found."""
    if scan_lock.locked():
        log.info("Scan already running; skipping")
        return
    async with scan_lock:
        started = dt.datetime.now(NY)
        log.info("Scan started")
        stock_symbols = await asyncio.to_thread(universe.get_universe)
        crypto_symbols = universe.get_crypto_universe() if config.CRYPTO_ENABLED else []
        stats = engine.new_stats(len(stock_symbols) + len(crypto_symbols))
        matched_symbols: set[str] = set()
        sent_count = 0

        # Crypto first: it is a single quick batch, so those alerts go out
        # within seconds; stocks and coins are never mixed in one message.
        plan = [("crypto", batch) for batch in engine.make_batches(crypto_symbols)]
        plan += [("stock", batch) for batch in engine.make_batches(stock_symbols)]

        for kind, batch in plan:
            matches = await asyncio.to_thread(engine.scan_batch, batch, stats)
            matched_symbols.update(m.symbol for m in matches)
            to_send = state.fresh_matches(matches) if only_changes else matches
            state.record(matches)
            if to_send:
                sent_count += len(to_send)
                header = f"{KIND_HEADERS[kind]} — {dt.datetime.now(NY):%H:%M} ET"
                for chunk in build_messages(header, to_send):
                    await broadcast(app, chunk)
                state.save()  # crash-safe: never re-alert what was already sent

        state.prune()
        state.save()
        log.info("Scan done: %d matched, %d sent, stats=%s",
                 len(matched_symbols), sent_count, stats)

        if sent_count == 0 and notify_empty:
            await broadcast(
                app,
                f"🔎 اكتمل المسح ({started:%H:%M} ET): لا توجد أسهم تحقق "
                f"{config.FILTERS_REQUIRED}/4 من الفلاتر "
                f"({stats['liquid']} سهم مفحوص).",
            )
        elif not only_changes:
            await broadcast(
                app,
                f"✅ اكتمل المسح: {sent_count} سهم مطابق "
                f"من أصل {stats['liquid']} سهم مفحوص.",
            )


async def hourly_job(context: ContextTypes.DEFAULT_TYPE):
    # Runs around the clock; outside market hours prices barely move, so the
    # dedup layer keeps the chat quiet unless something actually changed.
    if not state.subscribers:
        return
    await do_scan(context.application, only_changes=True, notify_empty=False)


# --------------------------------------------------------------- commands

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state.subscribers.add(update.effective_chat.id)
    state.save()
    await update.message.reply_text(
        "أهلاً! ✅ تم تفعيل الاشتراك.\n\n"
        "سأمسح كل الأسهم الأمريكية 📈 وأهم 100 عملة رقمية 🪙 "
        "كل ساعة على مدار اليوم (فريم الساعة) "
        "وأرسل لك فقط الإشارات *الجديدة أو المتغيرة* التي تحقق "
        f"{config.FILTERS_REQUIRED} فلاتر من 4:\n"
        "1️⃣ السعر عند الحد السفلي لبولينجر باند\n"
        "2️⃣ RSI أقل من 30 (تشبع بيعي)\n"
        "3️⃣ السعر عند منطقة دعم\n"
        "4️⃣ نموذج وتد هابط\n\n"
        "الأوامر: /scan مسح يدوي فوري • /status الحالة • /stop إيقاف",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state.subscribers.discard(update.effective_chat.id)
    state.save()
    await update.message.reply_text("تم إيقاف التنبيهات. أرسل /start لإعادة التفعيل.")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if scan_lock.locked():
        await update.message.reply_text("⏳ يوجد مسح قيد التنفيذ حالياً، انتظر انتهاءه.")
        return
    state.subscribers.add(update.effective_chat.id)
    state.save()
    await update.message.reply_text(
        "🔎 بدأ المسح اليدوي (العملات الرقمية أولاً ثم كل الأسهم الأمريكية)... "
        "سأرسل كل إشارة فور اكتشافها، ثم رسالة عند اكتمال المسح "
        "(المسح الكامل يستغرق 15-40 دقيقة)."
    )
    await do_scan(context.application, only_changes=False, notify_empty=True)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_now = "مفتوح ✅" if market_is_open() else "مغلق ❌"
    scanning = "نعم ⏳" if scan_lock.locked() else "لا"
    await update.message.reply_text(
        f"السوق الأمريكي الآن: {open_now}\n"
        f"مسح قيد التنفيذ: {scanning}\n"
        f"عدد المشتركين: {len(state.subscribers)}\n"
        f"أسهم في آخر تنبيه: {len(state.last_alerts)}\n"
        f"الشرط: {config.FILTERS_REQUIRED}/4 فلاتر • الفريم: {config.INTERVAL}"
    )


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN environment variable")
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("status", cmd_status))
    app.job_queue.run_repeating(hourly_job, interval=3600, first=30)
    log.info("Bot starting (polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
