"""The pipeline, identical for paper and live:

    channel post -> record -> parse -> link/dedupe -> validate (Abyan + quotes)
    -> limits -> executor -> order tracking -> position management -> reports

The engine is synchronous and single-writer (one lock); the Telegram layer
calls it from a worker thread and sends the notifications it queues.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import sqlite3
import threading

from . import market_calendar as cal
from .config import Env, Settings
from .models import (FINAL_ORDER_STATES, OPEN_ORDER_STATES, ORDER_CANCELED, ORDER_EXPIRED,
                     ORDER_FILLED, ORDER_PARTIAL, ORDER_REJECTED, ORDER_SUBMITTED,
                     ORDER_UNKNOWN, OptionContract, OrderUpdate, ParsedAlert, Quote, RawMessage)
from .parser import parse
from .quotes import QuoteError
from .storage import Store, iso, now_utc, parse_iso
from .validator import ChainCache, RiskSnapshot, Validator, round_tick

log = logging.getLogger("alert_trader.engine")

ACTIVE = ("pending_entry", "open", "closing")


class Engine:
    def __init__(self, settings: Settings, env: Env, store: Store, quotes, broker,
                 executor, clock=now_utc):
        self.s, self.env, self.store = settings, env, store
        self.quotes, self.broker, self.executor = quotes, broker, executor
        self.clock = clock
        self.chain = ChainCache(broker) if broker is not None else None
        self.validator = Validator(settings, quotes, self.chain)
        self.out: list[tuple[str, str]] = []      # (dedupe key, text) for the UI
        self._lock = threading.RLock()
        self._quote_cache: dict[str, tuple[dt.datetime, Quote | None, str | None]] = {}
        cal.set_overrides(settings.extra_holidays, settings.extra_early_closes)

    # ------------------------------------------------------------------ utils
    @property
    def mode(self) -> str:
        return self.executor.mode

    def tag(self) -> str:
        return "🧪 [ورقي]" if self.mode == "paper" else "💵 [حقيقي]"

    def notify(self, key: str, text: str) -> None:
        self.out.append((key, f"{self.tag()} {text}"))

    def drain(self) -> list[tuple[str, str]]:
        with self._lock:
            out, self.out = self.out, []
            return out

    def _quote(self, contract: OptionContract, now: dt.datetime) -> tuple[Quote | None, str | None]:
        hit = self._quote_cache.get(contract.occ)
        if hit and (now - hit[0]).total_seconds() < 3:
            return hit[1], hit[2]
        try:
            q, err = self.quotes.quote(contract), None
            self.store.set("last_quote_ok_at", iso(now))
        except QuoteError as e:
            q, err = None, str(e)
        except Exception as e:          # network errors from requests
            q, err = None, f"{type(e).__name__}"
        self._quote_cache[contract.occ] = (now, q, err)
        return q, err

    @staticmethod
    def _contract(trade) -> OptionContract:
        return OptionContract(trade["underlying"], trade["right"], trade["strike"],
                              dt.date.fromisoformat(trade["expiry"]))

    # ------------------------------------------------------------- startup
    def start(self) -> None:
        with self._lock:
            now = self.clock()
            if self.mode == "paper" and self.store.get("paper_week_start") is None:
                self.store.set("paper_week_start", iso(now))
                self.store.event("paper_week", "بدء أسبوع المحاكاة", data={"at": iso(now)})
                self.notify("paper_week", f"بدأ أسبوع التداول الورقي: {cal.fmt_both(now)}")
            if self.mode == "paper" and not self.store.q("SELECT 1 FROM ledger WHERE kind='deposit'"):
                self.store.ledger("deposit", self.s.paper_capital_usd, note="رأس المال الورقي")
            self.store.set("mode", self.mode)
            self.reconcile()

    def reconcile(self) -> None:
        """Bring local orders in line with the executor before anything new."""
        working = self.store.q(f"SELECT * FROM orders WHERE status IN ({_ph(OPEN_ORDER_STATES)})",
                               tuple(OPEN_ORDER_STATES))
        fixed = 0
        for order in working:
            if order["status"] == "new":     # never handed over before the stop
                self._apply(order, OrderUpdate(status=ORDER_CANCELED, reason="لم يُرسل قبل التوقف"))
                fixed += 1
                continue
            if order["mode"] != self.mode:
                self.store.event("reconcile", "أمر من وضع آخر لم يُلمس", "warning",
                                 {"order": order["id"], "mode": order["mode"]})
                continue
            if order["status"] in (ORDER_UNKNOWN, ORDER_SUBMITTED) and self.mode == "live":
                upd = self.executor.reconcile_unknown(self._order_view(order))
                if upd is None:
                    self._apply(order, OrderUpdate(status=ORDER_REJECTED,
                                                   reason="لم يُعثر على الأمر لدى الوسيط بعد الانقطاع"))
                else:
                    self._apply(order, upd)
                fixed += 1
        if self.mode == "live" and self.broker is not None:
            self._compare_positions()
        self.store.event("reconcile", "مطابقة عند البدء",
                         data={"working_orders": len(working), "resolved": fixed})
        if working:
            self.notify("reconcile", f"استئناف بعد التشغيل: {len(working)} أمر قيد المتابعة.")

    def _compare_positions(self) -> None:
        try:
            held = {p["symbol"].replace("O:", ""): int(float(p.get("qty", 0)))
                    for p in self.broker.positions() if p.get("asset_class") == "us_option"}
        except Exception as e:
            self.store.set("broker_outage", str(e))
            self.notify("broker_outage", f"تعذرت مطابقة المراكز مع أبيان: {e} — الدخول موقوف.")
            return
        ours = {}
        for t in self.store.q("SELECT occ, qty_open FROM trades WHERE mode='live' AND qty_open>0"):
            ours[t["occ"]] = ours.get(t["occ"], 0) + t["qty_open"]
        diffs = {k: (ours.get(k, 0), held.get(k, 0)) for k in set(ours) | set(held)
                 if ours.get(k, 0) > held.get(k, 0)}
        if diffs:
            self.store.set("broker_outage", "اختلاف المراكز مع أبيان")
            self.notify("position_mismatch", "⚠️ مراكز البوت أكبر مما في أبيان: "
                        + json.dumps(diffs, ensure_ascii=False) + " — الدخول موقوف حتى المراجعة.")

    # --------------------------------------------------------- inbound posts
    def handle_message(self, msg: RawMessage) -> str:
        with self._lock:
            try:
                return self._handle(msg)
            except Exception as e:
                log.exception("message %s failed", msg.message_id)
                self.store.event("processing_error", f"خطأ أثناء معالجة رسالة: {type(e).__name__}: {e}",
                                 "error", {"message_id": msg.message_id})
                self.notify(f"perr:{msg.message_id}:{msg.version}",
                            f"🧯 خطأ داخلي أثناء معالجة رسالة {msg.message_id} — لم يُنفذ شيء. "
                            f"({type(e).__name__})")
                return "error"

    def _allowed_source(self, msg: RawMessage) -> bool:
        if self.env.channel_id is None or msg.chat_id != self.env.channel_id:
            return False
        want = self.env.source_author
        if not want:
            return True
        return want in {msg.author_signature, msg.via_bot, str(msg.sender_id)}

    def _handle(self, msg: RawMessage) -> str:
        now = self.clock()
        if not self._allowed_source(msg):
            self.store.event("foreign", "رسالة من مصدر غير مسموح تم تجاهلها", "warning",
                             {"chat_id": msg.chat_id, "author": msg.author_signature})
            return "ignored_source"
        self.store.set("last_channel_msg_at", iso(now))
        digest = hashlib.sha256(msg.text.encode()).hexdigest()
        prev = self.store.one("SELECT content_hash FROM messages WHERE chat_id=? AND message_id=? "
                              "ORDER BY version DESC LIMIT 1", (msg.chat_id, msg.message_id))
        try:
            row_id = self.store.x(
                "INSERT INTO messages(chat_id, message_id, version, date, edit_date, received_at, text,"
                " transport, author, via_bot, sender_id, reply_to, content_hash)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (msg.chat_id, msg.message_id, msg.version, iso(msg.date), iso(msg.edit_date),
                 iso(msg.received_at), msg.text, msg.transport, msg.author_signature, msg.via_bot,
                 msg.sender_id, msg.reply_to_message_id, digest))
        except sqlite3.IntegrityError:
            self.store.event("duplicate_delivery", "نفس نسخة الرسالة وصلت مرة أخرى",
                             data={"message_id": msg.message_id, "version": msg.version})
            return "duplicate_delivery"
        if prev and prev["content_hash"] == digest:
            self.store.event("edit_nochange", "تعديل بلا تغيير في النص",
                             data={"message_id": msg.message_id})
            return "edit_nochange"

        alert = parse(msg.text, self.s.date_order)
        if msg.version and self._edited_after_entry(msg, alert, row_id, now):
            return "edit_of_trade"
        if alert.kind == "entry":
            return self._entry(msg, alert, row_id, now)
        if alert.kind in ("update", "exit"):
            return self._manage_alert(msg, alert, row_id, now)
        if alert.underlying or alert.occ_symbol or alert.entry_low:
            self._record(msg, alert, row_id, "rejected", ["نوع الرسالة غير واضح (دخول/تعديل/خروج)"])
            self.notify(f"rej:{msg.message_id}:{msg.version}",
                        f"❌ رُفضت رسالة: نوعها غير واضح.\n{_snippet(msg.text)}")
            return "rejected"
        self._record(msg, alert, row_id, "ignored", ["ليست تنبيه تداول"])
        return "ignored"

    def _record(self, msg, alert: ParsedAlert, row_id, status, reasons, checks=None,
                quote: Quote | None = None, fmv=None, trade_id=None) -> int:
        return self.store.x(
            "INSERT INTO alerts(message_row, chat_id, message_id, version, kind, status, reasons,"
            " checks, parsed, quote, fmv, trade_id, mode, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row_id, msg.chat_id, msg.message_id, msg.version, alert.kind, status,
             json.dumps(reasons, ensure_ascii=False), json.dumps(checks or [], ensure_ascii=False),
             json.dumps(alert.as_dict(), ensure_ascii=False),
             json.dumps(quote.as_dict(), ensure_ascii=False) if quote else None,
             fmv, trade_id, self.mode, iso(self.clock())))

    def _edited_after_entry(self, msg, alert, row_id, now) -> bool:
        """An edit of a post that already produced a trade never buys again."""
        trade = self.store.one("SELECT * FROM trades WHERE trade_key=?",
                               (f"{msg.chat_id}:{msg.message_id}",))
        if trade is None:
            return False
        c = alert.contract()
        if alert.kind == "entry" and c and c.occ != trade["occ"]:
            self._record(msg, alert, row_id, "rejected",
                         ["تعديل غيّر العقد بعد تسجيل الصفقة — لن يُنفذ شراء جديد"], trade_id=trade["id"])
            self.notify(f"edit:{trade['id']}:{msg.version}",
                        f"⚠️ تعديل على تنبيه الصفقة #{trade['id']} غيّر العقد إلى {c.label()}. "
                        "لم يُنفذ أي شراء جديد؛ الصفقة الأصلية كما هي.")
            return True
        if alert.kind == "entry" and (alert.stop is not None or alert.targets):
            alert.kind = "update"
            self._apply_update(trade, alert, msg, row_id, source="تعديل رسالة الدخول")
            return True
        self._record(msg, alert, row_id, "info", ["تعديل على رسالة صفقة قائمة دون تغيير قابل للتطبيق"],
                     trade_id=trade["id"])
        return True

    # ------------------------------------------------------------------ entry
    def entries_paused(self) -> str | None:
        manual = self.store.get("entries_paused_manual")
        if manual:
            return f"إيقاف يدوي ({manual})"
        for key, label in (("data_outage", "تعطل بيانات الأسعار"), ("broker_outage", "مشكلة اتصال/مطابقة أبيان")):
            v = self.store.get(key)
            if v:
                return f"{label}: {v}"
        if self.daily_pnl() <= -self.s.daily_loss_limit_usd:
            return "تجاوز حد الخسارة اليومية"
        return None

    def risk_snapshot(self) -> RiskSnapshot:
        open_n = self.store.one(f"SELECT COUNT(*) n FROM trades WHERE status IN ({_ph(ACTIVE)}) AND mode=?",
                                (*ACTIVE, self.mode))["n"]
        exposure = self.store.one(
            f"SELECT COALESCE(SUM(qty_open * avg_entry * multiplier + entry_fees),0) v FROM trades "
            f"WHERE status IN ({_ph(ACTIVE)}) AND mode=? AND qty_open>0", (*ACTIVE, self.mode))["v"]
        holds = self.store.one("SELECT COALESCE(SUM(reserved),0) v FROM orders WHERE side='buy' "
                               f"AND status IN ({_ph(OPEN_ORDER_STATES)}) AND mode=?",
                               (*OPEN_ORDER_STATES, self.mode))["v"]
        if self.mode == "paper":
            cash, reserved = self.store.paper_cash()
            available = cash - reserved
        else:
            try:
                available = float(self.broker.account().get("buying_power") or 0)
            except Exception:
                available = 0.0
        return RiskSnapshot(open_n, exposure + holds, self.daily_pnl(), available,
                            self.entries_paused())

    def _entry(self, msg, alert, row_id, now) -> str:
        key = f"{msg.chat_id}:{msg.message_id}"
        c = alert.contract()
        dup = None
        if self.store.one("SELECT 1 FROM trades WHERE trade_key=?", (key,)):
            dup = "هذا التنبيه سُجّل كصفقة مسبقًا"
        elif alert.alert_ref and self.store.one(
                f"SELECT 1 FROM trades WHERE alert_ref=? AND status IN ({_ph(ACTIVE)})",
                (alert.alert_ref, *ACTIVE)):
            dup = f"يوجد صفقة نشطة بنفس معرّف التنبيه {alert.alert_ref}"
        elif c:
            since = iso(now - dt.timedelta(seconds=self.s.duplicate_window_sec))
            if self.store.one(f"SELECT 1 FROM trades WHERE occ=? AND (status IN ({_ph(ACTIVE)}) "
                              "OR created_at>=?)", (c.occ, *ACTIVE, since)):
                dup = f"يوجد صفقة نشطة/حديثة على نفس العقد {c.occ}"
        if dup:
            self._record(msg, alert, row_id, "duplicate", [dup])
            self.store.event("duplicate_alert", dup, data={"message_id": msg.message_id})
            self.notify(f"dup:{msg.message_id}:{msg.version}", f"🔁 تنبيه مكرر تم تجاهله: {dup}")
            return "duplicate"

        fmv_lookup = self.broker.fmv if self.broker is not None else None
        d = self.validator.entry(alert, msg, self.risk_snapshot(), now, fmv_lookup)
        if self.broker is not None:
            if self.broker.last_error:
                self.store.set("last_abyan_error", self.broker.last_error)
            if self.broker.last_ok:
                self.store.set("last_abyan_ok_at", iso(self.broker.last_ok))
        if not d.ok:
            self._record(msg, alert, row_id, "rejected", d.reasons, d.checks, d.quote, d.fmv)
            label = c.label() if c else (alert.underlying or "؟")
            self.notify(f"rej:{msg.message_id}:{msg.version}",
                        f"❌ رُفض تنبيه {label}\nالسبب: " + "؛ ".join(d.reasons[:4]))
            return "rejected"

        with self.store.tx():
            trade_id = self.store.x(
                "INSERT INTO trades(trade_key, mode, alert_ref, occ, underlying, right, strike, expiry,"
                " multiplier, status, qty_planned, stop, targets, alert_entry_low, alert_entry_high,"
                " alert_time, received_at, broker_verified, quote_source, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, self.mode, alert.alert_ref, c.occ, c.underlying, c.right, c.strike,
                 c.expiry.isoformat(), d.multiplier, "pending_entry", d.qty, alert.stop,
                 json.dumps(alert.targets), alert.entry_low, alert.entry_high, iso(msg.date),
                 iso(msg.received_at), int(d.broker_verified), d.quote.source, iso(now)))
            self._record(msg, alert, row_id, "accepted", [], d.checks, d.quote, d.fmv, trade_id)
            order = self._new_order(trade_id, "buy", "entry", d.qty, d.limit,
                                    self.s.entry_order_ttl_sec, now,
                                    reserved=d.est_cost)
        self._submit(order, fmv=d.fmv)
        verified = "" if d.broker_verified else "\n⚠️ لم يُتحقق من العقد في أبيان"
        self.notify(f"accepted:{trade_id}",
                    f"✅ قُبل التنبيه — صفقة #{trade_id}\n{c.label()}\n"
                    f"أُرسل أمر شراء محدد: {d.qty} عقد بسعر حد {d.limit:.2f} "
                    f"(الطلب {d.quote.ask:.2f} / العرض {d.quote.bid:.2f} من {d.quote.source})\n"
                    f"التكلفة التقديرية مع الرسوم {d.est_cost:.2f}$ — بانتظار التنفيذ{verified}")
        return "accepted"

    # ----------------------------------------------------------- orders
    def _new_order(self, trade_id, side, purpose, qty, limit, ttl, now, reserved=0.0):
        oid = self.store.x(
            "INSERT INTO orders(trade_id, mode, side, purpose, qty, limit_price, status, reserved,"
            " created_at, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (trade_id, self.mode, side, purpose, qty, limit, "new", round(reserved, 4),
             iso(now), iso(now + dt.timedelta(seconds=ttl))))
        if reserved and self.mode == "paper":
            self.store.ledger("reserve", reserved, oid, "حجز لأمر شراء")
        return self.store.one("SELECT * FROM orders WHERE id=?", (oid,))

    def _order_view(self, order) -> dict:
        trade = self.store.one("SELECT occ FROM trades WHERE id=?", (order["trade_id"],))
        view = dict(order)
        view["occ"] = trade["occ"]
        return view

    def _submit(self, order, fmv=None) -> None:
        now = self.clock()
        self.store.x("UPDATE orders SET status=?, submitted_at=?, updated_at=? WHERE id=?",
                     (ORDER_SUBMITTED, iso(now), iso(now), order["id"]))
        order = self.store.one("SELECT * FROM orders WHERE id=?", (order["id"],))
        try:
            if self.mode == "live":
                upd = self.executor.submit(self._order_view(order), fmv=fmv)
            else:
                upd = self.executor.submit(self._order_view(order))
        except Exception as e:
            self.store.event("order_error", f"خطأ في إرسال الأمر: {e}", "error", {"order": order["id"]})
            upd = OrderUpdate(status=ORDER_REJECTED, reason=str(e))
        self._apply(order, upd)

    def _apply(self, order, upd: OrderUpdate) -> None:
        """Record an executor report: fills, fees, cash, trade state, notices."""
        now = self.clock()
        trade = self.store.one("SELECT * FROM trades WHERE id=?", (order["trade_id"],))
        mult = trade["multiplier"]
        with self.store.tx():
            for f in upd.fills:
                self.store.x("INSERT INTO fills(order_id, qty, price, fees, reference_price, slippage,"
                             " quote, ts) VALUES(?,?,?,?,?,?,?,?)",
                             (order["id"], f["qty"], f["price"], f["fees"], f.get("reference_price"),
                              f.get("slippage"), json.dumps(f.get("quote"), ensure_ascii=False), iso(now)))
                gross = f["qty"] * f["price"] * mult
                if order["side"] == "buy":
                    if self.mode == "paper":
                        self.store.ledger("buy", -gross, order["id"])
                        self.store.ledger("fee", -f["fees"], order["id"])
                        release = min(order["reserved"], f["qty"] * order["limit_price"] * mult
                                      + f["fees"])
                        self.store.ledger("release", -release, order["id"])
                        self.store.x("UPDATE orders SET reserved=reserved-? WHERE id=?",
                                     (release, order["id"]))
                        order = self.store.one("SELECT * FROM orders WHERE id=?", (order["id"],))
                    filled = trade["qty_filled"] + f["qty"]
                    avg = ((trade["avg_entry"] or 0) * trade["qty_filled"] + f["price"] * f["qty"]) / filled
                    self.store.x("UPDATE trades SET qty_filled=?, qty_open=qty_open+?, avg_entry=?,"
                                 " entry_fees=entry_fees+?, status='open', opened_at=COALESCE(opened_at, ?)"
                                 " WHERE id=?", (filled, f["qty"], round(avg, 4), f["fees"], iso(now),
                                                  trade["id"]))
                else:
                    if self.mode == "paper":
                        self.store.ledger("sell", gross, order["id"])
                        self.store.ledger("fee", -f["fees"], order["id"])
                    self.store.x("UPDATE trades SET qty_open=qty_open-?, exit_qty=exit_qty+?,"
                                 " exit_value=exit_value+?, exit_fees=exit_fees+? WHERE id=?",
                                 (f["qty"], f["qty"], gross, f["fees"], trade["id"]))
                trade = self.store.one("SELECT * FROM trades WHERE id=?", (trade["id"],))
            final = upd.status in FINAL_ORDER_STATES
            if final and self.mode == "paper" and order["reserved"] > 0:
                self.store.ledger("release", -order["reserved"], order["id"], "تحرير الحجز المتبقي")
                self.store.x("UPDATE orders SET reserved=0 WHERE id=?", (order["id"],))
            self.store.x("UPDATE orders SET status=?, filled_qty=?, avg_fill_price=?, fees=?,"
                         " broker_order_id=COALESCE(?, broker_order_id), reason=?, updated_at=?,"
                         " sim_last_quote=COALESCE(?, sim_last_quote) WHERE id=?",
                         (upd.status, upd.filled_qty, upd.avg_fill_price, upd.fees, upd.broker_order_id,
                          upd.reason, iso(now),
                          upd.fills[-1]["quote"]["quote_time"] if upd.fills else None, order["id"]))
        self._order_notice(order, trade, upd)
        if final:
            self._after_final(order)

    def _order_notice(self, order, trade, upd: OrderUpdate) -> None:
        side = "شراء" if order["side"] == "buy" else "بيع"
        tid = trade["id"]
        if upd.fills:
            f = upd.fills[-1]
            if upd.status == ORDER_PARTIAL:
                self.notify(f"fill:{order['id']}:{upd.filled_qty}",
                            f"🟡 تنفيذ جزئي ({side}) صفقة #{tid}: {upd.filled_qty}/{order['qty']} "
                            f"بسعر {f['price']:.2f} — المتبقي {order['qty'] - upd.filled_qty}")
            elif upd.status == ORDER_FILLED:
                self.notify(f"fill:{order['id']}:done",
                            f"🟢 تم تنفيذ {side} صفقة #{tid}: {upd.filled_qty} عقد بمتوسط "
                            f"{upd.avg_fill_price:.2f} (رسوم {upd.fees:.2f}$) — {trade['occ']}")
        if upd.status == ORDER_REJECTED:
            self.notify(f"orej:{order['id']}", f"⛔ رُفض أمر {side} صفقة #{tid}: {upd.reason}")
        elif upd.status in (ORDER_EXPIRED, ORDER_CANCELED) and order["side"] == "buy":
            msg = "انتهت صلاحية" if upd.status == ORDER_EXPIRED else "أُلغي"
            self.notify(f"oend:{order['id']}",
                        f"⌛ {msg} أمر الشراء لصفقة #{tid} — نُفذ {upd.filled_qty}/{order['qty']}")
        elif upd.status == ORDER_UNKNOWN:
            self.notify(f"ounk:{order['id']}",
                        f"⚠️ حالة أمر صفقة #{tid} غير معروفة بعد انقطاع — سيتم التحقق لدى الوسيط قبل أي إعادة إرسال")

    def _after_final(self, order) -> None:
        trade = self.store.one("SELECT * FROM trades WHERE id=?", (order["trade_id"],))
        working = self.store.one(f"SELECT 1 FROM orders WHERE trade_id=? AND status IN "
                                 f"({_ph(OPEN_ORDER_STATES)})", (trade["id"], *OPEN_ORDER_STATES))
        if working:
            return
        now = self.clock()
        if trade["qty_filled"] == 0 and trade["status"] == "pending_entry":
            self.store.x("UPDATE trades SET status='canceled', closed_at=?, exit_reason=? WHERE id=?",
                         (iso(now), "لم يُنفذ أمر الدخول", trade["id"]))
            return
        if trade["qty_open"] == 0 and trade["qty_filled"] > 0 and trade["status"] != "closed":
            pnl = (trade["exit_value"] - trade["qty_filled"] * trade["avg_entry"] * trade["multiplier"]
                   - trade["entry_fees"] - trade["exit_fees"])
            self.store.x("UPDATE trades SET status='closed', closed_at=?, realized_pnl=? WHERE id=?",
                         (iso(now), round(pnl, 2), trade["id"]))
            self.notify(f"closed:{trade['id']}",
                        f"🏁 أُغلقت صفقة #{trade['id']} {trade['occ']} — السبب: {trade['exit_reason'] or '—'}\n"
                        f"النتيجة بعد الرسوم: {pnl:+.2f}$")
        elif trade["status"] == "closing":
            self.store.x("UPDATE trades SET status='open' WHERE id=?", (trade["id"],))

    # --------------------------------------------------- update / exit alerts
    def _link(self, msg, alert) -> tuple[object | None, str | None]:
        if msg.reply_to_message_id:
            t = self.store.one(f"SELECT * FROM trades WHERE trade_key=? AND status IN ({_ph(ACTIVE)})",
                               (f"{msg.chat_id}:{msg.reply_to_message_id}", *ACTIVE))
            if t:
                c = alert.contract()
                if c and c.occ != t["occ"]:
                    return None, "الرسالة ترد على صفقة لكن تذكر عقدًا مختلفًا"
                return t, None
        tries = []
        if alert.alert_ref:
            tries.append(("alert_ref=?", (alert.alert_ref,)))
        c = alert.contract()
        if c:
            tries.append(("occ=?", (c.occ,)))
        elif alert.underlying:
            if alert.right:
                tries.append(("underlying=? AND right=?", (alert.underlying, alert.right)))
            else:
                tries.append(("underlying=?", (alert.underlying,)))
        for where, args in tries:
            rows = self.store.q(f"SELECT * FROM trades WHERE {where} AND mode=? AND status IN ({_ph(ACTIVE)})",
                                (*args, self.mode, *ACTIVE))
            if len(rows) == 1:
                return rows[0], None
            if len(rows) > 1:
                return None, "التحديث يطابق أكثر من صفقة مفتوحة — مرفوض لالتباسه"
        return None, "لا توجد صفقة مفتوحة مطابقة لهذا التحديث"

    def _manage_alert(self, msg, alert, row_id, now) -> str:
        if alert.errors:
            self._record(msg, alert, row_id, "rejected", alert.errors)
            self.notify(f"rej:{msg.message_id}:{msg.version}",
                        "❌ رُفض تحديث/خروج: " + "؛ ".join(alert.errors))
            return "rejected"
        trade, why = self._link(msg, alert)
        if trade is None:
            self._record(msg, alert, row_id, "rejected", [why])
            self.notify(f"rej:{msg.message_id}:{msg.version}", f"❌ رُفض {_kind_ar(alert.kind)}: {why}")
            return "rejected"
        if alert.kind == "update":
            return self._apply_update(trade, alert, msg, row_id, source="تنبيه تعديل")
        return self._apply_exit(trade, alert, msg, row_id, now)

    def _apply_update(self, trade, alert, msg, row_id, source) -> str:
        changes, problems = {}, []
        if alert.stop is not None:
            if alert.stop <= 0:
                problems.append("وقف غير موجب")
            else:
                changes["stop"] = alert.stop
        if alert.targets:
            if alert.targets != sorted(alert.targets) or min(alert.targets) <= 0:
                problems.append("أهداف غير مرتبة أو غير موجبة")
            else:
                changes["targets"] = json.dumps(alert.targets)
        if problems or not changes:
            reasons = problems or ["تحديث بلا وقف أو أهداف قابلة للتطبيق"]
            status = "rejected" if problems else "info"
            self._record(msg, alert, row_id, status, reasons, trade_id=trade["id"])
            if problems:
                self.notify(f"rej:{msg.message_id}:{msg.version}", "❌ رُفض التعديل: " + "؛ ".join(problems))
            return status
        if changes.get("stop") == trade["stop"]:
            changes.pop("stop")
        if changes.get("targets") == trade["targets"]:
            changes.pop("targets", None)
        if not changes:
            self._record(msg, alert, row_id, "info", ["التعديل لا يغيّر الوقف أو الأهداف"],
                         trade_id=trade["id"])
            return "info"
        # Targets already reached keep counting: the next exit uses the next index.
        sets = ", ".join(f"{k}=?" for k in changes)
        self.store.x(f"UPDATE trades SET {sets} WHERE id=?", (*changes.values(), trade["id"]))
        self._record(msg, alert, row_id, "applied", [], trade_id=trade["id"])
        parts = []
        if "stop" in changes:
            parts.append(f"الوقف {trade['stop'] or '—'} ← {changes['stop']:g}")
        if "targets" in changes:
            parts.append(f"الأهداف ← {', '.join(f'{t:g}' for t in alert.targets)}")
        self.notify(f"upd:{trade['id']}:{msg.message_id}:{msg.version}",
                    f"✏️ {source} طُبّق على صفقة #{trade['id']}: " + "، ".join(parts))
        return "applied"

    def _apply_exit(self, trade, alert, msg, row_id, now) -> str:
        if trade["status"] == "pending_entry" and trade["qty_filled"] == 0:
            self._cancel_working(trade["id"], "خروج من المصدر قبل التنفيذ")
            self._record(msg, alert, row_id, "applied", ["إلغاء أمر الدخول غير المنفذ"], trade_id=trade["id"])
            return "applied"
        request = {"reason": "تنبيه خروج من المصدر", "fraction": alert.exit_fraction,
                   "message_id": msg.message_id}
        self.store.x("UPDATE trades SET exit_request=? WHERE id=?",
                     (json.dumps(request, ensure_ascii=False), trade["id"]))
        self._record(msg, alert, row_id, "applied", [], trade_id=trade["id"])
        frac = f"{int(alert.exit_fraction * 100)}%" if alert.exit_fraction else "كامل"
        self.notify(f"exitreq:{trade['id']}:{msg.message_id}",
                    f"📤 تنبيه خروج ({frac}) لصفقة #{trade['id']} — سيُرسل أمر بيع محدد عند توفر سعر حديث")
        self._manage_trade(self.store.one("SELECT * FROM trades WHERE id=?", (trade["id"],)), now)
        return "applied"

    def _cancel_working(self, trade_id, why) -> None:
        for order in self.store.q(f"SELECT * FROM orders WHERE trade_id=? AND status IN "
                                  f"({_ph(OPEN_ORDER_STATES)})", (trade_id, *OPEN_ORDER_STATES)):
            try:
                upd = self.executor.cancel(self._order_view(order))
            except Exception as e:
                self.store.event("cancel_error", str(e), "error", {"order": order["id"]})
                continue
            upd.reason = why
            self._apply(order, upd)

    # ----------------------------------------------------------- owner actions
    def pause(self, who: str) -> None:
        with self._lock:
            self.store.set("entries_paused_manual", who)
            self.store.event("pause", "إيقاف الدخول الجديد", data={"by": who})

    def resume(self) -> None:
        with self._lock:
            self.store.set("entries_paused_manual", None)
            self.store.event("resume", "استئناف الدخول الجديد")

    def close_all(self, who: str) -> int:
        with self._lock:
            now, n = self.clock(), 0
            for t in self.store.q(f"SELECT * FROM trades WHERE mode=? AND status IN ({_ph(ACTIVE)})",
                                  (self.mode, *ACTIVE)):
                n += 1
                if t["qty_filled"] == 0:
                    self._cancel_working(t["id"], "إغلاق الكل")
                    continue
                self._cancel_working_entries(t["id"])
                self.store.x("UPDATE trades SET exit_request=? WHERE id=?",
                             (json.dumps({"reason": "إغلاق يدوي للكل", "fraction": None}), t["id"]))
                self._manage_trade(self.store.one("SELECT * FROM trades WHERE id=?", (t["id"],)), now)
            self.store.event("close_all", "طلب إغلاق جميع المراكز", data={"by": who, "trades": n})
            return n

    def _cancel_working_entries(self, trade_id) -> None:
        for order in self.store.q(f"SELECT * FROM orders WHERE trade_id=? AND side='buy' AND status IN "
                                  f"({_ph(OPEN_ORDER_STATES)})", (trade_id, *OPEN_ORDER_STATES)):
            upd = self.executor.cancel(self._order_view(order))
            upd.reason = "إغلاق الكل"
            self._apply(order, upd)

    # ------------------------------------------------------------- the loop
    def tick(self) -> None:
        with self._lock:
            now = self.clock()
            self._poll_orders(now)
            failures = 0
            active = self.store.q(f"SELECT * FROM trades WHERE mode=? AND status IN ({_ph(ACTIVE)})",
                                  (self.mode, *ACTIVE))
            for t in active:
                try:
                    if not self._manage_trade(t, now):
                        failures += 1
                except Exception as e:
                    log.exception("managing trade %s failed", t["id"])
                    self.store.event("manage_error", f"صفقة #{t['id']}: {type(e).__name__}: {e}", "error")
                    self.notify(f"merr:{t['id']}", f"🧯 خطأ في متابعة صفقة #{t['id']}: {type(e).__name__}")
            self._data_health(failures, len(active), now)
            self._broker_health(now)
            self._snapshot(now)

    def _poll_orders(self, now) -> None:
        for order in self.store.q(f"SELECT * FROM orders WHERE mode=? AND status IN "
                                  f"({_ph(OPEN_ORDER_STATES)})", (self.mode, *OPEN_ORDER_STATES)):
            trade = self.store.one("SELECT * FROM trades WHERE id=?", (order["trade_id"],))
            quote, _ = self._quote(self._contract(trade), now)
            try:
                upd = self.executor.poll(self._order_view(order), quote, now)
            except Exception as e:
                self.store.event("poll_error", str(e), "error", {"order": order["id"]})
                continue
            if upd is not None and (upd.fills or upd.status != order["status"]):
                self._apply(order, upd)

    def _manage_trade(self, t, now) -> bool:
        """Exit rules for one trade. Returns False when no usable quote."""
        if t["qty_open"] <= 0:
            return True
        c = self._contract(t)
        bounds = cal.session_bounds(c.expiry)
        if bounds is not None and now >= bounds[1]:
            self._cancel_working(t["id"], "انتهاء العقد")
            return self._settle_expired(self.store.one("SELECT * FROM trades WHERE id=?", (t["id"],)),
                                        c, now)
        if self.store.one(f"SELECT 1 FROM orders WHERE trade_id=? AND side='sell' AND status IN "
                          f"({_ph(OPEN_ORDER_STATES)})", (t["id"], *OPEN_ORDER_STATES)):
            return True
        q, err = self._quote(c, now)
        age = q.age_sec(now) if q is not None else None
        fresh = (q is not None and q.bid is not None and age is not None
                 and age <= self.s.max_quote_age_sec
                 and (not q.delayed or self.s.allow_delayed_quotes))
        if q is not None and q.bid is not None:
            self.store.x("UPDATE trades SET last_bid=?, last_mark_at=? WHERE id=?",
                         (q.bid, iso(q.quote_time or now), t["id"]))
        if not fresh:
            key = f"stale_logged:{t['id']}"
            last = parse_iso(self.store.get(key))
            if not last or (now - last).total_seconds() > 600:
                self.store.set(key, iso(now))
                self.store.event("stale_quote", f"لا سعر حديث لـ {t['occ']}: {err or 'سعر قديم'}", "warning")
            return False
        if not cal.is_open(now):
            return True

        req = json.loads(t["exit_request"]) if t["exit_request"] else None
        if req is not None and "until_open" not in req:      # fresh request (alert / close all)
            frac = req.get("fraction")
            qty = t["qty_open"] if not frac else max(1, math.floor(t["qty_open"] * frac))
            req["until_open"] = t["qty_open"] - min(qty, t["qty_open"])
        if req is not None and t["qty_open"] <= req["until_open"]:
            req = None
        reason, qty = self._exit_decision(t, q, now)
        if reason:
            until = t["qty_open"] - min(qty, t["qty_open"])
            if req is None or until < req["until_open"]:
                req = {"reason": reason, "until_open": until}
        self.store.x("UPDATE trades SET exit_request=? WHERE id=?",
                     (json.dumps(req, ensure_ascii=False) if req else None, t["id"]))
        if req is None:
            return True
        qty = t["qty_open"] - req["until_open"]
        tick = self.s.tick(q.bid)
        limit = max(tick, round_tick(q.bid - self.s.slippage_ticks * tick, tick, up=False))
        attempts = self.store.one("SELECT COUNT(*) n FROM orders WHERE trade_id=? AND side='sell' "
                                  "AND status IN ('expired','canceled','rejected')", (t["id"],))["n"]
        self.store.x("UPDATE trades SET status='closing', exit_reason=? WHERE id=?",
                     (req["reason"], t["id"]))
        order = self._new_order(t["id"], "sell", req["reason"][:60], qty, limit,
                                self.s.exit_order_ttl_sec, now)
        self._submit(order)
        self.notify(f"exit:{t['id']}:{req['reason']}",
                    f"📤 أُرسل أمر بيع محدد لصفقة #{t['id']}: {qty} عقد بسعر حد {limit:.2f} "
                    f"(العرض {q.bid:.2f}) — السبب: {req['reason']}")
        if attempts and attempts % max(1, self.s.exit_max_reprices) == 0:
            self.notify(f"exit_slow:{t['id']}:{attempts}",
                        f"⚠️ الخروج من صفقة #{t['id']} لم يكتمل بعد {attempts} محاولات — "
                        "السعر يتحرك أو السيولة ضعيفة؛ المحاولة مستمرة")
        return True

    def _exit_decision(self, t, q: Quote, now) -> tuple[str | None, int]:
        s, open_qty = self.s, t["qty_open"]
        today = cal.ny_date(now)
        close = cal.session_bounds(today)[1]
        if t["expiry"] == today.isoformat() and \
                now >= close - dt.timedelta(minutes=s.close_before_expiry_minutes):
            return "إغلاق إلزامي قبل الانتهاء", open_qty
        ews = s.exit_without_source or {}
        stop = t["stop"]
        if stop is None and ews.get("stop_pct"):
            stop = t["avg_entry"] * (1 - ews["stop_pct"] / 100)
        if stop is not None and q.bid <= stop:
            return f"وقف الخسارة ({stop:.2f}) — التنفيذ بسعر السوق المتاح وليس مضمونًا", open_qty
        targets = json.loads(t["targets"] or "[]")
        if not targets and ews.get("target_pct"):
            targets = [round(t["avg_entry"] * (1 + ews["target_pct"] / 100), 2)]
        hit = t["targets_hit"]
        if hit < len(targets) and q.bid >= targets[hit]:
            split = s.targets_split or [1.0]
            last = hit >= len(targets) - 1 or hit >= len(split) - 1
            qty = open_qty if last else max(1, math.floor(t["qty_filled"] * split[hit]))
            updates = "targets_hit=targets_hit+1"
            args: tuple = ()
            if hit == 0 and s.move_stop_to_entry_after_first_target:
                updates += ", stop=?"
                args = (t["avg_entry"],)
            self.store.x(f"UPDATE trades SET {updates} WHERE id=?", (*args, t["id"]))
            return f"الهدف {hit + 1} ({targets[hit]:g})", qty
        if ews.get("max_hold_minutes") and t["opened_at"]:
            held = (now - parse_iso(t["opened_at"])).total_seconds() / 60
            if held >= ews["max_hold_minutes"]:
                return f"انتهاء مدة الاحتفاظ ({ews['max_hold_minutes']} دقيقة) دون خروج من المصدر", open_qty
        if ews.get("close_at_session_end_minutes") is not None and \
                now >= close - dt.timedelta(minutes=ews["close_at_session_end_minutes"]):
            return "إغلاق نهاية الجلسة حسب الإعدادات", open_qty
        return None, 0

    def _settle_expired(self, t, c: OptionContract, now) -> bool:
        """A position still open after its last session: record what expiry does.

        Paper approximation: valued at intrinsic value from the last underlying
        price available; OCC auto-exercises contracts 0.01$ or more in the money.
        """
        q, _ = self._quote(c, now)
        spot = q.underlying_price if q else None
        if spot is None:
            self.notify(f"expiry:{t['id']}", f"⚠️ صفقة #{t['id']} انتهى عقدها دون إغلاق ولا يتوفر سعر الأصل لتسويتها")
            return True
        intrinsic = max(0.0, spot - c.strike) if c.right == "C" else max(0.0, c.strike - spot)
        value = round(intrinsic, 2)
        qty = t["qty_open"]
        gross = value * qty * t["multiplier"]
        pnl = (t["exit_value"] + gross - t["qty_filled"] * t["avg_entry"] * t["multiplier"]
               - t["entry_fees"] - t["exit_fees"])
        reason = ("انتهى داخل المال — يُمرَّن تلقائيًا (≥0.01$) ويتطلب استلام/تسليم أسهم"
                  if value >= 0.01 else "انتهى خارج المال بلا قيمة")
        with self.store.tx():
            if self.mode == "paper" and gross:
                self.store.ledger("sell", gross, None, "تسوية انتهاء ورقية بالقيمة الذاتية")
            self.store.x("UPDATE trades SET qty_open=0, exit_qty=exit_qty+?, exit_value=exit_value+?,"
                         " status='expired', closed_at=?, exit_reason=?, realized_pnl=? WHERE id=?",
                         (qty, gross, iso(now), reason, round(pnl, 2), t["id"]))
        self.notify(f"expiry:{t['id']}", f"⚠️ صفقة #{t['id']} {t['occ']}: {reason}. النتيجة {pnl:+.2f}$")
        return True

    def _data_health(self, failures, active, now) -> None:
        if active and failures == active:
            if not self.store.get("data_outage"):
                self.store.set("data_outage", f"لا أسعار حديثة منذ {cal.fmt_both(now)}")
                self.store.event("data_outage", "تعطل بيانات الأسعار", "error")
                self.notify("data_outage", "⚠️ تعطلت أسعار العرض/الطلب: أُوقف الدخول الجديد، "
                            "ومتابعة المراكز مستمرة وستُنفذ قواعد الخروج عند عودة البيانات.")
        elif self.store.get("data_outage") and (failures == 0 and active):
            self.store.set("data_outage", None)
            self.store.event("data_restored", "عادت بيانات الأسعار")
            self.notify("data_restored", "✅ عادت أسعار العرض/الطلب — استؤنف الدخول (ما لم يكن موقوفًا لسبب آخر).")

    def _broker_health(self, now) -> None:
        if self.broker is None:
            return
        last = parse_iso(self.store.get("last_broker_ping"))
        if last and (now - last).total_seconds() < 300:
            return
        self.store.set("last_broker_ping", iso(now))
        try:
            self.broker.account()
        except Exception as e:
            self.store.set("last_abyan_error", str(e))
            if self.mode == "live" or not self.s.paper_allow_without_broker_verification:
                if not self.store.get("broker_outage"):
                    self.store.set("broker_outage", str(e))
                    self.notify("broker_outage", f"⚠️ تعذر الاتصال بأبيان: {e} — الدخول الجديد موقوف.")
            return
        self.store.set("last_abyan_ok_at", iso(now))
        if self.store.get("broker_outage") and "اختلاف" not in self.store.get("broker_outage"):
            self.store.set("broker_outage", None)
            self.notify("broker_restored", "✅ عاد الاتصال بأبيان.")
            if self.mode == "live":
                self.reconcile()

    # ------------------------------------------------------------- numbers
    def daily_pnl(self, day: dt.date | None = None) -> float:
        day = day or cal.ny_date(self.clock())
        start = dt.datetime.combine(day, dt.time(0), cal.NY).astimezone(cal.UTC)
        end = start + dt.timedelta(days=1)
        rows = self.store.q(
            "SELECT f.qty, f.price, f.fees, o.side, t.avg_entry, t.multiplier FROM fills f "
            "JOIN orders o ON o.id=f.order_id JOIN trades t ON t.id=o.trade_id "
            "WHERE o.mode=? AND f.ts>=? AND f.ts<?", (self.mode, iso(start), iso(end)))
        realized = 0.0
        for r in rows:
            realized -= r["fees"]
            if r["side"] == "sell":
                realized += r["qty"] * (r["price"] - r["avg_entry"]) * r["multiplier"]
        for t in self.store.q("SELECT * FROM trades WHERE mode=? AND status='expired' AND closed_at>=? "
                              "AND closed_at<?", (self.mode, iso(start), iso(end))):
            realized += t["realized_pnl"] or 0
        if self.s.daily_loss_method == "realized_plus_unrealized":
            for t in self.store.q("SELECT * FROM trades WHERE mode=? AND qty_open>0", (self.mode,)):
                if t["last_bid"] is not None:
                    realized += t["qty_open"] * (t["last_bid"] - t["avg_entry"]) * t["multiplier"]
        return round(realized, 2)

    def equity(self) -> dict:
        cash, reserved = self.store.paper_cash() if self.mode == "paper" else (0.0, 0.0)
        value, stale = 0.0, 0
        for t in self.store.q("SELECT * FROM trades WHERE mode=? AND qty_open>0", (self.mode,)):
            mark = t["last_bid"] if t["last_bid"] is not None else t["avg_entry"]
            stale += t["last_bid"] is None
            value += t["qty_open"] * mark * t["multiplier"]
        return {"cash": cash, "reserved": reserved, "positions_value": round(value, 2),
                "equity": round(cash + value, 2), "stale_marks": stale}

    def _snapshot(self, now) -> None:
        last = parse_iso(self.store.get("last_equity_at"))
        if last and (now - last).total_seconds() < 60:
            return
        e = self.equity()
        self.store.x("INSERT INTO equity(ts, mode, cash, reserved, positions_value, equity, stale_marks)"
                     " VALUES(?,?,?,?,?,?,?)", (iso(now), self.mode, e["cash"], e["reserved"],
                                               e["positions_value"], e["equity"], e["stale_marks"]))
        self.store.set("last_equity_at", iso(now))


def _ph(values) -> str:
    return ",".join("?" * len(values))


def _snippet(text: str, n: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


def _kind_ar(kind: str) -> str:
    return {"update": "التعديل", "exit": "الخروج", "entry": "الدخول"}.get(kind, kind)
