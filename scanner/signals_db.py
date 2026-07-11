"""Persistent SQLite record of every signal any module has surfaced, plus
the read/write primitives /review and /stats (scanner/review_module.py)
need to score and summarize them.

Lives at config.SIGNALS_DB_FILE, inside DATA_DIR -- a Railway Volume mount
in production (see config.py's comment), so it survives redeploys unlike
the rest of the container's filesystem. Also holds the `positions` table:
real contracts a member self-reported via /track (the bot has no
brokerage integration -- it never detects a purchase on its own), scoped
per chat_id so each member only ever sees their own.

All functions here are synchronous (sqlite3 is not asyncio-native) --
callers wrap them in asyncio.to_thread, the same convention this codebase
already uses for yfinance calls in data.py/options.py. A fresh connection
is opened per call rather than shared across threads, since sqlite3
connections aren't safe to hand between threads by default.
"""
import datetime as dt
import logging
import sqlite3
import time
from contextlib import contextmanager

from . import config

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,                    -- unix time the signal was logged
    signal_date TEXT NOT NULL,           -- ts's calendar date (ISO), for same-day dedup
    section TEXT NOT NULL,               -- stocks | crypto | options | leaps | heavy | golden
    symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL,           -- buy (stocks/crypto) | call (options family)
    underlying_price REAL,               -- spot/price at signal time
    contract_price REAL,                 -- premium at signal time; NULL for stocks/crypto
    strike REAL NOT NULL DEFAULT 0,      -- 0 (not NULL) for stocks/crypto -- see log_signal
    expiry TEXT NOT NULL DEFAULT '',     -- '' (not NULL) for stocks/crypto -- see log_signal
    probability REAL,                    -- POP or heuristic score at signal time
    conditions TEXT,                     -- human-readable explanation
    filters_matched TEXT,                -- comma-separated filter keys, or tier/category
    status TEXT NOT NULL DEFAULT 'open', -- open until the 30-day checkpoint completes
    review_price_7d REAL,
    review_price_30d REAL,
    outcome_7d TEXT,                     -- hit | miss | NULL (not due/reviewed yet)
    outcome_30d TEXT,
    reviewed_7d_ts REAL,
    reviewed_30d_ts REAL,
    -- strike/expiry are part of the key (not just section+symbol+date) so two
    -- distinct option contracts on the same underlying the same day both get
    -- logged; for stocks/crypto they're constant (0, ''), so the key
    -- collapses to (section, symbol, date) -- exactly the dedup that stops
    -- five members running /stocks the same morning from logging the same
    -- COST signal five times. NULL would defeat this (SQLite treats every
    -- NULL as distinct in a UNIQUE index), hence the NOT NULL DEFAULT above.
    UNIQUE(section, symbol, signal_date, strike, expiry)
)
"""
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts)",
    "CREATE INDEX IF NOT EXISTS idx_signals_section ON signals(section)",
]

_POSITIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,            -- scoped per member -- see module docstring
    symbol TEXT NOT NULL,
    strike REAL NOT NULL,
    expiry TEXT NOT NULL,
    entry_price REAL NOT NULL,           -- premium paid, from /track's PRICE argument
    side TEXT NOT NULL DEFAULT 'call',   -- always 'call' -- the bot has no PUT support
    tracked_ts REAL NOT NULL,            -- when /track was run
    original_dte INTEGER NOT NULL,       -- days-to-expiry at /track time -- the "half the
                                          -- original duration" time-stop alert needs this,
                                          -- not today's DTE, which keeps shrinking
    status TEXT NOT NULL DEFAULT 'open', -- open | closed
    closed_ts REAL,
    closed_price REAL,
    closed_reason TEXT,
    alerted_stoploss INTEGER NOT NULL DEFAULT 0,   -- one-shot flags -- each alert fires
    alerted_profit INTEGER NOT NULL DEFAULT 0,     -- once per position, not every hour it
    alerted_timestop INTEGER NOT NULL DEFAULT 0,   -- stays past the threshold
    alerted_theta INTEGER NOT NULL DEFAULT 0
)
"""
_POSITIONS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_positions_chat ON positions(chat_id, status)",
]
_ALERT_COLUMNS = {"alerted_stoploss", "alerted_profit", "alerted_timestop", "alerted_theta"}


