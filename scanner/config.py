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
BATCH_SIZE = _int("BATCH_SIZE", 200)               # symbols per yfinance request

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
SUPPORT_PROXIMITY = _float("SUPPORT_PROXIMITY", 0.015)    # price within 1.5% above level
SUPPORT_BREAK_TOL = _float("SUPPORT_BREAK_TOL", 0.005)    # allow 0.5% dip below level

WEDGE_LOOKBACK = _int("WEDGE_LOOKBACK", 120)
WEDGE_PIVOT_ORDER = _int("WEDGE_PIVOT_ORDER", 3)
WEDGE_MIN_BARS = _int("WEDGE_MIN_BARS", 20)

# --- Alerting ---
FILTERS_REQUIRED = _int("FILTERS_REQUIRED", 3)     # minimum matched filters (out of 4)

# --- Files ---
# On Railway, attaching a volume sets RAILWAY_VOLUME_MOUNT_PATH automatically,
# so state survives redeploys with no extra configuration.
DATA_DIR = (os.environ.get("DATA_DIR")
            or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
            or ".")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(DATA_DIR, "state.json"))
UNIVERSE_CACHE = os.environ.get("UNIVERSE_CACHE", os.path.join(DATA_DIR, "universe.json"))
UNIVERSE_MAX_AGE_HOURS = _int("UNIVERSE_MAX_AGE_HOURS", 24)
