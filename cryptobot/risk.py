"""The risk gate — the last thing between an approved analysis and real money.

Position size is derived from the distance to the stop, never from a fixed
dollar amount: risking 0.5% of equity means 0.5%, whether the stop is 0.3% or
3% away. Every other rule here exists to bound how bad a single day can get.
"""
import time
from dataclasses import dataclass

from . import config
from .analyst import Verdict


@dataclass
class Sizing:
    ok: bool
    qty: float = 0.0
    notional: float = 0.0
    risk_amount: float = 0.0
    reason: str = ""


def day_key(now: float | None = None) -> str:
    """UTC day bucket used for the daily loss cap and trade counter."""
    return time.strftime("%Y-%m-%d", time.gmtime(now if now is not None else time.time()))


def check_gates(equity: float, open_positions: list, day_pnl: float,
                day_trades: int, symbol: str,
                cooldowns: dict[str, float], now: float | None = None) -> str:
    """Return an Arabic refusal reason, or '' when the trade may proceed."""
    now = now if now is not None else time.time()

    if equity <= 0:
        return "لا يوجد رصيد متاح"

    if any(getattr(p, "symbol", None) == symbol for p in open_positions):
        return f"يوجد مركز مفتوح على {symbol}"

    if len(open_positions) >= config.MAX_OPEN_POSITIONS:
        return f"بلغت الحد الأقصى للمراكز المفتوحة ({config.MAX_OPEN_POSITIONS})"

    if day_trades >= config.MAX_TRADES_PER_DAY:
        return f"بلغت الحد الأقصى للصفقات اليوم ({config.MAX_TRADES_PER_DAY})"

    # The daily stop is measured against the equity the day started with, which
    # the caller folds into day_pnl; a breach halts new entries, never exits.
    max_loss = equity * config.DAILY_MAX_LOSS_PCT / 100
    if day_pnl <= -max_loss:
        return (f"توقف الحد اليومي للخسارة "
                f"({config.DAILY_MAX_LOSS_PCT}% ≈ {max_loss:,.2f}$)")

    until = cooldowns.get(symbol, 0.0)
    if until > now:
        mins = int((until - now) / 60) + 1
        return f"{symbol} في فترة تهدئة بعد خسارة ({mins} دقيقة)"

    return ""


def size_position(verdict: Verdict, equity: float) -> Sizing:
    """Translate an approved verdict into a quantity, or explain why not."""
    risk_per_unit = verdict.risk_per_unit
    if risk_per_unit <= 0:
        return Sizing(False, reason="مسافة الوقف غير صالحة")

    risk_amount = equity * config.RISK_PER_TRADE_PCT / 100
    qty = risk_amount / risk_per_unit
    notional = qty * verdict.entry

    # Cap notional so one convenient tight stop cannot turn into an oversized
    # position. Leverage above 1 only applies on a derivatives market.
    leverage = config.LEVERAGE if config.shorts_enabled() else 1.0
    max_notional = equity * config.MAX_POSITION_PCT / 100 * max(leverage, 1.0)
    if notional > max_notional:
        notional = max_notional
        qty = notional / verdict.entry
        risk_amount = qty * risk_per_unit

    if notional < config.MIN_ORDER_USD:
        return Sizing(False, reason=f"حجم الصفقة {notional:.2f}$ أقل من الحد "
                                    f"الأدنى {config.MIN_ORDER_USD:.2f}$")

    if notional > equity * max(leverage, 1.0):
        return Sizing(False, reason="الرصيد لا يكفي لهذه الصفقة")

    return Sizing(True, qty=qty, notional=notional, risk_amount=risk_amount)
