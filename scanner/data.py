"""Bulk OHLCV download via yfinance, in batches."""
import logging

import pandas as pd
import yfinance as yf

from . import config

log = logging.getLogger(__name__)

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


def fetch_batch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Download hourly history for a batch of symbols.

    Returns {symbol: OHLCV DataFrame} for symbols that came back with data.
    """
    raw = yf.download(
        tickers=symbols,
        interval=config.INTERVAL,
        period=config.PERIOD,
        group_by="ticker",
        auto_adjust=True,
        threads=min(len(symbols), config.DOWNLOAD_THREADS),
        progress=False,
    )
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out

    if len(symbols) == 1:
        frames = {symbols[0]: raw}
    else:
        frames = {}
        top = raw.columns.get_level_values(0)
        for sym in symbols:
            if sym in top:
                frames[sym] = raw[sym]

    for sym, df in frames.items():
        try:
            df = df[REQUIRED_COLS].dropna(subset=["Close"])
        except KeyError:
            continue
        if df.empty:
            continue
        out[sym] = df
    return out


def passes_liquidity(df: pd.DataFrame, is_crypto: bool = False) -> bool:
    """Skip penny/illiquid assets before running the filters."""
    last_close = df["Close"].iloc[-1]
    avg_vol = df["Volume"].tail(20).mean()
    if is_crypto:
        # Coins range from cents to thousands of dollars, so judge liquidity
        # by traded dollar volume rather than price and unit count.
        return last_close * avg_vol >= config.MIN_CRYPTO_DOLLAR_VOLUME
    return last_close >= config.MIN_PRICE and avg_vol >= config.MIN_AVG_VOLUME
