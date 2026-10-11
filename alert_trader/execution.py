"""Order executors. Analysis, validation and position management are shared;
only this layer differs between paper and live.

PaperExecutor never talks to the broker. LiveExecutor refuses to exist unless
every live condition holds, and re-checks the guard on every single write, so
a paper run cannot place a real order even if a code path tried to.
"""
from __future__ import annotations

import datetime as dt

from . import fees as fee_model
from .broker.abyan import BrokerError, BrokerUnavailable, LiveTradingBlocked
from .config import Settings
from .models import (ORDER_ACCEPTED, ORDER_CANCELED, ORDER_EXPIRED, ORDER_FILLED,
                     ORDER_PARTIAL, ORDER_REJECTED, ORDER_UNKNOWN, OrderUpdate, Quote)
from .storage import iso, parse_iso


class PaperExecutor:
    """Simulated fills against real quotes, conservatively.

    * Buys fill at the ask, sells at the bid, plus configured slippage, never
      through the limit.
    * Only a quote stamped after the order reached the market (alert latency
      included) and within the freshness limit can fill it. No quote, a stale
      quote, or a quote without displayed size fills nothing.
    * Each new quote can fill at most its displayed size; the same quote is
      never consumed twice, so a large order fills in pieces.
    """
    mode = "paper"
    label = "ورقي"

    def __init__(self, settings: Settings):
        self.s = settings

    def submit(self, order) -> OrderUpdate:
        return OrderUpdate(status=ORDER_ACCEPTED, broker_order_id=f"paper-{order['id']}")

    def cancel(self, order) -> OrderUpdate:
        return OrderUpdate(status=ORDER_CANCELED, filled_qty=order["filled_qty"],
                           avg_fill_price=order["avg_fill_price"], fees=order["fees"])

    def poll(self, order, quote: Quote | None, now: dt.datetime) -> OrderUpdate | None:
        s = self.s
        filled, fees_so_far = order["filled_qty"], order["fees"] or 0.0
        remaining = order["qty"] - filled
        expires = parse_iso(order["expires_at"])

        def expire():
            return OrderUpdate(status=ORDER_EXPIRED, filled_qty=filled,
                               avg_fill_price=order["avg_fill_price"], fees=fees_so_far,
                               reason="انتهت صلاحية الأمر دون تنفيذ كامل")

        if quote is None or quote.quote_time is None:
            return expire() if expires and now >= expires else None
        age = quote.age_sec(now)
        reached = parse_iso(order["submitted_at"]) + dt.timedelta(seconds=s.simulated_latency_sec)
        stale = age is None or age > s.max_quote_age_sec or (quote.delayed and not s.allow_delayed_quotes)
        same_quote = order["sim_last_quote"] == iso(quote.quote_time)
        if stale or quote.quote_time < reached or same_quote:
            return expire() if expires and now >= expires else None

        limit = order["limit_price"]
        if order["side"] == "buy":
            px, size = quote.ask, quote.ask_size
            if px is None or px > limit + 1e-9:
                return expire() if expires and now >= expires else None
            price = min(limit, round(px + s.slippage_ticks * s.tick(px), 2))
        else:
            px, size = quote.bid, quote.bid_size
            if px is None or px < limit - 1e-9:
                return expire() if expires and now >= expires else None
            price = max(limit, round(px - s.slippage_ticks * s.tick(px), 2))
        if not size:
            if s.require_quote_size_for_fill:
                return expire() if expires and now >= expires else None
            size = remaining
        qty = min(remaining, size)
        new_filled = filled + qty
        fee = fee_model.incremental_fees(s.fees, filled, new_filled, price)
        prev_avg = order["avg_fill_price"] or 0.0
        avg = (prev_avg * filled + price * qty) / new_filled
        return OrderUpdate(
            status=ORDER_FILLED if new_filled == order["qty"] else ORDER_PARTIAL,
            filled_qty=new_filled, avg_fill_price=round(avg, 4), fees=round(fees_so_far + fee, 4),
            fills=[{"qty": qty, "price": price, "fees": fee, "reference_price": px,
                    "slippage": round(abs(price - px), 4), "quote": quote.as_dict()}])


