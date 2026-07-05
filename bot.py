"""Telegram bot: continuous scanner for US stocks, both directions.

Bullish reversal-up (a stock is reported when >= FILTERS_REQUIRED match):
  1. Price at the lower Bollinger Band
  2. RSI < 30 (oversold)
  3. Price at a support zone
  4. Falling wedge pattern

Bearish reversal-down / overbought (>= BEARISH_FILTERS_REQUIRED match):
  1. Price at the upper Bollinger Band
  2. RSI > 70 (overbought)
  3. Price at a resistance zone
  4. Rising wedge pattern
  5. Bearish MACD signal-line crossover

Run:  TELEGRAM_BOT_TOKEN=xxx python bot.py
"""
import asyncio
import concurrent.futures
import datetime as dt
import gc
import io
import logging
import multiprocessing
import os
import resource
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()  # must run before scanner.config reads the environment

import time

from telegram import (BotCommand, BotCommandScopeChat, BotCommandScopeDefault,
                      InlineKeyboardButton, InlineKeyboardMarkup, Update)
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

from scanner import chart, config, engine, market_calendar, options, performance, sentiment, universe
from scanner import data as data_mod
from scanner.indicators import FILTERS, FILTERS_BEARISH, fmt_price
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
cheap_options_lock = asyncio.Lock()
throttle = Throttle()
hotlist: set[str] = set()  # near-signal symbols, rebuilt every full cycle
pending_check: dict[int, float] = {}  # chat id -> time /check asked for a symbol

# Batches run in a single recycled worker process ("spawn" to stay safe with
# the bot's own threads): pandas/yfinance memory otherwise accumulates in the
# main process until the container is OOM-killed.
_scan_pool = None


def _get_scan_pool():
    global _scan_pool
    if _scan_pool is None:
        _scan_pool = concurrent.futures.ProcessPoolExecutor(
            max_workers=1, max_tasks_per_child=40,
            mp_context=multiprocessing.get_context("spawn"))
    return _scan_pool


