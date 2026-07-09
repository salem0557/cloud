"""Telegram bot: three fully independent, on-demand scanning modules --
stocks (reversal-up technical setups), options (CALL-contract screener),
and crypto (top-30 coins via Binance public data). No automatic background
scanning: every scan runs only when a member sends a command, and stops on
its own after SESSION_TIMEOUT_SECONDS (or instantly via /stop).

The bot is locked to whichever chat ids were already approved before this
restructure (scanner/state.py's `approved`) -- there is no /approve command
anymore, so no new member can be added from within the bot.

Run:  TELEGRAM_BOT_TOKEN=xxx python bot.py
"""
import asyncio
import logging
import time

from dotenv import load_dotenv

load_dotenv()  # must run before scanner.config reads the environment

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from scanner import config, crypto_module, market_calendar, options_module, stocks_module
from scanner.indicators import fmt_price
from scanner.state import State

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

MSG_LIMIT = 3800  # keep below Telegram's 4096-char cap

state = State()
# One session (asyncio Task) + cancel Event per chat, at most -- a chat can
# only have one module running at a time.
sessions: dict[int, asyncio.Task] = {}
cancel_events: dict[int, asyncio.Event] = {}

FOOTER = "⚠️ تحليل فني آلي — ليس توصية بشراء أو بيع، ولا يشكل استشارة استثمارية."


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

def _split_message(text: str) -> list[str]:
    """Split a long results block into Telegram-sized chunks on paragraph
    boundaries (each result is one blank-line-separated block)."""
    parts = text.split("\n\n")
    chunks, current = [], parts[0]
    for part in parts[1:]:
        if len(current) + len(part) + 2 > MSG_LIMIT:
            chunks.append(current)
            current = part
        else:
            current += "\n\n" + part
    chunks.append(current)
    return chunks


