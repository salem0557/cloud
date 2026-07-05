"""Track each alert's real-world return vs SPY, building an actual track
record instead of just claiming the filters work.

Every newly-sent alert is stamped with its price and SPY's price at that
moment. A periodic job later checks back at fixed horizons (default 24h and
72h) and computes alpha = the stock's return minus SPY's return over the
same window. Once every horizon for an entry is settled, its result is
folded into a running aggregate and the raw entry is dropped — so the file
stays small no matter how long the bot runs.

Bullish and bearish signals are tracked in separate summary buckets with an
inverted "win" definition: a bullish signal wins when the stock beat SPY
(alpha > 0); a bearish (overbought/reversal-down) signal wins when the stock
underperformed SPY (alpha < 0, i.e. it fell more / rose less than the market
— the outcome that thesis was calling for).

Pure price arithmetic; no LLM involved.
"""
import json
import logging
import time

import yfinance as yf

from . import config

log = logging.getLogger(__name__)

SPY = "SPY"
MAX_ATTEMPTS = 8       # give up on a horizon after this many failed price fetches
SPY_CACHE_TTL = 300    # seconds; avoid re-fetching SPY on every track_alerts call

_spy_cache = {"price": None, "ts": 0.0}


def _is_win(kind: str, alpha: float) -> bool:
    return alpha > 0 if kind == "bullish" else alpha < 0


def _load() -> dict:
    try:
        with open(config.PERFORMANCE_FILE) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault("summary", {})
    data.setdefault("tracked", [])

    # Migrate the pre-bearish-signals shape: summary used to be flat
    # {horizon: {...}} (only bullish signals existed then); tracked entries
    # had no "kind" field. Both default to "bullish", the only kind that
    # existed at the time.
    summary = data["summary"]
    if summary and "bullish" not in summary and "bearish" not in summary:
        data["summary"] = {"bullish": summary}
    data["summary"].setdefault("bullish", {})
    data["summary"].setdefault("bearish", {})
    for entry in data["tracked"]:
        entry.setdefault("kind", "bullish")

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
    if len(symbols) == 1:
        frames = {symbols[0]: raw}
    else:
        top = raw.columns.get_level_values(0)
        frames = {s: raw[s] for s in symbols if s in top}
    for sym, df in frames.items():
        close = df["Close"].dropna()
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
            "kind": m.kind,
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
        kind = entry.get("kind", "bullish")
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
            bucket = data["summary"].setdefault(kind, {}).setdefault(
                h, {"count": 0, "wins": 0, "sum_alpha": 0.0})
            bucket["count"] += 1
            bucket["sum_alpha"] += alpha
            if _is_win(kind, alpha):
                bucket["wins"] += 1

        if all(c.get("resolved") for c in entry["checks"].values()):
            continue  # every horizon settled -> drop the raw record
        still_pending.append(entry)

    data["tracked"] = still_pending
    _save(data)


def compact_summary(kind: str = "bullish") -> str | None:
    """One-line blurb for embedding directly in every alert of `kind` (the
    full breakdown lives in the dedicated /performance command). Withheld
    until a horizon has at least PERFORMANCE_MIN_SAMPLE resolved signals, so
    a lucky early streak isn't advertised as a real track record."""
    data = _load()
    bucket_by_h = data["summary"].get(kind, {})
    for h in sorted(bucket_by_h, key=int):
        b = bucket_by_h[h]
        if b["count"] >= config.PERFORMANCE_MIN_SAMPLE:
            win_rate = b["wins"] / b["count"] * 100
            if kind == "bullish":
                return (f"📊 سجل الأداء: تفوق على السوق في {win_rate:.0f}% من آخر "
                        f"{b['count']} إشارة (بعد {h} ساعة) — التفاصيل: /performance")
            return (f"📉 سجل الأداء: تفوقت في {win_rate:.0f}% من آخر {b['count']} إشارة "
                    f"(انخفض السهم أكثر من السوق، بعد {h} ساعة) — التفاصيل: /performance")
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
    bullish = data["summary"].get("bullish", {})
    bearish = data["summary"].get("bearish", {})
    has_bullish = any(b["count"] for b in bullish.values())
    has_bearish = any(b["count"] for b in bearish.values())

    if not has_bullish and not has_bearish:
        base = "📊 لا توجد نتائج مؤكدة بعد"
        return f"{base} ({pending} إشارة قيد المتابعة)" if pending else f"{base}."

    lines = []
    if has_bullish:
        lines += _section("📊 سجل أداء إشارات الصعود (السهم مقابل مؤشر SPY):",
                          bullish, "تفوق على السوق في")
    if has_bearish:
        if lines:
            lines.append("")
        lines += _section("📉 سجل أداء إشارات الهبوط (انخفاض السهم مقابل مؤشر SPY):",
                          bearish, "تفوقت في")
    if pending:
        lines.append(f"⏳ {pending} إشارة قيد المتابعة (لم يحن وقت تقييمها بعد)")
    return "\n".join(lines)
