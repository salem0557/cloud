"""The trader agent: opens what the analyst approved and, more importantly,
manages the exit.

Entries are the easy half. Everything expensive happens after the fill, so the
management rules are explicit and ordered: the stop is always evaluated first,
partial profit is banked at 1R, the stop moves to breakeven right after, and a
trade that has gone nowhere by the clock is cut so the capital can work
somewhere else.
"""
import time
from dataclasses import asdict, dataclass, field

from . import config
from .indicators import fmt_price


@dataclass
class Position:
    symbol: str
    side: str                  # "long" | "short"
    qty: float                 # remaining quantity
    initial_qty: float
    entry: float
    stop: float
    tp1: float
    tp2: float
    risk_per_unit: float       # R, in quote currency per unit
    opened_at: float
    mode: str = "paper"        # "paper" | "live"
    tp1_done: bool = False
    best_price: float = 0.0    # high-water mark, drives the trailing stop
    fees: float = 0.0
    realized: float = 0.0      # profit already banked from partials
    score: float = 0.0         # the analyst score that justified this entry
    order_id: str = ""

    def __post_init__(self):
        if not self.best_price:
            self.best_price = self.entry

    @property
    def long(self) -> bool:
        return self.side == "long"

    def unrealized(self, price: float) -> float:
        diff = (price - self.entry) if self.long else (self.entry - price)
        return diff * self.qty

    def r_multiple(self, price: float) -> float:
        if self.risk_per_unit <= 0:
            return 0.0
        diff = (price - self.entry) if self.long else (self.entry - price)
        return diff / self.risk_per_unit

    def pnl_pct(self, price: float) -> float:
        if self.entry <= 0:
            return 0.0
        diff = (price - self.entry) if self.long else (self.entry - price)
        return diff / self.entry * 100

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class Action:
    kind: str          # "close" | "partial" | "move_stop"
    reason: str        # Arabic, shown to the user
    qty: float = 0.0
    new_stop: float = 0.0


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    entry: float
    exit: float
    qty: float
    pnl: float
    pnl_pct: float
    r: float
    reason: str
    opened_at: float
    closed_at: float
    mode: str = "paper"
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def update_trail(pos: Position, price: float, atr_val: float) -> float | None:
    """Trailing stop, active only after TP1 so normal noise cannot stop us out
    of a trade that has not proven anything yet. Returns a new stop or None."""
    if not pos.tp1_done or atr_val <= 0:
        return None
    if pos.long:
        pos.best_price = max(pos.best_price, price)
        candidate = pos.best_price - config.TRAIL_ATR_MULT * atr_val
        return candidate if candidate > pos.stop else None
    pos.best_price = min(pos.best_price, price)
    candidate = pos.best_price + config.TRAIL_ATR_MULT * atr_val
    return candidate if candidate < pos.stop else None


def manage(pos: Position, price: float, atr_val: float,
           now: float | None = None, momentum_flip: bool = False) -> list[Action]:
    """Decide what to do with an open position at the current price.

    Order is deliberate: the stop is checked before any target, because on a
    fast candle both can be touched and assuming the good one filled first is
    how paper results drift away from reality.
    """
    now = now if now is not None else time.time()
    actions: list[Action] = []

    hit_stop = price <= pos.stop if pos.long else price >= pos.stop
    if hit_stop:
        reason = "وقف الخسارة" if not pos.tp1_done else "الوقف المتحرك"
        return [Action("close", reason, qty=pos.qty)]

    hit_tp2 = price >= pos.tp2 if pos.long else price <= pos.tp2
    if hit_tp2:
        return [Action("close", f"الهدف الثاني {fmt_price(pos.tp2)}", qty=pos.qty)]

    hit_tp1 = price >= pos.tp1 if pos.long else price <= pos.tp1
    if hit_tp1 and not pos.tp1_done:
        part = pos.initial_qty * config.TP1_FRACTION
        part = min(part, pos.qty)
        actions.append(Action("partial", f"الهدف الأول {fmt_price(pos.tp1)}", qty=part))
        if config.BREAKEVEN_AFTER_TP1:
            better = (pos.entry > pos.stop) if pos.long else (pos.entry < pos.stop)
            if better:
                actions.append(Action("move_stop", "نقل الوقف لنقطة الدخول",
                                      new_stop=pos.entry))
        return actions

    new_stop = update_trail(pos, price, atr_val)
    if new_stop is not None:
        actions.append(Action("move_stop", f"وقف متحرك {fmt_price(new_stop)}",
                              new_stop=new_stop))

    # Momentum flipped against a position that already banked TP1: take what is
    # left rather than donate it back.
    if momentum_flip and pos.tp1_done:
        actions.append(Action("close", "انعكاس الزخم بعد الهدف الأول", qty=pos.qty))
        return actions

    held_min = (now - pos.opened_at) / 60
    if held_min >= config.MAX_HOLD_MINUTES and pos.r_multiple(price) < config.STALE_EXIT_R:
        actions.append(Action("close",
                              f"انتهاء المدة ({int(held_min)}د) دون حركة",
                              qty=pos.qty))
    return actions


def apply_close(pos: Position, price: float, qty: float, reason: str,
                fee: float = 0.0, now: float | None = None) -> ClosedTrade:
    """Book a (partial or full) close and return the trade record."""
    now = now if now is not None else time.time()
    qty = min(qty, pos.qty)
    diff = (price - pos.entry) if pos.long else (pos.entry - price)
    gross = diff * qty
    pnl = gross - fee
    pos.qty = max(pos.qty - qty, 0.0)
    pos.fees += fee
    pos.realized += pnl
    return ClosedTrade(
        symbol=pos.symbol, side=pos.side, entry=pos.entry, exit=price, qty=qty,
        pnl=pnl, pnl_pct=(diff / pos.entry * 100) if pos.entry else 0.0,
        r=(diff / pos.risk_per_unit) if pos.risk_per_unit else 0.0,
        reason=reason, opened_at=pos.opened_at, closed_at=now,
        mode=pos.mode, score=pos.score,
    )


def format_position(pos: Position, price: float) -> str:
    side = "شراء" if pos.long else "بيع"
    pnl = pos.unrealized(price)
    sign = "🟢" if pnl >= 0 else "🔴"
    return (f"{sign} <b>{pos.symbol}</b> ({side})\n"
            f"   الدخول {fmt_price(pos.entry)} | الحالي {fmt_price(price)}\n"
            f"   الكمية {pos.qty:g} | الوقف {fmt_price(pos.stop)}\n"
            f"   الربح {pnl:+,.2f}$ ({pos.pnl_pct(price):+.2f}%) | "
            f"{pos.r_multiple(price):+.2f}R"
            + ("\n   ✔️ تم تحقيق الهدف الأول" if pos.tp1_done else ""))
