"""OHLCV loading, strictly on the requested timeframe.

Two rules matter here:
  * the frame the user asked for decides the interval — nothing else;
  * if that frame genuinely has no data (Yahoo does not serve 1-minute bars
    for every Saudi listing), the loader steps up to the next frame and says
    so in `fallback_note`, instead of quietly answering on another timeframe.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf

from .frames import FRAMES, Frame, context_frame
from .symbols import Candidate

log = logging.getLogger(__name__)

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _step_up(frame: Frame) -> list[Frame]:
    """Frames longer than `frame`, shortest first — used only when the asked-for
    frame returns no bars for that symbol."""
    return sorted((f for f in FRAMES.values() if f.minutes > frame.minutes),
                  key=lambda f: f.minutes)


@dataclass
class MarketData:
    symbol: str
    frame: Frame
    df: pd.DataFrame                     # bars on the requested frame
    context_df: pd.DataFrame | None = None   # one frame higher, for alignment
    daily_df: pd.DataFrame | None = None     # always daily, for 52w context
    meta: dict = field(default_factory=dict)
    fallback_note: str | None = None

    @property
    def last_close(self) -> float:
        return float(self.df["Close"].iloc[-1])

    @property
    def last_time(self) -> pd.Timestamp:
        return self.df.index[-1]

    @property
    def bars(self) -> int:
        return len(self.df)


def _resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = df.resample(rule, label="left", closed="left").agg({
        "Open": "first", "High": "max", "Low": "min",
        "Close": "last", "Volume": "sum",
    })
    return out.dropna(subset=["Open", "High", "Low", "Close"])


def _download(symbol: str, interval: str, period: str) -> pd.DataFrame | None:
    try:
        df = yf.Ticker(symbol).history(
            period=period, interval=interval, auto_adjust=True, actions=False,
        )
    except Exception:
        log.warning("history failed for %s %s/%s", symbol, interval, period, exc_info=True)
        return None
    if df is None or df.empty:
        return None
    missing = [c for c in OHLCV if c not in df.columns]
    if missing:
        return None
    df = df[OHLCV].dropna(subset=["Close"])
    return df if not df.empty else None


def fetch(symbol: str, frame: Frame) -> pd.DataFrame | None:
    """Bars for exactly this frame (resampled when Yahoo has no native one)."""
    df = _download(symbol, frame.interval, frame.period)
    if df is None:
        return None
    if frame.resample:
        df = _resample(df, frame.resample)
    return df if len(df) >= 5 else None


def _meta(symbol: str) -> dict:
    """Name, currency, exchange, valuation, next earnings — all best effort."""
    out: dict = {"symbol": symbol}
    try:
        ticker = yf.Ticker(symbol)
        info = {}
        try:
            info = ticker.get_info() or {}
        except Exception:
            info = {}
        for key, src in (
            ("name", "shortName"), ("long_name", "longName"),
            ("exchange", "fullExchangeName"), ("currency", "currency"),
            ("sector", "sector"), ("industry", "industry"),
            ("market_cap", "marketCap"), ("pe", "trailingPE"),
            ("forward_pe", "forwardPE"), ("beta", "beta"),
            ("target_mean", "targetMeanPrice"), ("recommendation", "recommendationKey"),
            ("shares_short_pct", "shortPercentOfFloat"),
            ("float_shares", "floatShares"), ("avg_volume", "averageVolume"),
            ("week52_high", "fiftyTwoWeekHigh"), ("week52_low", "fiftyTwoWeekLow"),
            ("dividend_yield", "dividendYield"), ("quote_type", "quoteType"),
        ):
            value = info.get(src)
            if value not in (None, "", 0) or key in ("market_cap",):
                out[key] = value
        try:
            cal = ticker.get_calendar() or {}
            earnings = cal.get("Earnings Date") or cal.get("earningsDate")
            if isinstance(earnings, (list, tuple)) and earnings:
                earnings = earnings[0]
            if earnings:
                out["next_earnings"] = str(earnings)[:10]
        except Exception:
            pass
    except Exception:
        log.warning("meta failed for %s", symbol, exc_info=True)
    return out


def load(candidates: list[Candidate] | list[str], frame: Frame,
         with_meta: bool = True) -> MarketData | None:
    """First candidate that actually returns bars on `frame`, with context.

    Confirming the symbol by download is what keeps a mis-read screenshot from
    producing a confident read on the wrong asset.
    """
    ordered: list[str] = []
    for c in candidates:
        symbol = c.symbol if isinstance(c, Candidate) else str(c)
        if symbol and symbol not in ordered:
            ordered.append(symbol)

    for symbol in ordered[:6]:
        used = frame
        df = fetch(symbol, frame)
        note = None
        if df is None or len(df) < 20:
            for bigger in _step_up(frame):
                df = fetch(symbol, bigger)
                if df is not None and len(df) >= 20:
                    note = (f"لا تتوفر بيانات كافية على فريم {frame.label_ar} لهذا الرمز، "
                            f"فتم التحليل على {bigger.label_ar}.")
                    used = bigger
                    break
        if df is None or len(df) < 20:
            continue

        ctx = context_frame(used)
        ctx_df = fetch(symbol, ctx) if ctx else None
        daily_df = df if used.key == "1d" else _download(symbol, "1d", "3y")
        data = MarketData(
            symbol=symbol, frame=used, df=df, context_df=ctx_df,
            daily_df=daily_df, fallback_note=note,
        )
        data.meta = _meta(symbol) if with_meta else {"symbol": symbol}
        data.meta["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data.meta["bars"] = len(df)
        data.meta["requested_frame"] = frame.key
        data.meta["used_frame"] = used.key
        return data
    return None
