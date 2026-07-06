"""Track each alert's real-world return vs SPY, building an actual track
record instead of just claiming the filters work.

Every newly-sent alert is stamped with its price and SPY's price at that
moment. A periodic job later checks back at fixed horizons (default 24h and
72h) and computes alpha = the stock's return minus SPY's return over the
same window. Once every horizon for an entry is settled, its result is
folded into a running aggregate and the raw entry is dropped — so the file
stays small no matter how long the bot runs.

A signal wins when the stock beat SPY over that window (alpha > 0).

Pure price arithmetic; no LLM involved.
"""
import json
import logging
import time

import pandas as pd
import yfinance as yf

from . import config

log = logging.getLogger(__name__)

SPY = "SPY"
MAX_ATTEMPTS = 8       # give up on a horizon after this many failed price fetches
SPY_CACHE_TTL = 300    # seconds; avoid re-fetching SPY on every track_alerts call

_spy_cache = {"price": None, "ts": 0.0}


def _load() -> dict:
    try:
        with open(config.PERFORMANCE_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault("summary", {})
    data.setdefault("tracked", [])

    # Migrate the old bullish/bearish-split shape back to a flat
    # {horizon: {...}} now that only one signal direction exists; any
    # still-pending bearish entries are dropped, there's nothing to resolve
    # them against anymore.
    summary = data["summary"]
    if "bullish" in summary or "bearish" in summary:
        data["summary"] = summary.get("bullish", {})
    data["tracked"] = [e for e in data["tracked"] if e.get("kind", "bullish") == "bullish"]
    for entry in data["tracked"]:
        entry.pop("kind", None)

    return data


def _save(data: dict):
    with open(config.PERFORMANCE_FILE, "w") as f:
        json.dump(data, f)


def _fetch_last_prices(symbols: list[str]) -> dict[str, float]:
    """{symbol: latest close}, via a lightweight daily-bar fetch."""
    if not symbols:
        return {}
    try:
        raw = yf.download(tickers=symbols, period="5d", interval="1d",
                          group_by="ticker", auto_adjust=True,
                          threads=True, progress=False)
    except Exception:
        log.exception("Performance price fetch failed")
        return {}
    out: dict[str, float] = {}
    if raw is None or raw.empty:
        return out
    # yfinance returns MultiIndex columns (ticker, field) for a list input
    # regardless of how many tickers are in it — including a list of one
    # (this is the exact same bug already fixed in data.fetch_batch: a
    # "single symbol -> flat columns" special case is wrong and silently
    # crashed every call with exactly one symbol, e.g. _get_spy_price's
    # _fetch_last_prices([SPY])).
    if isinstance(raw.columns, pd.MultiIndex):
        top = raw.columns.get_level_values(0)
        frames = {s: raw[s] for s in symbols if s in top}
    else:
        frames = {symbols[0]: raw}
    for sym, df in frames.items():
        try:
            close = df["Close"].dropna()
        except KeyError:
            continue
        if not close.empty:
            out[sym] = float(close.iloc[-1])
    return out


def _get_spy_price() -> float | None:
    now = time.time()
    if _spy_cache["price"] is not None and now - _spy_cache["ts"] < SPY_CACHE_TTL:
        return _spy_cache["price"]
    price = _fetch_last_prices([SPY]).get(SPY)
    if price is not None:
        _spy_cache["price"] = price
        _spy_cache["ts"] = now
    return price


def track_alerts(matches: list):
    """Register newly-sent alerts for later performance checks."""
    if not config.PERFORMANCE_ENABLED or not matches:
        return
    spy_price = _get_spy_price()
    if spy_price is None:
        log.warning("SPY price unavailable; skipping performance tracking this batch")
        return
    data = _load()
    now = time.time()
    for m in matches:
        data["tracked"].append({
            "symbol": m.symbol,
            "score": m.score,
            "alert_ts": now,
            "alert_price": m.price,
            "spy_price": spy_price,
            "checks": {str(h): {"due_ts": now + h * 3600, "attempts": 0}
                       for h in config.PERFORMANCE_HORIZONS_HOURS},
        })
    _save(data)


def resolve_due():
    """Settle any due horizons and fold fully-resolved entries into the summary."""
    if not config.PERFORMANCE_ENABLED:
        return
    data = _load()
    now = time.time()

    due_symbols = {
        entry["symbol"]
        for entry in data["tracked"]
        for check in entry["checks"].values()
        if not check.get("resolved") and check["due_ts"] <= now
    }
    if not due_symbols:
        return

    prices = _fetch_last_prices(sorted(due_symbols) + [SPY])
    spy_now = prices.get(SPY)

    still_pending = []
    for entry in data["tracked"]:
        price_now = prices.get(entry["symbol"])
        for h, check in entry["checks"].items():
            if check.get("resolved") or check["due_ts"] > now:
                continue
            if price_now is None or spy_now is None:
                check["attempts"] += 1
                if check["attempts"] < MAX_ATTEMPTS:
                    continue
                check["resolved"] = True  # gave up; not counted in the summary
                continue
            alpha = ((price_now / entry["alert_price"] - 1)
                     - (spy_now / entry["spy_price"] - 1))
            check["resolved"] = True
            bucket = data["summary"].setdefault(
                h, {"count": 0, "wins": 0, "sum_alpha": 0.0})
            bucket["count"] += 1
            bucket["sum_alpha"] += alpha
            if alpha > 0:
                bucket["wins"] += 1

        if all(c.get("resolved") for c in entry["checks"].values()):
            continue  # every horizon settled -> drop the raw record
        still_pending.append(entry)

    data["tracked"] = still_pending
    _save(data)


def compact_summary() -> str | None:
    """One-line blurb for embedding directly in every alert (the full
    breakdown lives in the dedicated /performance command). Withheld until a
    horizon has at least PERFORMANCE_MIN_SAMPLE resolved signals, so a lucky
    early streak isn't advertised as a real track record."""
    data = _load()
    for h in sorted(data["summary"], key=int):
        b = data["summary"][h]
        if b["count"] >= config.PERFORMANCE_MIN_SAMPLE:
            win_rate = b["wins"] / b["count"] * 100
            return (f"📊 سجل الأداء: تفوق على السوق في {win_rate:.0f}% من آخر "
                    f"{b['count']} إشارة (بعد {h} ساعة) — التفاصيل: /performance")
    return None


def _section(label: str, bucket_by_h: dict, win_phrase: str) -> list[str]:
    lines = [label]
    for h in sorted(bucket_by_h, key=int):
        b = bucket_by_h[h]
        if not b["count"]:
            continue
        win_rate = b["wins"] / b["count"] * 100
        avg_alpha = b["sum_alpha"] / b["count"] * 100
        lines.append(
            f"  بعد {h} ساعة: {b['count']} إشارة • {win_phrase} {win_rate:.0f}% منها"
            f" • متوسط الفارق {avg_alpha:+.2f}%"
        )
    return lines


def summary_text() -> str:
    """Human-readable (Arabic) track record for the /performance command."""
    data = _load()
    pending = len(data["tracked"])
    has_data = any(b["count"] for b in data["summary"].values())

    if not has_data:
        base = "📊 لا توجد نتائج مؤكدة بعد"
        return f"{base} ({pending} إشارة قيد المتابعة)" if pending else f"{base}."

    lines = _section("📊 سجل أداء الإشارات (السهم مقابل مؤشر SPY):",
                     data["summary"], "تفوق على السوق في")
    if pending:
        lines.append(f"⏳ {pending} إشارة قيد المتابعة (لم يحن وقت تقييمها بعد)")
    return "\n".join(lines)
