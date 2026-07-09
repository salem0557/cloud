"""Central configuration, overridable via environment variables.

The bot is three fully independent, on-demand modules (stocks/options/
crypto — see stocks_module.py/options_module.py/crypto_module.py). Each has
its own filter thresholds and its own watchlist below; nothing here is
shared between modules except general Telegram/session/file settings.
"""
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

# --- Access: the bot is locked to whichever chat ids are already present in
# --- state.approved (see scanner/state.py) at the time this restructure
# --- shipped. There is no /approve command anymore, so no new member can
# --- ever be added by the bot itself -- membership is now a fixed roster,
# --- editable only by hand-editing the state file on disk. ---
ADMIN_CHAT_ID = _int("ADMIN_CHAT_ID", 0)   # your own Telegram chat id (always eligible)

# --- Manual-command sessions: every /stocks or /crypto run is capped at
# --- this long, then auto-stops with a "انتهت الجلسة" notice. /stop
# --- cancels a running session instantly. /options gets its own, longer
# --- cap (see OPTIONS_SESSION_TIMEOUT_SECONDS) since its watchlist is much
# --- bigger and each symbol needs a full options-chain fetch, not just a
# --- quote. ---
SESSION_TIMEOUT_SECONDS = _int("SESSION_TIMEOUT_SECONDS", 300)
OPTIONS_SESSION_TIMEOUT_SECONDS = _int("OPTIONS_SESSION_TIMEOUT_SECONDS", 1200)

# --- Data fetching (stocks + options watchlists; yfinance) ---
DOWNLOAD_THREADS = _int("DOWNLOAD_THREADS", 12)
BATCH_SIZE = _int("BATCH_SIZE", 100)

# --- Liquidity pre-filter (skip dead/penny stocks; stocks module only) ---
MIN_PRICE = _float("MIN_PRICE", 2.0)
MIN_AVG_VOLUME = _int("MIN_AVG_VOLUME", 100_000)   # avg daily volume

# =====================================================================
# 1) STOCKS module -- reversal-up technical scan (Bollinger/RSI/support/
#    falling wedge), 3 of 4 filters required.
# =====================================================================
STOCKS_INTERVAL = os.environ.get("STOCKS_INTERVAL", "1d")
STOCKS_PERIOD = os.environ.get("STOCKS_PERIOD", "6mo")
STOCKS_FILTERS_REQUIRED = _int("STOCKS_FILTERS_REQUIRED", 3)   # out of 4
STOCKS_TOP_N = _int("STOCKS_TOP_N", 5)

STOCKS_BB_PERIOD = _int("STOCKS_BB_PERIOD", 20)
STOCKS_BB_STD = _float("STOCKS_BB_STD", 2.0)
STOCKS_BB_TOLERANCE = _float("STOCKS_BB_TOLERANCE", 0.02)      # within 2% of lower band

STOCKS_RSI_PERIOD = _int("STOCKS_RSI_PERIOD", 14)
STOCKS_RSI_OVERSOLD = _float("STOCKS_RSI_OVERSOLD", 35.0)

STOCKS_SUPPORT_LOOKBACK = _int("STOCKS_SUPPORT_LOOKBACK", 60)   # daily bars (~60 days)
STOCKS_SUPPORT_CLUSTER_TOL = _float("STOCKS_SUPPORT_CLUSTER_TOL", 0.01)
STOCKS_SUPPORT_MIN_TOUCHES = _int("STOCKS_SUPPORT_MIN_TOUCHES", 2)
STOCKS_SUPPORT_MARGIN = _float("STOCKS_SUPPORT_MARGIN", 0.03)   # within 3% of the level
STOCKS_SUPPORT_BREAK_TOL = _float("STOCKS_SUPPORT_BREAK_TOL", 0.005)

STOCKS_WEDGE_LOOKBACK = _int("STOCKS_WEDGE_LOOKBACK", 120)
STOCKS_WEDGE_PIVOT_ORDER = _int("STOCKS_WEDGE_PIVOT_ORDER", 3)
STOCKS_WEDGE_MIN_BARS = _int("STOCKS_WEDGE_MIN_BARS", 20)

