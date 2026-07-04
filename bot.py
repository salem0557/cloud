"""Telegram bot: continuous scanner for US stocks and top cryptocurrencies.

Filters (a stock is reported when >= FILTERS_REQUIRED of them match):
  1. Price at the lower Bollinger Band
  2. RSI < 30 (oversold)
  3. Price at a support zone
  4. Falling wedge pattern

Run:  TELEGRAM_BOT_TOKEN=xxx python bot.py
"""
import asyncio
import datetime as dt
import gc
import logging
import resource
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()  # must run before scanner.config reads the environment

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from scanner import config, engine, options, universe
from scanner.indicators import FILTERS, fmt_price
from scanner.state import State
from scanner.throttle import Throttle

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

NY = ZoneInfo("America/New_York")
MSG_LIMIT = 3800  # keep below Telegram's 4096-char cap

state = State()
scan_lock = asyncio.Lock()
hot_lock = asyncio.Lock()
throttle = Throttle()
hotlist: set[str] = set()  # near-signal symbols, rebuilt every full cycle


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
    if m.options_text:
        lines.append(m.options_text)
    return "\n".join(lines)


SIDE_LABELS = {"call": "🟢📈 CALL", "put": "🔴📉 PUT"}


def format_options(picks: dict) -> str:
    """Best-contracts block: top picks per side, cheapest premium first."""
    if not picks or not (picks.get("call") or picks.get("put")):
        return ""
    lines = ["  📊 أفضل عقود الأوبشنز (الأرخص أولاً):"]
    for side in ("call", "put"):
        contracts = picks.get(side) or []
        if not contracts:
            continue
        lines.append(f"  {SIDE_LABELS[side]}:")
        for i, c in enumerate(contracts, 1):
            lines.append(
                f"    {i}) تنفيذ {c['strike']:.2f}$ • ينتهي {c['expiry']}"
                f" ({c['days']} يوم) • بريميوم {c['premium']:.2f}$"
                f" = {c['premium'] * 100:.0f}$/عقد"
            )
    return "\n".join(lines)


async def attach_options(matches):
    """Fill options_text on stock matches (coins have no listed options)."""
    if not config.OPTIONS_ENABLED:
        return
    for m in matches:
        if m.is_crypto or m.options_text:
            continue
        try:
            picks = await asyncio.to_thread(options.best_options, m.symbol, m.price)
            m.options_text = format_options(picks)
        except Exception:
            log.exception("Options lookup failed for %s", m.symbol)


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

async def send_matches(app, kind: str, to_send, hot: bool = False):
    if kind == "stock":
        await attach_options(to_send)
    flame = "🔥 " if hot else ""
    header = f"{flame}{KIND_HEADERS[kind]} — {dt.datetime.now(NY):%H:%M} ET"
    for chunk in build_messages(header, to_send):
        await broadcast(app, chunk)


async def do_scan(app: Application, only_changes: bool, notify_empty: bool):
    """Scan batch by batch, pushing each matching stock the moment it's found."""
    global hotlist
    if scan_lock.locked():
        log.info("Scan already running; skipping")
        return
    async with scan_lock:
        started = dt.datetime.now(NY)
        # A fresh daily "qualified" list keeps continuous cycles small; when
        # it's stale, this cycle covers the whole universe and rebuilds it.
        full_pass, stock_symbols = await asyncio.to_thread(universe.stock_scan_list)
        crypto_symbols = universe.get_crypto_universe() if config.CRYPTO_ENABLED else []
        log.info("Scan started (full_pass=%s, %d stocks)", full_pass, len(stock_symbols))
        kstats = {"stock": engine.new_stats(len(stock_symbols)),
                  "crypto": engine.new_stats(len(crypto_symbols))}
        matched = {"stock": 0, "crypto": 0}
        sent_count = 0
        new_hot: set[str] = set()
        qualified: list[str] = []

        # Crypto first: it is a single quick batch, so those alerts go out
        # within seconds; stocks and coins are never mixed in one message.
        plan = [("crypto", batch) for batch in engine.make_batches(crypto_symbols)]
        plan += [("stock", batch) for batch in engine.make_batches(stock_symbols)]

        for kind, batch in plan:
            result = await asyncio.to_thread(engine.scan_batch, batch, kstats[kind])
            matched[kind] += len(result.matches)
            new_hot.update(result.hot)
            if kind == "stock":
                qualified.extend(result.liquid)
            to_send = state.fresh_matches(result.matches) if only_changes else result.matches
            state.record(result.matches)
            if to_send:
                sent_count += len(to_send)
                await send_matches(app, kind, to_send)
                state.save()  # crash-safe: never re-alert what was already sent
            throttle.report(result.data_ratio)
            # Always pace batches; unpaced cycles crashed the container
            await asyncio.sleep(max(throttle.delay, config.BATCH_INTERVAL_SECONDS))

        if full_pass and qualified:
            await asyncio.to_thread(universe.save_qualified, qualified)
        hotlist = new_hot
        state.prune()
        state.save()
        gc.collect()  # drop per-cycle DataFrames before the next cycle starts
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        log.info("Scan done: matched=%s sent=%d hot=%d peak_rss=%.0fMB stats=%s",
                 matched, sent_count, len(hotlist), rss_mb, kstats)

        breakdown = (f"📈 الأسهم: {matched['stock']} مطابق "
                     f"من {kstats['stock']['liquid']} مفحوص")
        if config.CRYPTO_ENABLED:
            breakdown += (f"\n🪙 العملات الرقمية: {matched['crypto']} مطابق "
                          f"من {kstats['crypto']['liquid']} مفحوص")

        if sent_count == 0 and notify_empty:
            await broadcast(
                app,
                f"🔎 اكتمل المسح ({started:%H:%M} ET) — لا إشارات تحقق "
                f"{config.FILTERS_REQUIRED}/4 من الفلاتر.\n{breakdown}",
            )
        elif not only_changes:
            await broadcast(app, f"✅ اكتمل المسح:\n{breakdown}")


