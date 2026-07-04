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
                          ContextTypes)

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

ALERT_FOOTER = "⚠️ تحليل فني آلي — ليس توصية بشراء أو بيع"

DISCLAIMER = (
    "⚠️ *إخلاء مسؤولية — يُرجى القراءة بعناية*\n\n"
    "هذه الخدمة *أداة تحليل فني آلية* تعتمد على مؤشرات رياضية "
    "(بولينجر باند، مؤشر القوة النسبية RSI، مستويات الدعم، الأنماط السعرية) "
    "لرصد الحالات الفنية في سوق الأسهم الأمريكية والعملات الرقمية، "
    "وعرض بيانات عقود الخيارات الأنشط سيولةً وفق معايير آلية بحتة.\n\n"
    "1️⃣ ما تقدمه هذه الخدمة *ليس توصية ولا مشورة استثمارية* ولا دعوة أو تحريضاً "
    "على شراء أو بيع أي ورقة مالية أو أصل رقمي أو عقد مشتقات، ولا يجوز تفسيره "
    "أو الاعتماد عليه بهذه الصفة.\n\n"
    "2️⃣ الخدمة ومشغّلها *غير مرخصين من هيئة السوق المالية في المملكة العربية "
    "السعودية* ولا من أي جهة تنظيمية أخرى لمزاولة أعمال الأوراق المالية أو تقديم "
    "المشورة الاستثمارية، ولا تقدم الخدمة أي عمل من الأعمال الخاضعة للترخيص.\n\n"
    "3️⃣ المؤشرات الفنية أدوات إحصائية *قد تخطئ*، والنتائج السابقة لا تضمن "
    "الأداء المستقبلي، والبيانات المعروضة قد يشوبها تأخير أو خطأ من مصادرها.\n\n"
    "4️⃣ التداول في الأسهم وعقود الخيارات والعملات الرقمية *ينطوي على مخاطر "
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
            approx = "≈" if c.get("estimated") else ""
            lines.append(
                f"    {i}) تنفيذ {c['strike']:.2f}$ • ينتهي {c['expiry']}"
                f" ({c['days']} يوم) • بريميوم {approx}{c['premium']:.2f}$"
                f" = {approx}{c['premium'] * 100:.0f}$/عقد"
            )
    if any(c.get("estimated") for side in ("call", "put") for c in picks.get(side) or []):
        lines.append("  (≈ آخر سعر تداول — سوق الأوبشنز مغلق الآن)")
    return "\n".join(lines)


async def attach_options(matches):
    """Fill options_text on stock matches (coins have no listed options)."""
    if not config.OPTIONS_ENABLED:
        return
    for m in matches:
        if m.is_crypto or m.options_text:
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


async def broadcast(app: Application, text: str):
    for chat_id in list(state.subscribers):
        expiry = sub_expiry(chat_id)
        if not eligible(chat_id):
            # Notify once when a subscription lapses, then stop sending
            if expiry and expiry != 0 and expiry <= time.time():
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
            continue
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

WELCOME = (
    "أهلاً بك في بوت المسح الفني للسوق الأمريكي 📊\n\n"
    "تفحص الخدمة كل الأسهم الأمريكية 📈 وأهم 100 عملة رقمية 🪙 بشكل متواصل "
    "(فريم الساعة) وترصد الحالات الفنية التي تحقق "
    f"{config.FILTERS_REQUIRED} شروط من 4:\n"
    "1️⃣ السعر عند الحد السفلي لبولينجر باند\n"
    "2️⃣ RSI أقل من 30 (تشبع بيعي)\n"
    "3️⃣ السعر عند منطقة دعم\n"
    "4️⃣ نموذج وتد هابط\n\n"
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
        "🔎 بدأ المسح اليدوي (العملات الرقمية أولاً ثم كل الأسهم الأمريكية)... "
        "سأرسل كل إشارة فور اكتشافها، ثم رسالة عند اكتمال المسح "
        "(المسح الكامل يستغرق 15-40 دقيقة)."
    )
    await do_scan(context.application, only_changes=False, notify_empty=True)


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


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    open_now = "مفتوح ✅" if market_is_open() else "مغلق ❌"
    scanning = "نعم ⏳" if scan_lock.locked() else "لا"
    if config.CRYPTO_ENABLED:
        crypto_line = f"مفعّلة 🪙 ({len(universe.get_crypto_universe())} عملة)"
    else:
        crypto_line = "معطلة"
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
    await update.message.reply_text(
        f"اشتراكك: {sub_line}\n"
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


PUBLIC_COMMANDS = [
    BotCommand("start", "التسجيل والموافقة على إخلاء المسؤولية"),
    BotCommand("scan", "مسح فوري لكل السوق"),
    BotCommand("status", "حالة البوت واشتراكك"),
    BotCommand("disclaimer", "عرض إخلاء المسؤولية"),
    BotCommand("stop", "إيقاف التنبيهات"),
]
ADMIN_COMMANDS = PUBLIC_COMMANDS + [
    BotCommand("approve", "تفعيل مشترك: /approve <id> <أيام>"),
    BotCommand("revoke", "إلغاء اشتراك: /revoke <id>"),
    BotCommand("subs", "قائمة المشتركين"),
    BotCommand("reset", "مسح ذاكرة المسح والبدء من جديد"),
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
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("disclaimer", cmd_disclaimer))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("subs", cmd_subs))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CallbackQueryHandler(on_accept, pattern="^accept_disclaimer$"))
    app.job_queue.run_repeating(scan_loop_job,
                                interval=config.SCAN_PAUSE_SECONDS, first=10)
    app.job_queue.run_repeating(hot_job,
                                interval=config.HOTLIST_INTERVAL_SECONDS, first=90)
    log.info("Bot starting (polling)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