# Full US market instead of a curated watchlist: the whole NYSE+Nasdaq+AMEX
# common-share universe (scanner/universe.py, refreshed daily) is scanned,
# narrowed down only by this price band -- so the watchlist itself isn't a
# fixed list here, just the $-range gate applied in stocks_module.
STOCKS_MIN_PRICE = _float("STOCKS_MIN_PRICE", 15.0)
STOCKS_MAX_PRICE = _float("STOCKS_MAX_PRICE", 100.0)

# =====================================================================
# 2) OPTIONS module -- CALL-contract-only scan across a separate, more
#    liquid watchlist. Independent of the stocks module's technical
#    signals: an option can qualify here even if its underlying doesn't
#    match any of the 4 stock filters, and vice versa.
# =====================================================================
OPTIONS_MAX_WEEKS = _int("OPTIONS_MAX_WEEKS", 18)        # nearest expiry .. ~ DTE_MAX
OPTIONS_MAX_EXPIRIES = _int("OPTIONS_MAX_EXPIRIES", 6)    # chain requests per stock
OPTIONS_MIN_ACTIVITY = _int("OPTIONS_MIN_ACTIVITY", 20)   # min OI+volume per contract
OPTIONS_TOP_N = _int("OPTIONS_TOP_N", 5)

OPTIONS_DELTA_MIN = _float("OPTIONS_DELTA_MIN", 0.55)
OPTIONS_DELTA_MAX = _float("OPTIONS_DELTA_MAX", 0.80)
OPTIONS_DTE_MIN = _int("OPTIONS_DTE_MIN", 1)
OPTIONS_DTE_MAX = _int("OPTIONS_DTE_MAX", 120)
OPTIONS_VOLUME_MIN = _int("OPTIONS_VOLUME_MIN", 30)
OPTIONS_OI_MIN = _int("OPTIONS_OI_MIN", 200)
OPTIONS_IV_MAX = _float("OPTIONS_IV_MAX", 0.60)
OPTIONS_SPREAD_MAX = _float("OPTIONS_SPREAD_MAX", 0.10)
# Per-share ask price bound (contract cost = ask * 100), e.g. 0.30$-2.00$
# means a 30$-200$ contract.
OPTIONS_ASK_MIN = _float("OPTIONS_ASK_MIN", 0.30)
OPTIONS_ASK_MAX = _float("OPTIONS_ASK_MAX", 2.00)
# Minimum probability of profit (%) -- a second, independent cut applied
# after PoP is computed, on top of the delta/DTE/liquidity/IV/spread/ask
# filters above.
OPTIONS_MIN_POP = _float("OPTIONS_MIN_POP", 50.0)