async def scan_batch_async(batch):
    """Run one batch in the worker process; returns (BatchResult, stats)."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_scan_pool(),
                                      engine.scan_batch_task, batch)


def merge_stats(target: dict, delta: dict):
    for key in ("with_data", "liquid", "errors"):
        target[key] += delta[key]


def market_is_open(now: dt.datetime | None = None) -> bool:
    now = (now or dt.datetime.now(NY)).astimezone(NY)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=10, second=0, microsecond=0)
    return open_t <= now <= close_t


# ------------------------------------------------------------- formatting

ALERT_HEADERS = {
    "bullish": "⚡ إشارة فورية — 📈 احتمال صعود",
    "bearish": "⚡ إشارة فورية — 📉 تشبع شرائي/احتمال هبوط",
}

# /check <symbol>: an on-demand personal lookup, replied privately to whoever
# asked — distinct wording from ALERT_HEADERS so it's never mistaken for a
# real streamed alert.
CHECK_HEADERS = {
    "bullish": "🔍 نتيجة الفحص اليدوي — 📈 صعود",
    "bearish": "🔍 نتيجة الفحص اليدوي — 📉 هبوط",
}

ALERT_FOOTER = "⚠️ تحليل فني آلي — ليس توصية بشراء أو بيع"

DISCLAIMER = (
    "⚠️ *إخلاء مسؤولية — يُرجى القراءة بعناية*\n\n"
    "هذه الخدمة *أداة تحليل فني آلية* تعتمد على مؤشرات رياضية "
    "(بولينجر باند، مؤشر القوة النسبية RSI، مستويات الدعم والمقاومة، MACD، "
    "الأنماط السعرية) لرصد الحالات الفنية في سوق الأسهم الأمريكية — سواء "
    "حالات تشبع بيعي (احتمال صعود) أو تشبع شرائي (احتمال هبوط) — "
    "وعرض بيانات عقود الخيارات الأنشط سيولةً وفق معايير آلية بحتة.\n\n"
    "1️⃣ ما تقدمه هذه الخدمة *ليس توصية ولا مشورة استثمارية* ولا دعوة أو تحريضاً "
    "على شراء أو بيع أي ورقة مالية أو أصل رقمي أو عقد مشتقات، ولا يجوز تفسيره "
    "أو الاعتماد عليه بهذه الصفة.\n\n"
    "2️⃣ الخدمة ومشغّلها *غير مرخصين من هيئة السوق المالية في المملكة العربية "
    "السعودية* ولا من أي جهة تنظيمية أخرى لمزاولة أعمال الأوراق المالية أو تقديم "
    "المشورة الاستثمارية، ولا تقدم الخدمة أي عمل من الأعمال الخاضعة للترخيص.\n\n"
    "3️⃣ المؤشرات الفنية أدوات إحصائية *قد تخطئ*، والنتائج السابقة لا تضمن "
    "الأداء المستقبلي، والبيانات المعروضة قد يشوبها تأخير أو خطأ من مصادرها.\n\n"
    "4️⃣ التداول في الأسهم وعقود الخيارات *ينطوي على مخاطر "
    "عالية* قد تصل إلى خسارة رأس المال كاملاً. عقود الخيارات المعروضة هي نتاج "
    "فرز آلي لأنشط العقود سيولةً وليست اقتراحاً بالتداول عليها.\n\n"
    "5️⃣ أي قرار استثماري تتخذه هو *مسؤوليتك وحدك*، ولا يتحمل مشغّل الخدمة أي "
    "مسؤولية عن أي خسارة أو ضرر ينشأ عن استخدامها. استشر مستشاراً مالياً مرخصاً "
    "قبل اتخاذ أي قرار.\n\n"
    "باشتراكك واستخدامك هذه الخدمة فأنت تقر بأنك قرأت هذا الإخلاء وفهمته "
    "ووافقت عليه."
)


# ------------------------------------------------------ subscription gate

def is_admin(chat_id: int) -> bool:
    return config.ADMIN_CHAT_ID and chat_id == config.ADMIN_CHAT_ID


def sub_expiry(chat_id: int):
    """None = not approved; 0 = lifetime; else unix expiry timestamp."""
    return state.approved.get(str(chat_id))


def eligible(chat_id: int) -> bool:
    """May receive alerts: accepted the disclaimer + active paid subscription."""
    if is_admin(chat_id):
        return True
    if str(chat_id) not in state.accepted:
        return False
    expiry = sub_expiry(chat_id)
    if expiry is None:
        return False
    return expiry == 0 or expiry > time.time()


def format_match(m) -> str:
    filters = FILTERS if m.kind == "bullish" else FILTERS_BEARISH
    lines = [f"*{m.symbol}* — {m.score}/{m.total_filters} — {fmt_price(m.price)}"]
    for key, (name, _) in filters.items():
        mark = "✅" if key in m.matched else "❌"
        lines.append(f"  {mark} {name}: {m.details.get(key, '-')}")
    if m.options_text:
        lines.append(m.options_text)
    if m.sentiment_text:
        lines.append(f"  💬 وجهة نظر البوت (ملخص لما يُتداول من أخبار وآراء):\n  {m.sentiment_text}")
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
            approx = "≈" if c.get("estimated") else ""
            lines.append(
                f"    {i}) تنفيذ {c['strike']:.2f}$ • ينتهي {c['expiry']}"
                f" ({c['days']} يوم) • بريميوم {approx}{c['premium']:.2f}$"
                f" = {approx}{c['premium'] * 100:.0f}$/عقد"
            )
    if any(c.get("estimated") for side in ("call", "put") for c in picks.get(side) or []):
        lines.append("  (≈ آخر سعر تداول — سوق الأوبشنز مغلق الآن)")
    return "\n".join(lines)


def format_cheap_picks(symbol: str, price: float, picks: dict) -> str:
    """One /cheapoptions result block: symbol + its qualifying contracts."""
    lines = [f"*{symbol}* — {fmt_price(price)}"]
    for side in ("call", "put"):
        contracts = picks.get(side) or []
        if not contracts:
            continue
        lines.append(f"  {SIDE_LABELS[side]}:")
        for c in contracts:
            approx = "≈" if c.get("estimated") else ""
            lines.append(
                f"    تنفيذ {c['strike']:.2f}$ • ينتهي {c['expiry']}"
                f" ({c['days']} يوم) • بريميوم {approx}{c['premium']:.2f}$"
                f" = {approx}{c['premium'] * 100:.0f}$/عقد"
            )
    return "\n".join(lines)


async def attach_options(matches):
    """Fill options_text on each match."""
    if not config.OPTIONS_ENABLED:
        return
    for m in matches:
        if m.options_text:
            continue
        no_options_line = "  📊 لا يوجد أوبشن لهذا السهم"
        try:
            picks = await asyncio.to_thread(options.best_options, m.symbol, m.price)
            m.options_text = format_options(picks) or no_options_line
        except options.OptionsFetchError:
            log.warning("Options fetch failed for %s (both providers)", m.symbol)
            m.options_text = no_options_line
        except Exception:
            log.exception("Options lookup failed for %s", m.symbol)
            m.options_text = no_options_line


async def attach_sentiment(matches):
    """Fill sentiment_text on each match: news + StockTwits merged into one
    short paragraph by Gemini. Silently skipped (no key, no data, or the
    call failing) — never blocks the rest of the alert."""
    if not config.SENTIMENT_ENABLED or not config.GEMINI_API_KEY:
        return
    for m in matches:
        if m.sentiment_text:
            continue
        try:
            summary = await asyncio.to_thread(sentiment.get_sentiment_summary, m.symbol)
        except Exception:
            log.exception("Sentiment summary failed for %s", m.symbol)
            summary = None
        if summary:
            m.sentiment_text = summary


CHART_CAPTION_LIMIT = 1024  # Telegram's hard cap on photo captions


async def attach_charts(matches):
    """Fill chart_png on each match, then drop the OHLCV frame — it only
    exists to render the chart and must not linger in memory afterward."""
    if not config.CHART_ENABLED:
        return
    for m in matches:
        if m.chart_df is None:
            continue
        try:
            m.chart_png = await asyncio.to_thread(
                chart.render_chart, m.symbol, m.chart_df, m.details, m.kind)
        except Exception:
            log.exception("Chart render failed for %s", m.symbol)
        m.chart_df = None


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
    return [c + f"\n\n{ALERT_FOOTER}" for c in chunks]


async def handle_expired_subscribers(app: Application):
    """Remove and notify subscribers whose paid term has lapsed. Called once
    per scan cycle, independent of whether alerts use text or photos."""
    now = time.time()
    for chat_id in list(state.subscribers):
        expiry = sub_expiry(chat_id)
        if expiry and expiry != 0 and expiry <= now:
            state.subscribers.discard(chat_id)
            state.approved.pop(str(chat_id), None)
            state.save()
            try:
                await app.bot.send_message(
                    chat_id,
                    "⏰ انتهت مدة اشتراكك وتوقفت التنبيهات. "
                    f"للتجديد تواصل مع {config.SUBSCRIBE_CONTACT}.")
            except Exception:
                log.warning("Expiry notice to %s failed", chat_id)


async def broadcast(app: Application, text: str):
    for chat_id in list(state.subscribers):
        if not eligible(chat_id):
            continue
        try:
            await app.bot.send_message(chat_id, text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            log.exception("Send to %s failed", chat_id)


async def broadcast_photo(app: Application, photo: bytes, caption: str):
    for chat_id in list(state.subscribers):
        if not eligible(chat_id):
            continue
        try:
            await app.bot.send_photo(chat_id, photo=io.BytesIO(photo), caption=caption,
                                     parse_mode=ParseMode.MARKDOWN)
        except Exception:
            log.exception("Send photo to %s failed", chat_id)


# ------------------------------------------------------------------ scans

async def send_matches(app, to_send, hot: bool = False):
    """Push each match as its own message: a chart photo (when available)
    carrying the full detail as its caption, or plain text otherwise —
    Telegram caps photo captions at 1024 chars, so an overlong detail block
    (rare, e.g. many option picks) falls back to a short caption + full text.
    """
    await attach_options(to_send)
    await attach_charts(to_send)
    await attach_sentiment(to_send)
    await asyncio.to_thread(performance.track_alerts, to_send)
    flame = "🔥 " if hot else ""
    for m in to_send:
        header = f"{flame}{ALERT_HEADERS[m.kind]} — {dt.datetime.now(NY):%H:%M} ET"
        perf_line = await asyncio.to_thread(performance.compact_summary, m.kind)
        text = f"{header}\n\n{format_match(m)}\n\n{ALERT_FOOTER}"
        if perf_line:
            text += f"\n{perf_line}"
        if not m.chart_png:
            await broadcast(app, text)
        elif len(text) <= CHART_CAPTION_LIMIT:
            await broadcast_photo(app, m.chart_png, text)
        else:
            await broadcast_photo(app, m.chart_png,
                                  f"{header}\n\n*{m.symbol}* — {m.score}/{m.total_filters}")
            await broadcast(app, text)


async def do_scan(app: Application, only_changes: bool, notify_empty: bool):
    """Scan batch by batch, pushing each matching stock the moment it's found."""
    global hotlist
    if scan_lock.locked():
        log.info("Scan already running; skipping")
        return
    async with scan_lock:
        await handle_expired_subscribers(app)
        started = dt.datetime.now(NY)
        # No US stock can move over the weekend/a market holiday, so skip
        # the whole scan and save the requests.
        paused = market_calendar.scan_paused()
        if paused:
            full_pass, stock_symbols = False, []
        else:
            full_pass, stock_symbols = await asyncio.to_thread(universe.stock_scan_list)
        log.info("Scan started (paused=%s, full_pass=%s, %d stocks)",
                 paused, full_pass, len(stock_symbols))
        stats = engine.new_stats(len(stock_symbols))
        matched = {"bullish": 0, "bearish": 0}
        sent_count = 0
        new_hot: set[str] = set()
        qualified: list[str] = []

        for batch in engine.make_batches(stock_symbols):
            result, delta = await scan_batch_async(batch)
            merge_stats(stats, delta)
            for m in result.matches:
                matched[m.kind] += 1
            new_hot.update(result.hot)
            qualified.extend(result.liquid)
            to_send = state.fresh_matches(result.matches) if only_changes else result.matches
            state.record(result.matches)
            if to_send:
                sent_count += len(to_send)
                await send_matches(app, to_send)
                state.save()  # crash-safe: never re-alert what was already sent
            throttle.report(result.data_ratio)
            # Always pace batches; unpaced cycles crashed the container
            await asyncio.sleep(max(throttle.delay, config.BATCH_INTERVAL_SECONDS))

        if full_pass and qualified:
            await asyncio.to_thread(universe.save_qualified, qualified)
        if paused:
            # Keep the prior hot list so the fast lane resumes instantly when
            # trading reopens instead of waiting for a fresh full pass.
            new_hot |= hotlist
        hotlist = new_hot
        state.prune()
        state.save()
        gc.collect()  # drop per-cycle DataFrames before the next cycle starts
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        log.info("Scan done: matched=%s sent=%d hot=%d peak_rss=%.0fMB stats=%s",
                 matched, sent_count, len(hotlist), rss_mb, stats)

        if paused:
            breakdown = "📈 الأسهم: متوقف مؤقتاً (نهاية أسبوع/عطلة رسمية)"
        else:
            breakdown = (f"📈 صعود: {matched['bullish']} مطابق • "
                        f"📉 هبوط: {matched['bearish']} مطابق — من {stats['liquid']} مفحوص")

        if sent_count == 0 and notify_empty:
            await broadcast(
                app,
                f"🔎 اكتمل المسح ({started:%H:%M} ET) — لا إشارات تحقق الحد "
                f"الأدنى من الفلاتر.\n{breakdown}",
            )
        elif not only_changes:
            await broadcast(app, f"✅ اكتمل المسح:\n{breakdown}")


