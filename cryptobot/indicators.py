"""Indicators over plain lists of floats.

Deliberately dependency-free: a scalper re-computes these every 30 seconds on
a few hundred candles, where pandas costs more in import weight and per-call
overhead than it saves. Every function returns a list aligned with the input,
using None for bars where the indicator is not yet defined.
"""
from dataclasses import dataclass

Num = float | None


@dataclass
class Candle:
    ts: int          # milliseconds since epoch (open time)
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_ccxt(cls, row: list) -> "Candle":
        ts, o, h, l, c, v = row[:6]
        return cls(int(ts), float(o), float(h), float(l), float(c), float(v or 0.0))


def closes(candles: list[Candle]) -> list[float]:
    return [c.close for c in candles]


def highs(candles: list[Candle]) -> list[float]:
    return [c.high for c in candles]


def lows(candles: list[Candle]) -> list[float]:
    return [c.low for c in candles]


def volumes(candles: list[Candle]) -> list[float]:
    return [c.volume for c in candles]


def fmt_price(p: float) -> str:
    """61,250.00$ for BTC, 0.000012$ for micro-caps."""
    if p >= 1:
        return f"{p:,.2f}$"
    return f"{p:.8f}".rstrip("0").rstrip(".") + "$"


# ----------------------------------------------------------------- averages

def sma(values: list[float], period: int) -> list[Num]:
    out: list[Num] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, len(values)):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def ema(values: list[float], period: int) -> list[Num]:
    """Seeded with the first SMA so short series do not start off biased."""
    out: list[Num] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def stdev(values: list[float], period: int) -> list[Num]:
    out: list[Num] = [None] * len(values)
    if period <= 1 or len(values) < period:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        mean = sum(window) / period
        var = sum((v - mean) ** 2 for v in window) / period
        out[i] = var ** 0.5
    return out


def bollinger(values: list[float], period: int, num_std: float):
    mid = sma(values, period)
    sd = stdev(values, period)
    lower: list[Num] = []
    upper: list[Num] = []
    for m, s in zip(mid, sd):
        if m is None or s is None:
            lower.append(None)
            upper.append(None)
        else:
            lower.append(m - num_std * s)
            upper.append(m + num_std * s)
    return lower, mid, upper


# ---------------------------------------------------------------- momentum

def rsi(values: list[float], period: int = 14) -> list[Num]:
    """Wilder's RSI. 100 when there is no loss in the window, by definition."""
    out: list[Num] = [None] * len(values)
    if len(values) <= period:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9):
    """Returns (macd_line, signal_line, histogram), all input-aligned."""
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    line: list[Num] = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    defined = [v for v in line if v is not None]
    sig_tail = ema(defined, signal)
    sig: list[Num] = [None] * (len(line) - len(sig_tail)) + sig_tail
    hist: list[Num] = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(line, sig)
    ]
    return line, sig, hist


# -------------------------------------------------------------- volatility

def true_range(candles: list[Candle]) -> list[Num]:
    out: list[Num] = [None] * len(candles)
    for i, c in enumerate(candles):
        if i == 0:
            out[i] = c.high - c.low
            continue
        prev_close = candles[i - 1].close
        out[i] = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
    return out


def atr(candles: list[Candle], period: int = 14) -> list[Num]:
    """Wilder-smoothed ATR."""
    tr = true_range(candles)
    out: list[Num] = [None] * len(candles)
    if len(candles) < period + 1:
        return out
    seed = sum(v for v in tr[1:period + 1] if v is not None) / period
    out[period] = seed
    prev = seed
    for i in range(period + 1, len(candles)):
        prev = (prev * (period - 1) + (tr[i] or 0.0)) / period
        out[i] = prev
    return out


def vwap(candles: list[Candle], period: int = 20) -> list[Num]:
    """Rolling VWAP — a session VWAP is meaningless on a 24/7 market."""
    out: list[Num] = [None] * len(candles)
    if len(candles) < period:
        return out
    for i in range(period - 1, len(candles)):
        window = candles[i - period + 1:i + 1]
        vol = sum(c.volume for c in window)
        if vol <= 0:
            out[i] = sum((c.high + c.low + c.close) / 3 for c in window) / period
        else:
            out[i] = sum(((c.high + c.low + c.close) / 3) * c.volume for c in window) / vol
    return out


# --------------------------------------------------------------- structure

def swing_lows(candles: list[Candle], order: int = 2) -> list[int]:
    """Indexes of bars whose low is the strict minimum of +/- `order` bars."""
    out = []
    for i in range(order, len(candles) - order):
        window = [c.low for c in candles[i - order:i + order + 1]]
        if candles[i].low == min(window) and window.count(candles[i].low) == 1:
            out.append(i)
    return out


def swing_highs(candles: list[Candle], order: int = 2) -> list[int]:
    out = []
    for i in range(order, len(candles) - order):
        window = [c.high for c in candles[i - order:i + order + 1]]
        if candles[i].high == max(window) and window.count(candles[i].high) == 1:
            out.append(i)
    return out


def rising_lows(candles: list[Candle], order: int = 2, lookback: int = 40) -> bool:
    """Higher-low structure over the recent window (uptrend skeleton)."""
    recent = candles[-lookback:]
    idx = swing_lows(recent, order)
    if len(idx) < 2:
        return False
    return recent[idx[-1]].low > recent[idx[-2]].low


def falling_highs(candles: list[Candle], order: int = 2, lookback: int = 40) -> bool:
    recent = candles[-lookback:]
    idx = swing_highs(recent, order)
    if len(idx) < 2:
        return False
    return recent[idx[-1]].high < recent[idx[-2]].high


def recent_swing_low(candles: list[Candle], lookback: int = 20) -> float:
    return min(c.low for c in candles[-lookback:])


def recent_swing_high(candles: list[Candle], lookback: int = 20) -> float:
    return max(c.high for c in candles[-lookback:])


def last(series: list[Num]) -> Num:
    """Last defined value of an indicator series, or None."""
    for value in reversed(series):
        if value is not None:
            return value
    return None