# A much broader liquid-options watchlist (300-500 names) -- still a
# separate list from STOCKS_WATCHLIST on purpose, since "actively traded
# options" and "matches a reversal-up technical setup" are unrelated
# properties. Bigger than the stocks module's implicit price band, so a
# single /options run takes noticeably longer (see
# OPTIONS_SESSION_TIMEOUT_SECONDS below) -- edit freely, but every name
# added trades directly against how long a full scan takes.
_OPTIONS_CORE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "NFLX", "AVGO",
    "CRM", "ORCL", "ADBE", "INTC", "QCOM", "MU", "PYPL", "SHOP", "UBER", "PLTR",
    "SNOW", "NET", "CRWD", "ZS", "DDOG", "MDB", "PANW", "NOW", "TEAM", "ABNB",
    "DASH", "COIN", "RBLX", "SOFI", "RIVN", "LCID", "F", "GM", "BA", "DIS",
    "NKE", "SBUX", "MCD", "WMT", "TGT", "HD", "LOW", "XOM", "CVX", "JPM",
    "BAC", "WFC", "GS", "MS", "C", "V", "MA", "PINS", "SNAP", "TWLO",
    "ROKU", "DKNG", "MSTR", "RIOT", "MARA", "GME", "AMC", "BABA", "JD", "PDD",
    "NIO", "XPEV", "LI", "TSM", "ASML", "MRNA", "PFE", "JNJ", "UNH", "CVS",
    "LLY", "KO", "PEP", "COST", "T", "VZ", "CMCSA", "PARA", "WBD", "CAT",
    "DE", "BKNG", "ISRG", "REGN", "VRTX", "GILD", "APP", "ARM", "SMCI",
]
# Large/mid-cap names across every sector (same base list the stocks module
# used before it switched to a full-market scan) -- virtually all of these
# have liquid, actively-traded options.
_OPTIONS_LARGE_MID_CAP = [
    "GOOG", "PEP", "LIN", "CSCO", "TMUS", "TXN", "AMGN", "HON", "AMAT", "ISRG",
    "ADP", "MDLZ", "LRCX", "ADI", "KLAC", "SNPS", "CDNS", "MELI", "MAR", "ORLY",
    "CTAS", "CSX", "WDAY", "FTNT", "PCAR", "ROP", "NXPI", "CHTR", "MRVL", "AEP",
    "PAYX", "ROST", "ODFL", "EXC", "TTD", "FAST", "EA", "CPRT", "DXCM", "BKR",
    "VRSK", "KHC", "XEL", "ANSS", "ON", "TEAM", "FANG", "WBD", "BIIB", "EBAY",
    "TTWO", "LULU", "MCHP", "ENPH", "GLOB", "JPM", "BAC", "WFC", "SCHW", "BLK",
    "AXP", "SPGI", "ICE", "CME", "MMC", "AON", "PGR", "TRV", "ALL", "MET",
    "PRU", "AFL", "COF", "USB", "PNC", "TFC", "BK", "STT", "DFS", "SYF",
    "FI", "GPN", "ABT", "TMO", "DHR", "BMY", "ABBV", "CI", "HUM", "ELV",
    "SYK", "BSX", "MDT", "ZTS", "HCA", "BDX", "IQV", "WAT", "RMD", "EW",
    "COP", "EOG", "SLB", "PSX", "MPC", "OXY", "WMB", "KMI", "HES", "DVN",
    "HAL", "LMT", "RTX", "GE", "UNP", "UPS", "NOC", "GD", "EMR", "ETN",
    "ITW", "PH", "NSC", "FDX", "WM", "RSG", "CMI", "DOV", "IR", "TT",
    "JCI", "CMG", "TJX", "YUM", "DG", "DLTR", "PG", "PM", "MO", "CL",
    "EL", "KMB", "GIS", "HSY", "STZ", "CLX", "K", "SYY", "ADM", "NEE",
    "DUK", "SO", "D", "SRE", "PEG", "ED", "EIX", "WEC", "ES", "PPL",
    "IBM", "ACN", "HPQ", "HPE", "DELL", "WDC", "STX", "DAL", "UAL", "LUV",
    "RCL", "CCL", "NCLH", "HLT", "NUE", "FCX", "APD", "ECL", "NEM", "DD",
    "DOW", "PPG", "VMC", "MLM", "LYB", "AMT", "PLD", "CCI", "EQIX", "PSA",
    "O", "SPG", "WELL", "DLR", "AVB", "EQR", "SNOW", "COIN", "BRK-B",
]
# Popular but somewhat smaller-cap names with real, liquid options volume
# (airlines/casinos/energy/biotech/miners/EV) -- kept to well-established,
# still-listed tickers rather than speculative micro-caps.
_OPTIONS_EXTRA = [
    "AAL", "ALK", "JBLU", "SAVE", "WYNN", "MGM", "LVS", "CZR", "PENN", "FUBO",
    "GPRO", "PLUG", "FCEL", "BE", "CHPT", "BLNK", "QS", "SPCE", "TLRY", "CGC",
    "ACB", "ET", "EPD", "MPLX", "OKE", "TRGP", "AR", "RRC", "SWN", "CTRA",
    "EQT", "OVV", "MRO", "APA", "PXD", "CLR", "CNX", "SU", "CNQ", "NOV",
    "RIG", "NE", "AFRM", "UPST", "BILL", "GDDY", "HIMS", "OSCR", "CLOV", "NVAX",
    "BNTX", "SRPT", "BMRN", "ALNY", "CRSP", "EXAS", "VEEV", "TDOC", "PODD", "TNDM",
    "INSP", "SWAV", "NVCR", "XRAY", "MASI", "ZBH", "STE", "CRL", "LH", "THC",
    "UHS", "CNC", "MOH", "X", "AA", "CLF", "MT", "STLD", "RS", "CMC",
    "ATI", "CENX", "ALB", "SQM", "LAC", "MP", "UUUU", "CCJ", "DNN", "UEC",
    "VALE", "RIO", "BHP", "GOLD", "NEM", "AEM", "KGC", "AU", "HL", "CDE",
    "PAAS", "FSM", "EXK", "MUX", "SIRI", "TWLO", "ETSY", "W", "CHWY", "CVNA",
    "CARG", "VRM", "OPEN", "COMP", "Z", "ZG", "RDFN", "EXPI", "DASH", "SQ",
]
OPTIONS_WATCHLIST = sorted(set(_OPTIONS_CORE + _OPTIONS_LARGE_MID_CAP + _OPTIONS_EXTRA))

