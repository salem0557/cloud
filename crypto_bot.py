"""Telegram front-end for the crypto scalping bot.

Two agents behind it: an analyst that scores every setup out of 100 and a
trader that only touches what survived the score and the risk gate.

Run:  CRYPTO_BOT_TOKEN=xxx python crypto_bot.py
Live trading stays off until CRYPTO_LIVE_TRADING=1 *and*
CRYPTO_LIVE_CONFIRM=I_UNDERSTAND_THE_RISK are both set.
"""
import asyncio
import logging

from dotenv import load_dotenv

load_dotenv()  # must run before cryptobot.config reads the environment

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

from cryptobot import analyst, config
from cryptobot.engine import Engine
from cryptobot.indicators import fmt_price
from cryptobot.state import State
from cryptobot.trader import format_position

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("crypto_bot")

MSG_LIMIT = 3800  # Telegram caps a message at 4096 characters

state = State()
engine = Engine(state)
tick_lock = asyncio.Lock()


# ---------------------------------------------------------------- utilities

def is_admin(update: Update) -> bool:
    chat_id = update.effective_chat.id if update.effective_chat else 0
    return config.ADMIN_CHAT_ID != 0 and chat_id == config.ADMIN_CHAT_ID


async def deny(update: Update) -> None:
    await update.message.reply_text(
        "هذا الأمر مخصّص لصاحب البوت فقط.\n"
        "اضبط CRYPTO_ADMIN_CHAT_ID بمعرّف محادثتك لتفعيل أوامر التداول."
    )


def normalize(symbol: str) -> str:
    """btc -> BTC/USDT, btcusdt -> BTC/USDT, btc/usdt -> BTC/USDT."""
    s = symbol.strip().upper().replace("-", "/")
    if "/" in s:
        return s
    if s.endswith(config.QUOTE) and len(s) > len(config.QUOTE):
        return f"{s[:-len(config.QUOTE)]}/{config.QUOTE}"
    return f"{s}/{config.QUOTE}"


async def send_chunks(bot, chat_id: int, text: str) -> None:
    """Split on line boundaries so HTML tags are never cut in half."""
    buf = ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > MSG_LIMIT:
            await bot.send_message(chat_id, buf, parse_mode=ParseMode.HTML)
            buf = ""
        buf += line + "\n"
    if buf.strip():
        await bot.send_message(chat_id, buf, parse_mode=ParseMode.HTML)


async def broadcast(bot, text: str) -> None:
    targets = set(state.subscribers)
    if config.ADMIN_CHAT_ID:
        targets.add(config.ADMIN_CHAT_ID)
    for chat_id in targets:
        try:
            await send_chunks(bot, chat_id, text)
        except Exception as exc:
            log.warning("could not message %s: %s", chat_id, exc)


def mode_banner() -> str:
    if config.live_enabled():
        return "🔴 <b>وضع حقيقي</b> — البوت ينفّذ أوامر على حسابك"
    return f"🧪 <b>وضع تجريبي</b> — {config.live_blocker()}"


# ----------------------------------------------------------------- commands

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id not in state.subscribers:
        state.subscribers.append(chat_id)
        state.save()
    await update.message.reply_text(
        "🤖 <b>بوت التداول اللحظي للعملات الرقمية</b>\n\n"
        "يعمل بعقلين:\n"
        "• <b>المحلل</b> — يقيّم كل فرصة من 100 نقطة على فريمين، "
        "ويرفض أي إعداد ضعيف أو سوق سيّئ التنفيذ.\n"
        "• <b>المتداول</b> — ينفّذ فقط ما تجاوز التقييم وبوابة المخاطر، "
        "ويدير الوقف والأهداف بنفسه.\n\n"
        f"{mode_banner()}\n\n"
        "الأوامر: /help",
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "<b>الأوامر</b>\n\n"
        "<b>تحليل</b>\n"
        "/analyze BTC — تحليل مفصّل لعملة\n"
        "/scan — فحص كل قائمة المراقبة وعرض الأفضل\n\n"
        "<b>الحساب</b>\n"
        "/positions — المراكز المفتوحة\n"
        "/balance — الرصيد والوضع الحالي\n"
        "/pnl — أداء اليوم والإجمالي\n"
        "/history — آخر الصفقات المغلقة\n\n"
        "<b>التحكم</b>\n"
        "/watch BTC — إضافة عملة للمراقبة\n"
        "/unwatch BTC — إزالتها\n"
        "/list — قائمة المراقبة\n"
        "/pause — إيقاف الدخول (تبقى إدارة المراكز شغّالة)\n"
        "/resume — استئناف الدخول\n"
        "/closeall — إغلاق كل المراكز فوراً\n"
        "/settings — إعدادات المخاطرة الحالية\n",
        parse_mode=ParseMode.HTML,
    )


