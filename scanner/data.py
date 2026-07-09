"""Bulk OHLCV download via yfinance, in batches."""
import logging

import pandas as pd
import yfinance as yf

from . import config

log = logging.getLogger(__name__)

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]


def fetch_batch(symbols: list[str], interval: str, period: str) -> dict[str, pd.DataFrame]:
    """Download history for a batch of symbols at the given candle interval
    and lookback period.

    Returns {symbol: OHLCV DataFrame} for symbols that came back with data.
    """
    raw = yf.download(
        tickers=symbols,
        interval=interval,
        period=period,
        group_by="ticker",
        auto_adjust=True,
        threads=min(len(symbols), config.DOWNLOAD_THREADS),
        progress=False,
    )
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out

    # yfinance returns MultiIndex columns (ticker, field) for a list input
    # regardless of how many tickers are in it — including a list of one,
    # since multi_level_index defaults to True and we never override it.
    frames = {}
    if isinstance(raw.columns, pd.MultiIndex):
        top = raw.columns.get_level_values(0)
        for sym in symbols:
            if sym in top:
                frames[sym] = raw[sym]
    else:
        frames = {symbols[0]: raw}

    for sym, df in frames.items():
        try:
            df = df[REQUIRED_COLS].dropna(subset=["Close"])
        except KeyError:
            continue
        if df.empty:
            continue
        out[sym] = df
    return out


def passes_liquidity(df: pd.DataFrame) -> bool:
    """Skip penny/illiquid stocks before running the filters."""
    last_close = df["Close"].iloc[-1]
    avg_vol = df["Volume"].tail(20).mean()
    return last_close >= config.MIN_PRICE and avg_vol >= config.MIN_AVG_VOLUME


def make_batches(symbols: list[str]) -> list[list[str]]:
    return [symbols[i:i + config.BATCH_SIZE] for i in range(0, len(symbols), config.BATCH_SIZE)]
