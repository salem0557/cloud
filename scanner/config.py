"""Central configuration, overridable via environment variables."""
import logging
import os

log = logging.getLogger(__name__)


def _int(name: str, default: int) -> int:
    # A malformed env value (e.g. "@username" in ADMIN_CHAT_ID) must not
    # crash the whole bot — warn loudly and fall back to the default.
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


# --- Telegram ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# --- Paid subscription gate ---
ADMIN_CHAT_ID = _int("ADMIN_CHAT_ID", 0)   # your own Telegram chat id (always eligible)
SUBSCRIBE_CONTACT = os.environ.get("SUBSCRIBE_CONTACT", "مشغّل البوت")
DEFAULT_SUB_DAYS = _int("DEFAULT_SUB_DAYS", 30)

# --- Data fetching ---
INTERVAL = os.environ.get("SCAN_INTERVAL", "1h")   # candle timeframe
PERIOD = os.environ.get("SCAN_PERIOD", "3mo")      # history depth per symbol
BATCH_SIZE = _int("BATCH_SIZE", 100)               # symbols per yfinance request
SCAN_PAUSE_SECONDS = _int("SCAN_PAUSE_SECONDS", 60)  # breather between cycles
# Pacing guards: unbounded parallel downloads ballooned memory and hammered
# Yahoo (a 3265-stock cycle finished in 66s), crashing the container.
DOWNLOAD_THREADS = _int("DOWNLOAD_THREADS", 12)     # parallel requests per batch
BATCH_INTERVAL_SECONDS = _float("BATCH_INTERVAL_SECONDS", 2.0)  # floor between batches

# --- Off-peak savings: no scanning at all on weekends, market holidays, or
# --- (by default) outside the daily active window: 9:30 AM ET open through
# --- 3:00 AM Riyadh time (see scanner/market_calendar.is_active_session) ---
WEEKEND_HOLIDAY_PAUSE_ENABLED = os.environ.get("WEEKEND_HOLIDAY_PAUSE_ENABLED", "1") == "1"
MARKET_HOURS_ONLY_ENABLED = os.environ.get("MARKET_HOURS_ONLY_ENABLED", "1") == "1"

# --- Liquidity pre-filter (skip dead/penny stocks) ---
MIN_PRICE = _float("MIN_PRICE", 2.0)               # USD
MIN_AVG_VOLUME = _int("MIN_AVG_VOLUME", 30_000)    # avg volume per hourly bar

# --- Filter parameters ---
BB_PERIOD = _int("BB_PERIOD", 20)
BB_STD = _float("BB_STD", 2.0)
BB_TOUCH_TOLERANCE = _float("BB_TOUCH_TOLERANCE", 0.005)  # close within 0.5% of lower band

RSI_PERIOD = _int("RSI_PERIOD", 14)
RSI_OVERSOLD = _float("RSI_OVERSOLD", 30.0)

SUPPORT_LOOKBACK = _int("SUPPORT_LOOKBACK", 250)          # bars scanned for pivot lows
SUPPORT_CLUSTER_TOL = _float("SUPPORT_CLUSTER_TOL", 0.01) # pivots within 1% form one level
SUPPORT_MIN_TOUCHES = _int("SUPPORT_MIN_TOUCHES", 2)
SUPPORT_PROXIMITY = _float("SUPPORT_PROXIMITY", 0.015)    # price within 1.5% of level
SUPPORT_BREAK_TOL = _float("SUPPORT_BREAK_TOL", 0.005)    # allow 0.5% slip past the level

WEDGE_LOOKBACK = _int("WEDGE_LOOKBACK", 120)
WEDGE_PIVOT_ORDER = _int("WEDGE_PIVOT_ORDER", 3)
WEDGE_MIN_BARS = _int("WEDGE_MIN_BARS", 20)

# --- Qualified list (full passes qualify liquid symbols; continuous cycles
# --- then scan only those, cutting request volume drastically) ---
# Rebuilt on a schedule (ET times, comma-separated), following the trading
# day's natural order — the overnight session opens the day (20:00 ET, the
# first session after a weekend), then pre-market, regular hours, and the
# close. First rebuild 19:30 (just before overnight opens, including Sunday
# night after the weekend), second 16:30 (right after the regular close).
def _times(name: str, default: str) -> list[tuple[int, int]]:
    out = []
    for part in os.environ.get(name, default).split(","):
        hh, mm = part.strip().split(":")
        out.append((int(hh), int(mm)))
    return out


QUALIFY_REBUILD_TIMES = _times("QUALIFY_REBUILD_TIMES", "19:30,16:30")

