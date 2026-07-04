"""Central configuration, overridable via environment variables."""
import os


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


# --- Telegram ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# --- Data fetching ---
INTERVAL = os.environ.get("SCAN_INTERVAL", "1h")   # candle timeframe
PERIOD = os.environ.get("SCAN_PERIOD", "3mo")      # history depth per symbol
BATCH_SIZE = _int("BATCH_SIZE", 100)               # symbols per yfinance request
SCAN_PAUSE_SECONDS = _int("SCAN_PAUSE_SECONDS", 60)  # breather between cycles
# Pacing guards: unbounded parallel downloads ballooned memory and hammered
# Yahoo (a 3265-stock cycle finished in 66s), crashing the container.
DOWNLOAD_THREADS = _int("DOWNLOAD_THREADS", 12)     # parallel requests per batch
BATCH_INTERVAL_SECONDS = _float("BATCH_INTERVAL_SECONDS", 2.0)  # floor between batches

# --- Liquidity pre-filter (skip dead/penny stocks) ---
MIN_PRICE = _float("MIN_PRICE", 2.0)               # USD
MIN_AVG_VOLUME = _int("MIN_AVG_VOLUME", 30_000)    # avg volume per hourly bar

# --- Crypto ---
CRYPTO_ENABLED = os.environ.get("CRYPTO_ENABLED", "1") == "1"
# Coins are priced from cents to thousands of dollars, so liquidity is
# judged in dollar volume per hourly bar instead of price/share-count.
MIN_CRYPTO_DOLLAR_VOLUME = _float("MIN_CRYPTO_DOLLAR_VOLUME", 50_000)
CRYPTO_EXTRA = [s.strip().upper() for s in
                os.environ.get("CRYPTO_EXTRA", "").split(",") if s.strip()]

# --- Filter parameters ---
BB_PERIOD = _int("BB_PERIOD", 20)
BB_STD = _float("BB_STD", 2.0)
BB_TOUCH_TOLERANCE = _float("BB_TOUCH_TOLERANCE", 0.005)  # close within 0.5% of lower band

RSI_PERIOD = _int("RSI_PERIOD", 14)
RSI_OVERSOLD = _float("RSI_OVERSOLD", 30.0)

SUPPORT_LOOKBACK = _int("SUPPORT_LOOKBACK", 250)          # bars scanned for pivot lows
SUPPORT_CLUSTER_TOL = _float("SUPPORT_CLUSTER_TOL", 0.01) # pivots within 1% form one level
SUPPORT_MIN_TOUCHES = _int("SUPPORT_MIN_TOUCHES", 2)
SUPPORT_PROXIMITY = _float("SUPPORT_PROXIMITY", 0.015)    # price within 1.5% above level
SUPPORT_BREAK_TOL = _float("SUPPORT_BREAK_TOL", 0.005)    # allow 0.5% dip below level

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

# --- Alerting ---
FILTERS_REQUIRED = _int("FILTERS_REQUIRED", 3)     # minimum matched filters (out of 4)
ALERT_MEMORY_HOURS = _int("ALERT_MEMORY_HOURS", 24)  # identical alert not resent unless
                                                     # its signal was gone this long

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
