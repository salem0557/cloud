"""Central configuration for the crypto bot, overridable via environment.

Same convention as scanner/config.py: a bad env value warns and falls back to
the default instead of crashing the bot mid-session.
"""
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


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [s.strip().upper() for s in raw.replace(";", ",").split(",") if s.strip()]


# --- Telegram ---
BOT_TOKEN = os.environ.get("CRYPTO_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Only this chat may touch money-moving commands. 0 disables every trade
# command, which is the right default for a token that leaked somewhere.
ADMIN_CHAT_ID = _int("CRYPTO_ADMIN_CHAT_ID", 0) or _int("ADMIN_CHAT_ID", 0)

# --- Exchange connection ---
# ccxt exchange id: binance, bybit, okx, kucoin, ...
EXCHANGE_ID = os.environ.get("EXCHANGE_ID", "binance").strip().lower()
API_KEY = os.environ.get("EXCHANGE_API_KEY", "")
API_SECRET = os.environ.get("EXCHANGE_API_SECRET", "")
API_PASSWORD = os.environ.get("EXCHANGE_API_PASSWORD", "")  # okx/kucoin passphrase
MARKET_TYPE = os.environ.get("EXCHANGE_MARKET_TYPE", "spot").strip().lower()  # spot|swap
TESTNET = _bool("EXCHANGE_TESTNET", False)

# --- The live-trading airlock ---
# Two independent switches: a flag you can flip by accident, and a phrase you
# cannot. Both must agree or the trader stays in paper mode.
LIVE_TRADING = _bool("CRYPTO_LIVE_TRADING", False)
LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_THE_RISK"
LIVE_CONFIRM = os.environ.get("CRYPTO_LIVE_CONFIRM", "").strip()

# --- Universe ---
QUOTE = os.environ.get("CRYPTO_QUOTE", "USDT").strip().upper()
WATCHLIST = _list("CRYPTO_WATCHLIST", "BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT")
# 0 = watchlist only; >0 also pulls the N most-traded pairs of the quote asset
AUTO_TOP_N = _int("CRYPTO_AUTO_TOP_N", 0)
MIN_24H_QUOTE_VOLUME = _float("CRYPTO_MIN_24H_VOLUME", 20_000_000.0)
MAX_SPREAD_PCT = _float("CRYPTO_MAX_SPREAD_PCT", 0.08)  # bid/ask gap, percent

# --- Timeframes (fast scalper: decide on the low frame, obey the high one) ---
LTF = os.environ.get("CRYPTO_LTF", "5m")     # entry/trigger frame
HTF = os.environ.get("CRYPTO_HTF", "1h")     # trend/context frame
LTF_LIMIT = _int("CRYPTO_LTF_LIMIT", 200)    # candles pulled per scan
HTF_LIMIT = _int("CRYPTO_HTF_LIMIT", 250)
LOOP_SECONDS = _int("CRYPTO_LOOP_SECONDS", 30)  # engine tick

# --- Analyst thresholds ---
MIN_SCORE = _float("CRYPTO_MIN_SCORE", 70.0)        # of 100 weighted points
MIN_RR = _float("CRYPTO_MIN_RR", 1.5)               # reward:risk before fees
ALLOW_SHORT = _bool("CRYPTO_ALLOW_SHORT", False)    # needs MARKET_TYPE=swap
RSI_PERIOD = _int("CRYPTO_RSI_PERIOD", 14)
ATR_PERIOD = _int("CRYPTO_ATR_PERIOD", 14)
EMA_FAST = _int("CRYPTO_EMA_FAST", 9)
EMA_SLOW = _int("CRYPTO_EMA_SLOW", 21)
EMA_TREND = _int("CRYPTO_EMA_TREND", 50)
EMA_TREND_SLOW = _int("CRYPTO_EMA_TREND_SLOW", 200)
BB_PERIOD = _int("CRYPTO_BB_PERIOD", 20)
BB_STD = _float("CRYPTO_BB_STD", 2.0)
VOL_SURGE_MULT = _float("CRYPTO_VOL_SURGE", 1.5)    # volume vs 20-bar average
MIN_ATR_PCT = _float("CRYPTO_MIN_ATR_PCT", 0.15)    # dead market -> skip
MAX_ATR_PCT = _float("CRYPTO_MAX_ATR_PCT", 3.0)     # knife market -> skip
MAX_EXTENSION_ATR = _float("CRYPTO_MAX_EXTENSION_ATR", 2.0)  # no chasing pumps

# --- Risk (the part that decides how much you can lose, not how much you win)
RISK_PER_TRADE_PCT = _float("CRYPTO_RISK_PER_TRADE_PCT", 0.5)   # % of equity at stop
# Ceiling on a single position's notional. With a tight scalping stop this cap
# binds before the risk formula does, which only ever lowers the risk taken —
# MAX_OPEN_POSITIONS x this value is the most equity that can be deployed.
MAX_POSITION_PCT = _float("CRYPTO_MAX_POSITION_PCT", 25.0)      # % of equity notional
MAX_OPEN_POSITIONS = _int("CRYPTO_MAX_OPEN_POSITIONS", 3)
MAX_TRADES_PER_DAY = _int("CRYPTO_MAX_TRADES_PER_DAY", 20)
DAILY_MAX_LOSS_PCT = _float("CRYPTO_DAILY_MAX_LOSS_PCT", 3.0)   # halts the day
SYMBOL_COOLDOWN_MIN = _int("CRYPTO_SYMBOL_COOLDOWN_MIN", 30)    # after a loss
MIN_ORDER_USD = _float("CRYPTO_MIN_ORDER_USD", 10.0)
LEVERAGE = _float("CRYPTO_LEVERAGE", 1.0)                       # swap only

# --- Exit plan ---
SL_ATR_MULT = _float("CRYPTO_SL_ATR_MULT", 1.2)
TP1_R = _float("CRYPTO_TP1_R", 1.0)      # first target in R multiples
TP2_R = _float("CRYPTO_TP2_R", 2.0)
TP1_FRACTION = _float("CRYPTO_TP1_FRACTION", 0.5)   # portion sold at TP1
TRAIL_ATR_MULT = _float("CRYPTO_TRAIL_ATR_MULT", 1.0)  # trails only after TP1
BREAKEVEN_AFTER_TP1 = _bool("CRYPTO_BREAKEVEN_AFTER_TP1", True)
MAX_HOLD_MINUTES = _int("CRYPTO_MAX_HOLD_MINUTES", 90)
STALE_EXIT_R = _float("CRYPTO_STALE_EXIT_R", 0.3)   # cut if flat past the clock

# --- Paper-trading realism ---
PAPER_START_BALANCE = _float("CRYPTO_PAPER_BALANCE", 1000.0)
TAKER_FEE_PCT = _float("CRYPTO_TAKER_FEE_PCT", 0.1)   # per side
SLIPPAGE_PCT = _float("CRYPTO_SLIPPAGE_PCT", 0.05)

STATE_FILE = os.environ.get("CRYPTO_STATE_FILE", "crypto_state.json")


def live_enabled() -> bool:
    """True only when both airlock switches agree."""
    return LIVE_TRADING and LIVE_CONFIRM == LIVE_CONFIRM_PHRASE


def live_blocker() -> str:
    """Human-readable reason live trading is off, or '' when it is on."""
    if not LIVE_TRADING:
        return "CRYPTO_LIVE_TRADING غير مفعّل"
    if LIVE_CONFIRM != LIVE_CONFIRM_PHRASE:
        return f"CRYPTO_LIVE_CONFIRM يجب أن يساوي {LIVE_CONFIRM_PHRASE}"
    if not (API_KEY and API_SECRET):
        return "مفاتيح المنصة غير مضبوطة"
    return ""


def shorts_enabled() -> bool:
    """Spot cannot short; only a derivatives market can."""
    return ALLOW_SHORT and MARKET_TYPE in ("swap", "future", "futures")
