"""Central configuration — edit values here, not inside the scripts."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Writable location for state and the journal. On Railway this points at the
# mounted volume (set SNIPER_DATA_DIR=/data); everywhere else it is the project
# folder. Railway containers have an ephemeral filesystem, so without the volume
# every redeploy would wipe the daily counter and the paper-trading history.
DATA_DIR = Path(os.environ.get("SNIPER_DATA_DIR") or Path(__file__).parent)


def _load_env():
    env = Path(__file__).parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

# ── API keys (loaded from .env) ─────────────────────────────────
# The .env.example placeholders are shipped in the repo, and Railway's
# "Suggested Variables" panel offers to import them verbatim. A placeholder is
# a non-empty string, so an emptiness check would pass and the first UW call
# would fail with a bare 401 every 30 minutes instead of saying why.
_PLACEHOLDER_MARKERS = ("ضع_", "your-token-here", "your_username", "_هنا")


def _clean(name):
    v = os.environ.get(name, "").strip()
    if v and any(m in v for m in _PLACEHOLDER_MARKERS):
        return ""
    return v


UW_API_KEY       = _clean("UW_API_KEY")
UW_BASE          = "https://api.unusualwhales.com"
FINVIZ_AUTH      = _clean("FINVIZ_AUTH")
TELEGRAM_TOKEN   = _clean("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _clean("TELEGRAM_CHAT_ID")

# ── Scoring (agreed design: 30/30/20/20, threshold 85) ──────────
WEIGHTS = {"flow": 30, "technical": 30, "catalyst": 20, "liquidity": 20}
THRESHOLD          = 85     # calibrate from journal.csv after 2-4 weeks paper
WATCHLIST_FLOOR    = 65     # candidates >= this go to shortlist.json
MAX_ALERTS_PER_DAY = 5

# ── Budget tiers (contract cost = ask x 100) ────────────────────
BUDGET_TIERS = [
    ("🟢 آمن (ITM) <200$",         200),
    ("🟡 متوازن (ATM) <100$",      100),
    ("🔴 عالي المخاطرة (OTM) <50$", 50),
]

# ── 15m technical frame ─────────────────────────────────────────
CANDLE_SIZE        = "15m"
CANDLES_LOOKBACK   = 40     # bars used for level detection
ATR_PERIOD         = 14
VOLUME_SPIKE_RATIO = 1.5    # candle volume vs prior-bar average
TARGET_ATR_MULT    = 1.5    # target = broken level +/- 1.5 x ATR
STOP_ATR_MULT      = 1.0    # stop   = broken level -/+ 1.0 x ATR
MIN_REMAINING_ATR  = 0.75   # reject a setup whose target is already this close.
                            # Without it the scanner alerts on breakouts that have
                            # ALREADY run to target: price $102.40, target $102.58,
                            # 18c of room left and an "expected profit" of 6%.
                            # A late entry costs more than a false one.
REGULAR_HOURS_ONLY = True   # ignore pre/post-market candles (market_time == "r")

# ── Exit rules for open positions ───────────────────────────────
PROFIT_TAKE_PCT = 60
STOP_LOSS_PCT   = -40

# ── Liquidity minimums (contracts failing these are dropped) ────
MAX_SPREAD_PCT    = 8       # (ask-bid)/mid * 100
MAX_SPREAD_ABS    = 0.06    # ...OR this many dollars wide, whichever is kinder.
                            # A $0.45 contract with a normal 4c spread is 9% and
                            # would fail a pure percentage cap — which emptied the
                            # 🔴 OTM tier on almost every scan. Cheap contracts are
                            # judged in cents, expensive ones in percent.
MIN_OPEN_INTEREST = 300

# ── Contract selection window ───────────────────────────────────
MIN_DTE = 0                 # 0 = same-day expiry (0DTE) allowed — Salem's call
MAX_DTE = 45

# 0DTE-specific. A same-day contract loses its remaining value into the close,
# so an entry taken late in the session needs the move to happen almost at once.
# Set to 0 to disable the cutoff entirely.
MIN_MINUTES_TO_CLOSE = 45   # no new 0DTE alert inside this window before 16:00 ET

# Assumed holding time, in trading hours, used only to price theta into the
# profit estimate. The 15m breakout is expected to resolve within a couple of
# bars; raise it if you hold longer.
HOLD_HOURS = 2.0
TRADING_HOURS_PER_DAY = 6.5

# ── Scan limits (UW trial = 30,000 requests/day) ────────────────
MAX_CANDIDATES_PER_SCAN = 25   # tickers we spend chain/candle calls on
FLOW_ALERT_LIMIT        = 200
MIN_TICKER_PREMIUM      = 250_000   # skip tickers below this daily premium

# ── Finviz Elite (candidate discovery only — never scored) ──────
MAX_FINVIZ_MOVERS  = 40     # rows to pull from the screener
MAX_FINVIZ_LOOKUPS = 15     # of those, how many get a UW per-ticker flow call.
                            # Each is one request; the trial allows 30,000/day,
                            # so 15 x ~14 scans/day is comfortably inside it.

# ── Message composition ─────────────────────────────────────────
# True  = `claude -p` writes the Arabic message (Salem's original design)
# False = deterministic Python formatter (no LLM, zero fabrication risk)
# Either way every number comes from the computed JSON.
# Overridable per-environment: a Railway container has no `claude` CLI, so set
# USE_CLAUDE_COMPOSER=0 there.
USE_CLAUDE_COMPOSER = os.environ.get("USE_CLAUDE_COMPOSER", "1").lower() not in ("0", "false", "no")
CLAUDE_TIMEOUT_SEC  = 300

# ── Scheduler (used by scheduler.py on an always-on host) ───────
SCAN_EVERY_MIN    = 30
MONITOR_EVERY_MIN = 5
HEARTBEAT_MIN     = 60      # a line in the log so a healthy idle service is
                            # distinguishable from a dead one over a weekend

# ── State / data files ──────────────────────────────────────────
DATA_DIR.mkdir(parents=True, exist_ok=True)
SHORTLIST_FILE = DATA_DIR / "shortlist.json"
POSITIONS_FILE = DATA_DIR / "positions.json"
STATE_FILE     = DATA_DIR / "state.json"
LOCK_FILE      = DATA_DIR / ".state.lock"
JOURNAL_FILE   = DATA_DIR / "journal.csv"