async def _send(app, chat_id: int, text: str):
    for chunk in _split_message(text):
        try:
            await app.bot.send_message(chat_id, chunk, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            log.exception("Send to %s failed", chat_id)


# ------------------------------------------------------- session machinery

async def _run_watchlist_session(chat_id: int, title: str, scan_fn, format_fn, app):
    """Runs a module's scan() under the shared SESSION_TIMEOUT_SECONDS cap
    and instant-/stop cancellation; sends the results (or a timeout/stop/
    error notice) as its own private message. A failure here is this
    module's problem alone -- it never touches another module's session."""
    cancel_event = asyncio.Event()
    cancel_events[chat_id] = cancel_event
    try:
        try:
            results = await asyncio.wait_for(scan_fn(cancel_event),
                                             timeout=config.SESSION_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await _send(app, chat_id, f"⏰ {title} — انتهت الجلسة (تجاوزت 5 دقائق).")
            return
        if cancel_event.is_set():
            await _send(app, chat_id, f"⏹️ {title} — تم إيقاف الجلسة.")
            return
        if not results:
            await _send(app, chat_id, f"{title}\n\nلا نتائج تحقق الشروط حالياً.\n\n{FOOTER}")
            return
        blocks = "\n\n".join(format_fn(r) for r in results)
        await _send(app, chat_id, f"{title}\n\n{blocks}\n\n{FOOTER}")
    except asyncio.CancelledError:
        await _send(app, chat_id, f"⏹️ {title} — تم إيقاف الجلسة.")
    except Exception:
        log.exception("Session failed for chat %s (%s)", chat_id, title)
        await _send(app, chat_id, f"⚠️ {title} — حدث خطأ غير متوقع أثناء الفحص.")
    finally:
        sessions.pop(chat_id, None)
        cancel_events.pop(chat_id, None)


async def _run_ticker_session(chat_id: int, symbol: str, app):
    """/options TICKER: a single-symbol lookup, independent of the full
    watchlist scan -- same timeout/cancel machinery, its own message."""
    try:
        spot, contracts, error = await asyncio.wait_for(
            options_module.scan_symbol(symbol), timeout=config.SESSION_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        await _send(app, chat_id, "⏰ انتهت الجلسة (تجاوزت 5 دقائق).")
        return
    except asyncio.CancelledError:
        await _send(app, chat_id, "⏹️ تم إيقاف الجلسة.")
        return
    except Exception:
        log.exception("Ticker session failed for chat %s (%s)", chat_id, symbol)
        await _send(app, chat_id, "⚠️ حدث خطأ غير متوقع أثناء الفحص.")
        return
    finally:
        sessions.pop(chat_id, None)

    if error:
        await _send(app, chat_id, f"📊 *{symbol}* — {error}")
    elif not contracts:
        price_txt = fmt_price(spot) if spot else "-"
        await _send(app, chat_id,
                    f"📊 *{symbol}* ({price_txt}) — لا يوجد عقد يحقق الشروط حالياً.\n\n{FOOTER}")
    else:
        blocks = "\n\n".join(options_module.format_result(r) for r in contracts)
        await _send(app, chat_id, f"📊 عقود *{symbol}* المؤهلة:\n\n{blocks}\n\n{FOOTER}")


def _start_session(chat_id: int, coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    sessions[chat_id] = task
    return task


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
        f"حتى {config.SESSION_TIMEOUT_SECONDS // 60} دقائق أو /stop للإيقاف الفوري.")
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
        await update.message.reply_text(f"⏳ يفحص عقود {symbol}...")
        _start_session(chat_id, _run_ticker_session(chat_id, symbol, context.application))
    else:
        await update.message.reply_text(
            f"📊 بدأ فحص وحدة الأوبشن ({len(config.OPTIONS_WATCHLIST)} سهم)... "
            f"حتى {config.SESSION_TIMEOUT_SECONDS // 60} دقائق أو /stop للإيقاف الفوري.")
        _start_session(chat_id, _run_watchlist_session(
            chat_id, "📊 نتائج فحص الأوبشن", options_module.scan, options_module.format_result,
            context.application))


async def cmd_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_membership(update):
        return
    chat_id = update.effective_chat.id
    if chat_id in sessions:
        await update.message.reply_text("⏳ توجد جلسة قيد التنفيذ بالفعل — أرسل /stop لإيقافها أولاً.")
        return
    await update.message.reply_text(
        f"🪙 بدأ فحص وحدة الكريبتو ({len(config.CRYPTO_WATCHLIST)} عملة)... "
        f"حتى {config.SESSION_TIMEOUT_SECONDS // 60} دقائق أو /stop للإيقاف الفوري.")
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
        member_line = "عضو مفعّل ✅" if expiry == 0 else f"عضو مفعّل حتى {time.strftime('%Y-%m-%d', time.localtime(expiry))} ✅"
    else:
        member_line = "غير مصرح لك ❌ (الخدمة مقفلة على الأعضاء الحاليين فقط)"
    session_line = "قيد التنفيذ ⏳ (أرسل /stop لإيقافها)" if chat_id in sessions else "لا توجد"
    market_line = "مفتوح ✅" if market_calendar.market_is_open() else "مغلق ❌"
    await update.message.reply_text(
        f"عضويتك: {member_line}\n"
        f"جلستك الحالية: {session_line}\n"
        f"السوق الأمريكي الآن: {market_line}\n\n"
        "الأوامر المتاحة:\n"
        "/stocks — فحص وحدة الأسهم\n"
        "/options — فحص وحدة الأوبشن\n"
        "/options <رمز> — فحص عقود سهم محدد\n"
        "/crypto — فحص وحدة الكريبتو\n"
        "/stop — إيقاف الجلسة الحالية فوراً\n"
        "/status — هذه الرسالة\n\n"
        f"كل جلسة تتوقف تلقائياً بعد {config.SESSION_TIMEOUT_SECONDS // 60} دقائق كحد أقصى.\n"
        f"{FOOTER}"
    )


async def on_error(update, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled error", exc_info=context.error)


BOT_COMMANDS = [
    BotCommand("stocks", "فحص وحدة الأسهم"),
    BotCommand("options", "فحص وحدة الأوبشن، أو /options <رمز> لسهم واحد"),
    BotCommand("crypto", "فحص وحدة الكريبتو"),
    BotCommand("stop", "إيقاف الجلسة الحالية فوراً"),
    BotCommand("status", "حالة عضويتك وجلستك"),
]


async def post_init(app: Application):
    """Overwrites Telegram's cached "/" command menu -- without this call
    the client keeps showing whatever command list an older version of the
    bot last registered, even after those handlers are removed from the
    code."""
    try:
        await app.bot.set_my_commands(BOT_COMMANDS)
        log.info("Command menu set (%d commands)", len(BOT_COMMANDS))
    except Exception:
        log.exception("Failed to set command menu")


def main():
    if not config.BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN environment variable")
    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("stocks", cmd_stocks))
    app.add_handler(CommandHandler("options", cmd_options))
    app.add_handler(CommandHandler("crypto", cmd_crypto))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    log.info("Bot starting (polling, manual commands only)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