async def do_hot_scan(app: Application):
    """Fast lane: re-check near-signal symbols (>=2 filters) every couple of
    minutes so a setup completing between full cycles is caught immediately."""
    if not hotlist or hot_lock.locked() or market_calendar.scan_paused():
        return
    if throttle.delay >= 60:
        return  # Yahoo is pushing back; don't add fast-lane pressure
    async with hot_lock:
        symbols = sorted(hotlist)[:config.HOTLIST_MAX]
        for batch in engine.make_batches(symbols):
            result, _ = await scan_batch_async(batch)
            throttle.report(result.data_ratio)
            to_send = state.fresh_matches(result.matches)
            if not to_send:
                continue
            state.record(result.matches)
            await send_matches(app, to_send, hot=True)
            state.save()


async def hot_job(context: ContextTypes.DEFAULT_TYPE):
    if state.subscribers:
        await do_hot_scan(context.application)
    delay = (config.NIGHT_HOTLIST_INTERVAL_SECONDS if market_calendar.is_night_hours()
             else config.HOTLIST_INTERVAL_SECONDS)
    context.application.job_queue.run_once(hot_job, when=delay)


async def scan_loop_job(context: ContextTypes.DEFAULT_TYPE):
    # Self-rescheduling: each run queues the next one after it finishes, so a
    # new cycle starts SCAN_PAUSE_SECONDS after the previous one ends (not a
    # fixed wall-clock interval) — and that gap widens overnight to save
    # compute/requests during the quietest hours. The dedup layer ensures
    # only new or changed signals are ever sent regardless of pace.
    if state.subscribers:
        await do_scan(context.application, only_changes=True, notify_empty=False)
    delay = (config.NIGHT_SCAN_PAUSE_SECONDS if market_calendar.is_night_hours()
             else config.SCAN_PAUSE_SECONDS)
    context.application.job_queue.run_once(scan_loop_job, when=delay)


