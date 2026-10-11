"""Entry checks. "Accepted" means: complete, consistent, fresh, matched to a
listed contract with a fresh two-sided quote, and inside the owner's limits.
It says nothing about whether the trade will make money.

Every check is recorded with its outcome, so a rejection always carries the
exact reason and an acceptance shows what it rested on.
"""
from __future__ import annotations

import datetime as dt
import math
import time
from dataclasses import dataclass, field

from . import fees as fee_model
from . import market_calendar as cal
from .config import Settings
from .models import OptionContract, ParsedAlert, Quote, RawMessage
from .quotes import QuoteError

STANDARD_MULTIPLIER = 100


@dataclass
class RiskSnapshot:
    open_positions: int
    exposure: float              # cost basis of open trades + holds for pending entries
    daily_pnl: float             # by the configured method; negative is a loss
    available_cash: float        # paper: cash - holds. live: Abyan buying power
    entries_paused: str | None   # reason, or None


@dataclass
class ContractCheck:
    ok: bool
    detail: str
    broker_symbol: str | None = None
    multiplier: int | None = None
    multiplier_source: str | None = None


@dataclass
class Decision:
    ok: bool = False
    reasons: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    contract: OptionContract | None = None
    quote: Quote | None = None
    fmv: float | None = None
    multiplier: int | None = None
    qty: int = 0
    limit: float | None = None
    est_cost: float | None = None
    est_fees: float | None = None
    broker_verified: bool = False

    def add(self, name: str, ok: bool, detail: str) -> bool:
        self.checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            self.reasons.append(detail)
        return ok


def round_tick(price: float, tick: float, up: bool) -> float:
    steps = price / tick
    steps = math.ceil(steps - 1e-9) if up else math.floor(steps + 1e-9)
    return round(steps * tick, 2)


class ChainCache:
    """Abyan chain lookups, cached briefly to stay polite to the endpoint."""

    def __init__(self, broker, ttl: float = 600):
        self.broker, self.ttl = broker, ttl
        self._data: dict = {}

    def _get(self, key, fn):
        hit = self._data.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        value = fn()
        self._data[key] = (time.monotonic(), value)
        return value

    def verify(self, c: OptionContract) -> ContractCheck:
        expiries = self._get(("exp", c.underlying),
                             lambda: self.broker.expirations(c.underlying))
        if not expiries:
            return ContractCheck(False, f"أبيان لا يعرض سلسلة خيارات للرمز {c.underlying}")
        if c.expiry not in expiries:
            return ContractCheck(False, f"تاريخ الانتهاء {c.expiry} غير مدرج في أبيان للرمز {c.underlying}")
        rows = self._get(("con", c.underlying, c.expiry, c.right),
                         lambda: self.broker.contracts(c.underlying, c.expiry, c.right))
        match = [r for r in rows if r.get("symbol") == c.occ
                 or (abs(float(r.get("strike_price", -1)) - c.strike) < 1e-6
                     and (r.get("type") or "").lower() == c.right_name)]
        if not match:
            return ContractCheck(False, f"لا يوجد في أبيان عقد {c.label()}")
        if len(match) > 1:
            return ContractCheck(False, f"أكثر من عقد في أبيان يطابق {c.label()} (عقود معدّلة؟)")
        row = match[0]
        symbol = row.get("symbol") or ""
        if symbol != c.occ:
            return ContractCheck(False, f"رمز العقد في أبيان {symbol} يختلف عن {c.occ} (عقد معدّل)")
        if (row.get("underlying_symbol") or c.underlying).upper() != c.underlying:
            return ContractCheck(False, "الأصل في أبيان مختلف عن التنبيه")
        # Abyan's chain does not return a multiplier. A standard OCC root equal
        # to the underlying is a standard 100-share contract; anything else is
        # rejected above as an adjusted contract rather than assumed.
        return ContractCheck(True, f"العقد {symbol} مدرج في أبيان", symbol,
                             STANDARD_MULTIPLIER, "جذر OCC قياسي (أبيان لا يُرجع المضاعف)")


