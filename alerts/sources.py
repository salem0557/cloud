"""Price sources: crypto via ccxt, US stocks via yfinance.

Both are public endpoints — the bot needs no API key and no account, because
it only ever reads prices. Heavy imports happen lazily so the pure logic (and
its tests) stay dependency-free.
"""
import datetime as dt
import logging
from dataclasses import dataclass

from . import config
from .periods import NY, UTC, candle_interval, period_start

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Asset:
    symbol: str        # canonical id used everywhere: "BTC/USDT" or "AAPL"
    display: str       # what the user sees: "BTC" or "AAPL"
    market: str        # "crypto" | "stock"

    @property
    def is_crypto(self) -> bool:
        return self.market == "crypto"

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "display": self.display,
                "market": self.market}

    @classmethod
    def from_dict(cls, d: dict) -> "Asset":
        return cls(d["symbol"], d["display"], d["market"])


# ------------------------------------------------------------------- crypto

_exchange = None
_crypto_bases: dict[str, str] = {}   # "BTC" -> "BTC/USDT"


def exchange():
    global _exchange
    if _exchange is None:
        try:
            import ccxt
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise SourceError("مكتبة ccxt غير مثبّتة") from exc
        _exchange = getattr(ccxt, config.CRYPTO_EXCHANGE)({"enableRateLimit": True})
    return _exchange


def crypto_bases() -> dict[str, str]:
    """Map of base asset -> pair, for every active spot pair of the quote."""
    global _crypto_bases
    if not _crypto_bases:
        markets = exchange().load_markets()
        for sym, m in markets.items():
            if (m.get("quote") == config.CRYPTO_QUOTE and m.get("spot")
                    and m.get("active", True)):
                _crypto_bases[m["base"].upper()] = sym
    return _crypto_bases


def crypto_price(symbol: str) -> float:
    ticker = exchange().fetch_ticker(symbol)
    price = ticker.get("last") or ticker.get("close")
    if not price:
        raise SourceError(f"لا يوجد سعر لـ {symbol}")
    return float(price)


def crypto_extremes(symbol: str, period: str) -> tuple[float, float]:
    """(low, high) of the current period, rebuilt from candles."""
    start = period_start(dt.datetime.now(UTC), period)
    since = int(start.timestamp() * 1000)
    rows = exchange().fetch_ohlcv(symbol, timeframe=candle_interval(period),
                                  since=since, limit=1000)
    rows = [r for r in (rows or []) if r[0] >= since]
    if not rows:
        raise SourceError(f"لا توجد شموع لـ {symbol}")
    return min(r[3] for r in rows), max(r[2] for r in rows)


# ------------------------------------------------------------------- stocks

# yfinance caps intra-day history by interval, so each period gets the finest
# interval whose allowed window still covers it.
_YF_WINDOW = {"hour": ("1m", "1d"), "day": ("5m", "5d"),
              "week": ("30m", "1mo"), "month": ("1h", "3mo")}


def _yf():
    try:
        import yfinance
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise SourceError("مكتبة yfinance غير مثبّتة") from exc
    return yfinance


def _stock_history(symbol: str, period: str):
    interval, window = _YF_WINDOW[period]
    hist = _yf().Ticker(symbol).history(period=window, interval=interval)
    if hist is None or hist.empty:
        raise SourceError(f"لا توجد بيانات لـ {symbol}")
    return hist


def stock_price(symbol: str) -> float:
    ticker = _yf().Ticker(symbol)
    try:
        price = ticker.fast_info.get("lastPrice")
        if price:
            return float(price)
    except Exception:  # fast_info is best-effort; fall back to a real bar
        pass
    hist = ticker.history(period="1d", interval="1m")
    if hist is None or hist.empty:
        raise SourceError(f"لا يوجد سعر لـ {symbol}")
    return float(hist["Close"].iloc[-1])


def stock_extremes(symbol: str, period: str) -> tuple[float, float]:
    hist = _stock_history(symbol, period)
    start = period_start(dt.datetime.now(NY), period)
    index = hist.index
    if getattr(index, "tz", None) is None:
        hist = hist.tz_localize(NY)          # daily bars come back tz-naive
    else:
        hist = hist.tz_convert(NY)
    rows = hist[hist.index >= start]
    if rows.empty:                            # market has not opened yet today
        rows = hist.tail(1)
    return float(rows["Low"].min()), float(rows["High"].max())


def stock_market_open(now: dt.datetime | None = None) -> bool:
    """Weekday pre-market through post-market, ET. Holidays still poll — a
    quiet request costs far less than missing a session we wrongly excluded."""
    now = now or dt.datetime.now(NY)
    if now.weekday() >= 5:
        return False
    return config.STOCK_OPEN_HOUR <= now.hour < config.STOCK_CLOSE_HOUR


# ---------------------------------------------------------------- dispatch

def resolve(text: str) -> Asset | None:
    """Turn user input into an Asset.

    Crypto is tried first because that is what the bot is mostly used for;
    an explicit "s:" prefix (or "c:") settles the ambiguous tickers.
    """
    raw = text.strip().upper().replace("-", "/")
    force = ""
    if raw.startswith(("S:", "S ")) and len(raw) > 2:
        force, raw = "stock", raw[2:].strip()
    elif raw.startswith(("C:", "C ")) and len(raw) > 2:
        force, raw = "crypto", raw[2:].strip()
    if not raw or len(raw) > 20:
        return None

    if force != "stock":
        asset = _resolve_crypto(raw)
        if asset:
            return asset
    if force != "crypto":
        return _resolve_stock(raw)
    return None


def _resolve_crypto(raw: str) -> Asset | None:
    try:
        bases = crypto_bases()
    except Exception as exc:
        log.warning("crypto markets unavailable: %s", exc)
        return None
    if "/" in raw:
        if raw in exchange().markets:
            base = exchange().markets[raw]["base"].upper()
            return Asset(raw, base, "crypto")
        return None
    if raw in bases:
        return Asset(bases[raw], raw, "crypto")
    # "BTCUSDT" written without the slash
    if raw.endswith(config.CRYPTO_QUOTE):
        base = raw[:-len(config.CRYPTO_QUOTE)]
        if base in bases:
            return Asset(bases[base], base, "crypto")
    return None


def _resolve_stock(raw: str) -> Asset | None:
    if not raw.replace(".", "").isalnum():
        return None
    try:
        hist = _yf().Ticker(raw).history(period="5d", interval="1d")
    except Exception as exc:
        log.warning("stock lookup failed for %s: %s", raw, exc)
        return None
    if hist is None or hist.empty:
        return None
    return Asset(raw, raw, "stock")


def price(asset: Asset) -> float:
    return crypto_price(asset.symbol) if asset.is_crypto else stock_price(asset.symbol)


def extremes(asset: Asset, period: str) -> tuple[float, float]:
    if asset.is_crypto:
        return crypto_extremes(asset.symbol, period)
    return stock_extremes(asset.symbol, period)


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.4f}".rstrip("0").rstrip(".")
    return f"{p:.8f}".rstrip("0").rstrip(".")
