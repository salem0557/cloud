"""Telegram bot: three fully independent, on-demand scanning modules --
stocks (reversal-up technical setups, point-scored), options (CALL-only,
Black-Scholes probability + EV -- /options now merges three tags in one
unified live stream: general, LEAPS (365+ day), and HEAVY (curated mega/
large/ETF list), each result badged by kind -- see options_module.scan_all),
and crypto (top ~100 coins via Binance public data, point-scored). No
automatic background scanning: every scan runs only when a member sends a
command, and stops on its own after SESSION_TIMEOUT_SECONDS (15 minutes)
or instantly via /stop.

The bot is locked to whichever chat ids were already approved before this
restructure (scanner/state.py's `approved`) -- there is no /approve command
anymore, so no new member can be added from within the bot.

Note: this file stays named bot.py (not main.py) even though it's the
Telegram bot's entry point -- main.py in this repo already belongs to an
unrelated tool (options_scanner/cli.py's CLI entry point).

Run:  TELEGRAM_BOT_TOKEN=xxx python bot.py
"""
import asyncio
import datetime as dt
import io
import logging
import time

from dotenv import load_dotenv

load_dotenv()  # must run before scanner.config reads the environment

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from scanner import (config, crypto_module, golden_module, market_calendar,
                     options_module, positions_module, review_module, signals_db, stocks_module)
from scanner.state import State
from scanner.utils import fmt_price, split_message

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

state = State()
# One session (asyncio Task) + cancel Event per chat, at most -- a chat can
# only have one module running at a time.
sessions: dict[int, asyncio.Task] = {}
cancel_events: dict[int, asyncio.Event] = {}
# "watchlist" | "ticker" -- cmd_stop only hard-cancels ticker sessions (no
# internal checkpoints to drain gracefully); watchlist sessions rely purely
# on cancel_event so their scan loop can return whatever it already found.
session_kind: dict[int, str] = {}
# Last completed scan per chat, for /status -- {"title": str, "count": int, "ts": float}
last_results: dict[int, dict] = {}

FOOTER = "⚠️ تقديرات إحصائية وليست ضمانًا."
NO_RESULTS = "لا توجد فرص تحقق الحد الأدنى حاليًا."


# ------------------------------------------------------------ eligibility

def is_admin(chat_id: int) -> bool:
    return config.ADMIN_CHAT_ID and chat_id == config.ADMIN_CHAT_ID


def sub_expiry(chat_id: int):
    """None = not a member; 0 = lifetime; else unix expiry timestamp."""
    return state.approved.get(str(chat_id))


def eligible(chat_id: int) -> bool:
    """The bot is locked to the fixed roster already in state.approved --
    there is no command left that can grow this roster."""
    if is_admin(chat_id):
        return True
    expiry = sub_expiry(chat_id)
    if expiry is None:
        return False
    return expiry == 0 or expiry > time.time()


async def require_membership(update: Update) -> bool:
    chat_id = update.effective_chat.id
    if eligible(chat_id):
        return True
    await update.message.reply_text(
        "🚫 هذه الخدمة مقفلة على الأعضاء الحاليين فقط ولا يمكن إضافة أعضاء جدد.\n"
        f"إن كنت عضواً سابقاً وتواجه مشكلة تواصل مع {config.SUBSCRIBE_CONTACT}."
    )
    return False


# --------------------------------------------------------------- helpers

