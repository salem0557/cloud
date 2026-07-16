"""Central configuration, overridable via environment variables.

The bot is three fully independent, on-demand modules (stocks/options/
crypto — see stocks_module.py/options_module.py/crypto_module.py). Each has
its own filter thresholds, scoring weights, and watchlist below; nothing
here is shared between modules except general Telegram/session/file
settings.
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

# --- Update delivery: webhook vs long polling. "auto" (default) tries a
# --- webhook server when a public URL can be determined, and transparently
# --- falls back to polling either when no URL is configured or when
# --- starting the webhook server itself fails at startup (see bot.py's
# --- _try_run_webhook) -- so an unconfigured deployment just keeps working
# --- via polling instead of refusing to start. Force one explicitly with
# --- BOT_MODE=webhook (still falls back on failure) or BOT_MODE=polling
# --- (skips webhook entirely, e.g. to stay on a Railway "worker" process
# --- with no public port).
BOT_MODE = os.environ.get("BOT_MODE", "auto").lower()   # auto | webhook | polling
# On Railway, a service with a public domain generated sets
# RAILWAY_PUBLIC_DOMAIN automatically (bare host, no scheme) -- WEBHOOK_URL
# overrides that explicitly for any other host/platform.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") or (
    f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN']}"
    if os.environ.get("RAILWAY_PUBLIC_DOMAIN") else None)
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/telegram-webhook")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET") or None   # optional Telegram secret_token
WEBHOOK_LISTEN = os.environ.get("WEBHOOK_LISTEN", "0.0.0.0")
WEBHOOK_PORT = _int("PORT", 8080)   # Railway injects PORT for web-type services

# --- Web dashboard: a read-only browser view of the bot's own scan history
# --- (catalog/market-status/news/analyst pages -- see scanner/webapp.py).
# --- Runs as a background task inside the SAME process regardless of
# --- BOT_MODE, on its OWN port -- deliberately different from WEBHOOK_PORT
# --- so it never collides with the Telegram webhook listener even if both
# --- happen to be enabled at once (Railway can map a second public domain
# --- to this port independently; see README). A bind failure here only
# --- logs a warning -- it must never take down the Telegram bot itself.
DASHBOARD_ENABLED = os.environ.get("DASHBOARD_ENABLED", "true").lower() != "false"
DASHBOARD_LISTEN = os.environ.get("DASHBOARD_LISTEN", "0.0.0.0")
DASHBOARD_PORT = _int("DASHBOARD_PORT", 8090)
# Extra tickers always included in /api/news on top of whatever symbols are
# currently in the catalog, so the news page isn't empty right after a
# fresh deploy before anyone has run /options yet.
DASHBOARD_NEWS_SYMBOLS = [s.strip().upper() for s in
                          os.environ.get("DASHBOARD_NEWS_SYMBOLS", "SPY,QQQ").split(",") if s.strip()]
# How long a catalog/market-status/news response is cached in memory before
# the dashboard re-fetches from yfinance/signals.db -- keeps repeated page
# loads/refreshes from hammering Yahoo Finance.
DASHBOARD_CACHE_SECONDS = _int("DASHBOARD_CACHE_SECONDS", 600)
# Catalog freshness window -- only signals logged within this many days are
# shown (older, colder scan results aren't a useful "current opportunities"
# read even if the contract hasn't technically expired yet).
DASHBOARD_CATALOG_MAX_AGE_DAYS = _int("DASHBOARD_CATALOG_MAX_AGE_DAYS", 14)

# --- Access: the bot is locked to whichever chat ids are already present in
# --- state.approved (see scanner/state.py). There is no /approve command,
# --- so no new member can ever be added by the bot itself -- membership is
# --- a fixed roster, editable only by hand-editing the state file on disk.
ADMIN_CHAT_ID = _int("ADMIN_CHAT_ID", 0)   # your own Telegram chat id (always eligible)
SUBSCRIBE_CONTACT = os.environ.get("SUBSCRIBE_CONTACT", "مشغّل البوت")

# --- Manual-command sessions: every /stocks, /options*, or /crypto run is
# --- capped at this long, then auto-stops with a "انتهت الجلسة" notice.
# --- /stop cancels a running session instantly. One shared cap for all
# --- three modules (options' much bigger watchlist just means it's more
# --- likely to hit the cap before finishing a full pass). ---
SESSION_TIMEOUT_SECONDS = _int("SESSION_TIMEOUT_SECONDS", 900)   # 15 minutes

# --- Data fetching (stocks + options watchlists; yfinance) ---
DOWNLOAD_THREADS = _int("DOWNLOAD_THREADS", 12)
BATCH_SIZE = _int("BATCH_SIZE", 100)
MIN_AVG_VOLUME = _int("MIN_AVG_VOLUME", 100_000)   # avg daily volume, stocks liquidity floor

# =====================================================================
# 1) STOCKS module -- reversal-up technical scan (Bollinger/RSI/support/
#    falling wedge), 3 of 4 filters required, ranked by a 0-85 point score
#    (not a statistical probability -- a weighted heuristic score, see
#    stocks_module.py for the exact formula).
# =====================================================================
STOCKS_INTERVAL = os.environ.get("STOCKS_INTERVAL", "1d")
STOCKS_PERIOD = os.environ.get("STOCKS_PERIOD", "6mo")
STOCKS_FILTERS_REQUIRED = _int("STOCKS_FILTERS_REQUIRED", 2)   # out of 4
STOCKS_MIN_POP = _float("STOCKS_MIN_POP", 35.0)   # minimum score (%) to display
# No STOCKS_TOP_N / early-exit cap -- scan() walks the ENTIRE watchlist and
# yields every qualifying stock, not just the first N found (unlike
# options/leaps/heavy/crypto, which still stop early -- an explicit,
# stocks-only choice).
STOCKS_MAX_PRICE = _float("STOCKS_MAX_PRICE", 100.0)   # skip anything at/above this price

STOCKS_BB_PERIOD = _int("STOCKS_BB_PERIOD", 20)
STOCKS_BB_STD = _float("STOCKS_BB_STD", 2.0)
STOCKS_BB_TOLERANCE = _float("STOCKS_BB_TOLERANCE", 0.02)      # filter: within 2% of lower band

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

# --- Scoring weights (points) -- see stocks_module._score() ---
STOCKS_SCORE_RSI_STRONG = _int("STOCKS_SCORE_RSI_STRONG", 25)    # RSI < 30
STOCKS_SCORE_RSI_WEAK = _int("STOCKS_SCORE_RSI_WEAK", 15)        # 30 <= RSI < 35
STOCKS_SCORE_BB_STRONG = _int("STOCKS_SCORE_BB_STRONG", 25)      # within 1% of lower band
STOCKS_SCORE_BB_WEAK = _int("STOCKS_SCORE_BB_WEAK", 15)          # within 2%
STOCKS_SCORE_SUPPORT_STRONG = _int("STOCKS_SCORE_SUPPORT_STRONG", 25)  # tested 3+ times
STOCKS_SCORE_SUPPORT_WEAK = _int("STOCKS_SCORE_SUPPORT_WEAK", 15)      # tested 2 times
STOCKS_SCORE_WEDGE_COMPLETE = _int("STOCKS_SCORE_WEDGE_COMPLETE", 25)
STOCKS_SCORE_WEDGE_SEMI = _int("STOCKS_SCORE_WEDGE_SEMI", 10)
STOCKS_TREND_UP_MULT = _float("STOCKS_TREND_UP_MULT", 1.1)    # SPY above its 50-day SMA
STOCKS_TREND_DOWN_MULT = _float("STOCKS_TREND_DOWN_MULT", 0.90)  # SPY below it
STOCKS_SCORE_CAP = _float("STOCKS_SCORE_CAP", 85.0)
STOCKS_TREND_SMA_PERIOD = _int("STOCKS_TREND_SMA_PERIOD", 50)

# S&P 500 + Nasdaq-100 -ish watchlist -- NOT a literally exhaustive,
# auto-synced index roster (index constituents change over time and
# hardcoding avoids a fragile scrape dependency). Edit freely; review every
# few months for reconstitution changes (additions/removals/delistings).
_NASDAQ_100 = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "GOOG", "META", "TSLA", "AVGO", "COST",
    "NFLX", "AMD", "PEP", "ADBE", "LIN", "CSCO", "TMUS", "QCOM", "INTU", "TXN",
    "AMGN", "CMCSA", "HON", "AMAT", "BKNG", "ISRG", "VRTX", "PANW", "ADP", "SBUX",
    "GILD", "MDLZ", "LRCX", "REGN", "ADI", "PYPL", "MU", "KLAC", "SNPS", "CDNS",
    "MELI", "CRWD", "PDD", "MAR", "CTAS", "ORLY", "ASML", "ABNB", "CSX", "WDAY",
    "FTNT", "MNST", "PCAR", "ROP", "NXPI", "CHTR", "MRVL", "DASH", "AEP", "PAYX",
    "ROST", "ODFL", "KDP", "EXC", "TTD", "IDXX", "FAST", "EA", "CPRT", "DXCM",
    "BKR", "VRSK", "CTSH", "KHC", "XEL", "CCEP", "GEHC", "ANSS", "ON", "DDOG",
    "ZS", "TEAM", "MDB", "FANG", "GFS", "WBD", "BIIB", "CDW", "EBAY", "TTWO",
    "ARM", "APP", "LULU", "MCHP", "ILMN", "SIRI", "ENPH", "JD", "GLOB", "PLTR",
]
_SP500_EXTRA = [
    # Financials
    "JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW", "BLK", "AXP", "SPGI",
    "ICE", "CME", "MMC", "AON", "PGR", "TRV", "ALL", "MET", "PRU", "AFL",
    "COF", "USB", "PNC", "TFC", "BK", "STT", "DFS", "SYF", "V", "MA", "FI", "GPN",
    # Healthcare
    "UNH", "JNJ", "LLY", "PFE", "MRK", "ABT", "TMO", "DHR", "BMY", "ABBV",
    "CVS", "CI", "HUM", "ELV", "SYK", "BSX", "MDT", "ZTS", "HCA", "BDX",
    "A", "IQV", "MTD", "WAT", "RMD", "EW",
    # Energy
    "XOM", "CVX", "COP", "EOG", "SLB", "PSX", "MPC", "OXY", "WMB", "KMI", "HES", "DVN", "HAL",
    # Industrials
    "BA", "CAT", "DE", "LMT", "RTX", "GE", "UNP", "UPS", "NOC", "GD",
    "EMR", "ETN", "ITW", "PH", "CSX", "NSC", "FDX", "WM", "RSG", "CMI",
    "DOV", "XYL", "IR", "TT", "JCI",
    # Consumer discretionary / staples
    "WMT", "HD", "LOW", "TGT", "MCD", "NKE", "DIS", "CMG", "TJX", "YUM",
    "DG", "DLTR", "PG", "KO", "PM", "MO", "CL", "EL", "KMB", "GIS",
    "HSY", "STZ", "CLX", "K", "SYY", "ADM",
    # Utilities
    "NEE", "DUK", "SO", "D", "SRE", "PEG", "ED", "EIX", "WEC", "ES", "PPL", "FE", "AEE",
    # Communication / other tech
    "T", "VZ", "CRM", "ORCL", "IBM", "ACN", "NOW", "INTC", "ANET", "HPQ", "HPE", "DELL", "WDC", "STX",
    # Autos / travel
    "F", "GM", "DAL", "UAL", "LUV", "RCL", "CCL", "NCLH", "HLT",
    # Materials
    "NUE", "FCX", "APD", "ECL", "NEM", "DD", "DOW", "PPG", "VMC", "MLM", "LYB",
    # Real estate
    "AMT", "PLD", "CCI", "EQIX", "PSA", "O", "SPG", "WELL", "DLR", "AVB", "EQR",
    # Newer large caps
    "SHOP", "UBER", "LYFT", "SNOW", "NET", "DKNG", "COIN", "RBLX", "SOFI", "RIVN", "LCID",
    "BRK-B",
]
# Additional S&P 500 constituents to broaden coverage closer to the full
# index (~500 names) -- same "curated, not auto-synced" caveat as above.
_SP500_MORE = [
    # Tech / software / semis
    "ADSK", "AKAM", "EPAM", "FFIV", "FICO", "FTV", "GDDY", "GEN",
    "JNPR", "KEYS", "MPWR", "MSI", "NTAP", "PAYC", "PTC", "QRVO", "SWKS", "TDY", "TER",
    "TRMB", "TYL", "VRSN", "WU", "ZBRA",
    # Financials
    "AIG", "AJG", "AMP", "BEN", "BRO", "CBOE", "CFG", "CMA", "FDS", "FITB", "GL",
    "HBAN", "IVZ", "KEY", "L", "MCO", "MKTX", "MSCI", "MTB", "NDAQ", "NTRS", "PFG",
    "RF", "RJF", "TROW", "WRB", "WTW", "ZION",
    # Healthcare
    "ALGN", "BAX", "CAH", "CNC", "COR", "CRL", "HOLX", "INCY", "MCK", "MOH", "MRNA",
    "PODD", "RVTY", "TECH", "VTRS", "XRAY", "ZBH",
    # Consumer staples / discretionary
    "APTV", "AZO", "BBY", "BF-B", "BWA", "CAG", "CHD", "CHRW", "CPB", "DPZ", "ETSY",
    "EXPD", "EXPE", "GRMN", "HAS", "HRL", "KMX", "LEN", "LKQ", "LW", "MGM", "MHK",
    "MKC", "NVR", "PHM", "POOL", "RL", "SJM", "TAP", "TPR", "TSCO", "TSN", "ULTA", "WYNN",
    # Industrials
    "ALLE", "AME", "AOS", "BALL", "CARR", "EFX", "GNRC", "GWW", "HII", "HWM", "IEX",
    "J", "LDOS", "LHX", "MAS", "MMM", "NDSN", "OTIS", "PWR", "ROK", "SNA", "SWK",
    "TDG", "TXT", "URI", "VLTO", "WAB",
    # Energy / materials
    "ALB", "AVY", "CE", "CF", "EMN", "FMC", "IFF", "IP", "MOS", "OKE", "PKG", "SHW",
    "SW", "TRGP", "VLO",
    # Real estate
    "ARE", "BXP", "CPT", "CSGP", "DOC", "EXR", "FRT", "HST", "INVH", "IRM", "KIM",
    "MAA", "REG", "SBAC", "UDR", "VICI", "VTR", "WY",
    # Utilities
    "AES", "ATO", "CMS", "CNP", "DTE", "ETR", "EVRG", "LNT", "NI", "NRG", "PCG", "PNW",
    # Communication / media
    "FOX", "FOXA", "IPG", "LYV", "MTCH", "NWS", "NWSA", "OMC", "PARA",
]
# Round 3: smaller/mid-cap S&P 500 names skipped by the earlier passes above
# (which leaned toward well-known large caps) -- exactly the kind of stock
# STOCKS_MAX_PRICE (<$100) is meant to surface, so worth covering properly.
_SP500_ROUND3 = [
    "ACGL", "AIZ", "AMCR", "BR", "CINF", "CTLT", "CZR", "DGX", "DVA", "GPC",
    "HIG", "HRB", "JBHT", "KVUE", "LH", "LII", "LNC", "LSTR", "NWL", "PNR",
    "RHI", "ROL", "SAIA", "THC", "UHS", "UNM", "WBA", "WHR",
]
STOCKS_WATCHLIST = sorted(set(_NASDAQ_100 + _SP500_EXTRA + _SP500_MORE + _SP500_ROUND3))

# =====================================================================
# 2) OPTIONS module -- CALL + PUT contract scan across a separate, more
#    liquid watchlist. Independent of the stocks module's technical
#    signals: an option can qualify here even if its underlying doesn't
#    match any of the 4 stock filters, and vice versa.
# =====================================================================
OPTIONS_MAX_WEEKS = _int("OPTIONS_MAX_WEEKS", 53)        # nearest expiry .. ~ DTE_MAX (360d)
OPTIONS_MAX_EXPIRIES = _int("OPTIONS_MAX_EXPIRIES", 10)   # chain requests per stock
OPTIONS_MIN_ACTIVITY = _int("OPTIONS_MIN_ACTIVITY", 20)   # min OI+volume per contract
# No longer caps options_module.scan() or scan_leaps() (both the general
# /options watchlist scan and /leaps have no early-exit cap at all -- see
# options_module.py); still used by scan_symbol's per-ticker contract
# display cap (/options TICKER).
OPTIONS_TOP_N = _int("OPTIONS_TOP_N", 5)

# OPTIONS_DELTA_MIN/OPTIONS_IV_MAX are the single, unified delta/IV
# thresholds shared by /options, /leaps, and /heavy (merged into one
# /options command -- see options_module.scan_all). Was 0.40/0.80
# (general-only) before the merge, then loosened to a shared 0.35/0.50,
# then tightened again to these stricter shared values -- all three types
# apply exactly these two bounds.
OPTIONS_DELTA_MIN = _float("OPTIONS_DELTA_MIN", 0.50)     # applied to abs(delta)
OPTIONS_DELTA_MAX = _float("OPTIONS_DELTA_MAX", 1.0)       # no real upper bound (1.0 = max possible)
OPTIONS_DTE_MIN = _int("OPTIONS_DTE_MIN", 14)
OPTIONS_DTE_MAX = _int("OPTIONS_DTE_MAX", 360)
OPTIONS_VOLUME_MIN = _int("OPTIONS_VOLUME_MIN", 15)   # loosened from 30 -- widen the search
OPTIONS_OI_MIN = _int("OPTIONS_OI_MIN", 100)          # loosened from 200 -- widen the search
OPTIONS_IV_MAX = _float("OPTIONS_IV_MAX", 0.30)
OPTIONS_SPREAD_MAX = _float("OPTIONS_SPREAD_MAX", 0.15)   # loosened from 0.10
# Per-share ask price bound (contract cost = ask * 100), e.g. 0.05$-2.00$
# means a 5$-200$ contract.
OPTIONS_ASK_MIN = _float("OPTIONS_ASK_MIN", 0.05)
OPTIONS_ASK_MAX = _float("OPTIONS_ASK_MAX", 2.00)
# Minimum probability of profit (%) -- a second, independent cut applied
# after POP is computed, on top of the delta/DTE/liquidity/IV/spread/ask
# filters above. Also the "bronze" tier floor (see OPTIONS_TIER_*). Raised
# from 30 to 45 on request -- since this is also OPTIONS_TIER_BRONZE, the
# GOLD/SILVER bands below were shifted up by the same +15 points so a
# 3-tier spread survives (otherwise every passing result would always
# read as gold, since the old 40%/35% bands sat below the new floor).
OPTIONS_MIN_POP = _float("OPTIONS_MIN_POP", 45.0)

# Result tier badges by POP (%): 🥇 gold >= GOLD, 🥈 silver >= SILVER,
# 🥉 bronze >= BRONZE (== OPTIONS_MIN_POP, the display floor).
OPTIONS_TIER_GOLD = _float("OPTIONS_TIER_GOLD", 65.0)
OPTIONS_TIER_SILVER = _float("OPTIONS_TIER_SILVER", 55.0)
OPTIONS_TIER_BRONZE = OPTIONS_MIN_POP

# Duration tags by DTE: short/medium/long(LEAPS).
OPTIONS_DURATION_SHORT_MAX = _int("OPTIONS_DURATION_SHORT_MAX", 45)
OPTIONS_DURATION_MEDIUM_MAX = _int("OPTIONS_DURATION_MEDIUM_MAX", 120)

# --- /leaps: the long-dated deep/near-ITM tag within the merged /options
# --- command -- shares OPTIONS_DELTA_MIN/OPTIONS_IV_MAX above with the
# --- general and /heavy tags now; only its DTE floor, stock price range,
# --- and total contract cost cap below are still its own. Ranked locally
# --- by lowest IV (cheapest time value relative to the stock's own
# --- volatility), not POP. ---
LEAPS_DTE_MIN = _int("LEAPS_DTE_MIN", 365)
LEAPS_MIN_PRICE = _float("LEAPS_MIN_PRICE", 8.0)
LEAPS_MAX_PRICE = _float("LEAPS_MAX_PRICE", 100.0)
LEAPS_MAX_COST = _float("LEAPS_MAX_COST", 170.0)   # total contract cost = premium * 100
# Expiry lookup window: needs to reach well past 365 days, unlike the
# general options module's OPTIONS_MAX_WEEKS (~1 year). Widened from 104
# (~2 years) to capture longer-dated LEAPS chains that were previously
# out of reach.
LEAPS_MAX_WEEKS = _int("LEAPS_MAX_WEEKS", 156)   # ~3 years

# ~500 of the most liquid, most actively-optioned US stocks -- a separate
# list from STOCKS_WATCHLIST on purpose, since "actively traded options"
# and "matches a reversal-up technical setup" are unrelated properties.
# Bigger than the stocks watchlist, so a single /options run is more likely
# to need the full SESSION_TIMEOUT_SECONDS -- edit freely, but every name
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
# Large/mid-cap names across every sector -- virtually all of these have
# liquid, actively-traded options.
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
# A further batch to push the watchlist toward ~500 names: regional banks,
# more REITs, more consumer/industrial names, insurers, more small/mid-cap
# biotech and tech -- same "well-established, still-listed" bar as above.
_OPTIONS_EXTRA2 = [
    "RF", "KEY", "CFG", "HBAN", "FITB", "MTB", "ZION", "CMA", "WAL", "PNFP",
    "SIVB", "FRC", "PACW", "ALLY", "OMF", "NAVI", "SLM", "COF", "DFS", "SYF",
    "EQH", "VOYA", "LNC", "UNM", "GL", "AIZ", "RE", "RGA", "WRB", "CINF",
    "HIG", "CB", "AIG", "MMC", "BRO", "AJG", "WTW", "ERIE", "KMPR", "SIGI",
    "IRM", "REG", "FRT", "KIM", "MAC", "SLG", "BXP", "HST", "VTR", "PEAK",
    "ARE", "EXR", "CUBE", "LSI", "UDR", "MAA", "ESS", "CPT", "AIV", "ELS",
    "SUI", "INVH", "AMH", "RHP", "PK", "APLE", "SHO", "DRH", "XHR", "RLJ",
    "CHH", "H", "WH", "VAC", "TNL", "MTN", "SIX", "SEAS", "FUN", "PLAY",
    "BYD", "GLPI", "VICI", "IRTC", "PEN", "ICUI", "NUVA", "ATRC", "AXNX", "SILK",
    "GKOS", "TFX", "STAA", "OMCL", "NEOG", "CTLT", "AVTR", "BIO", "PKI", "WAT",
    "MKC", "CAG", "CPB", "SJM", "HRL", "TSN", "TAP", "SAM", "BF-B", "MNST",
    "KDP", "COTY", "CHD", "CLX", "EPC", "NWL", "SPB", "HELE", "ELF", "IPAR",
    "BURL", "ROST", "GPS", "ANF", "AEO", "URBN", "GES", "CRI", "PVH", "RL",
    "KSS", "M", "JWN", "DDS", "BBWI", "FIVE", "OLLI", "BIG", "DLTR", "COST",
    "CASY", "MUSA", "PLAY", "CAKE", "DIN", "DENN", "WING", "SHAK", "PZZA", "DPZ",
    "YUMC", "QSR", "JACK", "BLMN", "TXRH", "EAT", "CBRL", "BJRI", "RUTH", "LOCO",
    "TDG", "HEI", "TXT", "HWM", "CW", "AXON", "LHX", "LDOS", "SAIC", "BAH",
    "KBR", "J", "FLR", "ACM", "EME", "MAS", "AOS", "LII", "CARR", "OTIS",
    "PWR", "MYRG", "ROAD", "GVA", "NVR", "PHM", "DHI", "LEN", "KBH", "TOL",
    "MTH", "TMHC", "MHO", "GRBK", "CCS", "BZH", "LGIH", "IBP", "TPH", "WLK",
]
# STOCKS_WATCHLIST unioned in on request to widen the search significantly
# -- names liquid enough to qualify for the stocks scanner are generally
# liquid enough to have a real options market too (not guaranteed for
# every single one, but any that aren't simply fail the liquidity filters
# below like any other candidate -- no harm done, just extra scan time).
OPTIONS_WATCHLIST = sorted(set(
    _OPTIONS_CORE + _OPTIONS_LARGE_MID_CAP + _OPTIONS_EXTRA + _OPTIONS_EXTRA2
    + STOCKS_WATCHLIST))

# --- /heavy: the mega/large-cap/ETF tag within the merged /options command
# --- -- a small, curated ticker list (HEAVY_TICKERS) instead of the
# --- broader OPTIONS_WATCHLIST, with its own DTE floor, tight ±10% strike
# --- band, premium cap, and liquidity floors; shares OPTIONS_DELTA_MIN/
# --- OPTIONS_IV_MAX with the general and /leaps tags now too. Ranked
# --- locally by liquidity (volume + open interest), not POP. ---
HEAVY_DTE_MIN = _int("HEAVY_DTE_MIN", 45)             # no upper cap -- every expiry in the chain
HEAVY_STRIKE_PCT = _float("HEAVY_STRIKE_PCT", 0.10)   # strike within ±10% of spot, no widening
HEAVY_PREMIUM_MAX = _float("HEAVY_PREMIUM_MAX", 3.00)  # ask <= 3.00$/share (300$/contract)
HEAVY_VOLUME_MIN = _int("HEAVY_VOLUME_MIN", 25)   # loosened from 50 -- widen the search
HEAVY_OI_MIN = _int("HEAVY_OI_MIN", 150)          # loosened from 300 -- widen the search
HEAVY_SPREAD_MAX = _float("HEAVY_SPREAD_MAX", 0.10)
# Cap on Yahoo per-expiry requests IF the CBOE single-request provider
# (tried first for this module -- see heavy_module.py) fails; generous
# since HEAVY_DTE_MIN already drops near-term expiries before they count
# against it.
HEAVY_MAX_EXPIRIES = _int("HEAVY_MAX_EXPIRIES", 60)

_HEAVY_MEGA = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AVGO"]
_HEAVY_LARGE = [
    "BRK-B", "JPM", "V", "MA", "UNH", "XOM", "CVX", "WMT", "PG", "JNJ", "HD", "KO",
    "PEP", "MRK", "ABBV", "ORCL", "CRM", "AMD", "NFLX", "DIS", "CSCO", "INTC", "QCOM",
    "IBM", "MCD", "NKE", "T", "VZ", "CMCSA", "PFE", "F", "GM", "BA",
]
# Round 2: widening the deliberately-small HEAVY_TICKERS curated list on
# request, still mega/large-cap and broad-ETF only (not the general
# OPTIONS_WATCHLIST's ~600-name breadth) -- more sectors, more names per
# sector, so /options's HEAVY tag has a bigger pool to clear the raised
# OPTIONS_MIN_POP (45%) from.
_HEAVY_LARGE2 = [
    "ADBE", "TXN", "COST", "ABT", "TMO", "LIN", "NEE", "LOW", "SBUX", "MDT",
    "BMY", "GS", "MS", "BLK", "SCHW", "AXP", "C", "WFC", "USB", "PM", "MO",
    "CAT", "DE", "HON", "RTX", "LMT", "GE", "UPS", "ADP", "INTU", "NOW",
    "AMAT", "MU", "LRCX", "PANW", "UBER", "ABNB", "PYPL", "SQ", "COIN",
    "PLTR", "SOFI", "RIVN", "SHOP", "BABA",
]
_HEAVY_ETF = [
    "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "XLK", "XLF", "XLE", "SMH", "GLD", "SLV",
    "XLV", "XLY", "XLI", "XLP", "XLU", "ARKK", "EEM", "TLT",
]
HEAVY_TICKERS = _HEAVY_MEGA + _HEAVY_LARGE + _HEAVY_LARGE2 + _HEAVY_ETF
HEAVY_TAG = {**{s: "mega" for s in _HEAVY_MEGA},
            **{s: "large" for s in _HEAVY_LARGE + _HEAVY_LARGE2},
            **{s: "etf" for s in _HEAVY_ETF}}

# =====================================================================
# 3) CRYPTO module -- top ~100-by-market-cap coins via Binance public data
#    (ccxt, no API keys), 4h candles, 2 of 3 filters required
#    (bollinger/rsi/support -- no wedge pattern for crypto), ranked by a
#    0-75 point score (see crypto_module.py for the exact formula).
# =====================================================================
CRYPTO_TIMEFRAME = os.environ.get("CRYPTO_TIMEFRAME", "4h")
CRYPTO_CANDLE_LIMIT = _int("CRYPTO_CANDLE_LIMIT", 300)   # 4h bars fetched per symbol
CRYPTO_FILTERS_REQUIRED = _int("CRYPTO_FILTERS_REQUIRED", 2)   # out of 3
CRYPTO_TOP_N = _int("CRYPTO_TOP_N", 5)
CRYPTO_MIN_POP = _float("CRYPTO_MIN_POP", 40.0)   # minimum score (%) to display

CRYPTO_BB_PERIOD = _int("CRYPTO_BB_PERIOD", 20)
CRYPTO_BB_STD = _float("CRYPTO_BB_STD", 2.0)
CRYPTO_BB_TOLERANCE = _float("CRYPTO_BB_TOLERANCE", 0.02)      # filter: within 2% of lower band

CRYPTO_RSI_PERIOD = _int("CRYPTO_RSI_PERIOD", 14)
CRYPTO_RSI_OVERSOLD = _float("CRYPTO_RSI_OVERSOLD", 35.0)

# 30 days of 4h candles = 180 bars
CRYPTO_SUPPORT_LOOKBACK = _int("CRYPTO_SUPPORT_LOOKBACK", 180)
CRYPTO_SUPPORT_CLUSTER_TOL = _float("CRYPTO_SUPPORT_CLUSTER_TOL", 0.01)
CRYPTO_SUPPORT_MIN_TOUCHES = _int("CRYPTO_SUPPORT_MIN_TOUCHES", 2)
CRYPTO_SUPPORT_MARGIN = _float("CRYPTO_SUPPORT_MARGIN", 0.03)   # within 3% of the level
CRYPTO_SUPPORT_BREAK_TOL = _float("CRYPTO_SUPPORT_BREAK_TOL", 0.005)

# --- Scoring weights (points) -- see crypto_module._score() ---
CRYPTO_SCORE_RSI_STRONG = _int("CRYPTO_SCORE_RSI_STRONG", 25)    # RSI < 30
CRYPTO_SCORE_RSI_WEAK = _int("CRYPTO_SCORE_RSI_WEAK", 15)        # 30 <= RSI < 35
CRYPTO_SCORE_BB_STRONG = _int("CRYPTO_SCORE_BB_STRONG", 25)      # within 1% of lower band
CRYPTO_SCORE_BB_WEAK = _int("CRYPTO_SCORE_BB_WEAK", 15)          # within 2%
CRYPTO_SCORE_SUPPORT_STRONG = _int("CRYPTO_SCORE_SUPPORT_STRONG", 25)  # tested 3+ times
CRYPTO_SCORE_SUPPORT_WEAK = _int("CRYPTO_SCORE_SUPPORT_WEAK", 15)      # tested 2 times
CRYPTO_SCORE_VOLUME_INCREASE = _int("CRYPTO_SCORE_VOLUME_INCREASE", 20)  # rising buy volume, 12h
CRYPTO_TREND_UP_MULT = _float("CRYPTO_TREND_UP_MULT", 1.1)    # BTC above its 50-day SMA
CRYPTO_TREND_DOWN_MULT = _float("CRYPTO_TREND_DOWN_MULT", 0.85)  # BTC below it
CRYPTO_SCORE_CAP = _float("CRYPTO_SCORE_CAP", 75.0)
CRYPTO_TREND_SMA_PERIOD = _int("CRYPTO_TREND_SMA_PERIOD", 50)

# Top ~100 coins by market cap with a Binance USDT spot pair. Binance
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
    "ALGO/USDT", "VET/USDT", "EGLD/USDT", "FTM/USDT", "QNT/USDT",
    "AR/USDT", "GNO/USDT", "COMP/USDT", "1INCH/USDT", "ENJ/USDT",
    "ZEC/USDT", "DASH/USDT", "XMR/USDT", "NEO/USDT", "IOTA/USDT",
    "KSM/USDT", "WAVES/USDT", "ZIL/USDT", "BAT/USDT", "SC/USDT",
    "ANKR/USDT", "CELO/USDT", "ONE/USDT", "IOTX/USDT", "RSR/USDT",
    "GMT/USDT", "MASK/USDT", "OCEAN/USDT", "ROSE/USDT", "SKL/USDT",
    "DYDX/USDT", "GMX/USDT", "BLUR/USDT", "SEI/USDT", "STRK/USDT",
    "ORDI/USDT", "TAO/USDT", "NOT/USDT", "W/USDT", "AEVO/USDT",
]

# --- Files ---
# On Railway, attaching a volume sets RAILWAY_VOLUME_MOUNT_PATH automatically,
# so state survives redeploys with no extra configuration.
DATA_DIR = (os.environ.get("DATA_DIR")
            or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
            or ".")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.environ.get("STATE_FILE", os.path.join(DATA_DIR, "state.json"))
SIGNALS_DB_FILE = os.environ.get("SIGNALS_DB_FILE", os.path.join(DATA_DIR, "signals.db"))
POSITIONS_DB_FILE = os.environ.get("POSITIONS_DB_FILE", SIGNALS_DB_FILE)  # same file, separate table

# =====================================================================
# Signal history / review (scanner/signals_db.py, scanner/review_module.py)
# =====================================================================
# A signal "hits" if the underlying moved this much (%) in the expected
# direction by the review checkpoint (stocks/crypto only -- options/leaps/
# heavy use "the contract is worth more than it was bought for" instead).
REVIEW_HIT_MOVE_PCT = _float("REVIEW_HIT_MOVE_PCT", 3.0)
REVIEW_WINDOWS_DAYS = (7, 30)   # checkpoints /review evaluates
# Safety cap so one /review run can't stall on hundreds of accumulated
# signals -- run it again to keep working through the backlog.
REVIEW_MAX_PER_RUN = _int("REVIEW_MAX_PER_RUN", 150)
# A filter-combination needs at least this many reviewed signals before
# /stats will rank it among the "best combos" -- guards against a single
# lucky/unlucky signal dominating the ranking.
REVIEW_MIN_COMBO_SAMPLE = _int("REVIEW_MIN_COMBO_SAMPLE", 3)

# =====================================================================
# Open-position guard (scanner/positions_module.py) -- /track, /positions,
# /untrack, and the hourly monitoring job in bot.py. Self-reported
# positions only; the bot has no brokerage integration.
# =====================================================================
POSITION_STOPLOSS_PCT = _float("POSITION_STOPLOSS_PCT", -30.0)   # premium % vs entry
POSITION_PROFIT_PCT = _float("POSITION_PROFIT_PCT", 50.0)
POSITION_THETA_WARNING_DAYS = _int("POSITION_THETA_WARNING_DAYS", 21)
POSITION_MONITOR_INTERVAL_SECONDS = _int("POSITION_MONITOR_INTERVAL_SECONDS", 3600)

# =====================================================================
# Golden signal confluence (scanner/golden_module.py) -- runs after every
# /stocks session, on the stocks it already sent. A stricter bar than the
# general STOCKS_FILTERS_REQUIRED (2) on purpose: "golden" means both the
# stock AND one of its own CALL contracts qualify at once, which should be
# rarer than an ordinary /stocks hit.
# =====================================================================
GOLDEN_STOCKS_FILTERS_REQUIRED = _int("GOLDEN_STOCKS_FILTERS_REQUIRED", 3)   # out of 4
# A golden stock with a whale-backed CALL detected on it within this many
# days gets the upgraded "⭐⭐⭐ تعافي بدعم حوت" header instead of the plain
# "⭐ إشارة ذهبية" one -- see golden_module.check_confluence.
GOLDEN_WHALE_LOOKBACK_DAYS = _int("GOLDEN_WHALE_LOOKBACK_DAYS", 7)

# =====================================================================
# Whale detector (scanner/whale_module.py) -- flags a likely large CALL
# trade from a plain Vol/OI (today's volume vs open interest) ratio spike,
# from the same free yfinance/CBOE chain data everything else here uses
# (no paid order-flow subscription). CALL only, same as the rest of this
# bot -- no PUT support anywhere, so this can only ever read as a bullish
# signal, never compared against a PUT side.
#
# Runs automatically on its own schedule (bot.py's job_queue, same pattern
# as the hourly position monitor) for as long as the bot process is up, OR
# on demand via the /whales command -- either path pushes a Telegram
# message straight to every currently-eligible member (not just whoever
# ran the command) the moment it finds something new, deduplicated per
# contract per calendar day via signals_db.log_signal's own UNIQUE
# constraint (a contract that's still anomalous next hour simply doesn't
# re-insert, so it doesn't re-alert until a new calendar day).
# =====================================================================
WHALE_SCAN_INTERVAL_SECONDS = _int("WHALE_SCAN_INTERVAL_SECONDS", 3600)   # hourly, market hours only
WHALE_TICKERS = HEAVY_TICKERS   # same curated mega/large/ETF list -- see its own comment above

# Classification thresholds (today's volume / open interest) -- cumulative,
# not exclusive bands: a ratio of 12 is "whale" (the highest one it clears).
# Every tier alerts (ratio > WHALE_RATIO_NOTABLE is the only floor) -- there
# is deliberately no minimum OI/volume/flow/DTE filter on top anymore (had
# one; removed on request). A very small-OI contract can swing this ratio
# wildly, so expect noisier alerts on illiquid names -- that tradeoff is
# intentional here, not an oversight.
WHALE_RATIO_NOTABLE = _float("WHALE_RATIO_NOTABLE", 3.0)
WHALE_RATIO_UNUSUAL = _float("WHALE_RATIO_UNUSUAL", 5.0)
WHALE_RATIO_WHALE = _float("WHALE_RATIO_WHALE", 10.0)

# With no quality filter left above (see the comment block above), removing
# the volume noise floor would otherwise flood the chat -- a hard cap on
# how many alerts one scan cycle can ever send, highest ratio first across
# the WHOLE run (not per symbol). Requires collecting every qualifying
# contract before sending any (see whale_module.scan) -- fine here since
# this is a background job, not a live user-facing streamed session.
WHALE_MAX_ALERTS_PER_RUN = _int("WHALE_MAX_ALERTS_PER_RUN", 15)
