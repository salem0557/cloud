"""Render a candlestick chart (Bollinger Bands + support/resistance lines)
for a result, so its Telegram message carries a visual of the setup.
Generic over any OHLCV DataFrame -- used by both stocks_module (daily bars)
and crypto_module (4h bars).

Rendering never raises: any failure returns None and the caller just sends
a plain text message instead.
"""
import io
import logging

import matplotlib
matplotlib.use("Agg")  # headless: no display available on the server
import mplfinance as mpf

from .indicators import bollinger

log = logging.getLogger(__name__)

CHART_BARS = 80  # most recent bars plotted


def render_chart(symbol: str, df, bb_period: int, bb_std: float,
                 support: float | None = None, resistance: float | None = None) -> bytes | None:
    try:
        plot_df = df.tail(CHART_BARS).copy()
        lower, mid, upper = bollinger(df["Close"], bb_period, bb_std)
        addplots = [
            mpf.make_addplot(lower.tail(CHART_BARS), color="orange", width=0.8),
            mpf.make_addplot(mid.tail(CHART_BARS), color="gray", width=0.6),
            mpf.make_addplot(upper.tail(CHART_BARS), color="orange", width=0.8),
        ]
        hlines = dict(hlines=[], colors=[], linestyle="dashed", linewidths=0.8)
        if support is not None:
            hlines["hlines"].append(support)
            hlines["colors"].append("green")
        if resistance is not None:
            hlines["hlines"].append(resistance)
            hlines["colors"].append("red")

        buf = io.BytesIO()
        mpf.plot(
            plot_df, type="candle", style="charles", addplot=addplots,
            hlines=hlines if hlines["hlines"] else None,
            title=symbol, volume=True, savefig=dict(fname=buf, dpi=110, bbox_inches="tight"),
        )
        buf.seek(0)
        return buf.read()
    except Exception:
        log.exception("Chart render failed for %s", symbol)
        return None
