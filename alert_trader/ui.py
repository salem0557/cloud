"""Arabic Telegram control panel and notifications, owner-only.

Every command checks the sender against OWNER_TELEGRAM_ID; anyone else gets
no answer. Channel posts are routed here only for the Bot API transport.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from . import market_calendar as cal
from . import reports
from .channel import from_ptb
from .storage import parse_iso

log = logging.getLogger("alert_trader.ui")

B_STATUS, B_PORT, B_POS, B_PERF = "📊 الحالة", "💼 المحفظة", "📈 المراكز", "🧾 الأداء"
B_ACC, B_REJ, B_SET = "✅ المقبولة", "❌ المرفوضة", "⚙️ الإعدادات"
B_PAUSE, B_RESUME, B_CLOSE = "⏸ إيقاف الدخول", "▶️ استئناف الدخول", "🛑 إغلاق جميع المراكز"
B_DAY, B_WEEK, B_PROBE = "📅 تقرير اليوم", "🗓 تقرير الأسبوع", "🔌 فحص الربط"

KEYBOARD = ReplyKeyboardMarkup(
    [[B_STATUS, B_PORT, B_POS], [B_PERF, B_ACC, B_REJ], [B_DAY, B_WEEK, B_SET],
     [B_PAUSE, B_RESUME, B_PROBE], [B_CLOSE]], resize_keyboard=True)

COOLDOWN_PREFIXES = ("data_", "broker_", "stale", "exit_slow", "exit:", "position_mismatch")


class Notifier:
    def __init__(self, bot, owner_id: int, cooldown: float = 900):
        self.bot, self.owner_id, self.cooldown = bot, owner_id, cooldown
        self._sent: dict[str, float] = {}

    async def send(self, items) -> None:
        now = time.monotonic()
        for key, text in items:
            last = self._sent.get(key)
            if last is not None and (key.startswith(COOLDOWN_PREFIXES) and now - last < self.cooldown
                                     or not key.startswith(COOLDOWN_PREFIXES)):
                continue
            self._sent[key] = now
            await send_long(self.bot, self.owner_id, text)


async def send_long(bot, chat_id, text, **kw):
    for i in range(0, len(text), 4000):
        await bot.send_message(chat_id, text[i:i + 4000], **kw)


class Panel:
    def __init__(self, env, engine):
        self.env, self.engine = env, engine
        self.notifier: Notifier | None = None

    # ---------------------------------------------------------- plumbing
    def owner(self, update: Update) -> bool:
        user = update.effective_user
        return bool(user and self.env.owner_id and user.id == self.env.owner_id)

    async def flush(self) -> None:
        if self.notifier:
            await self.notifier.send(self.engine.drain())

    async def reply(self, update: Update, text: str, **kw) -> None:
        await send_long(update.get_bot(), update.effective_chat.id, text, **kw)

    async def run(self, fn, *args):
        result = await asyncio.to_thread(fn, *args)
        await self.flush()
        return result

    # ------------------------------------------------------- channel input
    async def on_channel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        message = update.channel_post or update.edited_channel_post
        if message is None:
            return
        self.engine.store.set("last_botapi_post_at", dt.datetime.now(dt.timezone.utc).isoformat())
        if self.env.transport != "botapi":
            return       # the userbot is the source of record; this only proves reachability
        await self.run(self.engine.handle_message, from_ptb(message))

    async def on_raw(self, raw) -> None:
        await self.run(self.engine.handle_message, raw)

    # ------------------------------------------------------------ commands
    async def start(self, update: Update, context) -> None:
        if not self.owner(update):
            return
        await self.reply(update, f"{self.engine.tag()} لوحة تحكم بوت تنبيهات الخيارات.", reply_markup=KEYBOARD)

    async def status(self, update: Update, context) -> None:
        if not self.owner(update):
            return
        e, s, st = self.engine, self.engine.s, self.engine.store
        now = e.clock()
        start = parse_iso(st.get("paper_week_start"))
        sessions = cal.sessions_between(start, now) if start else []
        market = ("مفتوح" if cal.is_open(now) else f"مغلق — يفتح {cal.fmt_both(cal.next_open(now))}")
        active = st.one("SELECT COUNT(*) n FROM trades WHERE status IN ('pending_entry','open','closing') "
                        "AND mode=?", (e.mode,))["n"]
        working = st.one("SELECT COUNT(*) n FROM orders WHERE status IN "
                         "('new','submitted','accepted','partially_filled','unknown') AND mode=?", (e.mode,))["n"]
        lines = [
            f"{e.tag()} الحالة",
            f"• الوضع: {'ورقي' if e.mode == 'paper' else 'حقيقي'}"
            + (" — إعدادات تجريبية (demo)" if s.demo else ""),
            f"• الآن: {cal.fmt_both(now)}",
            f"• السوق: {market}",
            f"• الدخول الجديد: {e.entries_paused() or 'مفعّل'}",
            f"• أسبوع المحاكاة بدأ: {cal.fmt_both(start)} — جلسات حتى الآن: {len(sessions)}",
            f"• طريقة ربط القناة: {self.env.transport} | آخر منشور: "
            f"{cal.fmt_both(parse_iso(st.get('last_channel_msg_at')))}",
            f"• أبيان: {'غير مهيأ (ABYAN_MCP_URL فارغ)' if e.broker is None else 'مهيأ للقراءة فقط' if e.broker.read_only else 'مهيأ للكتابة'}"
            f" | آخر نجاح: {cal.fmt_both(parse_iso(st.get('last_abyan_ok_at')))}",
        ]
        if st.get("last_abyan_error"):
            lines.append(f"  آخر خطأ أبيان: {st.get('last_abyan_error')}")
        lines += [
            f"• مصدر العرض/الطلب: {e.quotes.name} | آخر سعر ناجح: "
            f"{cal.fmt_both(parse_iso(st.get('last_quote_ok_at')))}",
            f"• صفقات نشطة: {active} | أوامر قيد المتابعة: {working}",
        ]
        await self.reply(update, "\n".join(lines))

    async def portfolio(self, update: Update, context) -> None:
        if not self.owner(update):
            return
        e = self.engine
        eq = e.equity()
        total = e.store.one("SELECT COALESCE(SUM(realized_pnl),0) v FROM trades WHERE mode=?", (e.mode,))["v"]
        lines = [f"{e.tag()} المحفظة"]
        if e.mode == "paper":
            lines += [
                f"• رأس المال الورقي: {e.s.paper_capital_usd:,.2f}$",
                f"• النقد: {eq['cash']:,.2f}$ (محجوز لأوامر: {eq['reserved']:,.2f}$)",
                f"• قيمة المراكز (بسعر العرض): {eq['positions_value']:,.2f}$"
                + (f" — {eq['stale_marks']} بلا سعر حديث" if eq["stale_marks"] else ""),
                f"• إجمالي القيمة: {eq['equity']:,.2f}$ | المحقق: {total:+,.2f}$",
                f"• نتيجة اليوم ({e.s.daily_loss_method}): {e.daily_pnl():+,.2f}$",
            ]
        if e.broker is not None:
            try:
                acct = await asyncio.to_thread(e.broker.account)
                lines += ["", "حسابك الحقيقي في أبيان (قراءة فقط، منفصل عن المحاكاة):",
                          f"• القوة الشرائية: {acct.get('buying_power')}$ | القيمة: {acct.get('equity')}$"]
            except Exception as ex:
                lines.append(f"تعذر قراءة حساب أبيان: {ex}")
        await self.reply(update, "\n".join(lines))

    async def positions(self, update: Update, context) -> None:
        if not self.owner(update):
            return
        e = self.engine
        rows = e.store.q("SELECT * FROM trades WHERE mode=? AND status IN ('pending_entry','open','closing') "
                         "ORDER BY id", (e.mode,))
        if not rows:
            await self.reply(update, f"{e.tag()} لا توجد مراكز نشطة.")
            return
        lines = [f"{e.tag()} المراكز"]
        for t in rows:
            mark = t["last_bid"]
            upnl = (mark - t["avg_entry"]) * t["qty_open"] * t["multiplier"] if mark and t["avg_entry"] else None
            lines.append(
                f"#{t['id']} {t['occ']} — {t['status']}\n"
                f"  الكمية {t['qty_open']}/{t['qty_planned']} | متوسط الدخول {t['avg_entry'] or '—'}"
                f" | العرض {mark or '—'} | غير محقق {f'{upnl:+.2f}$' if upnl is not None else '—'}\n"
                f"  الوقف {t['stop'] or '—'} | الأهداف {', '.join(map(str, json.loads(t['targets'] or '[]'))) or '—'}"
                f" (المحقق {t['targets_hit']})")
        await self.reply(update, "\n".join(lines))

    async def performance(self, update: Update, context) -> None:
        if not self.owner(update):
            return
        e = self.engine
        closed = e.store.q("SELECT realized_pnl, entry_fees, exit_fees FROM trades WHERE mode=? "
                           "AND status IN ('closed','expired')", (e.mode,))
        pnl = [r["realized_pnl"] or 0 for r in closed]
        fees = sum((r["entry_fees"] or 0) + (r["exit_fees"] or 0) for r in closed)
        eq = [r["equity"] for r in e.store.q("SELECT equity FROM equity WHERE mode=? ORDER BY ts", (e.mode,))]
        peak, dd = None, 0.0
        for v in eq:
            peak = v if peak is None else max(peak, v)
            dd = max(dd, peak - v)
        wins = sum(1 for p in pnl if p > 0)
        await self.reply(update, "\n".join([
            f"{e.tag()} الأداء (بعد الرسوم)",
            f"• صفقات مغلقة: {len(pnl)} | رابحة: {wins} | خاسرة: {len(pnl) - wins}",
            f"• الصافي: {sum(pnl):+,.2f}$ | الرسوم: {fees:,.2f}$",
            f"• أقصى تراجع للمحفظة: {dd:,.2f}$",
            "ℹ️ لا يثبت الأداء الماضي ربحية مستقبلية.",
        ]))

    async def _alerts(self, update: Update, statuses: tuple, title: str) -> None:
        if not self.owner(update):
            return
        e = self.engine
        rows = e.store.q(f"SELECT a.*, m.text FROM alerts a LEFT JOIN messages m ON m.id=a.message_row "
                         f"WHERE a.status IN ({','.join('?' * len(statuses))}) ORDER BY a.id DESC LIMIT 10",
                         statuses)
        if not rows:
            await self.reply(update, f"{e.tag()} {title}: لا يوجد.")
            return
        lines = [f"{e.tag()} {title} (آخر 10)"]
        for a in rows:
            when = cal.fmt_both(parse_iso(a["created_at"]))
            p = json.loads(a["parsed"] or "{}")
            label = p.get("occ_symbol") or p.get("underlying") or "—"
            reasons = "؛ ".join(json.loads(a["reasons"] or "[]")[:3])
            lines.append(f"• {when} [{a['kind']}/{a['status']}] {label}" + (f"\n  ↳ {reasons}" if reasons else ""))
        await self.reply(update, "\n".join(lines))

    async def accepted(self, update, context):
        await self._alerts(update, ("accepted", "applied"), "التنبيهات المقبولة")

    async def rejected(self, update, context):
        await self._alerts(update, ("rejected", "duplicate"), "التنبيهات المرفوضة")

    async def settings(self, update: Update, context) -> None:
        if not self.owner(update):
            return
        s = self.engine.s
        data = {k: getattr(s, k) for k in s.__dataclass_fields__}
        problems = s.problems()
        text = (f"{self.engine.tag()} الإعدادات (من الملف، بلا أسرار)\n"
                + json.dumps(data, ensure_ascii=False, indent=1, default=str)
                + ("\n\nملاحظات: " + "؛ ".join(problems) if problems else "")
                + "\n\nالتعديل من ملف الإعدادات ثم إعادة التشغيل؛ البوت لا يغيّر قيم المخاطرة بنفسه.")
        await self.reply(update, text)

    async def pause(self, update: Update, context) -> None:
        if not self.owner(update):
            return
        await self.run(self.engine.pause, "المالك عبر تيليجرام")
        await self.reply(update, f"{self.engine.tag()} ⏸ أُوقف الدخول الجديد. المراكز القائمة تبقى تحت المتابعة.")

    async def resume(self, update: Update, context) -> None:
        if not self.owner(update):
            return
        await self.run(self.engine.resume)
        other = self.engine.entries_paused()
        await self.reply(update, f"{self.engine.tag()} ▶️ أُلغي الإيقاف اليدوي."
                         + (f"\nلكن الدخول ما زال موقوفًا: {other}" if other else ""))

    async def close_all_ask(self, update: Update, context) -> None:
        if not self.owner(update):
            return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("نعم، أغلق جميع المراكز", callback_data="closeall:yes"),
                                    InlineKeyboardButton("إلغاء", callback_data="closeall:no")]])
        await self.reply(update, f"{self.engine.tag()} سيُرسل أمر بيع محدد لكل المراكز المفتوحة ويُلغى كل أمر "
                         "دخول معلق. هذا لا يوقف الدخول الجديد (استخدم ⏸ لذلك). تأكيد؟", reply_markup=kb)

    async def close_all_cb(self, update: Update, context) -> None:
        q = update.callback_query
        if not self.owner(update):
            await q.answer()
            return
        await q.answer()
        if q.data != "closeall:yes":
            await q.edit_message_text("أُلغي طلب الإغلاق.")
            return
        n = await self.run(self.engine.close_all, "المالك عبر تيليجرام")
        await q.edit_message_text(f"{self.engine.tag()} 🛑 طُلب إغلاق {n} صفقة. ستصلك إشعارات التنفيذ الفعلي.")

    async def daily(self, update: Update, context) -> None:
        if self.owner(update):
            await self.reply(update, await asyncio.to_thread(reports.daily, self.engine))

    async def weekly(self, update: Update, context) -> None:
        if self.owner(update):
            await self.reply(update, await asyncio.to_thread(reports.weekly, self.engine))

    async def probe(self, update: Update, context) -> None:
        if not self.owner(update):
            return
        e, st = self.engine, self.engine.store
        bot = update.get_bot()
        lines = [f"{e.tag()} فحص الربط"]
        try:
            me = await bot.get_me()
            member = await bot.get_chat_member(self.env.channel_id, me.id)
            lines.append(f"• صلاحية البوت في القناة: {member.status}"
                         + ("" if member.status == "administrator" else " ✗ (يجب أن يكون مشرفًا)"))
        except Exception as ex:
            lines.append(f"• تعذر فحص القناة عبر Bot API: {ex}")
        day_ago = (e.clock() - dt.timedelta(days=1)).isoformat()
        for transport in ("botapi", "userbot"):
            n = st.one("SELECT COUNT(*) n FROM messages WHERE transport=? AND received_at>=?",
                       (transport, day_ago))["n"]
            lines.append(f"• منشورات وصلت عبر {transport} آخر 24 ساعة: {n}")
        lines.append(f"• آخر منشور رآه Bot API (حتى لو لم يكن المصدر المعتمد): "
                     f"{cal.fmt_both(parse_iso(st.get('last_botapi_post_at')))}")
        pr = st.get("probe_result")
        if pr:
            lines.append(f"• آخر فحص مستقل (python -m alert_trader probe): {pr['at']} — "
                         + ", ".join(f"{r['transport']}={'✓' if r['ok'] else '✗'}({len(r['posts'])})"
                                     for r in pr["results"]))
        lines.append("الاستقبال لا يُعتبر مؤكدًا إلا بعد وصول منشور فعلي من بوت المصدر.")
        await self.reply(update, "\n".join(lines))

    async def buttons(self, update: Update, context) -> None:
        routes = {B_STATUS: self.status, B_PORT: self.portfolio, B_POS: self.positions,
                  B_PERF: self.performance, B_ACC: self.accepted, B_REJ: self.rejected,
                  B_SET: self.settings, B_PAUSE: self.pause, B_RESUME: self.resume,
                  B_CLOSE: self.close_all_ask, B_DAY: self.daily, B_WEEK: self.weekly,
                  B_PROBE: self.probe}
        fn = routes.get((update.message.text or "").strip())
        if fn:
            await fn(update, context)

    # --------------------------------------------------------------- jobs
    async def monitor(self, context) -> None:
        try:
            await self.run(self.engine.tick)
        except Exception:
            log.exception("monitor tick failed")
            self.engine.store.event("tick_error", "خطأ في دورة المتابعة", "error")
        await self._maybe_weekly()

    async def _maybe_weekly(self) -> None:
        st = self.engine.store
        start = parse_iso(st.get("paper_week_start"))
        if start and not st.get("weekly_report_sent") and self.engine.clock() >= start + dt.timedelta(days=7):
            st.set("weekly_report_sent", True)
            await send_long(self.notifier.bot, self.env.owner_id, reports.weekly(self.engine))
            await send_long(self.notifier.bot, self.env.owner_id,
                            f"{self.engine.tag()} انتهى أسبوع المحاكاة. البوت يستمر ورقيًا حتى تفعّل الوضع الحقيقي بنفسك.")

    async def daily_job(self, context) -> None:
        day = cal.ny_date(self.engine.clock())
        if cal.is_trading_day(day):
            await send_long(context.bot, self.env.owner_id, reports.daily(self.engine, day))


def build_app(env, engine) -> tuple[Application, Panel]:
    app = Application.builder().token(env.bot_token).build()
    panel = Panel(env, engine)
    panel.notifier = Notifier(app.bot, env.owner_id)
    owner = filters.User(user_id=env.owner_id) & filters.ChatType.PRIVATE
    for name, fn in (("start", panel.start), ("status", panel.status), ("portfolio", panel.portfolio),
                     ("positions", panel.positions), ("performance", panel.performance),
                     ("accepted", panel.accepted), ("rejected", panel.rejected),
                     ("settings", panel.settings), ("pause", panel.pause), ("resume", panel.resume),
                     ("closeall", panel.close_all_ask), ("daily", panel.daily), ("weekly", panel.weekly),
                     ("probe", panel.probe)):
        app.add_handler(CommandHandler(name, fn, filters=owner))
    app.add_handler(CallbackQueryHandler(panel.close_all_cb, pattern=r"^closeall:"))
    if env.channel_id is not None:
        app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POSTS & filters.Chat(env.channel_id),
                                       panel.on_channel))
    app.add_handler(MessageHandler(owner & filters.TEXT & ~filters.COMMAND, panel.buttons))
    app.job_queue.run_repeating(panel.monitor, interval=engine.s.monitor_interval_sec, first=5)
    app.job_queue.run_daily(panel.daily_job, time=dt.time(16, 20, tzinfo=cal.NY))
    return app, panel
