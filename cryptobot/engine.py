"""The loop that puts the two agents together.

Every tick, in this order:
  1. manage open positions (exits always come before entries — protecting
     capital that is already at risk beats finding a new place to risk more)
  2. refresh the daily counters
  3. ask the analyst about each watched symbol
  4. push whatever passed through the risk gate to the trader

The engine is synchronous and blocking by design; the Telegram layer runs it
in a worker thread so network stalls never freeze the bot.
"""
import logging
import time

from . import analyst, config, risk, trader
from .exchange import Exchange, ExchangeError, PaperBroker
from .indicators import Candle, closes, ema, fmt_price, last
from .state import State
from .trader import Position

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, state: State | None = None, exchange: Exchange | None = None):
        self.state = state or State()
        self.exchange = exchange or Exchange()
        self.paper = PaperBroker(self.exchange)
        self.last_scan: float = 0.0
        self.last_error: str = ""
        self.last_verdicts: list[analyst.Verdict] = []

    # ------------------------------------------------------------ accounting
    @property
    def mode(self) -> str:
        return "live" if config.live_enabled() else "paper"

    def equity(self) -> float:
        """Capital the risk gate is allowed to size against."""
        if self.mode == "live":
            try:
                return self.exchange.fetch_free_balance()
            except ExchangeError as exc:
                log.error("balance unavailable: %s", exc)
                self.last_error = str(exc)
                return 0.0
        return self.state.paper_balance

    # ------------------------------------------------------------ data layer
    def fetch(self, symbol: str) -> tuple[list[Candle], list[Candle], dict | None]:
        ltf = self.exchange.fetch_candles(symbol, config.LTF, config.LTF_LIMIT)
        htf = self.exchange.fetch_candles(symbol, config.HTF, config.HTF_LIMIT)
        try:
            ticker = self.exchange.fetch_ticker(symbol)
        except Exception as exc:  # ticker is a nice-to-have, candles are not
            log.debug("ticker unavailable for %s: %s", symbol, exc)
            ticker = None
        return ltf, htf, ticker

    def symbols(self) -> list[str]:
        syms = list(self.state.watchlist)
        if config.AUTO_TOP_N > 0:
            try:
                for s in self.exchange.top_symbols(config.AUTO_TOP_N):
                    if s not in syms:
                        syms.append(s)
            except Exception as exc:
                log.warning("top-symbols lookup failed: %s", exc)
        return syms

    # --------------------------------------------------------------- orders
    def _fill(self, symbol: str, side: str, qty: float,
              price: float | None = None) -> dict:
        if self.mode == "live":
            return self.exchange.create_market_order(symbol, side, qty)
        return self.paper.market_order(symbol, side, qty, price)

    # ------------------------------------------------------------ managing
    def manage_open(self) -> list[str]:
        """Run the exit rules over every open position. Returns user messages."""
        events: list[str] = []
        for symbol, pos in list(self.state.positions.items()):
            try:
                ltf = self.exchange.fetch_candles(symbol, config.LTF, 60)
                price = self.exchange.last_price(symbol)
            except Exception as exc:
                log.warning("manage %s failed: %s", symbol, exc)
                continue
            if not ltf:
                continue

            from .indicators import atr as atr_fn
            atr_val = last(atr_fn(ltf, config.ATR_PERIOD)) or 0.0
            lc = closes(ltf)
            ef, es = last(ema(lc, config.EMA_FAST)), last(ema(lc, config.EMA_SLOW))
            flip = False
            if ef is not None and es is not None:
                flip = (ef < es) if pos.long else (ef > es)

            for action in trader.manage(pos, price, atr_val, momentum_flip=flip):
                events += self._apply(pos, action, price)
            if pos.qty <= 0:
                self.state.positions.pop(symbol, None)
        self.state.save()
        return events

    def _apply(self, pos: Position, action: trader.Action, price: float) -> list[str]:
        if action.kind == "move_stop":
            pos.stop = action.new_stop
            return [f"🛡️ <b>{pos.symbol}</b> — {action.reason}"]

        if action.kind not in ("close", "partial"):
            return []

        qty = min(action.qty, pos.qty)
        if qty <= 0:
            return []
        side = "sell" if pos.long else "buy"
        try:
            fill = self._fill(pos.symbol, side, qty, price)
        except Exception as exc:
            log.error("exit order failed for %s: %s", pos.symbol, exc)
            return [f"⚠️ <b>{pos.symbol}</b> — فشل تنفيذ الخروج: {exc}"]

        closed = trader.apply_close(pos, fill["price"], fill["qty"],
                                    action.reason, fill.get("fee", 0.0))
        self.state.record_trade(closed)
        if action.kind == "partial":
            pos.tp1_done = True
            head = "💰 جني جزئي"
        else:
            head = "✅ إغلاق" if closed.pnl >= 0 else "🔻 إغلاق"
        return [f"{head} <b>{pos.symbol}</b> — {action.reason}\n"
                f"   خروج {fmt_price(closed.exit)} | "
                f"{closed.pnl:+,.2f}$ ({closed.r:+.2f}R)"]

    # -------------------------------------------------------------- entries
    def scan(self) -> list[analyst.Verdict]:
        verdicts = []
        for symbol in self.symbols():
            if symbol in self.state.positions:
                continue
            try:
                ltf, htf, ticker = self.fetch(symbol)
            except Exception as exc:
                log.warning("fetch %s failed: %s", symbol, exc)
                self.last_error = f"{symbol}: {exc}"
                continue
            verdicts.append(analyst.analyze(symbol, ltf, htf, ticker))
        self.last_scan = time.time()
        self.last_verdicts = verdicts
        return verdicts

    def try_enter(self, v: analyst.Verdict) -> list[str]:
        """Push one approved verdict through the risk gate and open it."""
        if not v.ok:
            return []
        equity = self.equity()
        blocked = risk.check_gates(equity, self.state.open_list(), self.state.day_pnl,
                                   self.state.day_trades, v.symbol, self.state.cooldowns)
        if blocked:
            log.info("risk gate blocked %s: %s", v.symbol, blocked)
            return []

        sizing = risk.size_position(v, equity)
        if not sizing.ok:
            log.info("sizing rejected %s: %s", v.symbol, sizing.reason)
            return []

        qty = sizing.qty
        if self.mode == "live":
            qty = self.exchange.amount_to_precision(v.symbol, qty)
            if qty <= 0:
                return []
        side = "buy" if v.side == "long" else "sell"
        try:
            fill = self._fill(v.symbol, side, qty, v.entry)
        except Exception as exc:
            log.error("entry order failed for %s: %s", v.symbol, exc)
            return [f"⚠️ <b>{v.symbol}</b> — فشل تنفيذ الدخول: {exc}"]

        entry = fill["price"]
        # Re-anchor the plan on the actual fill: the stop distance the analyst
        # sized on has to survive slippage, so shift the levels, not the risk.
        drift = entry - v.entry
        pos = Position(
            symbol=v.symbol, side=v.side, qty=fill["qty"], initial_qty=fill["qty"],
            entry=entry, stop=v.stop + drift, tp1=v.tp1 + drift, tp2=v.tp2 + drift,
            risk_per_unit=abs(entry - (v.stop + drift)), opened_at=time.time(),
            mode=self.mode, fees=fill.get("fee", 0.0), score=v.score,
            order_id=str(fill.get("id") or ""),
        )
        self.state.positions[v.symbol] = pos
        self.state.day_trades += 1
        self.state.save()

        tag = "🔴 حقيقي" if self.mode == "live" else "🧪 تجريبي"
        reasons = "\n".join(f"   ✅ {c.name} — {c.detail}" for c in v.passed_checks[:5])
        return [f"🚀 <b>دخول {pos.symbol}</b> ({tag})\n"
                f"   الاتجاه: {'شراء' if pos.long else 'بيع'} | "
                f"القوة {v.score:.0f}/100 | ع/م {v.rr}\n"
                f"   السعر {fmt_price(entry)} | الكمية {pos.qty:g} "
                f"({sizing.notional:,.2f}$)\n"
                f"   الوقف {fmt_price(pos.stop)} | الأهداف {fmt_price(pos.tp1)} → "
                f"{fmt_price(pos.tp2)}\n"
                f"   المخاطرة {sizing.risk_amount:,.2f}$ "
                f"({config.RISK_PER_TRADE_PCT}% من الرصيد)\n{reasons}"]

    # ------------------------------------------------------------------ tick
    def tick(self) -> list[str]:
        """One full cycle. Never raises — the caller is a scheduled job."""
        events: list[str] = []
        try:
            if self.state.roll_day():
                events.append("🌅 بدأ يوم تداول جديد — أُعيد ضبط حدود اليوم.")
                self.state.save()

            events += self.manage_open()

            if self.state.paused:
                return events

            equity = self.equity()
            max_loss = equity * config.DAILY_MAX_LOSS_PCT / 100
            if equity > 0 and self.state.day_pnl <= -max_loss:
                return events + [
                    f"🛑 توقّف الدخول اليوم: بلغت خسارة "
                    f"{config.DAILY_MAX_LOSS_PCT}% ({self.state.day_pnl:,.2f}$)."
                ]

            for v in self.scan():
                events += self.try_enter(v)
        except Exception as exc:  # a scheduled job must not die on one bad tick
            log.exception("tick failed")
            self.last_error = str(exc)
        return events

    # ---------------------------------------------------------------- manual
    def close_all(self, reason: str = "إغلاق يدوي") -> list[str]:
        events: list[str] = []
        for symbol, pos in list(self.state.positions.items()):
            try:
                price = self.exchange.last_price(symbol)
            except Exception as exc:
                events.append(f"⚠️ {symbol}: تعذّر الإغلاق ({exc})")
                continue
            events += self._apply(pos, trader.Action("close", reason, qty=pos.qty), price)
            if pos.qty <= 0:
                self.state.positions.pop(symbol, None)
        self.state.save()
        return events or ["لا توجد مراكز مفتوحة."]