async def performance_job(context: ContextTypes.DEFAULT_TYPE):
    """Settle any due performance checks (pure price lookups, cheap and
    independent of subscriber count or market hours)."""
    await asyncio.to_thread(performance.resolve_due)


# --------------------------------------------------------------- commands

WELCOME = (
    "أهلاً بك في بوت المسح الفني للسوق الأمريكي 📊\n\n"
    "تفحص الخدمة كل الأسهم الأمريكية بشكل متواصل (فريم الساعة) وترصد اتجاهين:\n\n"
    f"📈 *احتمال صعود* — تحقق {config.FILTERS_REQUIRED} شروط من 4:\n"
    "1️⃣ السعر عند الحد السفلي لبولينجر باند\n"
    "2️⃣ RSI أقل من 30 (تشبع بيعي)\n"
    "3️⃣ السعر عند منطقة دعم\n"
    "4️⃣ نموذج وتد هابط\n\n"
    f"📉 *احتمال هبوط (تشبع شرائي)* — تحقق {config.BEARISH_FILTERS_REQUIRED} شروط من 5:\n"
    "1️⃣ السعر عند الحد العلوي لبولينجر باند\n"
    "2️⃣ RSI أكبر من 70 (تشبع شرائي)\n"
    "3️⃣ السعر عند منطقة مقاومة\n"
    "4️⃣ نموذج وتد صاعد\n"
    "5️⃣ تقاطع MACD هابط\n\n"
    "قبل تفعيل الخدمة يجب قراءة إخلاء المسؤولية التالي والموافقة عليه:"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.MARKDOWN)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ قرأت إخلاء المسؤولية وأوافق عليه",
                             callback_data="accept_disclaimer")
    ]])
    await update.message.reply_text(DISCLAIMER, parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=keyboard)
    log.info("Start from chat %s", chat_id)