# =====================================================================
# 3) CRYPTO module -- top ~60-by-market-cap coins via Binance public data
#    (ccxt, no API keys), 4h candles, 2 of 3 filters required
#    (bollinger/rsi/support -- no wedge pattern for crypto).
# =====================================================================
CRYPTO_TIMEFRAME = os.environ.get("CRYPTO_TIMEFRAME", "4h")
CRYPTO_CANDLE_LIMIT = _int("CRYPTO_CANDLE_LIMIT", 300)   # 4h bars fetched per symbol
CRYPTO_FILTERS_REQUIRED = _int("CRYPTO_FILTERS_REQUIRED", 2)   # out of 3
CRYPTO_TOP_N = _int("CRYPTO_TOP_N", 5)

CRYPTO_BB_PERIOD = _int("CRYPTO_BB_PERIOD", 20)
CRYPTO_BB_STD = _float("CRYPTO_BB_STD", 2.0)
CRYPTO_BB_TOLERANCE = _float("CRYPTO_BB_TOLERANCE", 0.02)      # within 2% of lower band

CRYPTO_RSI_PERIOD = _int("CRYPTO_RSI_PERIOD", 14)
CRYPTO_RSI_OVERSOLD = _float("CRYPTO_RSI_OVERSOLD", 35.0)

# 30 days of 4h candles = 180 bars
CRYPTO_SUPPORT_LOOKBACK = _int("CRYPTO_SUPPORT_LOOKBACK", 180)
CRYPTO_SUPPORT_CLUSTER_TOL = _float("CRYPTO_SUPPORT_CLUSTER_TOL", 0.01)
CRYPTO_SUPPORT_MIN_TOUCHES = _int("CRYPTO_SUPPORT_MIN_TOUCHES", 2)
CRYPTO_SUPPORT_MARGIN = _float("CRYPTO_SUPPORT_MARGIN", 0.03)   # within 3% of the level
CRYPTO_SUPPORT_BREAK_TOL = _float("CRYPTO_SUPPORT_BREAK_TOL", 0.005)

# Top ~60 coins by market cap with a Binance USDT spot pair. Binance
# relisted its old MATIC pair as POL in 2024; edit freely as rankings shift.
CRYPTO_WATCHLIST = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT",
    "DOGE/USDT", "ADA/USDT", "TRX/USDT", "AVAX/USDT", "LINK/USDT",
    "DOT/USDT", "TON/USDT", "POL/USDT", "SHIB/USDT", "LTC/USDT",
    "BCH/USDT", "NEAR/USDT", "UNI/USDT", "ICP/USDT", "ETC/USDT",
    "XLM/USDT", "ATOM/USDT", "FIL/USDT", "APT/USDT", "ARB/USDT",
    "OP/USDT", "INJ/USDT", "SUI/USDT", "TIA/USDT", "HBAR/USDT",
    "AAVE/USDT", "MKR/USDT", "SNX/USDT", "CRV/USDT", "LDO/USDT",
    "RUNE/USDT", "KAVA/USDT", "MINA/USDT", "FLOW/USDT", "XTZ/USDT",
    "EOS/USDT", "THETA/USDT", "AXS/USDT", "SAND/USDT", "MANA/USDT",
    "GALA/USDT", "CHZ/USDT", "APE/USDT", "GRT/USDT", "IMX/USDT",
    "STX/USDT", "KAS/USDT", "PEPE/USDT", "WIF/USDT", "BONK/USDT",
    "FET/USDT", "RENDER/USDT", "JUP/USDT", "PYTH/USDT", "ENA/USDT",
]

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