async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not ctx.args:
        await update.message.reply_text("اكتب الرمز، مثال: /analyze BTC")
        return
    symbol = normalize(ctx.args[0])
    await update.message.reply_text(f"⏳ يحلّل {symbol}…")
    try:
        ltf, htf, ticker = await asyncio.to_thread(engine.fetch, symbol)
    except Exception as exc:
        await update.message.reply_text(f"تعذّر جلب بيانات {symbol}: {exc}")
        return
    verdict = analyst.analyze(symbol, ltf, htf, ticker)
    await send_chunks(ctx.bot, update.effective_chat.id,
                      analyst.format_verdict(verdict))


async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("⏳ يفحص قائمة المراقبة…")
    verdicts = await asyncio.to_thread(engine.scan)
    if not verdicts:
        await update.message.reply_text("لا توجد بيانات — تحقّق من قائمة المراقبة.")
        return
    verdicts.sort(key=lambda v: v.score, reverse=True)
    lines = [f"<b>نتيجة الفحص</b> ({len(verdicts)} عملة)\n"]
    for v in verdicts:
        mark = "🟢" if v.ok else "⚪️"
        side = "شراء" if v.side == "long" else "بيع" if v.side == "short" else "—"
        lines.append(f"{mark} <b>{v.symbol}</b> — {v.score:.0f}/100 | {side} | "
                     f"{fmt_price(v.price)}")
        if v.blockers:
            lines.append(f"    ↳ {v.blockers[0]}")
    lines.append("\nللتفاصيل: /analyze &lt;الرمز&gt;")
    await send_chunks(ctx.bot, update.effective_chat.id, "\n".join(lines))


async def cmd_positions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    positions = state.open_list()
    if not positions:
        await update.message.reply_text("لا توجد مراكز مفتوحة.")
        return
    lines = ["<b>المراكز المفتوحة</b>\n"]
    total = 0.0
    for pos in positions:
        try:
            price = await asyncio.to_thread(engine.exchange.last_price, pos.symbol)
        except Exception:
            price = pos.entry
        total += pos.unrealized(price)
        lines.append(format_position(pos, price))
    lines.append(f"\n<b>الإجمالي غير المحقق: {total:+,.2f}$</b>")
    await send_chunks(ctx.bot, update.effective_chat.id, "\n".join(lines))


