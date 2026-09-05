"""Historical candles for backtesting — Yahoo first, UW as the fallback.

Salem's point: the backtest does not need real-time data, so it should not be
limited by what a real-time subscription serves. Yahoo gives intraday history
free, does not consume the UW request allowance, and is not capped by a trial
plan's lookback.

The split matters and is deliberate:

  live      uw.candles(). Real-time, which is the whole reason for the
            subscription. Yahoo is delayed ~15 minutes and is useless for
            deciding on a 15-minute breakout as it forms.

  backtest  this module. Delay is irrelevant on data from last month, and
            depth is what matters instead.

Yahoo caps intraday history by interval — roughly 60 days at 15m and far more
at 1h — but the exact ceiling is Yahoo's to change, so nothing here assumes a
number. Every fetch reports the span it actually returned, and the backtest
prints that coverage rather than implying it got what it asked for.
"""
import datetime
import sys

import config as C

_ET = None
try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                                       # pragma: no cover
    pass


class HistoryError(RuntimeError):
    pass


# ── Yahoo ───────────────────────────────────────────────────────
# Yahoo's own vocabulary for a lookback window, longest first. The fetch walks
# down this list until one returns data, so a reduced ceiling degrades to the
# next shorter window instead of returning nothing.
_YF_PERIODS = ["2y", "1y", "6mo", "3mo", "60d", "1mo", "5d"]


def _yahoo(ticker, interval, days):
    try:
        import yfinance as yf
    except ImportError as e:
        raise HistoryError(
            "yfinance is not importable by this interpreter "
            f"({sys.executable}). Either it is not installed, or this is the "
            "system python and the packages are in the venv — run "
            "/opt/venv/bin/python instead.") from e

    wanted = [p for p in _YF_PERIODS if _period_days(p) >= days] or ["60d"]
    tried = []
    for period in wanted + [p for p in _YF_PERIODS if p not in wanted]:
        tried.append(period)
        try:
            df = yf.Ticker(ticker).history(period=period, interval=interval,
                                           auto_adjust=False)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        return _from_dataframe(df, interval)
    raise HistoryError(f"Yahoo returned nothing for {ticker} {interval} "
                       f"(tried {', '.join(tried)})")


def _period_days(p):
    n = int("".join(ch for ch in p if ch.isdigit()) or 0)
    if p.endswith("y"):
        return n * 365
    if p.endswith("mo"):
        return n * 30
    return n


def _from_dataframe(df, interval):
    """DataFrame -> the same candle dicts uw.candles() produces."""
    minutes = _interval_minutes(interval)
    out = []
    for ts, row in df.iterrows():
        start = ts.tz_convert("UTC") if ts.tzinfo else ts.tz_localize("UTC")
        end = start + datetime.timedelta(minutes=minutes)
        close = float(row["Close"])
        if close <= 0 or close != close:                # NaN guard
            continue
        out.append({
            "open": float(row["Open"]), "high": float(row["High"]),
            "low": float(row["Low"]), "close": close,
            "volume": float(row.get("Volume", 0) or 0),
            "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "closed": True,                             # all history is closed
        })
    return sorted(out, key=lambda b: b["start_time"])


def _interval_minutes(interval):
    return {"1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "60m": 60, "1d": 390}.get(interval, 15)


# ── Unusual Whales ──────────────────────────────────────────────
def _uw(ticker, interval, days):
    import uw
    bars, end = [], None
    for _ in range(12):
        page = uw.candles(ticker, candle_size=interval,
                          timeframe=f"{min(days, 365)}D", limit=2500,
                          end_date=end)
        if not page:
            break
        known = {b["start_time"] for b in bars}
        fresh = [b for b in page if b["start_time"] not in known]
        if not fresh:
            break
        bars = sorted(fresh + bars, key=lambda b: b["start_time"])
        oldest = bars[0]["start_time"][:10]
        if span_days(bars) >= days:
            break
        end = (datetime.date.fromisoformat(oldest)
               - datetime.timedelta(days=1)).isoformat()
    if not bars:
        raise HistoryError(f"UW returned no {interval} candles for {ticker}")
    return bars


# ── Public ──────────────────────────────────────────────────────
def fetch(ticker, interval="15m", days=365, source="auto"):
    """-> candle dicts, oldest first. Raises HistoryError if no source works."""
    order = {"yahoo": ["yahoo"], "uw": ["uw"]}.get(source, ["yahoo", "uw"])
    errors = []
    for name in order:
        try:
            bars = (_yahoo if name == "yahoo" else _uw)(ticker, interval, days)
            if bars:
                return bars
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise HistoryError("; ".join(errors) or "no source available")


def span_days(bars):
    if not bars:
        return 0
    try:
        a = datetime.date.fromisoformat(bars[0]["start_time"][:10])
        b = datetime.date.fromisoformat(bars[-1]["start_time"][:10])
    except ValueError:
        return 0
    return (b - a).days


def session_third(end_time):
    """Which third of the US session a bar closed in, in real ET.

    The first version subtracted a fixed 4 hours from UTC, which is right in
    summer and an hour off all winter — enough to file an opening-hour setup
    as midday and blur the base rates the analyst reads.
    """
    try:
        t = datetime.datetime.strptime(end_time, "%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError):
        return "unknown"
    t = t.replace(tzinfo=datetime.timezone.utc)
    et = t.astimezone(_ET) if _ET else t - datetime.timedelta(hours=5)
    if et.hour < 11:
        return "open"
    return "midday" if et.hour < 14 else "close"