class Validator:
    def __init__(self, settings: Settings, quotes, chain: ChainCache | None):
        self.s = settings
        self.quotes = quotes
        self.chain = chain

    def entry(self, alert: ParsedAlert, msg: RawMessage, risk: RiskSnapshot,
              now: dt.datetime, fmv_lookup=None) -> Decision:
        s, d = self.s, Decision()

        # 1. Parse quality and completeness.
        if alert.errors:
            for e in alert.errors:
                d.add("parse", False, e)
        missing = [name for name, v in (("رمز الأصل", alert.underlying), ("النوع Call/Put", alert.right),
                                         ("سعر التنفيذ", alert.strike), ("تاريخ الانتهاء الكامل", alert.expiry),
                                         ("سعر الدخول", alert.entry_low)) if v in (None, "")]
        if s.require_stop and alert.stop is None:
            missing.append("وقف الخسارة")
        if missing:
            d.add("complete", False, "بيانات ناقصة: " + "، ".join(missing))
        if d.reasons:
            return d
        d.add("complete", True, "البيانات الأساسية مكتملة")
        c = alert.contract()
        d.contract = c

        # 2. Internal consistency (long premium: stop < entry < targets).
        today = cal.ny_date(now)
        if alert.entry_low <= 0 or alert.strike <= 0:
            d.add("consistent", False, "سعر غير موجب")
        if alert.stop is not None and alert.stop >= alert.entry_low:
            d.add("consistent", False, f"الوقف {alert.stop:g} ليس أقل من سعر الدخول {alert.entry_low:g}")
        if alert.targets and min(alert.targets) <= alert.entry_high:
            d.add("consistent", False, "هدف أقل من أو يساوي سعر الدخول")
        if alert.targets != sorted(alert.targets):
            d.add("consistent", False, "الأهداف ليست مرتبة تصاعديًا")
        if c.expiry < today:
            d.add("consistent", False, f"العقد منتهٍ ({c.expiry})")
        if alert.entry_high > alert.entry_low * 1.5:
            d.add("consistent", False, "نطاق الدخول واسع بشكل غير معقول")
        if d.reasons:
            return d
        d.add("consistent", True, "البيانات متسقة")

        # 3. Freshness of the alert itself (from the post's original time).
        age = (now - msg.date).total_seconds()
        if not d.add("alert_age", age <= s.max_alert_age_sec,
                     f"عمر التنبيه {age:.0f} ث (الحد {s.max_alert_age_sec:g} ث)"):
            return d

        # 4. Owner's universe.
        dte = (c.expiry - today).days
        d.add("symbol", s.symbol_allowed(c.underlying), f"الرمز {c.underlying} " +
              ("مسموح" if s.symbol_allowed(c.underlying) else "غير مسموح في الإعدادات"))
        if dte == 0 and not s.allow_0dte:
            d.add("dte", False, "عقود يوم الانتهاء (0DTE) غير مسموحة")
        else:
            d.add("dte", s.min_dte <= dte <= s.max_dte,
                  f"الأيام حتى الانتهاء {dte} (المسموح {s.min_dte}-{s.max_dte})")
        if s.entry_only_regular_session:
            d.add("session", cal.is_open(now), "السوق مفتوح" if cal.is_open(now)
                  else "السوق مغلق — لا دخول خارج الجلسة الرسمية")
        if dte == 0 and cal.is_open(now):
            close = cal.session_bounds(today)[1]
            cutoff = close - dt.timedelta(minutes=s.close_before_expiry_minutes)
            d.add("expiry_cutoff", now < cutoff, "بعد موعد الإغلاق الإلزامي قبل الانتهاء"
                  if now >= cutoff else "قبل موعد الإغلاق الإلزامي")
        if risk.entries_paused:
            d.add("paused", False, f"الدخول الجديد موقوف: {risk.entries_paused}")
        if d.reasons:
            return d

        # 5. The contract at the broker.
        if self.chain is None:
            if s.mode == "live" or not s.paper_allow_without_broker_verification:
                d.add("broker", False, "لا يوجد اتصال بأبيان للتحقق من العقد")
                return d
            d.add("broker", True, "تحذير: لم يُتحقق من العقد في أبيان (مسموح ورقيًا بالإعدادات)")
            d.multiplier = STANDARD_MULTIPLIER
        else:
            try:
                check = self.chain.verify(c)
            except Exception as e:      # BrokerError, network
                d.add("broker", False, f"تعذر التحقق من أبيان: {e}")
                return d
            if not d.add("broker", check.ok, check.detail):
                return d
            d.add("multiplier", True, f"المضاعف {check.multiplier} — {check.multiplier_source}")
            d.multiplier = check.multiplier
            d.broker_verified = True

        # 6. Executable prices.
        try:
            q = self.quotes.quote(c)
        except QuoteError as e:
            d.add("quote", False, f"تعذر جلب العرض/الطلب: {e}")
            return d
        except Exception as e:          # network failure of the provider
            d.add("quote", False, f"تعذر جلب العرض/الطلب: {type(e).__name__}")
            return d
        d.quote = q
        qage = q.age_sec(now)
        if q.bid is None or q.ask is None or q.ask < q.bid:
            d.add("quote", False, f"لا يوجد عرض وطلب صالحان ({q.source})")
            return d
        if qage is None:
            d.add("quote_age", False, f"سعر بلا توقيت من {q.source}")
            return d
        if q.delayed and not s.allow_delayed_quotes:
            d.add("quote_age", False, f"أسعار {q.source} متأخرة — غير مسموحة في الإعدادات")
            return d
        if not d.add("quote_age", qage <= s.max_quote_age_sec,
                     f"عمر السعر {qage:.0f} ث من {q.source} (الحد {s.max_quote_age_sec:g} ث)"):
            return d
        spread = q.spread_pct()
        d.add("spread", spread is not None and spread <= s.max_spread_pct,
              f"فارق العرض/الطلب {spread:.1f}% (الحد {s.max_spread_pct:g}%)")
        deviation = (q.ask - alert.entry_high) / alert.entry_high * 100
        d.add("deviation", deviation <= s.max_entry_deviation_pct,
              f"الطلب {q.ask:.2f} مقابل سعر التنبيه {alert.entry_high:.2f}: انحراف {deviation:+.1f}% "
              f"(الحد {s.max_entry_deviation_pct:g}%)")
        if s.min_open_interest and (q.open_interest or 0) < s.min_open_interest:
            d.add("liquidity", False, f"العقود المفتوحة {q.open_interest} أقل من {s.min_open_interest}")
        if s.min_volume and (q.volume or 0) < s.min_volume:
            d.add("liquidity", False, f"حجم التداول {q.volume} أقل من {s.min_volume}")
        if s.require_quote_size_for_fill and not q.ask_size:
            d.add("liquidity", False, "حجم الطلب المعروض غير متوفر")
        if fmv_lookup is not None:
            try:
                d.fmv = fmv_lookup(c)
            except Exception as e:
                d.checks.append({"check": "fmv", "ok": None, "detail": f"تعذر جلب FMV من أبيان: {e}"})
            if d.fmv and q.mid:
                gap = abs(d.fmv - q.mid) / q.mid * 100
                d.checks.append({"check": "fmv", "ok": gap <= s.fmv_tolerance_pct,
                                 "detail": f"FMV أبيان {d.fmv:.2f} مقابل منتصف {q.mid:.2f} ({gap:.1f}%)"})
                if gap > s.fmv_tolerance_pct:
                    d.reasons.append(f"FMV أبيان {d.fmv:.2f} بعيد عن منتصف العرض/الطلب {q.mid:.2f} "
                                     f"({gap:.0f}%) — مصدر الأسعار مشكوك فيه")
        if d.reasons:
            return d

        # 7. Size and limits.
        tick = s.tick(q.ask)
        cap = alert.entry_high * (1 + s.max_entry_deviation_pct / 100)
        limit = round_tick(min(q.ask + s.slippage_ticks * tick, cap), tick, up=False)
        if limit < q.ask:
            limit = q.ask
        mult = d.multiplier
        qty = int(s.max_cost_per_trade_usd // (limit * mult))
        while qty > 0 and qty * limit * mult + fee_model.order_fees(s.fees, qty, limit) \
                > s.max_cost_per_trade_usd:
            qty -= 1
        if not d.add("size", qty >= 1,
                     f"عقد واحد يكلف {limit * mult:.2f}$ + رسوم، والحد {s.max_cost_per_trade_usd:g}$"
                     if qty < 1 else f"الكمية {qty} بسعر حد {limit:.2f}"):
            return d
        est_fees = fee_model.order_fees(s.fees, qty, limit)
        cost = qty * limit * mult + est_fees
        d.add("positions", risk.open_positions < s.max_open_positions,
              f"المراكز المفتوحة {risk.open_positions} (الحد {s.max_open_positions})")
        d.add("exposure", risk.exposure + cost <= s.max_total_exposure_usd,
              f"التعرض بعد الصفقة {risk.exposure + cost:.2f}$ (الحد {s.max_total_exposure_usd:g}$)")
        d.add("daily_loss", risk.daily_pnl > -s.daily_loss_limit_usd,
              f"نتيجة اليوم {risk.daily_pnl:+.2f}$ (حد الخسارة {s.daily_loss_limit_usd:g}$)")
        d.add("cash", risk.available_cash >= cost,
              f"الرصيد المتاح {risk.available_cash:.2f}$ مقابل تكلفة {cost:.2f}$")
        d.qty, d.limit, d.est_cost, d.est_fees = qty, limit, round(cost, 2), est_fees
        d.ok = not d.reasons
        return d
