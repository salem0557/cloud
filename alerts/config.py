"""Configuration for the price-extreme alert bot, overridable via environment."""
import logging
import os

log = logging.getLogger(__name__)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        log.error("Env %s=%r is not a number; using default %r",
                  name, os.environ.get(name), default)
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        log.error("Env %s=%r is not a number; using default %r",
                  name, os.environ.get(name), default)
        return default


def _list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [s.strip().lower() for s in raw.replace(";", ",").split(",") if s.strip()]


BOT_TOKEN = os.environ.get("ALERTS_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_CHAT_ID = _int("ALERTS_ADMIN_CHAT_ID", 0)

# --- Data sources (both public, no API keys needed) ---
CRYPTO_EXCHANGE = os.environ.get("ALERTS_EXCHANGE", "binance").strip().lower()
CRYPTO_QUOTE = os.environ.get("ALERTS_QUOTE", "USDT").strip().upper()

# --- Polling ---
POLL_SECONDS = _int("ALERTS_POLL_SECONDS", 60)
MAX_WATCHES_PER_CHAT = _int("ALERTS_MAX_WATCHES", 25)

# --- Alert behaviour ---
# What counts as a *new* extreme worth a message: the price must beat the last
# one we announced by this much, so a symbol grinding down tick by tick sends
# one alert per real move instead of one per poll.
MIN_MOVE_PCT = _float("ALERTS_MIN_MOVE_PCT", 0.05)
# Floor on the gap between two alerts for the same symbol/period/direction.
COOLDOWN_MINUTES = _int("ALERTS_COOLDOWN_MINUTES", 5)
# Periods enabled when you just send a symbol name with no options.
DEFAULT_PERIODS = _list("ALERTS_DEFAULT_PERIODS", "day")
DEFAULT_DIRECTIONS = _list("ALERTS_DEFAULT_DIRECTIONS", "low,high")

# --- Stocks ---
# Poll US stocks only from pre-market through the post-market close; outside
# that window the price cannot move, so every request would be wasted.
STOCK_HOURS_ONLY = os.environ.get("ALERTS_STOCK_HOURS_ONLY", "1") == "1"
STOCK_OPEN_HOUR = _int("ALERTS_STOCK_OPEN_HOUR", 4)     # ET, pre-market
STOCK_CLOSE_HOUR = _int("ALERTS_STOCK_CLOSE_HOUR", 20)  # ET, post-market

STATE_FILE = os.environ.get("ALERTS_STATE_FILE", "alerts_state.json")