# --- Hot list: near-signal symbols get re-checked on a fast lane ---
HOTLIST_MIN_SCORE = _int("HOTLIST_MIN_SCORE", 2)       # filters needed to be "hot"
HOTLIST_INTERVAL_SECONDS = _int("HOTLIST_INTERVAL_SECONDS", 120)
HOTLIST_MAX = _int("HOTLIST_MAX", 300)                 # safety cap per fast pass

# --- Adaptive throttle (temporary: backs off on Yahoo rejections, recovers) ---
THROTTLE_MAX_DELAY = _float("THROTTLE_MAX_DELAY", 600)  # seconds between batches

# --- Chart image (candles + Bollinger + support + RSI, one per alert) ---
CHART_ENABLED = os.environ.get("CHART_ENABLED", "1") == "1"
CHART_BARS = _int("CHART_BARS", 80)     # most recent hourly bars plotted

# --- Options picks (attached to each alerted stock) ---
OPTIONS_ENABLED = os.environ.get("OPTIONS_ENABLED", "1") == "1"
OPTIONS_MAX_WEEKS = _int("OPTIONS_MAX_WEEKS", 13)     # nearest expiry .. ~3 months
OPTIONS_MAX_EXPIRIES = _int("OPTIONS_MAX_EXPIRIES", 6)  # chain requests per stock
OPTIONS_TOP_N = _int("OPTIONS_TOP_N", 3)              # picks per side (call/put)
OPTIONS_MIN_ACTIVITY = _int("OPTIONS_MIN_ACTIVITY", 20)  # min OI+volume per contract

# --- On-demand /cheapoptions search: scans the current qualified list for
# --- contracts priced at or under a cap (contract cost = premium * 100) ---
CHEAP_OPTION_DEFAULT_MAX = _float("CHEAP_OPTION_DEFAULT_MAX", 50.0)  # $ per contract
CHEAP_OPTIONS_PACE_SECONDS = _float("CHEAP_OPTIONS_PACE_SECONDS", 0.3)  # between symbols
CHEAP_OPTIONS_PROGRESS_EVERY = _int("CHEAP_OPTIONS_PROGRESS_EVERY", 150)  # symbols per status edit

# --- Alerting ---
FILTERS_REQUIRED = _int("FILTERS_REQUIRED", 4)     # minimum matched filters (out of 4)
ALERT_MEMORY_HOURS = _int("ALERT_MEMORY_HOURS", 24)  # identical alert not resent unless
                                                     # its signal was gone this long

# --- Performance tracking: each alert's return vs SPY over fixed horizons,
# --- building a real track record (pure price math, no LLM/extra cost) ---
PERFORMANCE_ENABLED = os.environ.get("PERFORMANCE_ENABLED", "1") == "1"


def _int_list(name: str, default: str) -> list[int]:
    return [int(x.strip()) for x in os.environ.get(name, default).split(",") if x.strip()]


PERFORMANCE_HORIZONS_HOURS = _int_list("PERFORMANCE_HORIZONS_HOURS", "24,72")
PERFORMANCE_CHECK_INTERVAL_SECONDS = _int("PERFORMANCE_CHECK_INTERVAL_SECONDS", 1800)
# Below this many resolved signals for a horizon, the one-line summary is
# withheld from alerts (a "100% win rate" off 1 signal is misleading).
PERFORMANCE_MIN_SAMPLE = _int("PERFORMANCE_MIN_SAMPLE", 5)

# --- "وجهة نظر البوت": news headlines + StockTwits chatter merged into one
# --- short paragraph by a single cheap Gemini call per alert. Pure
# --- consolidation of external sources, not the bot's own analysis. Both
# --- data sources are free/keyless; only the summarization call needs a key.
SENTIMENT_ENABLED = os.environ.get("SENTIMENT_ENABLED", "1") == "1"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
SENTIMENT_NEWS_LIMIT = _int("SENTIMENT_NEWS_LIMIT", 5)
SENTIMENT_SOCIAL_LIMIT = _int("SENTIMENT_SOCIAL_LIMIT", 20)
SENTIMENT_MAX_CHARS = _int("SENTIMENT_MAX_CHARS", 500)

# --- Files ---
# On Railway, attaching a volume sets RAILWAY_VOLUME_MOUNT_PATH automatically,
# so state survives redeploys with no extra configuration.
DATA_DIR = (os.environ.get("DATA_DIR")
            or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
            or ".")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(DATA_DIR, "state.json"))
UNIVERSE_CACHE = os.environ.get("UNIVERSE_CACHE", os.path.join(DATA_DIR, "universe.json"))
QUALIFIED_FILE = os.environ.get("QUALIFIED_FILE", os.path.join(DATA_DIR, "qualified.json"))
UNIVERSE_MAX_AGE_HOURS = _int("UNIVERSE_MAX_AGE_HOURS", 24)
PERFORMANCE_FILE = os.environ.get("PERFORMANCE_FILE", os.path.join(DATA_DIR, "performance.json"))