async def _send(app, chat_id: int, text: str):
    for chunk in split_message(text):
        try:
            await app.bot.send_message(chat_id, chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            log.exception("Send to %s failed", chat_id)


async def _send_row(app, chat_id: int, row: dict, format_fn):
    """One result = one Telegram message: a chart photo (stocks/crypto) with
    the summary as its caption, or a plain text message (options' table has
    no chart) if there's no chart_png -- never batched together."""
    text = format_fn(row)
    png = row.get("chart_png")
    if not png:
        await _send(app, chat_id, text)
        return
    caption = text if len(text) <= 1024 else text[:1000] + "…"
    try:
        await app.bot.send_photo(chat_id, photo=io.BytesIO(png), caption=caption,
                                 parse_mode=ParseMode.MARKDOWN)
        if len(text) > 1024:
            await _send(app, chat_id, text)  # full text (was truncated in the caption)
    except Exception:
        log.exception("send_photo to %s failed, falling back to text", chat_id)
        await _send(app, chat_id, text)


async def _log_row(section: str, row: dict):
    """Records a qualifying result to signals.db for /review and /stats --
    independent of whether the Telegram send above it succeeded (the
    signal genuinely qualified either way). A logging failure must never
    take down the scan session itself."""
    try:
        await asyncio.to_thread(signals_db.log_signal, section, row)
    except Exception:
        log.exception("Failed to log %s signal for %s", section, row.get("symbol"))


# ------------------------------------------------------- session machinery

async def _run_watchlist_session(chat_id: int, title: str, scan_fn, format_fn, app, section: str):
    """Runs a module's scan() -- an async generator -- and sends each
    qualifying result to Telegram AS SOON AS it's found, instead of
    collecting them and sending a batch at the end. A background timer sets
    cancel_event after SESSION_TIMEOUT_SECONDS (the same signal /stop
    sends); scan_fn's own per-symbol checkpoint notices it and stops
    yielding, same as a manual /stop -- whatever was already sent stays
    sent. A failure here is this module's problem alone -- it never touches
    another module's session.

    Tradeoff: since results are streamed as they're found rather than
    collected and ranked, this is "first N qualifying results in randomized
    scan order", not "best N results across the whole watchlist" (global
    ranking would require scanning everything before sending anything).

    `section` tags every result logged to signals.db (stocks/crypto/
    options/leaps/heavy/golden) -- overridden per-row by row["kind"] when
    present (the merged /options scan tags each row with which of the
    three types -- options/leaps/heavy -- it actually is). See _log_row."""
    cancel_event = asyncio.Event()
    cancel_events[chat_id] = cancel_event
    timed_out = False
    stats: dict = {}

    async def _timeout_setter():
        nonlocal timed_out
        await asyncio.sleep(config.SESSION_TIMEOUT_SECONDS)
        timed_out = True
        cancel_event.set()

    timer = asyncio.create_task(_timeout_setter())
    sent_count = 0
    golden_candidates: list[dict] = []
    try:
        try:
            async for row in scan_fn(cancel_event, stats=stats):
                await _send_row(app, chat_id, row, format_fn)
                await _log_row(row.get("kind", section), row)
                sent_count += 1
                if section == "stocks" and golden_module.stock_qualifies_for_golden(row):
                    golden_candidates.append(row)
        finally:
            timer.cancel()

        last_results[chat_id] = {"title": title, "count": sent_count, "ts": time.time()}
        excluded = stats.get("excluded_bad_data", 0)
        excluded_line = f"⚠️ استُبعد {excluded} عقد لبيانات غير موثوقة" if excluded else ""
        stopped_early = cancel_event.is_set()

        if sent_count == 0:
            if stopped_early and timed_out:
                reason = "⏰ انتهت الجلسة (15 دقيقة) قبل إيجاد أي نتيجة."
            elif stopped_early:
                reason = "⏹️ تم إيقاف الجلسة قبل إيجاد أي نتيجة."
            else:
                reason = NO_RESULTS
            msg = f"{title}\n\n{reason}"
            if excluded_line:
                msg += f"\n\n{excluded_line}"
            await _send(app, chat_id, f"{msg}\n\n{FOOTER}")
            return

        if stopped_early and timed_out:
            closing = f"⏰ {title} — انتهت الجلسة (15 دقيقة)، أُرسل {sent_count} نتيجة أعلاه."
        elif stopped_early:
            closing = f"⏹️ {title} — تم إيقاف الجلسة، أُرسل {sent_count} نتيجة أعلاه."
        else:
            closing = f"✅ {title} — اكتمل الفحص، أُرسل {sent_count} نتيجة."
        footer_msg = (f"{closing}\n\n{excluded_line}\n\n{FOOTER}" if excluded_line
                     else f"{closing}\n\n{FOOTER}")
        await _send(app, chat_id, footer_msg)

        if golden_candidates:
            await _run_golden_pass(chat_id, golden_candidates, app)
    except asyncio.CancelledError:
        await _send(app, chat_id, f"⏹️ {title} — تم إيقاف الجلسة.")
    except Exception:
        log.exception("Session failed for chat %s (%s)", chat_id, title)
        await _send(app, chat_id, f"⚠️ {title} — حدث خطأ غير متوقع أثناء الفحص. جرّب لاحقاً.")
    finally:
        sessions.pop(chat_id, None)
        cancel_events.pop(chat_id, None)
        session_kind.pop(chat_id, None)


async def _run_golden_pass(chat_id: int, golden_candidates: list[dict], app):
    """After a /stocks session, a follow-up check (bounded by however many
    stocks qualified -- /stocks itself has no early-exit cap anymore, no
    scan-session timeout of its own here either) on just the stocks that
    already qualified: does this exact ticker also have a CALL contract
    passing every /options filter right now? A stock with no qualifying
    contract is skipped silently -- same as the LEAPS/HEAVY tags within
    /options do for an unqualified symbol, not an error, most stocks
    simply won't have one."""
    for stock_row in golden_candidates:
        golden_row = await golden_module.check_confluence(stock_row)
        if golden_row is None:
            continue
        await _send(app, chat_id, golden_module.format_golden_result(golden_row))
        await _log_row("golden", golden_row)


async def _run_ticker_session(chat_id: int, symbol: str, app):
    """/options TICKER: a single-symbol lookup (Call فقط)، مستقل عن فحص
    القائمة الكاملة -- نفس آلية المهلة/الإلغاء، رسالة خاصة به."""
    try:
        spot, contracts, error, excluded = await asyncio.wait_for(
            options_module.scan_symbol(symbol), timeout=config.SESSION_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        await _send(app, chat_id, "⏰ انتهت الجلسة، أرسل أمر جديد.")
        return
    except asyncio.CancelledError:
        await _send(app, chat_id, "⏹️ تم إيقاف الجلسة.")
        return
    except Exception:
        log.exception("Ticker session failed for chat %s (%s)", chat_id, symbol)
        await _send(app, chat_id, "⚠️ حدث خطأ غير متوقع أثناء الفحص. جرّب لاحقاً.")
        return
    finally:
        sessions.pop(chat_id, None)
        session_kind.pop(chat_id, None)

    if error:
        await _send(app, chat_id, f"📊 *{symbol}* — {error}")
        return
    last_results[chat_id] = {"title": f"📊 عقود {symbol}", "count": len(contracts), "ts": time.time()}
    excluded_line = f"⚠️ استُبعد {excluded} عقد لبيانات غير موثوقة" if excluded else ""
    if not contracts:
        price_txt = fmt_price(spot) if spot else "-"
        msg = f"📊 *{symbol}* ({price_txt}) — {NO_RESULTS}"
        if excluded_line:
            msg += f"\n\n{excluded_line}"
        await _send(app, chat_id, f"{msg}\n\n{FOOTER}")
    else:
        await _send(app, chat_id, f"📊 عقود *{symbol}* المؤهلة — {len(contracts)}:")
        for row in contracts:
            await _send_row(app, chat_id, row, options_module.format_result)
            await _log_row("options", row)
        await _send(app, chat_id, f"{excluded_line}\n\n{FOOTER}" if excluded_line else FOOTER)


def _start_session(chat_id: int, coro, kind: str) -> asyncio.Task:
    task = asyncio.create_task(coro)
    sessions[chat_id] = task
    session_kind[chat_id] = kind
    return task


def _session_minutes() -> int:
    return config.SESSION_TIMEOUT_SECONDS // 60


# --------------------------------------------------------------- commands

async def cmd_stocks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in sessions:
        await update.message.reply_text("⏳ توجد جلسة قيد التنفيذ بالفعل — أرسل /stop لإيقافها أولاً.")
        return
    await update.message.reply_text(
        f"📈 بدأ فحص وحدة الأسهم ({len(config.STOCKS_WATCHLIST)} سهم)... "
        f"حتى {_session_minutes()} دقيقة أو /stop للإيقاف الفوري.")
    _start_session(chat_id, _run_watchlist_session(
        chat_id, "📈 نتائج فحص الأسهم", stocks_module.scan, stocks_module.format_result,
        context.application, section="stocks"), kind="watchlist")


async def cmd_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/options بلا وسيط: فحص موحّد يجمع ثلاثة أنواع من عقود CALL بتيار حي
    واحد -- عادي (كامل OPTIONS_WATCHLIST)، LEAPS (365+ يوم)، وHEAVY (قائمة
    HEAVY_TICKERS المختارة) -- كل نتيجة تصل بوسم نوعها فور اكتشافها (انظر
    options_module.scan_all/format_any). /options TICKER يبقى فحص سهم واحد
    بمعزل عن ذلك (النوع العادي فقط)."""
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in sessions:
        await update.message.reply_text("⏳ توجد جلسة قيد التنفيذ بالفعل — أرسل /stop لإيقافها أولاً.")
        return
    if context.args:
        symbol = context.args[0].upper()
        await update.message.reply_text(f"⏳ يفحص عقود {symbol} (Call فقط)...")
        _start_session(chat_id, _run_ticker_session(chat_id, symbol, context.application),
                      kind="ticker")
    else:
        await update.message.reply_text(
            f"📊 بدأ فحص الأوبشن الموحّد (Call فقط) — عادي + LEAPS "
            f"({config.LEAPS_DTE_MIN}+ يوم) + HEAVY (Mega/Large/ETF)، "
            f"{len(config.OPTIONS_WATCHLIST)} سهم + {len(config.HEAVY_TICKERS)} رمز مختار... "
            f"حتى {_session_minutes()} دقيقة أو /stop للإيقاف الفوري.")
        _start_session(chat_id, _run_watchlist_session(
            chat_id, "📊 نتائج فحص الأوبشن الموحّد (Call فقط)", options_module.scan_all,
            options_module.format_any, context.application, section="options"), kind="watchlist")


async def cmd_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in sessions:
        await update.message.reply_text("⏳ توجد جلسة قيد التنفيذ بالفعل — أرسل /stop لإيقافها أولاً.")
        return
    await update.message.reply_text(
        f"🪙 بدأ فحص وحدة الكريبتو ({len(config.CRYPTO_WATCHLIST)} عملة)... "
        f"حتى {_session_minutes()} دقيقة أو /stop للإيقاف الفوري.")
    _start_session(chat_id, _run_watchlist_session(
        chat_id, "🪙 نتائج فحص الكريبتو", crypto_module.scan, crypto_module.format_result,
        context.application, section="crypto"), kind="watchlist")


async def cmd_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Scores every signal due at its 7-day/30-day checkpoint against real
    market data and updates signals.db -- open to any approved member
    (the record isn't per-user, it's a shared market log), independent of
    the one-scan-session-per-chat machinery above (/review isn't a scan)."""
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    await update.message.reply_text("📋 جارٍ مراجعة الإشارات المستحقة (7 و30 يوم)...")
    try:
        summary = await review_module.run_review()
    except Exception:
        log.exception("Review failed for chat %s", chat_id)
        await update.message.reply_text("⚠️ حدث خطأ غير متوقع أثناء المراجعة. جرّب لاحقاً.")
        return
    await _send(context.application, chat_id, review_module.format_review_summary(summary))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aggregate performance report over every reviewed signal -- run
    /review first if this comes back empty, it only reports on signals
    that have already crossed a review checkpoint."""
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    try:
        stats = await review_module.compute_stats()
    except Exception:
        log.exception("Stats failed for chat %s", chat_id)
        await update.message.reply_text("⚠️ حدث خطأ غير متوقع أثناء إعداد التقرير. جرّب لاحقاً.")
        return
    await _send(context.application, chat_id, review_module.format_stats_report(stats))


async def cmd_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/track TICKER STRIKE EXPIRY PRICE TYPE -- self-reported: the bot has
    no brokerage integration, it only knows about a position because the
    member typed it in. Scoped per chat_id (see signals_db.py), so this
    never touches another member's positions."""
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    args = context.args
    if len(args) != 5:
        await update.message.reply_text(
            "الصيغة: /track TICKER STRIKE EXPIRY PRICE TYPE\n"
            "مثال: /track T 21 2026-10-16 1.27 call")
        return
    symbol, strike_s, expiry_s, price_s, side = args
    symbol = symbol.upper()
    if side.lower() != "call":
        await update.message.reply_text("🚫 البوت الحالي Call فقط — ما يقدر يتابع مراكز Put.")
        return
    try:
        strike = float(strike_s)
        entry_price = float(price_s)
        expiry_date = dt.date.fromisoformat(expiry_s)
    except ValueError:
        await update.message.reply_text(
            "تعذر فهم الأمر. تأكد إن STRIKE وPRICE أرقام، وEXPIRY بصيغة YYYY-MM-DD.")
        return
    if strike <= 0 or entry_price <= 0:
        await update.message.reply_text("STRIKE وPRICE لازم يكونوا أكبر من صفر.")
        return
    original_dte = (expiry_date - dt.date.today()).days
    if original_dte < 0:
        await update.message.reply_text("⚠️ تاريخ الانتهاء اللي كتبته بالماضي.")
        return

    position_id = await asyncio.to_thread(
        signals_db.add_position, chat_id, symbol, strike, expiry_s, entry_price, original_dte)
    if position_id is None:
        await update.message.reply_text(
            f"⚠️ عندك مركز مفتوح بالفعل على {symbol} {strike:.2f}$ {expiry_s} — "
            f"شغّل /untrack {symbol} أولاً لو تبي تحدّثه.")
        return
    await update.message.reply_text(
        f"✅ تمت متابعة المركز: *{symbol}* {strike:.2f}$ {expiry_s} — "
        f"دخول {fmt_price(entry_price)} ({original_dte} يوم للانتهاء).\n"
        f"مراقبة تلقائية كل ساعة خلال ساعات السوق.",
        parse_mode=ParseMode.MARKDOWN)


async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    rows = await asyncio.to_thread(signals_db.fetch_open_positions, chat_id)
    if not rows:
        await update.message.reply_text("ما عندك مراكز مفتوحة حالياً. استخدم /track لإضافة واحد.")
        return
    await update.message.reply_text(f"📌 مراكزك المفتوحة ({len(rows)}):")
    for row in rows:
        current = await positions_module.reprice(row)
        await _send(context.application, chat_id, positions_module.format_position_line(row, current))


async def cmd_untrack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/untrack TICKER [STRIKE] [EXPIRY] -- STRIKE/EXPIRY only needed to
    disambiguate when a member has more than one open position on the
    same underlying."""
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await update.message.reply_text("الصيغة: /untrack TICKER [STRIKE] [EXPIRY]")
        return
    symbol = args[0].upper()
    strike = expiry = None
    try:
        if len(args) >= 2:
            strike = float(args[1])
        if len(args) >= 3:
            expiry = args[2]
    except ValueError:
        await update.message.reply_text("تعذر فهم STRIKE — لازم يكون رقم.")
        return

    matches = await asyncio.to_thread(
        signals_db.fetch_matching_positions, chat_id, symbol, strike, expiry)
    if not matches:
        await update.message.reply_text(f"ما لقيت مركز مفتوح على {symbol}.")
        return
    if len(matches) > 1:
        lines = "\n".join(f"• {m['strike']:.2f}$ {m['expiry']}" for m in matches)
        await update.message.reply_text(
            f"عندك أكثر من مركز مفتوح على {symbol}، حدد أكثر:\n{lines}\n\n"
            f"مثال: /untrack {symbol} {matches[0]['strike']:.2f} {matches[0]['expiry']}")
        return

    row = matches[0]
    final_price = await positions_module.reprice(row)
    await asyncio.to_thread(signals_db.close_position, row["id"], final_price, "manual")
    pl_txt = ""
    if final_price is not None:
        pl_pct = (final_price - row["entry_price"]) / row["entry_price"] * 100
        pl_txt = (f" — النتيجة النهائية: {pl_pct:+.0f}% "
                 f"({fmt_price(row['entry_price'])} → {fmt_price(final_price)})")
    await update.message.reply_text(
        f"✅ تم إيقاف متابعة {symbol} {row['strike']:.2f}$ {row['expiry']}{pl_txt}")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    task = sessions.get(chat_id)
    if task is None:
        await update.message.reply_text("لا توجد جلسة قيد التنفيذ حالياً.")
        return
    cancel_event = cancel_events.get(chat_id)
    if cancel_event is not None:
        cancel_event.set()
    # Watchlist sessions (stocks/options [incl. its leaps/heavy tags]/
    # crypto) check cancel_event at their own per-symbol checkpoint and
    # return gracefully with whatever they already found -- hard-cancelling
    # the task would discard that. A single-symbol ticker lookup has no such
    # checkpoint, so it still needs the hard cancel to actually stop.
    if session_kind.get(chat_id) == "ticker":
        task.cancel()
    await update.message.reply_text("⏹️ جارٍ إيقاف الجلسة...")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if is_admin(chat_id):
        member_line = "أنت المشرف 👑"
    elif eligible(chat_id):
        expiry = sub_expiry(chat_id)
        member_line = ("عضو مفعّل ✅" if expiry == 0 else
                       f"عضو مفعّل حتى {time.strftime('%Y-%m-%d', time.localtime(expiry))} ✅")
    else:
        member_line = "غير مصرح لك ❌ (الخدمة مقفلة على الأعضاء الحاليين فقط)"
    session_line = "قيد التنفيذ ⏳ (أرسل /stop لإيقافها)" if chat_id in sessions else "لا توجد"
    market_line = "مفتوح ✅" if market_calendar.market_is_open() else "مغلق ❌"

    last = last_results.get(chat_id)
    if last:
        mins_ago = int((time.time() - last["ts"]) / 60)
        when = "الآن" if mins_ago < 1 else f"قبل {mins_ago} دقيقة"
        results_line = f"{last['title']} — {last['count']} نتيجة ({when})"
    else:
        results_line = "لا توجد نتائج سابقة في هذه الجلسة"

    await update.message.reply_text(
        f"عضويتك: {member_line}\n"
        f"جلستك الحالية: {session_line}\n"
        f"السوق الأمريكي الآن: {market_line}\n"
        f"آخر نتائج: {results_line}\n\n"
        "الأوامر المتاحة:\n"
        "/stocks — فحص وحدة الأسهم\n"
        "/options — فحص أوبشن موحّد (Call فقط): عادي + LEAPS (365+ يوم) + "
        "HEAVY (Mega/Large/ETF)، كل نتيجة موسومة بنوعها\n"
        "/options <رمز> — فحص عقود سهم محدد (Call فقط، النوع العادي)\n"
        "/crypto — فحص وحدة الكريبتو\n"
        "/review — مراجعة الإشارات المستحقة (7/30 يوم) وتحديث سجل الأداء\n"
        "/stats — تقرير أداء من السجل التاريخي\n"
        "/track TICKER STRIKE EXPIRY PRICE TYPE — متابعة مركز مفتوح\n"
        "/positions — مراكزك المفتوحة وربح/خسارة كل واحد الآن\n"
        "/untrack TICKER [STRIKE] [EXPIRY] — إيقاف متابعة مركز\n"
        "/stop — إيقاف الجلسة الحالية فوراً\n"
        "/status — هذه الرسالة\n\n"
        f"كل جلسة تتوقف تلقائياً بعد {_session_minutes()} دقيقة كحد أقصى.\n"
        f"{FOOTER}"
    )


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


async def _position_monitor_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every hour (job_queue.run_repeating below), but only actually
    does anything during market hours -- a permanent lightweight exception
    to the "every scan is a manual 15-minute session" rule (see cmd_stocks
    etc.): it never touches `sessions`/`cancel_events`, never blocks a
    member from starting a scan, and a member starting a scan doesn't
    delay it either. Failures here must never crash the bot process."""
    if not market_calendar.market_is_open():
        return
    try:
        alerts = await positions_module.check_positions_for_alerts()
    except Exception:
        log.exception("Position monitor job failed")
        return
    for alert in alerts:
        await _send(context.application, alert["chat_id"], alert["message"])


BOT_COMMANDS = [
    BotCommand("stocks", "فحص وحدة الأسهم"),
    BotCommand("options", "فحص أوبشن موحّد (Call فقط): عادي+LEAPS+HEAVY، أو /options <رمز> لسهم محدد"),
    BotCommand("crypto", "فحص وحدة الكريبتو"),
    BotCommand("review", "مراجعة الإشارات المستحقة (7/30 يوم)"),
    BotCommand("stats", "تقرير أداء من السجل التاريخي"),
    BotCommand("track", "متابعة مركز: /track TICKER STRIKE EXPIRY PRICE TYPE"),
    BotCommand("positions", "عرض مراكزك المفتوحة وربح/خسارة كل واحد الآن"),
    BotCommand("untrack", "إيقاف متابعة مركز: /untrack TICKER [STRIKE] [EXPIRY]"),
    BotCommand("stop", "إيقاف الجلسة الحالية فوراً"),
    BotCommand("status", "حالة البوت وآخر النتائج"),
]


# Telegram resolves a client's "/" menu by the MOST SPECIFIC (scope,
# language_code) pair that has commands set for it; a per-chat scope beats
# the default scope, and a language-specific list beats the "all languages"
# (language_code unset) list within the same scope. Different Telegram
# clients have shown stale menus for this bot that don't match anything in
# this repo's history (i.e. they were set manually, probably via
# @BotFather, at unknown scope/language combinations) -- so rather than
# guess, every plausible combination is cleared here on every startup, then
# only the intended default-scope/no-language list is set.
_CANDIDATE_LANGS = [None, "ar", "en"]


async def post_init(app: Application):
    """Wipes every plausible leftover command-menu scope/language, logs
    what was actually found server-side (so a stale menu can be diagnosed
    from the Railway logs instead of guessed at), then sets the single
    default-scope menu everyone -- including the admin -- should see."""
    scopes = [("default", BotCommandScopeDefault())]
    if config.ADMIN_CHAT_ID:
        scopes.append(("admin_chat", BotCommandScopeChat(chat_id=config.ADMIN_CHAT_ID)))

    try:
        for scope_name, scope in scopes:
            for lang in _CANDIDATE_LANGS:
                try:
                    existing = await app.bot.get_my_commands(scope=scope, language_code=lang)
                    if existing:
                        log.info("Found stale menu at scope=%s lang=%s: %s",
                                 scope_name, lang, [c.command for c in existing])
                    await app.bot.delete_my_commands(scope=scope, language_code=lang)
                except Exception:
                    log.exception("Could not inspect/clear scope=%s lang=%s", scope_name, lang)

        await app.bot.set_my_commands(BOT_COMMANDS, scope=BotCommandScopeDefault())
        log.info("Command menu set (%d commands) at default scope, all stale overrides cleared",
                 len(BOT_COMMANDS))
    except Exception:
        log.exception("Failed to set command menu")


def _try_run_webhook(app: Application, run_webhook=None, delete_webhook=None) -> bool:
    """Attempts to start the built-in webhook server (blocks until the bot
    stops, same as run_polling). Returns True if it ran to a normal stop --
    the caller should just return in that case. Returns False if it never
    got the chance to start (no URL configured) or failed at startup, in
    which case the caller falls back to run_polling instead.

    A failure AFTER a successful start (e.g. the server crashes mid-flight)
    is not something this function can retroactively fall back from --
    only startup-time failures are caught here.

    `run_webhook`/`delete_webhook` default to `app.run_webhook`/
    `app.bot.delete_webhook` -- overridable only so tests can simulate a
    startup failure without binding a real port or calling Telegram's API
    (python-telegram-bot's Bot/Application objects don't allow monkey-
    patching their own methods directly)."""
    run_webhook = run_webhook or app.run_webhook
    delete_webhook = delete_webhook or app.bot.delete_webhook

    if not config.WEBHOOK_URL:
        if config.BOT_MODE == "webhook":
            log.warning("BOT_MODE=webhook but no WEBHOOK_URL/RAILWAY_PUBLIC_DOMAIN could be "
                       "determined -- falling back to polling")
        return False

    full_url = config.WEBHOOK_URL.rstrip("/") + config.WEBHOOK_PATH
    log.info("Starting in webhook mode at %s (listen=%s port=%s)",
             full_url, config.WEBHOOK_LISTEN, config.WEBHOOK_PORT)
    try:
        run_webhook(
            listen=config.WEBHOOK_LISTEN,
            port=config.WEBHOOK_PORT,
            url_path=config.WEBHOOK_PATH,
            webhook_url=full_url,
            secret_token=config.WEBHOOK_SECRET,
            allowed_updates=Update.ALL_TYPES,
        )
        return True
    except Exception:
        log.exception("Webhook startup failed -- clearing any webhook registration with "
                      "Telegram and falling back to polling")
        try:
            asyncio.run(delete_webhook(drop_pending_updates=False))
        except Exception:
            log.exception("Could not clear webhook registration before falling back to polling")
        return False


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN environment variable")
    signals_db.init_db()
    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("stocks", cmd_stocks))
    app.add_handler(CommandHandler("options", cmd_options))
    app.add_handler(CommandHandler("crypto", cmd_crypto))
    app.add_handler(CommandHandler("review", cmd_review))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("track", cmd_track))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("untrack", cmd_untrack))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    # The hourly position monitor is identical under either mode -- it's a
    # JobQueue job on the same Application, not tied to how updates arrive.
    app.job_queue.run_repeating(_position_monitor_job,
                                interval=config.POSITION_MONITOR_INTERVAL_SECONDS, first=60)

    if config.BOT_MODE != "polling" and _try_run_webhook(app):
        return
    log.info("Bot starting (polling, manual commands only + hourly position monitor)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