@contextmanager
def _db():
    conn = sqlite3.connect(config.SIGNALS_DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Idempotent -- safe to call on every startup."""
    with _db() as conn:
        conn.execute(_SCHEMA)
        for stmt in _INDEXES:
            conn.execute(stmt)
        conn.execute(_POSITIONS_SCHEMA)
        for stmt in _POSITIONS_INDEXES:
            conn.execute(stmt)


def _row_fields(section: str, row: dict) -> dict:
    """Maps a module's result-row dict onto the signals table's columns.
    stocks_module/crypto_module rows share one shape; options_module/
    heavy_module (and later golden) rows share another -- kept in one
    place so a new module only ever needs one branch here, not changes
    scattered through bot.py."""
    is_options_family = section in ("options", "leaps", "heavy", "golden")

    if is_options_family:
        probability = row.get("probability_of_profit")
        if section == "heavy":
            filters_matched = row.get("category", "")
        elif probability is not None:
            if probability >= config.OPTIONS_TIER_GOLD:
                filters_matched = "gold"
            elif probability >= config.OPTIONS_TIER_SILVER:
                filters_matched = "silver"
            else:
                filters_matched = "bronze"
        else:
            filters_matched = ""
        delta, iv, days = row.get("delta"), row.get("iv"), row.get("days")
        conditions = (f"delta={delta:.2f}, iv={iv * 100:.0f}%, dte={days}"
                     if delta is not None and iv is not None else f"dte={days}")
        return {
            "underlying_price": row.get("spot"),
            "signal_type": row.get("side", "call"),
            "contract_price": row.get("premium"),
            "strike": row.get("strike") or 0.0,
            "expiry": row.get("expiry") or "",
            "probability": probability,
            "conditions": conditions,
            "filters_matched": filters_matched,
        }

    return {
        "underlying_price": row.get("price"),
        "signal_type": "buy",
        "contract_price": None,
        "strike": 0.0,
        "expiry": "",
        "probability": row.get("probability_of_profit"),
        "conditions": row.get("explanation", ""),
        "filters_matched": ",".join(row.get("matched", [])),
    }


def log_signal(section: str, row: dict, ts: float | None = None) -> bool:
    """Inserts one signal row, skipping a same-day duplicate for the same
    (section, symbol, strike, expiry) -- see the UNIQUE constraint's
    comment above. Returns True if a new row was actually inserted."""
    symbol = row.get("symbol")
    if not symbol:
        return False
    ts = ts if ts is not None else time.time()
    signal_date = dt.date.fromtimestamp(ts).isoformat()
    f = _row_fields(section, row)
    with _db() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO signals
               (ts, signal_date, section, symbol, signal_type, underlying_price,
                contract_price, strike, expiry, probability, conditions, filters_matched)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, signal_date, section, symbol, f["signal_type"], f["underlying_price"],
             f["contract_price"], f["strike"], f["expiry"], f["probability"],
             f["conditions"], f["filters_matched"]),
        )
        return cur.rowcount > 0


def fetch_due_for_review(window_days: int, now: float | None = None,
                         limit: int | None = None) -> list[sqlite3.Row]:
    """Signals at least `window_days` old that haven't been reviewed at
    that checkpoint yet, oldest first."""
    now = now if now is not None else time.time()
    cutoff_ts = now - window_days * 86400
    outcome_col = "outcome_7d" if window_days == 7 else "outcome_30d"
    sql = f"SELECT * FROM signals WHERE ts <= ? AND {outcome_col} IS NULL ORDER BY ts ASC"
    params: tuple = (cutoff_ts,)
    if limit is not None:
        sql += " LIMIT ?"
        params += (limit,)
    with _db() as conn:
        return conn.execute(sql, params).fetchall()


def update_review_outcome(signal_id: int, window_days: int, price: float | None,
                          outcome: str, ts: float | None = None) -> None:
    ts = ts if ts is not None else time.time()
    price_col = "review_price_7d" if window_days == 7 else "review_price_30d"
    outcome_col = "outcome_7d" if window_days == 7 else "outcome_30d"
    ts_col = "reviewed_7d_ts" if window_days == 7 else "reviewed_30d_ts"
    with _db() as conn:
        if window_days == 30:
            conn.execute(
                f"UPDATE signals SET {price_col}=?, {outcome_col}=?, {ts_col}=?, "
                f"status='reviewed' WHERE id=?",
                (price, outcome, ts, signal_id))
        else:
            conn.execute(
                f"UPDATE signals SET {price_col}=?, {outcome_col}=?, {ts_col}=? WHERE id=?",
                (price, outcome, ts, signal_id))


def bulk_update_outcomes(window_days: int, updates: list[tuple[int, float | None, str]],
                         ts: float | None = None) -> None:
    """updates: [(signal_id, price, outcome), ...] -- one connection/
    transaction for the whole batch instead of one per row, since a single
    /review run can touch up to REVIEW_MAX_PER_RUN signals."""
    if not updates:
        return
    ts = ts if ts is not None else time.time()
    price_col = "review_price_7d" if window_days == 7 else "review_price_30d"
    outcome_col = "outcome_7d" if window_days == 7 else "outcome_30d"
    ts_col = "reviewed_7d_ts" if window_days == 7 else "reviewed_30d_ts"
    status_clause = ", status='reviewed'" if window_days == 30 else ""
    with _db() as conn:
        conn.executemany(
            f"UPDATE signals SET {price_col}=?, {outcome_col}=?, {ts_col}=?{status_clause} "
            f"WHERE id=?",
            [(price, outcome, ts, signal_id) for signal_id, price, outcome in updates],
        )


def fetch_reviewed_signals() -> list[sqlite3.Row]:
    """Every signal with at least one completed review checkpoint -- the
    dataset /stats aggregates over."""
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM signals WHERE outcome_7d IS NOT NULL OR outcome_30d IS NOT NULL"
        ).fetchall()


def count_open_signals() -> int:
    with _db() as conn:
        return conn.execute("SELECT COUNT(*) FROM signals WHERE status='open'").fetchone()[0]


# ------------------------------------------------------------- positions

def find_open_position(chat_id: int, symbol: str, strike: float, expiry: str) -> sqlite3.Row | None:
    with _db() as conn:
        return conn.execute(
            "SELECT * FROM positions WHERE chat_id=? AND symbol=? AND strike=? "
            "AND expiry=? AND status='open'",
            (chat_id, symbol, strike, expiry)).fetchone()


def add_position(chat_id: int, symbol: str, strike: float, expiry: str, entry_price: float,
                 original_dte: int, ts: float | None = None) -> int | None:
    """Returns the new position's id, or None if an identical OPEN position
    already exists for this chat -- the caller should tell the member to
    /untrack it first instead of silently creating a duplicate tracker."""
    if find_open_position(chat_id, symbol, strike, expiry) is not None:
        return None
    ts = ts if ts is not None else time.time()
    with _db() as conn:
        cur = conn.execute(
            """INSERT INTO positions
               (chat_id, symbol, strike, expiry, entry_price, tracked_ts, original_dte)
               VALUES (?,?,?,?,?,?,?)""",
            (chat_id, symbol, strike, expiry, entry_price, ts, original_dte))
        return cur.lastrowid


def fetch_open_positions(chat_id: int | None = None) -> list[sqlite3.Row]:
    """All open positions, or one chat's -- the monitoring job wants every
    chat's; /positions wants just the caller's."""
    with _db() as conn:
        if chat_id is None:
            return conn.execute("SELECT * FROM positions WHERE status='open'").fetchall()
        return conn.execute(
            "SELECT * FROM positions WHERE chat_id=? AND status='open'", (chat_id,)).fetchall()


def fetch_matching_positions(chat_id: int, symbol: str, strike: float | None = None,
                             expiry: str | None = None) -> list[sqlite3.Row]:
    """For /untrack -- symbol alone often disambiguates, strike/expiry
    narrow it further when a member has more than one open position on the
    same underlying."""
    sql = "SELECT * FROM positions WHERE chat_id=? AND symbol=? AND status='open'"
    params: tuple = (chat_id, symbol)
    if strike is not None:
        sql += " AND strike=?"
        params += (strike,)
    if expiry is not None:
        sql += " AND expiry=?"
        params += (expiry,)
    with _db() as conn:
        return conn.execute(sql, params).fetchall()


def close_position(position_id: int, closed_price: float | None, reason: str,
                   ts: float | None = None) -> None:
    ts = ts if ts is not None else time.time()
    with _db() as conn:
        conn.execute(
            "UPDATE positions SET status='closed', closed_ts=?, closed_price=?, closed_reason=? "
            "WHERE id=?",
            (ts, closed_price, reason, position_id))


def mark_alerted(position_id: int, column: str) -> None:
    if column not in _ALERT_COLUMNS:
        raise ValueError(f"unknown alert column: {column!r}")
    with _db() as conn:
        conn.execute(f"UPDATE positions SET {column}=1 WHERE id=?", (position_id,))