_ABYAN_STATUS = {
    "new": ORDER_ACCEPTED, "accepted": ORDER_ACCEPTED, "pending_new": ORDER_ACCEPTED,
    "accepted_for_bidding": ORDER_ACCEPTED, "calculated": ORDER_ACCEPTED,
    "pending_replace": ORDER_ACCEPTED, "pending_cancel": ORDER_ACCEPTED,
    "partially_filled": ORDER_PARTIAL, "filled": ORDER_FILLED,
    "canceled": ORDER_CANCELED, "replaced": ORDER_CANCELED, "done_for_day": ORDER_EXPIRED,
    "expired": ORDER_EXPIRED, "rejected": ORDER_REJECTED, "stopped": ORDER_ACCEPTED,
    "suspended": ORDER_ACCEPTED,
}


class LiveExecutor:
    """Real limit orders through Abyan. Not verified end to end (see README)."""
    mode = "live"
    label = "حقيقي"

    def __init__(self, settings: Settings, env, broker, guard):
        problems = settings.problems()
        if settings.mode != "live":
            raise LiveTradingBlocked("الوضع في الإعدادات ليس live")
        if not env.live_armed:
            raise LiveTradingBlocked("LIVE_TRADING_ARMED غير مفعّل في متغيرات البيئة")
        if problems:
            raise LiveTradingBlocked("؛ ".join(problems))
        if broker is None or broker.read_only:
            raise LiveTradingBlocked("اتصال أبيان غير متاح للكتابة")
        self.s, self.broker, self.guard = settings, broker, guard

    def _check(self):
        reason = self.guard()
        if reason:
            raise LiveTradingBlocked(reason)

    def submit(self, order, fmv: float | None = None) -> OrderUpdate:
        self._check()
        try:
            res = self.broker.place_limit(order["occ"], order["side"], order["qty"],
                                          order["limit_price"], fmv)
        except BrokerUnavailable as e:
            return OrderUpdate(status=ORDER_UNKNOWN, reason=str(e))
        except BrokerError as e:
            return OrderUpdate(status=ORDER_REJECTED, reason=str(e))
        return self._map(res) or OrderUpdate(status=ORDER_UNKNOWN, reason="رد بلا معرّف أمر")

    def _map(self, row: dict) -> OrderUpdate | None:
        oid = row.get("client_order_id") or row.get("order_id")
        if not oid:
            return None
        status = _ABYAN_STATUS.get((row.get("status_raw") or row.get("status") or "").lower(),
                                   ORDER_ACCEPTED)
        fees_total = sum(float(row.get(k) or 0) for k in ("commission", "vat", "third_party_fees"))
        return OrderUpdate(status=status, filled_qty=int(float(row.get("filled_qty") or 0)),
                           avg_fill_price=float(row.get("filled_avg_price") or 0) or None,
                           fees=fees_total, broker_order_id=oid)

    def poll(self, order, quote, now) -> OrderUpdate | None:
        rows = self.broker.orders(symbol=order["occ"])
        if order["broker_order_id"]:
            row = next((r for r in rows if r.get("client_order_id") == order["broker_order_id"]), None)
            return self._map(row) if row else None
        return self.reconcile_unknown(order, rows)

    def reconcile_unknown(self, order, rows=None) -> OrderUpdate | None:
        """After a dropped connection: find the order at the broker before any resend.

        Abyan's place_order takes no client id, so the match is by contract,
        side, quantity, limit and creation time after our submit.
        """
        rows = rows if rows is not None else self.broker.orders(symbol=order["occ"])
        sent = parse_iso(order["submitted_at"]) - dt.timedelta(seconds=5)
        cands = []
        for r in rows:
            created = r.get("created_at")
            try:
                created_t = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
            except (AttributeError, ValueError):
                continue
            if (created_t >= sent and (r.get("side_raw") or "").lower() == order["side"]
                    and int(float(r.get("qty") or 0)) == order["qty"]
                    and abs(float(r.get("limit_price") or 0) - order["limit_price"]) < 0.005):
                cands.append(r)
        if len(cands) == 1:
            return self._map(cands[0])
        if len(cands) > 1:
            return OrderUpdate(status=ORDER_UNKNOWN, reason="أكثر من أمر مطابق لدى الوسيط — يتطلب مراجعة يدوية")
        return None

    def cancel(self, order) -> OrderUpdate:
        self._check()
        if order["broker_order_id"]:
            self.broker.cancel(order["broker_order_id"])
        return self.poll(order, None, None) or OrderUpdate(status=ORDER_UNKNOWN)
