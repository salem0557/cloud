"""Telegram bot: three fully independent, on-demand scanning modules --
stocks (reversal-up technical setups, point-scored), options (CALL+PUT
screener, Black-Scholes probability + EV), and crypto (top ~100 coins via
Binance public data, point-scored). No automatic background scanning:
every scan runs only when a member sends a command, and stops on its own
after SESSION_TIMEOUT_SECONDS (15 minutes) or instantly via /stop.

The bot is locked to whichever chat ids were already approved before this
restructure (scanner/state.py's `approved`) -- there is no /approve command
anymore, so no new member can be added from within the bot.

Note: this file stays named bot.py (not main.py) even though it's the
Telegram bot's entry point -- main.py in this repo already belongs to an
unrelated tool (options_scanner/cli.py's CLI entry point).

Run:  TELEGRAM_BOT_TOKEN=xxx python bot.py
"""
import asyncio
import functools
import io
import logging
import time

from dotenv import load_dotenv

load_dotenv()  # must run before scanner.config reads the environment

from telegram import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from scanner import config, crypto_module, market_calendar, options_module, stocks_module
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


# ------------------------------------------------------- session machinery

async def _run_watchlist_session(chat_id: int, title: str, scan_fn, format_fn, app):
    """Runs a module's scan() under SESSION_TIMEOUT_SECONDS and instant-
    /stop cancellation; sends each result as its OWN message (never
    batched) and a timeout/stop/error notice as its own message too. A
    failure here is this module's problem alone -- it never touches
    another module's session."""
    cancel_event = asyncio.Event()
    cancel_events[chat_id] = cancel_event
    try:
        try:
            results = await asyncio.wait_for(scan_fn(cancel_event),
                                             timeout=config.SESSION_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await _send(app, chat_id, f"⏰ {title} — انتهت الجلسة، أرسل أمر جديد.")
            return
        if cancel_event.is_set():
            await _send(app, chat_id, f"⏹️ {title} — تم إيقاف الجلسة.")
            return
        last_results[chat_id] = {"title": title, "count": len(results), "ts": time.time()}
        excluded = getattr(results, "excluded_bad_data", 0)
        excluded_line = f"⚠️ استُبعد {excluded} عقد لبيانات غير موثوقة" if excluded else ""
        if not results:
            msg = f"{title}\n\n{NO_RESULTS}"
            if excluded_line:
                msg += f"\n\n{excluded_line}"
            await _send(app, chat_id, f"{msg}\n\n{FOOTER}")
            return
        await _send(app, chat_id, f"{title} — {len(results)} نتيجة:")
        for row in results:
            if cancel_event.is_set():
                break
            await _send_row(app, chat_id, row, format_fn)
        await _send(app, chat_id, f"{excluded_line}\n\n{FOOTER}" if excluded_line else FOOTER)
    except asyncio.CancelledError:
        await _send(app, chat_id, f"⏹️ {title} — تم إيقاف الجلسة.")
    except Exception:
        log.exception("Session failed for chat %s (%s)", chat_id, title)
        await _send(app, chat_id, f"⚠️ {title} — حدث خطأ غير متوقع أثناء الفحص. جرّب لاحقاً.")
    finally:
        sessions.pop(chat_id, None)
        cancel_events.pop(chat_id, None)


async def _run_ticker_session(chat_id: int, symbol: str, app):
    """/options TICKER: a single-symbol lookup (كول وبوت معاً)، مستقل عن
    فحص القائمة الكاملة -- نفس آلية المهلة/الإلغاء، رسالة خاصة به."""
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
        await _send(app, chat_id, f"{excluded_line}\n\n{FOOTER}" if excluded_line else FOOTER)


def _start_session(chat_id: int, coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    sessions[chat_id] = task
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
        context.application))


async def cmd_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in sessions:
        await update.message.reply_text("⏳ توجد جلسة قيد التنفيذ بالفعل — أرسل /stop لإيقافها أولاً.")
        return
    if context.args:
        symbol = context.args[0].upper()
        await update.message.reply_text(f"⏳ يفحص عقود {symbol} (Call و Put)...")
        _start_session(chat_id, _run_ticker_session(chat_id, symbol, context.application))
    else:
        await update.message.reply_text(
            f"📊 بدأ فحص وحدة الأوبشن (Call + Put، {len(config.OPTIONS_WATCHLIST)} سهم)... "
            f"حتى {_session_minutes()} دقيقة أو /stop للإيقاف الفوري.")
        _start_session(chat_id, _run_watchlist_session(
            chat_id, "📊 نتائج فحص الأوبشن (Call + Put)", options_module.scan,
            options_module.format_result, context.application))