async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    equity = await asyncio.to_thread(engine.equity)
    lines = [
        mode_banner(),
        "",
        f"المنصة: <b>{config.EXCHANGE_ID}</b> ({config.MARKET_TYPE})",
        f"الرصيد المتاح: <b>{equity:,.2f} {config.QUOTE}</b>",
        f"المراكز المفتوحة: {len(state.positions)}/{config.MAX_OPEN_POSITIONS}",
        f"صفقات اليوم: {state.day_trades}/{config.MAX_TRADES_PER_DAY}",
        f"الحالة: {'⏸ متوقف عن الدخول' if state.paused else '▶️ يعمل'}",
    ]
    if engine.last_error:
        lines.append(f"\n⚠️ آخر خطأ: {engine.last_error}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    s = state.stats()
    pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
    await update.message.reply_text(
        f"<b>الأداء</b>\n\n"
        f"اليوم: <b>{state.day_pnl:+,.2f}$</b> ({state.day_trades} صفقة)\n"
        f"الحد اليومي للخسارة: {config.DAILY_MAX_LOSS_PCT}%\n\n"
        f"الإجمالي: <b>{s['pnl']:+,.2f}$</b> على {s['trades']} صفقة\n"
        f"نسبة الربح: {s['win_rate']:.1f}% ({s['wins']}✅ / {s['losses']}❌)\n"
        f"متوسط R: {s['avg_r']:+.2f} | عامل الربح: {pf}\n"
        f"أفضل: {s['best']:+,.2f}$ | أسوأ: {s['worst']:+,.2f}$",
        parse_mode=ParseMode.HTML,
    )


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    rows = state.history[-15:]
    if not rows:
        await update.message.reply_text("لا توجد صفقات مغلقة بعد.")
        return
    lines = ["<b>آخر الصفقات</b>\n"]
    for r in reversed(rows):
        mark = "🟢" if r.get("pnl", 0) > 0 else "🔴"
        lines.append(f"{mark} {r['symbol']} — {r['pnl']:+,.2f}$ "
                     f"({r.get('r', 0):+.2f}R) — {r.get('reason', '')}")
    await send_chunks(ctx.bot, update.effective_chat.id, "\n".join(lines))


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await deny(update)
    if not ctx.args:
        await update.message.reply_text("مثال: /watch SOL")
        return
    added = []
    for raw in ctx.args:
        symbol = normalize(raw)
        if symbol not in state.watchlist:
            state.watchlist.append(symbol)
            added.append(symbol)
    state.save()
    await update.message.reply_text(
        f"أُضيف: {', '.join(added)}" if added else "موجود مسبقاً في القائمة.")


async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await deny(update)
    if not ctx.args:
        await update.message.reply_text("مثال: /unwatch SOL")
        return
    removed = []
    for raw in ctx.args:
        symbol = normalize(raw)
        if symbol in state.watchlist:
            state.watchlist.remove(symbol)
            removed.append(symbol)
    state.save()
    await update.message.reply_text(
        f"أُزيل: {', '.join(removed)}" if removed else "غير موجود في القائمة.")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    extra = (f"\n+ أعلى {config.AUTO_TOP_N} عملة سيولة تلقائياً"
             if config.AUTO_TOP_N else "")
    await update.message.reply_text(
        "<b>قائمة المراقبة</b>\n" + "\n".join(f"• {s}" for s in state.watchlist) + extra,
        parse_mode=ParseMode.HTML,
    )


async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await deny(update)
    state.paused = True
    state.save()
    await update.message.reply_text(
        "⏸ أُوقف الدخول في صفقات جديدة.\n"
        "المراكز المفتوحة تبقى تحت الإدارة (الوقف والأهداف تعمل).")


async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await deny(update)
    state.paused = False
    state.save()
    await update.message.reply_text("▶️ استُؤنف البحث عن الصفقات.")


async def cmd_closeall(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        return await deny(update)
    await update.message.reply_text("⏳ يغلق كل المراكز…")
    events = await asyncio.to_thread(engine.close_all)
    await send_chunks(ctx.bot, update.effective_chat.id, "\n".join(events))


async def cmd_settings(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"{mode_banner()}\n\n"
        f"<b>الفريمات</b>: دخول {config.LTF} / اتجاه {config.HTF}\n"
        f"<b>دورة الفحص</b>: كل {config.LOOP_SECONDS} ثانية\n\n"
        f"<b>شروط المحلل</b>\n"
        f"• أقل قوة مقبولة: {config.MIN_SCORE:.0f}/100\n"
        f"• أقل عائد/مخاطرة: {config.MIN_RR}\n"
        f"• نطاق التذبذب: {config.MIN_ATR_PCT}% – {config.MAX_ATR_PCT}%\n"
        f"• أقصى فارق سعري: {config.MAX_SPREAD_PCT}%\n"
        f"• البيع المكشوف: {'مفعّل' if config.shorts_enabled() else 'معطّل'}\n\n"
        f"<b>المخاطرة</b>\n"
        f"• لكل صفقة: {config.RISK_PER_TRADE_PCT}% من الرصيد\n"
        f"• أقصى حجم مركز: {config.MAX_POSITION_PCT}%\n"
        f"• أقصى مراكز مفتوحة: {config.MAX_OPEN_POSITIONS}\n"
        f"• أقصى صفقات يومياً: {config.MAX_TRADES_PER_DAY}\n"
        f"• حد الخسارة اليومي: {config.DAILY_MAX_LOSS_PCT}%\n"
        f"• تهدئة بعد الخسارة: {config.SYMBOL_COOLDOWN_MIN} دقيقة\n\n"
        f"<b>الخروج</b>\n"
        f"• الوقف: {config.SL_ATR_MULT}× ATR أو خلف آخر قاع\n"
        f"• هدف ١: {config.TP1_R}R (بيع {config.TP1_FRACTION * 100:.0f}%) "
        f"ثم نقل الوقف للتعادل\n"
        f"• هدف ٢: {config.TP2_R}R\n"
        f"• وقف متحرك: {config.TRAIL_ATR_MULT}× ATR بعد الهدف الأول\n"
        f"• أقصى مدة للصفقة: {config.MAX_HOLD_MINUTES} دقيقة\n\n"
        f"لتغيير أي قيمة: عدّل ملف ‎.env ثم أعد تشغيل البوت.",
        parse_mode=ParseMode.HTML,
    )


# --------------------------------------------------------------- background

async def tick_job(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Engine cycle. Skipped if the previous one is still running."""
    if tick_lock.locked():
        log.info("previous tick still running; skipping")
        return
    async with tick_lock:
        events = await asyncio.to_thread(engine.tick)
    for event in events:
        await broadcast(ctx.bot, event)


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("analyze", "تحليل عملة"),
        BotCommand("scan", "فحص قائمة المراقبة"),
        BotCommand("positions", "المراكز المفتوحة"),
        BotCommand("balance", "الرصيد والوضع"),
        BotCommand("pnl", "الأداء"),
        BotCommand("history", "آخر الصفقات"),
        BotCommand("watch", "إضافة عملة"),
        BotCommand("unwatch", "إزالة عملة"),
        BotCommand("list", "قائمة المراقبة"),
        BotCommand("pause", "إيقاف الدخول"),
        BotCommand("resume", "استئناف الدخول"),
        BotCommand("closeall", "إغلاق كل المراكز"),
        BotCommand("settings", "الإعدادات"),
        BotCommand("help", "المساعدة"),
    ])
    log.info("mode=%s exchange=%s watchlist=%s",
             engine.mode, config.EXCHANGE_ID, state.watchlist)


def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("CRYPTO_BOT_TOKEN غير مضبوط")
    if config.live_enabled():
        log.warning("LIVE TRADING ENABLED on %s — real orders will be placed",
                    config.EXCHANGE_ID)

    app = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()
    for name, handler in [
        ("start", cmd_start), ("help", cmd_help),
        ("analyze", cmd_analyze), ("a", cmd_analyze), ("scan", cmd_scan),
        ("positions", cmd_positions), ("balance", cmd_balance),
        ("pnl", cmd_pnl), ("history", cmd_history),
        ("watch", cmd_watch), ("unwatch", cmd_unwatch), ("list", cmd_list),
        ("pause", cmd_pause), ("resume", cmd_resume), ("closeall", cmd_closeall),
        ("settings", cmd_settings),
    ]:
        app.add_handler(CommandHandler(name, handler))

    app.job_queue.run_repeating(tick_job, interval=config.LOOP_SECONDS, first=10)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