async def do_hot_scan(app: Application):
    """Fast lane: re-check near-signal symbols (>=2 filters) every couple of
    minutes so a setup completing between full cycles is caught immediately."""
    if not hotlist or hot_lock.locked():
        return
    if throttle.delay >= 60:
        return  # Yahoo is pushing back; don't add fast-lane pressure
    async with hot_lock:
        symbols = sorted(hotlist)[:config.HOTLIST_MAX]
        stats = engine.new_stats(len(symbols))
        for batch in engine.make_batches(symbols):
            result = await asyncio.to_thread(engine.scan_batch, batch, stats)
            throttle.report(result.data_ratio)
            to_send = state.fresh_matches(result.matches)
            if not to_send:
                continue
            state.record(result.matches)
            for kind in ("crypto", "stock"):
                group = [m for m in to_send if (kind == "crypto") == m.is_crypto]
                if group:
                    await send_matches(app, kind, group, hot=True)
            state.save()


async def hot_job(context: ContextTypes.DEFAULT_TYPE):
    if not state.subscribers:
        return
    await do_hot_scan(context.application)


async def scan_loop_job(context: ContextTypes.DEFAULT_TYPE):
    # Continuous scanning: this job ticks frequently, and do_scan's lock makes
    # each tick a no-op while a cycle is still running — so a new cycle starts
    # within SCAN_PAUSE_SECONDS of the previous one finishing. The dedup layer
    # ensures only new or changed signals are ever sent.
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
        "بشكل متواصل على مدار اليوم (فريم الساعة): فور انتهاء الدورة تبدأ التالية، "
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
    if config.CRYPTO_ENABLED:
        crypto_line = f"مفعّلة 🪙 ({len(universe.get_crypto_universe())} عملة)"
    else:
        crypto_line = "معطلة"
    qualified = universe.load_qualified()
    universe_line = (f"قائمة مؤهلة ({len(qualified)} سهم)" if qualified
                     else "دورة تأهيل كاملة (كل الأسهم)")
    throttle_line = (f"نشطة مؤقتاً ({throttle.delay:.0f} ثانية بين الدفعات)"
                     if throttle.active else "غير نشطة")
    await update.message.reply_text(
        f"السوق الأمريكي الآن: {open_now}\n"
        f"العملات الرقمية: {crypto_line}\n"
        f"نطاق الأسهم: {universe_line}\n"
        f"القائمة الساخنة 🔥: {len(hotlist)} رمز (فحص كل {config.HOTLIST_INTERVAL_SECONDS // 60} دقيقة)\n"
        f"التهدئة التلقائية: {throttle_line}\n"
        f"مسح قيد التنفيذ: {scanning}\n"
        f"عدد المشتركين: {len(state.subscribers)}\n"
        f"إشارات في الذاكرة: {len(state.last_alerts)}\n"
        f"الشرط: {config.FILTERS_REQUIRED}/4 فلاتر • الفريم: {config.INTERVAL}"
    )


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN environment variable")
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("status", cmd_status))
    app.job_queue.run_repeating(scan_loop_job,
                                interval=config.SCAN_PAUSE_SECONDS, first=10)
    app.job_queue.run_repeating(hot_job,
                                interval=config.HOTLIST_INTERVAL_SECONDS, first=90)
    log.info("Bot starting (polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
