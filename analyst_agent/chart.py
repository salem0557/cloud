"""The chart the agent sends back: real bars for the requested frame with the
indicators, the levels it is quoting, and the trade plan drawn on top.

Rebuilding the chart from data beats annotating the user's screenshot — the
screenshot has no numbers behind its pixels, so levels drawn on it could not be
trusted. Labels are English on purpose: matplotlib does not shape Arabic script,
so Arabic belongs in the message text, not in the image.
"""
from __future__ import annotations

import io
import logging

import matplotlib
matplotlib.use("Agg")  # headless server
import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402

from . import config  # noqa: E402
from .indicators import series_for_chart  # noqa: E402

log = logging.getLogger(__name__)

UP = "#26a69a"
DOWN = "#ef5350"
LEVEL_SUP = "#22c55e"
LEVEL_RES = "#f87171"
PLAN_ENTRY = "#60a5fa"
PLAN_STOP = "#f43f5e"
PLAN_TARGET = "#fbbf24"


def _style():
    market_colors = mpf.make_marketcolors(up=UP, down=DOWN, edge="inherit",
                                          wick="inherit", volume="in")
    try:
        return mpf.make_mpf_style(base_mpf_style=config.CHART_STYLE,
                                  marketcolors=market_colors,
                                  gridstyle=":", gridcolor="#33415555",
                                  facecolor="#0f172a", figcolor="#0f172a",
                                  rc={"font.size": 9, "axes.labelsize": 8,
                                      "axes.edgecolor": "#334155",
                                      "text.color": "#e2e8f0",
                                      "axes.labelcolor": "#cbd5e1",
                                      "xtick.color": "#94a3b8",
                                      "ytick.color": "#94a3b8"})
    except Exception:
        return "charles"


