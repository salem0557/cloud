"""Persisted bot state: open positions, closed trades, daily counters.

Written to disk after every change so a container restart mid-trade does not
orphan a position the trader still has to manage.
"""
import json
import logging
import os
import tempfile
import time

from . import config
from .risk import day_key
from .trader import ClosedTrade, Position

log = logging.getLogger(__name__)


class State:
    def __init__(self, path: str | None = None):
        self.path = path or config.STATE_FILE
        self.positions: dict[str, Position] = {}
        self.history: list[dict] = []
        self.cooldowns: dict[str, float] = {}
        self.day: str = day_key()
        self.day_pnl: float = 0.0
        self.day_trades: int = 0
        self.paper_balance: float = config.PAPER_START_BALANCE
        self.paused: bool = False
        self.watchlist: list[str] = list(config.WATCHLIST)
        self.subscribers: list[int] = []
        self.load()

    # ------------------------------------------------------------ lifecycle
    def load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            log.error("state file unreadable (%s); starting fresh", exc)
            return
        self.positions = {s: Position.from_dict(d)
                          for s, d in (data.get("positions") or {}).items()}
        self.history = data.get("history") or []
        self.cooldowns = {k: float(v) for k, v in (data.get("cooldowns") or {}).items()}
        self.day = data.get("day") or day_key()
        self.day_pnl = float(data.get("day_pnl") or 0.0)
        self.day_trades = int(data.get("day_trades") or 0)
        self.paper_balance = float(data.get("paper_balance")
                                   or config.PAPER_START_BALANCE)
        self.paused = bool(data.get("paused"))
        self.watchlist = data.get("watchlist") or list(config.WATCHLIST)
        self.subscribers = data.get("subscribers") or []
        self.roll_day()

    def save(self) -> None:
        data = {
            "positions": {s: p.to_dict() for s, p in self.positions.items()},
            "history": self.history[-500:],
            "cooldowns": self.cooldowns,
            "day": self.day,
            "day_pnl": self.day_pnl,
            "day_trades": self.day_trades,
            "paper_balance": self.paper_balance,
            "paused": self.paused,
            "watchlist": self.watchlist,
            "subscribers": self.subscribers,
        }
        try:
            # Write-then-rename: a crash mid-write must not leave a truncated
            # state file that loses open positions on restart.
            directory = os.path.dirname(os.path.abspath(self.path)) or "."
            with tempfile.NamedTemporaryFile("w", dir=directory, delete=False,
                                             encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
                tmp = fh.name
            os.replace(tmp, self.path)
        except OSError as exc:
            log.error("could not save state: %s", exc)

    def roll_day(self) -> bool:
        """Reset the daily counters when the UTC day changes."""
        today = day_key()
        if today != self.day:
            self.day = today
            self.day_pnl = 0.0
            self.day_trades = 0
            return True
        return False

    # -------------------------------------------------------------- helpers
    def open_list(self) -> list[Position]:
        return list(self.positions.values())

    def record_trade(self, trade: ClosedTrade) -> None:
        self.history.append(trade.to_dict())
        self.day_pnl += trade.pnl
        if trade.mode == "paper":
            self.paper_balance += trade.pnl
        if trade.pnl < 0:
            self.cooldowns[trade.symbol] = time.time() + config.SYMBOL_COOLDOWN_MIN * 60

    def stats(self, limit: int | None = None) -> dict:
        rows = self.history[-limit:] if limit else self.history
        wins = [r for r in rows if r.get("pnl", 0) > 0]
        losses = [r for r in rows if r.get("pnl", 0) <= 0]
        total = sum(r.get("pnl", 0.0) for r in rows)
        gross_win = sum(r["pnl"] for r in wins)
        gross_loss = abs(sum(r["pnl"] for r in losses))
        return {
            "trades": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / len(rows) * 100) if rows else 0.0,
            "pnl": total,
            "avg_r": (sum(r.get("r", 0.0) for r in rows) / len(rows)) if rows else 0.0,
            "profit_factor": (gross_win / gross_loss) if gross_loss else
                             (float("inf") if gross_win else 0.0),
            "best": max((r.get("pnl", 0.0) for r in rows), default=0.0),
            "worst": min((r.get("pnl", 0.0) for r in rows), default=0.0),
        }