async def on_accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    chat_id = query.message.chat.id
    state.accepted[str(chat_id)] = time.time()
    await query.answer("تم تسجيل موافقتك")
    if eligible(chat_id):
        state.subscribers.add(chat_id)
        state.save()
        await query.message.reply_text(
            "✅ تم تسجيل موافقتك وتفعيل اشتراكك — ستصلك الإشارات الجديدة تلقائياً.\n"
            "الأوامر: /scan مسح فوري • /status الحالة • /stop إيقاف التنبيهات")
    else:
        state.save()
        await query.message.reply_text(
            "✅ تم تسجيل موافقتك على إخلاء المسؤولية.\n\n"
            "الخدمة بـ*اشتراك مدفوع*. للاشتراك تواصل مع "
            f"{config.SUBSCRIBE_CONTACT} وأرسل له رقم معرّفك التالي:\n"
            f"`{chat_id}`\n\n"
            "سيصلك إشعار فور تفعيل اشتراكك.",
            parse_mode=ParseMode.MARKDOWN)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state.subscribers.discard(update.effective_chat.id)
    state.save()
    await update.message.reply_text("تم إيقاف التنبيهات. أرسل /start لإعادة التفعيل.")


async def require_subscription(update: Update) -> bool:
    chat_id = update.effective_chat.id
    if eligible(chat_id):
        return True
    if str(chat_id) not in state.accepted:
        await update.message.reply_text(
            "أرسل /start أولاً للاطلاع على إخلاء المسؤولية والموافقة عليه.")
    else:
        await update.message.reply_text(
            f"هذه الخدمة باشتراك مدفوع. للاشتراك تواصل مع {config.SUBSCRIBE_CONTACT} "
            f"وأرسل له معرّفك: {chat_id}")
    return False


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_subscription(update):
        return
    if scan_lock.locked():
        await update.message.reply_text("⏳ يوجد مسح قيد التنفيذ حالياً، انتظر انتهاءه.")
        return
    state.subscribers.add(update.effective_chat.id)
    state.save()
    await update.message.reply_text(
        "🔎 بدأ المسح اليدوي لكل الأسهم الأمريكية... "
        "سأرسل كل إشارة فور اكتشافها، ثم رسالة عند اكتمال المسح "
        "(المسح الكامل يستغرق 15-40 دقيقة)."
    )
    await do_scan(context.application, only_changes=False, notify_empty=True)