def render(symbol: str, df: pd.DataFrame, frame_label: str, facts: dict,
           verdict_dict: dict | None = None, bars: int | None = None) -> bytes | None:
    """PNG bytes of the annotated chart, or None if it cannot be drawn.

    Never raises: a chart failure must not cost the user the written analysis.
    """
    try:
        bars = bars or config.CHART_BARS
        if df is None or len(df) < 25:
            return None
        series = series_for_chart(df)
        tail = df.tail(bars).copy()
        idx = tail.index

        for name, s in series.items():
            tail[name] = s.reindex(idx)

        price = float(tail["Close"].iloc[-1])
        plots = []
        has = lambda col: tail[col].notna().any()  # noqa: E731

        if has("ema_fast"):
            plots.append(mpf.make_addplot(tail["ema_fast"], color="#38bdf8", width=1.1, secondary_y=False,
                                          label=f"EMA{config.EMA_FAST}"))
        if has("ema_mid"):
            plots.append(mpf.make_addplot(tail["ema_mid"], color="#a78bfa", width=1.1, secondary_y=False,
                                          label=f"EMA{config.EMA_MID}"))
        if has("ema_slow"):
            plots.append(mpf.make_addplot(tail["ema_slow"], color="#fb923c", width=1.3, secondary_y=False,
                                          label=f"EMA{config.EMA_SLOW}"))
        for col in ("bb_upper", "bb_lower"):
            if has(col):
                plots.append(mpf.make_addplot(tail[col], color="#64748b", width=0.7,
                                              linestyle="--", secondary_y=False))
        if "volume_ma" in tail and has("volume_ma"):
            plots.append(mpf.make_addplot(tail["volume_ma"], panel=1, color="#94a3b8",
                                          width=0.9, secondary_y=False))
        if has("rsi"):
            plots.append(mpf.make_addplot(tail["rsi"], panel=2, color="#22d3ee",
                                          width=1.1, ylabel="RSI", secondary_y=False))
            for level, color in ((70.0, "#ef4444"), (50.0, "#475569"), (30.0, "#22c55e")):
                plots.append(mpf.make_addplot(pd.Series(level, index=idx), panel=2,
                                              color=color, width=0.6, linestyle=":",
                                              secondary_y=False))
        if has("macd"):
            colors = ["#26a69a" if v >= 0 else "#ef5350" for v in tail["macd_hist"].fillna(0)]
            plots.append(mpf.make_addplot(tail["macd_hist"], panel=3, type="bar",
                                          color=colors, alpha=0.6, ylabel="MACD",
                                          secondary_y=False))
            plots.append(mpf.make_addplot(tail["macd"], panel=3, color="#38bdf8",
                                          width=1.0, secondary_y=False))
            plots.append(mpf.make_addplot(tail["macd_signal"], panel=3, color="#f59e0b",
                                          width=1.0, secondary_y=False))

        fig, axes = mpf.plot(
            tail, type="candle", style=_style(), addplot=plots, volume=True,
            volume_panel=1, panel_ratios=(7, 1.6, 2.2, 2.2),
            figsize=(config.CHART_WIDTH, config.CHART_HEIGHT),
            figscale=1.0, tight_layout=True, returnfig=True,
            ylabel="", ylabel_lower="Vol", xrotation=15,
        )
        change = facts.get("change_pct")
        change_txt = f"   {change:+.2f}%" if change is not None else ""
        # No Arabic in the title: matplotlib's bidi pass reorders the numbers
        # next to it ("30.80  +0.98" came out as "0.98+  30.80%").
        fig.suptitle(f"{symbol}   •   {frame_label}   •   {price:,.2f}{change_txt}",
                     color="#f1f5f9", fontsize=13, y=1.005)
        ax = axes[0]
        right = len(tail) - 1

        labels: list[tuple[float, str, str]] = []  # (price, text, color)

        def hline(y, color, text, style="--", width=1.0, alpha=0.9):
            if y is None:
                return
            ax.axhline(y=float(y), color=color, linestyle=style, linewidth=width,
                       alpha=alpha)
            labels.append((float(y), text, color))

        def fit_levels():
            """Widen the price axis so a stop or target just outside the bars'
            range is still visible — otherwise its line and tag fall off-chart."""
            if not labels:
                return
            low, high = ax.get_ylim()
            near = [y for y, _, _ in labels if abs(y - price) <= price * 0.15]
            if not near:
                return
            pad = (high - low) * 0.04
            ax.set_ylim(min(low, min(near) - pad), max(high, max(near) + pad))

        def draw_labels():
            """Right-edge price tags, nudged apart so close levels stay readable."""
            if not labels:
                return
            low, high = ax.get_ylim()
            gap = (high - low) / 34
            placed: list[float] = []
            for price_y, text, color in sorted(labels, key=lambda item: item[0]):
                y = price_y
                while any(abs(y - other) < gap for other in placed):
                    y += gap
                placed.append(y)
                ax.annotate(text, xy=(right, y), xytext=(5, 0),
                            textcoords="offset points", color=color, fontsize=7.5,
                            va="center", ha="left", weight="bold",
                            annotation_clip=False)

        lv = facts.get("levels", {})
        for i, s in enumerate((lv.get("supports") or [])[:config.SHOW_LEVELS]):
            hline(s["price"], LEVEL_SUP, f"S{i + 1} {s['price']:,.2f}", ":", 0.9, 0.75)
        for i, r in enumerate((lv.get("resistances") or [])[:config.SHOW_LEVELS]):
            hline(r["price"], LEVEL_RES, f"R{i + 1} {r['price']:,.2f}", ":", 0.9, 0.75)

        fib = lv.get("fib")
        if fib and config.SHOW_FIB:
            zone = sorted(fib["golden_zone"])
            ax.axhspan(zone[0], zone[1], color="#facc15", alpha=0.07)
            ax.annotate("fib 0.5-0.618", xy=(0, zone[1]), xytext=(3, 2),
                        textcoords="offset points", color="#facc15", fontsize=7)

        if "vwap" in facts:
            hline(facts["vwap"], "#e879f9", f"VWAP {facts['vwap']:,.2f}", "-.", 0.9, 0.8)

        if verdict_dict and verdict_dict.get("entry"):
            hline(verdict_dict["entry"], PLAN_ENTRY,
                  f"ENTRY {verdict_dict['entry']:,.2f}", "-", 1.3)
            hline(verdict_dict.get("stop"), PLAN_STOP,
                  f"STOP {verdict_dict['stop']:,.2f}", "-", 1.3)
            for i, t in enumerate(verdict_dict.get("targets") or []):
                hline(t, PLAN_TARGET, f"TP{i + 1} {t:,.2f}", "-", 1.0, 0.85)

        trend = facts.get("trend", {})
        mom = facts.get("momentum", {})
        vola = facts.get("volatility", {})
        side = (verdict_dict or {}).get("side", "none")
        head = {"long": "LONG BIAS", "short": "SHORT BIAS", "none": "NO TRADE / RANGE"}[side]
        box = [
            f"{head}   conviction {(verdict_dict or {}).get('conviction', 0)}%",
            f"RSI {mom.get('rsi')}   MACD {mom.get('macd_hist')}   ADX {trend.get('adx')}",
            f"ATR {vola.get('atr')} ({vola.get('atr_pct')}%)   %B {vola.get('percent_b')}",
            f"EMA stack {trend.get('ema_stack')}   structure {trend.get('structure')}",
        ]
        if verdict_dict and verdict_dict.get("rr"):
            box.append(f"R:R {verdict_dict['rr']}   risk {verdict_dict.get('risk_pct')}%")
        market_session = facts.get("session") or {}
        if market_session.get("phase"):
            box.append(f"session {market_session['phase']}   {market_session.get('now_et', '')}")
        ax.text(0.006, 0.985, "\n".join(box), transform=ax.transAxes, fontsize=7.6,
                va="top", ha="left", color="#e2e8f0", family="monospace",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#111c33ee",
                          edgecolor="#334155"))
        fit_levels()
        draw_labels()
        ax.legend(loc="lower left", fontsize=7, framealpha=0.25, ncols=3)
        ax.margins(x=0.06)

        buf = io.BytesIO()
        fig.savefig(buf, dpi=config.CHART_DPI, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception:
        log.exception("chart render failed for %s", symbol)
        try:
            plt.close("all")
        except Exception:
            pass
        return None
