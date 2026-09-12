"""Every knob the analyst agent reads, all from the environment.

Nothing here raises on import: a missing key disables the feature that needs
it and the rest of the pipeline keeps working, so the agent can be deployed
half-configured and still answer.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on", "y")


def _ids(name: str) -> set[int]:
    """Comma/space separated Telegram ids -> set of ints ("-100123, 456")."""
    out: set[int] = set()
    for part in os.getenv(name, "").replace(",", " ").split():
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def _list(name: str, default: list[str]) -> list[str]:
    parts = [p.strip() for p in os.getenv(name, "").split(",") if p.strip()]
    return parts or default


# --- Groq -------------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
# Explicit model ids win; otherwise the client asks Groq which of the
# preferred ids are actually live, so a deprecated model never breaks a reply.
GROQ_TEXT_MODEL = os.getenv("GROQ_TEXT_MODEL", "").strip()
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "").strip()
TEXT_MODEL_PREFERENCE = _list("GROQ_TEXT_MODEL_PREFERENCE", [
    "moonshotai/kimi-k2-instruct-0905",
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
])
VISION_MODEL_PREFERENCE = _list("GROQ_VISION_MODEL_PREFERENCE", [
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct",
])
GROQ_TIMEOUT = _int("GROQ_TIMEOUT", 90)
GROQ_MAX_RETRIES = _int("GROQ_MAX_RETRIES", 3)
GROQ_TEMPERATURE = _float("GROQ_TEMPERATURE", 0.25)
GROQ_MAX_TOKENS = _int("GROQ_MAX_TOKENS", 2200)
MODEL_CACHE_TTL = _int("GROQ_MODEL_CACHE_TTL", 3600)

# --- Telegram ---------------------------------------------------------------
# Backend 1 (no login needed): a BotFather token. Must be its own bot — two
# pollers on one token fight over getUpdates.
ANALYST_BOT_TOKEN = os.getenv("ANALYST_BOT_TOKEN", "").strip()
# Backend 2: a userbot (a real account). Needs a one-time interactive login.
TELEGRAM_API_ID = _int("TELEGRAM_API_ID", 0)
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
TELEGRAM_SESSION = os.getenv("TELEGRAM_SESSION", "").strip()  # StringSession
TELEGRAM_SESSION_NAME = os.getenv("TELEGRAM_SESSION_NAME", "analyst_userbot").strip()
# Empty = answer in every chat the account is in. Non-empty = only these ids.
ALLOWED_CHATS = _ids("ANALYST_ALLOWED_CHATS")
BLOCKED_CHATS = _ids("ANALYST_BLOCKED_CHATS")
OWNER_IDS = _ids("ANALYST_OWNER_IDS")
# In a group the agent stays silent unless one of these words is in the
# message, it is a reply to the agent, or the agent is @mentioned.
TRIGGERS = _list("ANALYST_TRIGGERS", [
    "تحليل", "حلل", "حلّل", "شارت", "تشارت", "الشارت", "التشارت",
    "analyze", "analysis", "chart", "ta",
])
# In a private chat every photo is analysed without needing a trigger word.
DM_ALWAYS_ANSWER = _bool("ANALYST_DM_ALWAYS", True)
# Any photo in an allowed chat is treated as a request, whatever is written
# with it ("وش رايك؟", "ادخل ولا أنتظر؟", or nothing at all). Images that turn
# out not to be charts are dropped silently, so the group is never spammed.
# ANALYST_ANSWER_BARE_PHOTOS is the old name for this switch.
ANSWER_ALL_PHOTOS = _bool("ANALYST_ANSWER_ALL_PHOTOS",
                          _bool("ANALYST_ANSWER_BARE_PHOTOS", True))
MAX_CONCURRENT = _int("ANALYST_MAX_CONCURRENT", 2)
USER_COOLDOWN = _int("ANALYST_USER_COOLDOWN", 20)  # seconds between requests
SEND_TYPING = _bool("ANALYST_SEND_TYPING", True)
REPLY_LANG = os.getenv("ANALYST_LANG", "ar").strip().lower()

# --- Market data ------------------------------------------------------------
DEFAULT_FRAME = os.getenv("ANALYST_DEFAULT_FRAME", "1d").strip()
CHART_BARS = _int("ANALYST_CHART_BARS", 140)
MIN_BARS = _int("ANALYST_MIN_BARS", 60)  # below this the read is unreliable
DATA_TIMEOUT = _int("ANALYST_DATA_TIMEOUT", 30)
# US-only mode (the default): Saudi names and bare 4-digit Tadawul codes are
# not treated as symbols, and session/freshness notes use the NYSE clock.
# Set ANALYST_US_ONLY=false to bring Tadawul back.
US_ONLY = _bool("ANALYST_US_ONLY", True)
# Only consulted when US_ONLY is false: a bare 4-digit ticker (2222) -> 2222.SR.
ASSUME_TADAWUL_FOR_DIGITS = _bool("ANALYST_ASSUME_TADAWUL", True)
# Crypto, metals and FX stay available even in US-only mode; turn them off to
# make the agent answer for US-listed tickers and indices only.
ALLOW_NON_EQUITY = _bool("ANALYST_ALLOW_NON_EQUITY", True)

# --- Indicators -------------------------------------------------------------
EMA_FAST = _int("ANALYST_EMA_FAST", 20)
EMA_MID = _int("ANALYST_EMA_MID", 50)
EMA_SLOW = _int("ANALYST_EMA_SLOW", 200)
RSI_PERIOD = _int("ANALYST_RSI_PERIOD", 14)
BB_PERIOD = _int("ANALYST_BB_PERIOD", 20)
BB_STD = _float("ANALYST_BB_STD", 2.0)
ATR_PERIOD = _int("ANALYST_ATR_PERIOD", 14)
MACD_FAST = _int("ANALYST_MACD_FAST", 12)
MACD_SLOW = _int("ANALYST_MACD_SLOW", 26)
MACD_SIGNAL = _int("ANALYST_MACD_SIGNAL", 9)
PIVOT_WINDOW = _int("ANALYST_PIVOT_WINDOW", 5)   # bars each side of a swing
LEVEL_TOLERANCE = _float("ANALYST_LEVEL_TOLERANCE", 0.008)  # 0.8% clustering
STOP_ATR_MULT = _float("ANALYST_STOP_ATR_MULT", 1.2)

# --- News / chatter ---------------------------------------------------------
NEWS_ENABLED = _bool("ANALYST_NEWS", True)
NEWS_LIMIT = _int("ANALYST_NEWS_LIMIT", 8)
SOCIAL_ENABLED = _bool("ANALYST_SOCIAL", True)
SOCIAL_LIMIT = _int("ANALYST_SOCIAL_LIMIT", 15)
HTTP_TIMEOUT = _int("ANALYST_HTTP_TIMEOUT", 12)

# --- Chart rendering --------------------------------------------------------
CHART_DPI = _int("ANALYST_CHART_DPI", 130)
CHART_WIDTH = _float("ANALYST_CHART_WIDTH", 12.0)
CHART_HEIGHT = _float("ANALYST_CHART_HEIGHT", 9.0)
CHART_STYLE = os.getenv("ANALYST_CHART_STYLE", "nightclouds").strip()
SHOW_FIB = _bool("ANALYST_SHOW_FIB", True)
SHOW_LEVELS = _int("ANALYST_SHOW_LEVELS", 3)  # supports + resistances drawn
