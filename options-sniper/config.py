"""Central configuration — edit values here, not inside the scripts."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent


def _load_env():
    env = BASE_DIR / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


_load_env()

# ── API keys (loaded from .env) ─────────────────────────────────
UW_API_KEY       = os.environ.get("UW_API_KEY", "")
UW_BASE          = "https://api.unusualwhales.com"
FINVIZ_AUTH      = os.environ.get("FINVIZ_AUTH", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

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
MIN_DTE = 2                 # avoid same-day gamma roulette
MAX_DTE = 45

# ── Scan limits (UW trial = 30,000 requests/day) ────────────────
MAX_CANDIDATES_PER_SCAN = 25   # tickers we spend chain/candle calls on
FLOW_ALERT_LIMIT        = 200
MIN_TICKER_PREMIUM      = 250_000   # skip tickers below this daily premium

# ── Message composition ─────────────────────────────────────────
# True  = `claude -p` writes the Arabic message (Salem's original design)
# False = deterministic Python formatter (no LLM, zero fabrication risk)
# Either way every number comes from the computed JSON.
USE_CLAUDE_COMPOSER = True
CLAUDE_TIMEOUT_SEC  = 300

# ── State / data files ──────────────────────────────────────────
SHORTLIST_FILE = BASE_DIR / "shortlist.json"
POSITIONS_FILE = BASE_DIR / "positions.json"
STATE_FILE     = BASE_DIR / "state.json"
LOCK_FILE      = BASE_DIR / ".state.lock"
JOURNAL_FILE   = BASE_DIR / "journal.csv"