async def cmd_options_calls(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in sessions:
        await update.message.reply_text("⏳ توجد جلسة قيد التنفيذ بالفعل — أرسل /stop لإيقافها أولاً.")
        return
    await update.message.reply_text(
        f"📊 بدأ فحص عقود CALL فقط ({len(config.OPTIONS_WATCHLIST)} سهم)... "
        f"حتى {_session_minutes()} دقيقة أو /stop للإيقاف الفوري.")
    scan_calls = functools.partial(options_module.scan, sides=("call",))
    _start_session(chat_id, _run_watchlist_session(
        chat_id, "🟢 نتائج فحص عقود CALL", scan_calls, options_module.format_result,
        context.application))


async def cmd_options_puts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in sessions:
        await update.message.reply_text("⏳ توجد جلسة قيد التنفيذ بالفعل — أرسل /stop لإيقافها أولاً.")
        return
    await update.message.reply_text(
        f"📊 بدأ فحص عقود PUT فقط ({len(config.OPTIONS_WATCHLIST)} سهم)... "
        f"حتى {_session_minutes()} دقيقة أو /stop للإيقاف الفوري.")
    scan_puts = functools.partial(options_module.scan, sides=("put",))
    _start_session(chat_id, _run_watchlist_session(
        chat_id, "🔴 نتائج فحص عقود PUT", scan_puts, options_module.format_result,
        context.application))


async def cmd_leaps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in sessions:
        await update.message.reply_text("⏳ توجد جلسة قيد التنفيذ بالفعل — أرسل /stop لإيقافها أولاً.")
        return
    await update.message.reply_text(
        f"🗓️ بدأ فحص عقود LEAPS (CALL، {config.LEAPS_DTE_MIN}+ يوم، أسهم "
        f"{config.LEAPS_MIN_PRICE:.0f}$-{config.LEAPS_MAX_PRICE:.0f}$، "
        f"{len(config.OPTIONS_WATCHLIST)} سهم)... "
        f"حتى {_session_minutes()} دقيقة أو /stop للإيقاف الفوري.")
    _start_session(chat_id, _run_watchlist_session(
        chat_id, "🗓️ نتائج فحص LEAPS", options_module.scan_leaps,
        options_module.format_leaps_result, context.application))


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
        context.application))


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    task = sessions.get(chat_id)
    if task is None:
        await update.message.reply_text("لا توجد جلسة قيد التنفيذ حالياً.")
        return
    cancel_event = cancel_events.get(chat_id)
    if cancel_event is not None:
        cancel_event.set()
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
        "/options — فحص وحدة الأوبشن (Call + Put)\n"
        "/options_calls — عقود Call فقط\n"
        "/options_puts — عقود Put فقط\n"
        "/options <رمز> — فحص عقود سهم محدد (Call + Put)\n"
        "/leaps — عقود CALL طويلة الأجل (365+ يوم)\n"
        "/crypto — فحص وحدة الكريبتو\n"
        "/stop — إيقاف الجلسة الحالية فوراً\n"
        "/status — هذه الرسالة\n\n"
        f"كل جلسة تتوقف تلقائياً بعد {_session_minutes()} دقيقة كحد أقصى.\n"
        f"{FOOTER}"
    )


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


BOT_COMMANDS = [
    BotCommand("stocks", "فحص وحدة الأسهم"),
    BotCommand("options", "فحص وحدة الأوبشن (Call + Put)، أو /options <رمز> لسهم محدد"),
    BotCommand("options_calls", "فحص عقود CALL فقط"),
    BotCommand("options_puts", "فحص عقود PUT فقط"),
    BotCommand("leaps", "عقود CALL طويلة الأجل (365+ يوم)"),
    BotCommand("crypto", "فحص وحدة الكريبتو"),
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


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN environment variable")
    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("stocks", cmd_stocks))
    app.add_handler(CommandHandler("options", cmd_options))
    app.add_handler(CommandHandler("options_calls", cmd_options_calls))
    app.add_handler(CommandHandler("options_puts", cmd_options_puts))
    app.add_handler(CommandHandler("leaps", cmd_leaps))
    app.add_handler(CommandHandler("crypto", cmd_crypto))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    log.info("Bot starting (polling, manual commands only)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
