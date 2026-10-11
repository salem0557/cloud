"""Durable record of everything: SQLite in WAL mode on the persistent volume.

Every message version, parse result, validation check, order, fill, error and
equity snapshot is kept. Uniqueness constraints do the idempotency work: a
trade is keyed by (channel, message), so a restart, an edit or a re-delivered
update cannot open the same trade twice.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, version INTEGER NOT NULL,
    date TEXT NOT NULL, edit_date TEXT, received_at TEXT NOT NULL,
    text TEXT NOT NULL, transport TEXT, author TEXT, via_bot TEXT, sender_id INTEGER,
    reply_to INTEGER, content_hash TEXT NOT NULL,
    UNIQUE (chat_id, message_id, version)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    message_row INTEGER REFERENCES messages(id),
    chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, version INTEGER NOT NULL,
    kind TEXT, status TEXT NOT NULL, reasons TEXT, checks TEXT, parsed TEXT,
    quote TEXT, fmv REAL, trade_id INTEGER, mode TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    trade_key TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL, alert_ref TEXT, occ TEXT NOT NULL, underlying TEXT NOT NULL,
    right TEXT NOT NULL, strike REAL NOT NULL, expiry TEXT NOT NULL, multiplier INTEGER NOT NULL,
    status TEXT NOT NULL, qty_planned INTEGER NOT NULL, qty_filled INTEGER DEFAULT 0,
    qty_open INTEGER DEFAULT 0, avg_entry REAL, entry_fees REAL DEFAULT 0,
    exit_qty INTEGER DEFAULT 0, exit_value REAL DEFAULT 0, exit_fees REAL DEFAULT 0,
    stop REAL, targets TEXT, targets_hit INTEGER DEFAULT 0,
    alert_entry_low REAL, alert_entry_high REAL, alert_time TEXT, received_at TEXT,
    opened_at TEXT, closed_at TEXT, exit_reason TEXT, realized_pnl REAL,
    broker_verified INTEGER DEFAULT 0, quote_source TEXT, last_bid REAL, last_mark_at TEXT,
    exit_request TEXT, notes TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    trade_id INTEGER NOT NULL REFERENCES trades(id),
    mode TEXT NOT NULL, side TEXT NOT NULL, purpose TEXT NOT NULL,
    qty INTEGER NOT NULL, limit_price REAL NOT NULL, status TEXT NOT NULL,
    filled_qty INTEGER DEFAULT 0, avg_fill_price REAL, fees REAL DEFAULT 0,
    reserved REAL DEFAULT 0, broker_order_id TEXT, reason TEXT, reprices INTEGER DEFAULT 0,
    created_at TEXT NOT NULL, submitted_at TEXT, updated_at TEXT, expires_at TEXT,
    sim_last_quote TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    qty INTEGER NOT NULL, price REAL NOT NULL, fees REAL NOT NULL,
    reference_price REAL, slippage REAL, quote TEXT, ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,
    amount REAL NOT NULL, order_id INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, level TEXT NOT NULL,
    kind TEXT NOT NULL, message TEXT NOT NULL, data TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    id INTEGER PRIMARY KEY, ts TEXT NOT NULL, mode TEXT NOT NULL,
    cash REAL NOT NULL, reserved REAL NOT NULL, positions_value REAL NOT NULL,
    equity REAL NOT NULL, stale_marks INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_alerts_created ON alerts(created_at);
CREATE INDEX IF NOT EXISTS ix_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
"""


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime | None) -> str | None:
    return t.astimezone(dt.timezone.utc).isoformat() if t else None


def parse_iso(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(SCHEMA)
        self._depth = 0

    @contextlib.contextmanager
    def tx(self):
        """Atomic block; nests (inner blocks join the outer transaction)."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self.db.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self.db
            except BaseException:
                self._depth -= 1
                if outer:
                    self.db.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self.db.execute("COMMIT")

    def q(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(sql, args).fetchall()

    def one(self, sql: str, args=()) -> sqlite3.Row | None:
        with self._lock:
            return self.db.execute(sql, args).fetchone()

    def x(self, sql: str, args=()) -> int:
        with self._lock:
            return self.db.execute(sql, args).lastrowid

    # --- key/value state -------------------------------------------------
    def get(self, key: str, default=None):
        row = self.one("SELECT value FROM state WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value) -> None:
        self.x("INSERT INTO state(key, value) VALUES(?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value, ensure_ascii=False)))

    # --- events ------------------------------------------------------------
    def event(self, kind: str, message: str, level: str = "info", data=None) -> None:
        self.x("INSERT INTO events(ts, level, kind, message, data) VALUES(?,?,?,?,?)",
               (iso(now_utc()), level, kind, message,
                json.dumps(data, ensure_ascii=False, default=str) if data is not None else None))

    # --- paper ledger --------------------------------------------------------
    def ledger(self, kind: str, amount: float, order_id=None, note=None) -> None:
        self.x("INSERT INTO ledger(ts, kind, amount, order_id, note) VALUES(?,?,?,?,?)",
               (iso(now_utc()), kind, round(amount, 4), order_id, note))

    def paper_cash(self) -> tuple[float, float]:
        """(cash, reserved). Cash moves on deposit/buy/sell/fee; reserve is a hold."""
        row = self.one(
            "SELECT "
            "COALESCE(SUM(CASE WHEN kind IN ('deposit','buy','sell','fee') THEN amount END),0) AS cash, "
            "COALESCE(SUM(CASE WHEN kind IN ('reserve','release') THEN amount END),0) AS reserved "
            "FROM ledger")
        return round(row["cash"], 4), round(row["reserved"], 4)
