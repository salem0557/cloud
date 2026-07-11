"""Public Binance market data via ccxt (no API keys — spot OHLCV only).

Independent of scanner/data.py (which is yfinance/stocks-only) on purpose:
the crypto module must keep working even if the stocks/options modules'
data source has an outage, and vice versa.
"""
import logging

import ccxt
import pandas as pd

log = logging.getLogger(__name__)

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]

_exchange = None


def _get_exchange():
    global _exchange
    if _exchange is None:
        _exchange = ccxt.binance({"enableRateLimit": True})
    return _exchange


def fetch_ohlcv(symbol: str, timeframe: str, limit: int) -> pd.DataFrame | None:
    """One symbol's OHLCV candles (e.g. "BTC/USDT", "4h"), or None on any
    failure (delisted pair, network error, etc.) — the caller treats a
    missing symbol as "skip it", never as a crash."""
    try:
        candles = _get_exchange().fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    except Exception:
        log.warning("ccxt fetch_ohlcv failed for %s", symbol)
        return None
    if not candles:
        return None
    df = pd.DataFrame(candles, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
    df.index = pd.to_datetime(df["ts"], unit="ms")
    return df[REQUIRED_COLS]


def fetch_last_price(symbol: str) -> float | None:
    """Last traded price via the ticker endpoint -- lighter than pulling
    OHLCV candles just to read the latest close (used by /review, which
    only needs one number per symbol)."""
    try:
        ticker = _get_exchange().fetch_ticker(symbol)
    except Exception:
        log.warning("ccxt fetch_ticker failed for %s", symbol)
        return None
    last = ticker.get("last")
    return float(last) if last is not None else None


def fetch_24h_quote_volume(symbol: str) -> float | None:
    """24-hour trading volume in the quote currency (USDT) -- Binance's own
    liquidity figure (ticker's 24hr endpoint), not derived from the 4h
    candles used for the filters. None on any failure."""
    try:
        ticker = _get_exchange().fetch_ticker(symbol)
    except Exception:
        log.warning("ccxt fetch_ticker failed for %s", symbol)
        return None
    vol = ticker.get("quoteVolume")
    return float(vol) if vol is not None else None
