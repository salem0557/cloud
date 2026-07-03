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

from scanner import config, engine
from scanner.indicators import FILTERS
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

def format_match(m) -> str:
    lines = [f"*{m.symbol}* — {m.score}/4 — {m.price:.2f}$"]
    for key, (name, _) in FILTERS.items():
        mark = "✅" if key in m.matched else "▫️"
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
    if scan_lock.locked():
        log.info("Scan already running; skipping")
        return
    async with scan_lock:
        started = dt.datetime.now(NY)
        log.info("Scan started")
        matches, stats = await asyncio.to_thread(engine.run_scan)
        log.info("Scan done: %d matches, stats=%s", len(matches), stats)

        to_send = state.diff_alerts(matches) if only_changes else matches
        state.save()

        stamp = started.strftime("%H:%M")
        if not to_send:
            if notify_empty:
                await broadcast(
                    app,
                    f"🔎 مسح {stamp} ET: لا توجد أسهم جديدة تحقق "
                    f"{config.FILTERS_REQUIRED}/4 من الفلاتر "
                    f"({stats['liquid']} سهم مفحوص).",
                )
            return
        kind = "إشارات جديدة/متغيرة" if only_changes else "كل النتائج"
        header = (f"🔎 *مسح السوق الأمريكي* — {stamp} ET\n"
                  f"{kind}: {len(to_send)} سهم "
                  f"(من أصل {stats['liquid']} سهم مفحوص)")
        for chunk in build_messages(header, to_send):
            await broadcast(app, chunk)


async def hourly_job(context: ContextTypes.DEFAULT_TYPE):
    if not state.subscribers:
        return
    if not market_is_open():
        log.info("Market closed; skipping hourly scan")
        return
    await do_scan(context.application, only_changes=True, notify_empty=False)


# --------------------------------------------------------------- commands

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state.subscribers.add(update.effective_chat.id)
    state.save()
    await update.message.reply_text(
        "أهلاً! ✅ تم تفعيل الاشتراك.\n\n"
        "سأمسح كل الأسهم الأمريكية كل ساعة أثناء جلسة السوق (فريم الساعة) "
        "وأرسل لك فقط الأسهم *الجديدة أو المتغيرة* التي تحقق "
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
        "🔎 بدأ المسح اليدوي لكل الأسهم الأمريكية... قد يستغرق 15-40 دقيقة، "
        "سأرسل النتائج كاملة عند الانتهاء."
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
