"""Render a candlestick chart (Bollinger Bands + support line + RSI panel)
for an alerted stock, so the Telegram alert carries a visual of the setup.

Rendering never raises: any failure returns None and the text alert still
goes out on its own.
"""
import io
import logging

import matplotlib
matplotlib.use("Agg")  # headless: no display available on the server
import mplfinance as mpf

from . import config
from .indicators import bollinger, find_nearest_support, rsi

log = logging.getLogger(__name__)


def render_chart(symbol: str, df, details: dict) -> bytes | None:
    """PNG bytes of the chart, or None if rendering isn't possible: candles +
    Bollinger Bands + the support level (green) + an RSI panel with the
    oversold reference line."""
    try:
        if df is None or len(df) < config.BB_PERIOD + 5:
            return None

        lower, mid, upper = bollinger(df["Close"], config.BB_PERIOD, config.BB_STD)
        rsi_series = rsi(df["Close"], config.RSI_PERIOD)
        level = find_nearest_support(df)
        level_color = "#22c55e"
        rsi_ref, rsi_ref_color = config.RSI_OVERSOLD, "#ef4444"

        tail = df.tail(config.CHART_BARS).copy()
        tail["bb_lower"] = lower.reindex(tail.index)
        tail["bb_mid"] = mid.reindex(tail.index)
        tail["bb_upper"] = upper.reindex(tail.index)
        tail["rsi"] = rsi_series.reindex(tail.index)
        tail["rsi_ref"] = rsi_ref

        addplots = [
            mpf.make_addplot(tail["bb_upper"], color="#9aa0a6", width=0.8),
            mpf.make_addplot(tail["bb_mid"], color="#9aa0a6", width=0.6, linestyle="dotted"),
            mpf.make_addplot(tail["bb_lower"], color="#9aa0a6", width=0.8),
            mpf.make_addplot(tail["rsi"], panel=1, color="#3b82f6", ylabel="RSI"),
            mpf.make_addplot(tail["rsi_ref"], panel=1, color=rsi_ref_color,
                             width=0.7, linestyle="dashed"),
        ]
        plot_kwargs = dict(
            type="candle", style="charles", addplot=addplots,
            volume=False, panel_ratios=(3, 1), figsize=(8, 6),
            title=f"\n{symbol} — {config.INTERVAL}",
        )
        if level is not None:
            plot_kwargs["hlines"] = dict(hlines=[level], colors=[level_color],
                                         linestyle="dashed", linewidths=[0.9])

        buf = io.BytesIO()
        mpf.plot(tail, savefig=dict(fname=buf, dpi=110, bbox_inches="tight"),
                 **plot_kwargs)
        buf.seek(0)
        return buf.read()
    except Exception:
        log.exception("Chart render failed for %s", symbol)
        return None