async def _reply_match(update: Update, header: str, m):
    """Send one Match's chart+detail as a private reply (never broadcast)."""
    text = f"{header}\n\n{format_match(m)}\n\n{ALERT_FOOTER}"
    if not m.chart_png:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    elif len(text) <= CHART_CAPTION_LIMIT:
        await update.message.reply_photo(photo=io.BytesIO(m.chart_png), caption=text,
                                         parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_photo(
            photo=io.BytesIO(m.chart_png),
            caption=f"{header}\n\n*{m.symbol}* — {m.score}/{m.total_filters}",
            parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/check <symbol> — fetch a stock on demand with the same additions as a
    real alert (chart, options, وجهة نظر البوت), for both directions, replied
    privately to whoever asked. Not a real alert: no performance tracking.

    Tapping the command from Telegram's own "/" suggestion list sends it
    immediately with no way to type a symbol alongside it — a Telegram
    client behavior, not something a bot can override. So when /check
    arrives with no argument, we ask for the symbol as a follow-up message
    instead of forcing the user to retype the whole command by hand.
    """
    if not await require_subscription(update):
        return
    if not context.args:
        pending_check[update.effective_chat.id] = time.time()
        await update.message.reply_text(
            "أرسل الآن رمز السهم فقط في رسالة منفصلة (مثال: AAPL)\n"
            "أو اكتب الأمر كاملاً دفعة واحدة: /check AAPL")
        return
    await _do_check(update, context, context.args[0].upper())


async def _do_check(update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str):
    await update.message.reply_text(f"⏳ يجلب بيانات {symbol}...")
    try:
        frames = await asyncio.to_thread(data_mod.fetch_batch, [symbol])
    except Exception:
        log.exception("Check fetch failed for %s", symbol)
        frames = {}
    df = frames.get(symbol)
    if df is None:
        await update.message.reply_text(
            f"⚠️ تعذر جلب بيانات لسهم {symbol}. تأكد من صحة الرمز.")
        return

    matches = await asyncio.to_thread(engine.evaluate_symbol, symbol, df)
    await attach_options(matches)
    await attach_charts(matches)
    await attach_sentiment(matches)
    for m in matches:
        await _reply_match(update, CHECK_HEADERS[m.kind], m)


CHECK_PENDING_TTL = 180  # seconds a "waiting for a symbol" prompt stays active


async def handle_pending_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Follow-up to a bare /check: the next plain-text message from that chat
    (within CHECK_PENDING_TTL) is treated as the symbol. Any other free text,
    or one that arrives too late, is silently ignored — the bot has no
    general chat feature."""
    chat_id = update.effective_chat.id
    asked_at = pending_check.pop(chat_id, None)
    if asked_at is None or time.time() - asked_at > CHECK_PENDING_TTL:
        return
    text = (update.message.text or "").strip()
    if not text:
        return
    await _do_check(update, context, text.split()[0].upper())


async def cmd_cheap_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cheapoptions [max_contract_price] — scan the current qualified list
    (the same liquid symbols the regular cycle scans) for stocks with at
    least one reliable call/put contract at or under the price cap (default
    50$, meaning premium <= 0.50$/share since a contract is 100 shares).
    Replies privately; can take several minutes (one options-chain fetch per
    liquid symbol — there is no batched cross-symbol options API)."""
    if not await require_subscription(update):
        return
    if cheap_options_lock.locked():
        await update.message.reply_text("⏳ يوجد بحث آخر عن عقود رخيصة قيد التنفيذ، انتظر انتهاءه.")
        return
    try:
        max_contract = float(context.args[0]) if context.args else config.CHEAP_OPTION_DEFAULT_MAX
        if max_contract <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "الصيغة: /cheapoptions [الحد الأقصى لسعر العقد بالدولار]\n"
            "مثال: /cheapoptions 50")
        return
    max_premium = max_contract / 100.0

    symbols = await asyncio.to_thread(universe.load_qualified)
    if not symbols:
        await update.message.reply_text(
            "⚠️ القائمة المؤهلة غير جاهزة بعد (تُبنى مع أول دورة مسح كاملة). "
            "جرّب /scan أولاً ثم أعد المحاولة بعد قليل.")
        return

    async with cheap_options_lock:
        status = await update.message.reply_text(
            f"🔎 يبحث عن عقود لا يتجاوز سعرها {max_contract:.0f}$ ضمن {len(symbols)} سهم "
            "من القائمة المؤهلة... قد يستغرق هذا عدة دقائق."
        )
        found = []
        processed = 0
        for batch in engine.make_batches(symbols):
            try:
                frames = await asyncio.to_thread(data_mod.fetch_batch, batch)
            except Exception:
                log.exception("cheapoptions batch download failed (%s..)", batch[0])
                continue
            for sym, df in frames.items():
                if not data_mod.passes_liquidity(df):
                    continue
                price = float(df["Close"].iloc[-1])
                try:
                    picks = await asyncio.to_thread(
                        options.find_cheap_contracts, sym, price, max_premium)
                except options.OptionsFetchError:
                    picks = {"call": [], "put": []}
                if picks["call"] or picks["put"]:
                    found.append((sym, price, picks))
                processed += 1
                if processed % config.CHEAP_OPTIONS_PROGRESS_EVERY == 0:
                    try:
                        await status.edit_text(
                            f"🔎 جارٍ البحث... {processed}/{len(symbols)} سهم "
                            f"({len(found)} نتيجة حتى الآن)")
                    except Exception:
                        pass
                await asyncio.sleep(config.CHEAP_OPTIONS_PACE_SECONDS)

        if not found:
            await update.message.reply_text(
                f"لم يُعثر على أسهم بعقود لا يتجاوز سعرها {max_contract:.0f}$ "
                "ضمن القائمة الحالية.")
            return

        header = f"📊 أسهم بعقود لا يتجاوز سعرها {max_contract:.0f}$ للعقد ({len(found)} سهم):"
        chunks, current = [], header
        for sym, price, picks in found:
            block = "\n\n" + format_cheap_picks(sym, price, picks)
            if len(current) + len(block) > MSG_LIMIT:
                chunks.append(current)
                current = format_cheap_picks(sym, price, picks)
            else:
                current += block
        chunks.append(current)
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)


async def cmd_disclaimer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(DISCLAIMER, parse_mode=ParseMode.MARKDOWN)


# --------------------------------------------------------- admin commands

def _fmt_expiry(expiry: float) -> str:
    if expiry == 0:
        return "مدى الحياة"
    return dt.datetime.fromtimestamp(expiry, NY).strftime("%Y-%m-%d")


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/approve <chat_id> [days] — activate a paying subscriber (0 = lifetime)."""
    if not is_admin(update.effective_chat.id):
        return
    try:
        target = int(context.args[0])
        days = int(context.args[1]) if len(context.args) > 1 else config.DEFAULT_SUB_DAYS
    except (IndexError, ValueError):
        await update.message.reply_text(
            "الصيغة: /approve <chat_id> [عدد الأيام]\n"
            "مثال: /approve 123456789 30 — أو 0 أيام لاشتراك دائم")
        return
    expiry = 0 if days == 0 else time.time() + days * 86400
    state.approved[str(target)] = expiry
    if str(target) in state.accepted:
        state.subscribers.add(target)
    state.save()
    await update.message.reply_text(
        f"✅ تم تفعيل {target} حتى: {_fmt_expiry(expiry)}")
    try:
        await context.bot.send_message(
            target,
            f"🎉 تم تفعيل اشتراكك حتى: {_fmt_expiry(expiry)}\n"
            + ("ستصلك الإشارات تلقائياً." if str(target) in state.accepted
               else "أرسل /start للموافقة على إخلاء المسؤولية وبدء الاستقبال."))
    except Exception:
        await update.message.reply_text(
            "⚠️ لم أستطع مراسلته (ربما لم يبدأ محادثة مع البوت بعد).")


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/revoke <chat_id> — cancel a subscription."""
    if not is_admin(update.effective_chat.id):
        return
    try:
        target = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("الصيغة: /revoke <chat_id>")
        return
    state.approved.pop(str(target), None)
    state.subscribers.discard(target)
    state.save()
    await update.message.reply_text(f"🚫 أُلغي اشتراك {target}")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reset — wipe scan memory (dedup + hot list + qualified list) so the
    next cycle is a fresh full pass and everything alerts again (admin)."""
    global hotlist
    if not is_admin(update.effective_chat.id):
        return
    state.last_alerts = {}
    state.save()
    hotlist = set()
    try:
        os.remove(config.QUALIFIED_FILE)
    except FileNotFoundError:
        pass
    await update.message.reply_text(
        "🧹 مُسحت ذاكرة المسح بالكامل (التنبيهات السابقة + القائمة الساخنة + "
        "القائمة المؤهلة).\nالدورة القادمة ستكون دورة كاملة على كل الأسهم "
        "وستصلك كل الإشارات الحالية من جديد خلال دقائق. "
        "المشتركون وموافقاتهم لم يتأثروا.")
    log.info("Scan memory reset by admin")


async def cmd_subs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/subs — list active subscriptions (admin)."""
    if not is_admin(update.effective_chat.id):
        return
    if not state.approved:
        await update.message.reply_text("لا يوجد مشتركون مفعّلون.")
        return
    lines = ["المشتركون المفعّلون:"]
    for cid, expiry in sorted(state.approved.items()):
        active = "🟢" if eligible(int(cid)) else "🔴"
        accepted = "✅" if cid in state.accepted else "⬜"
        lines.append(f"{active} {cid} — حتى {_fmt_expiry(expiry)} — الإخلاء: {accepted}")
    await update.message.reply_text("\n".join(lines))


PREVIEW_BANNER = (
    "🧪 *مثال توضيحي فقط — ليس تنبيهاً حقيقياً ولا توصية*\n"
    "هذا نموذج لشكل الرسالة القادمة، مبني على بيانات حقيقية لسهم مذكور هنا "
    "لغرض العرض فقط — بصرف النظر عن نتيجة فلاتره الفعلية حالياً.\n"
)


async def cmd_preview_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/previewalert [ticker] — admin only: broadcast a clearly-labeled
    example alert (real market data, real chart/options/sentiment) to every
    current subscriber, so they see the new format without it being mistaken
    for a real signal. Never touches performance tracking (not a real alert)."""
    if not is_admin(update.effective_chat.id):
        return
    symbol = context.args[0].upper() if context.args else "AAPL"
    await update.message.reply_text(f"⏳ يجهّز مثالاً توضيحياً لسهم {symbol}...")
    try:
        frames = await asyncio.to_thread(data_mod.fetch_batch, [symbol])
    except Exception:
        log.exception("Preview fetch failed for %s", symbol)
        frames = {}
    df = frames.get(symbol)
    if df is None:
        await update.message.reply_text(
            f"⚠️ تعذر جلب بيانات لسهم {symbol}. جرّب رمزاً آخر، مثل: /previewalert MSFT")
        return

    matches = await asyncio.to_thread(engine.evaluate_symbol, symbol, df)
    await attach_options(matches)
    await attach_charts(matches)
    await attach_sentiment(matches)

    for m in matches:
        perf_line = await asyncio.to_thread(performance.compact_summary, m.kind)
        header = f"{ALERT_HEADERS[m.kind]}"
        body = f"{PREVIEW_BANNER}\n{header}\n\n{format_match(m)}\n\n{ALERT_FOOTER}"
        if perf_line:
            body += f"\n{perf_line}"

        if not m.chart_png:
            await broadcast(context.application, body)
        elif len(body) <= CHART_CAPTION_LIMIT:
            await broadcast_photo(context.application, m.chart_png, body)
        else:
            await broadcast_photo(context.application, m.chart_png,
                                  f"{PREVIEW_BANNER}\n{header}")
            await broadcast(context.application, body)

    await update.message.reply_text(
        f"✅ تم إرسال المثال التوضيحي (صعود وهبوط) لكل المشتركين ({len(state.subscribers)}).")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_now = "مفتوح ✅" if market_is_open() else "مغلق ❌"
    scanning = "نعم ⏳" if scan_lock.locked() else "لا"
    chat_id = update.effective_chat.id
    if is_admin(chat_id):
        sub_line = "أنت المشرف 👑"
    elif eligible(chat_id):
        sub_line = f"مفعّل حتى {_fmt_expiry(sub_expiry(chat_id))} ✅"
    else:
        sub_line = "غير مفعّل ❌"
    qualified = universe.load_qualified()
    universe_line = (f"قائمة مؤهلة ({len(qualified)} سهم)" if qualified
                     else "دورة تأهيل كاملة (كل الأسهم)")
    throttle_line = (f"نشطة مؤقتاً ({throttle.delay:.0f} ثانية بين الدفعات)"
                     if throttle.active else "غير نشطة")
    pace_line = ("ليلي 🌙 (بطيء لتوفير الاستهلاك)" if market_calendar.is_night_hours()
                else "نهاري (عادي)")
    stock_scan_line = ("متوقف 🚫 (نهاية أسبوع/عطلة رسمية)"
                       if market_calendar.scan_paused() else "نشط ✅")
    await update.message.reply_text(
        f"اشتراكك: {sub_line}\n"
        f"السوق الأمريكي الآن: {open_now}\n"
        f"مسح الأسهم: {stock_scan_line}\n"
        f"وتيرة المسح: {pace_line}\n"
        f"نطاق الأسهم: {universe_line}\n"
        f"القائمة الساخنة 🔥: {len(hotlist)} رمز (فحص كل {config.HOTLIST_INTERVAL_SECONDS // 60} دقيقة)\n"
        f"التهدئة التلقائية: {throttle_line}\n"
        f"مسح قيد التنفيذ: {scanning}\n"
        f"عدد المشتركين: {len(state.subscribers)}\n"
        f"إشارات في الذاكرة: {len(state.last_alerts)}\n"
        f"شرط الصعود: {config.FILTERS_REQUIRED}/4 فلاتر • "
        f"شرط الهبوط: {config.BEARISH_FILTERS_REQUIRED}/5 فلاتر • الفريم: {config.INTERVAL}"
    )


async def cmd_performance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Public track record: each alert's return vs SPY (pure price math)."""
    text = await asyncio.to_thread(performance.summary_text)
    await update.message.reply_text(text)


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


PUBLIC_COMMANDS = [
    BotCommand("start", "التسجيل والموافقة على إخلاء المسؤولية"),
    BotCommand("scan", "مسح فوري لكل السوق"),
    BotCommand("check", "فحص سهم محدد: /check <رمز>"),
    BotCommand("cheapoptions", "بحث عن أسهم بعقود رخيصة: /cheapoptions [سعر]"),
    BotCommand("status", "حالة البوت واشتراكك"),
    BotCommand("disclaimer", "عرض إخلاء المسؤولية"),
    BotCommand("performance", "سجل أداء الإشارات مقابل السوق"),
    BotCommand("stop", "إيقاف التنبيهات"),
]
ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand("approve", "تفعيل مشترك: /approve <id> <أيام>"),
    BotCommand("revoke", "إلغاء اشتراك: /revoke <id>"),
    BotCommand("subs", "قائمة المشتركين"),
    BotCommand("reset", "مسح ذاكرة المسح والبدء من جديد"),
    BotCommand("previewalert", "إرسال مثال توضيحي لشكل التنبيه لكل المشتركين"),
]


async def post_init(app: Application):
    """Telegram command menus by scope: everyone sees the public commands;
    the admin commands appear only in the admin's own chat menu."""
    try:
        await app.bot.set_my_commands(PUBLIC_COMMANDS,
                                      scope=BotCommandScopeDefault())
        if config.ADMIN_CHAT_ID:
            await app.bot.set_my_commands(
                ADMIN_COMMANDS,
                scope=BotCommandScopeChat(chat_id=config.ADMIN_CHAT_ID))
        log.info("Command menus set (admin scope: %s)", bool(config.ADMIN_CHAT_ID))
    except Exception:
        log.exception("Failed to set command menus")


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN environment variable")
    app = (Application.builder().token(config.BOT_TOKEN)
           .post_init(post_init).build())
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(CommandHandler("cheapoptions", cmd_cheap_options))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("disclaimer", cmd_disclaimer))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("subs", cmd_subs))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("performance", cmd_performance))
    app.add_handler(CommandHandler("previewalert", cmd_preview_alert))
    app.add_handler(CallbackQueryHandler(on_accept, pattern="^accept_disclaimer$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_pending_check))
    # Self-rescheduling jobs (see scan_loop_job/hot_job) so the pace can
    # widen at night; just kick off the first run of each here.
    app.job_queue.run_once(scan_loop_job, when=10)
    app.job_queue.run_once(hot_job, when=90)
    app.job_queue.run_repeating(performance_job,
                                interval=config.PERFORMANCE_CHECK_INTERVAL_SECONDS, first=120)
    log.info("Bot starting (polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
