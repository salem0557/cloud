"""The entry rules Salem's previous system used, made testable.

That system fired rarely and, on his account, won three of five. Five trades
cannot separate skill from luck — at a losing 40% base rate, three or better
happens about a third of the time by chance — so this is not an attempt to
reproduce a proven edge. It is an attempt to test three mechanisms that system
had and our measurement excluded by construction:

  UNIVERSE     it traded only SPY/QQQ/IWM/NVDA/TSLA (and SPX). Their 0DTE
               contracts quote 1-3% wide; the population we measured quoted
               10-25%. At a 2% spread a contract has to travel +43% for the
               trade to net +40%; at 15% it has to travel +63%. That difference
               alone can decide the result.

  NO CHASING   it refused any entry more than 0.30 ATR from the breakout level.
               Our backtest entered at EVERY minute of the session, including
               deep into a move that had already happened.

  AGREEMENT    four independent reads — trend, momentum, VWAP side and
               structure — had to agree at least 3 of 4, and it stayed silent
               rather than send a weak signal.

Nothing here is scored, weighted, or blended into a confidence number. Each
rule is a gate that either passes or does not, so a run can say which gate did
the work.
"""
import statistics

import config as C
import uw


def ema(values, periods):
    """Exponential moving average, seeded on the first value."""
    if not values:
        return 0.0
    k = 2 / (periods + 1)
    out = values[0]
    for v in values[1:]:
        out = (v - out) * k + out
    return out


def rsi(closes, period=14):
    """Wilder's RSI. None when there is not enough history."""
    if len(closes) <= period:
        return None
    deltas = [b - a for a, b in zip(closes, closes[1:])]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0 if avg_g > 0 else None
    return 100 - 100 / (1 + avg_g / avg_l)


def vwap(bars):
    """Volume-weighted average price over the bars given.

    Intraday traders read it as the line institutions are measured against:
    above it buyers are in control of the session, below it sellers are. The
    previous system used it as one of its four votes; ours used nothing like it.
    """
    num = den = 0.0
    for b in bars:
        typical = (b["high"] + b["low"] + b["close"]) / 3
        vol = b.get("volume") or 0
        num += typical * vol
        den += vol
    return num / den if den else (bars[-1]["close"] if bars else 0.0)


def atr(bars, period=14):
    """Simple ATR over the last `period` true ranges."""
    if len(bars) < 2:
        return 0.0
    trs = [max(b["high"] - b["low"], abs(b["high"] - a["close"]),
               abs(b["low"] - a["close"]))
           for a, b in zip(bars, bars[1:])]
    w = trs[-period:]
    return sum(w) / len(w) if w else 0.0


def signal(bars, i, lookback=15):
    """Is bar `i` a real breakout or breakdown, and who agrees with it?

    "Real" is stricter than a close beyond a level: the bar has to clear the
    highest high of the previous `lookback` bars AND the immediately preceding
    bar's high, with the fast average above the slow one and RSI in a band that
    excludes the exhausted end. That last condition is what stops the rule from
    buying the top of a move that is already over.
    """
    if i < lookback + 1:
        return None
    prior = bars[:i]
    bar = bars[i]
    price = bar["close"]
    window = prior[-lookback:]
    resistance = max(b["high"] for b in window)
    support = min(b["low"] for b in window)
    closes = [b["close"] for b in bars[:i + 1]]
    fast, slow = ema(closes[-30:], 9), ema(closes[-30:], 21)
    r = rsi(closes)
    if r is None:
        return None
    a = atr(bars[:i + 1])
    if a <= 0:
        return None
    session = [b for b in bars[:i + 1] if b.get("date", "") == bar.get("date", "")]
    vw = vwap(session or bars[:i + 1])

    direction = level = None
    if (price >= resistance and price > prior[-1]["high"]
            and fast > slow and 48 <= r <= 72):
        direction, level = "call", resistance
    elif (price <= support and price < prior[-1]["low"]
          and fast < slow and 28 <= r <= 52):
        direction, level = "put", support
    if not direction:
        return None

    up = direction == "call"
    votes = {
        "trend": (fast > slow) if up else (fast < slow),
        "momentum": (50 <= r <= 70) if up else (30 <= r <= 50),
        "vwap": (price >= vw) if up else (price <= vw),
        "structure": (price >= resistance) if up else (price <= support),
    }
    avg_vol = statistics.mean([b.get("volume") or 0 for b in window]) or 0
    return {
        "direction": direction,
        "level": level,
        "atr": a,
        "close": price,
        "rsi": r,
        "vwap": vw,
        "votes": votes,
        "agree": sum(1 for v in votes.values() if v),
        "chase_atr": abs(price - level) / a,
        "volume_ratio": (bar.get("volume") or 0) / avg_vol if avg_vol else 0.0,
    }


def gate(sig, minute_et):
    """Does this signal clear every gate? -> (ok, reason it did not).

    Order matters only for the message: the first failure is the one reported,
    and the cheapest checks come first so the reason names the real obstacle.
    """
    if not sig:
        return False, "no breakout"
    if sig["agree"] < C.MIN_AGREEMENT:
        return False, f"committee split ({sig['agree']}/4)"
    if sig["chase_atr"] > C.MAX_CHASE_ATR:
        return False, f"chasing ({sig['chase_atr']:.2f} ATR past the level)"
    window = time_window(minute_et)
    if window is None:
        return False, "outside the session"
    return True, window


def time_window(minute_et):
    """Which part of the session this minute falls in, or None if closed.

    The previous system demanded a higher score at the open and in the last
    hour, and treated the midday stretch as chop. The windows are kept; what is
    dropped is the score, because a threshold on an invented confidence number
    is not a filter, it is a decoration.
    """
    if not minute_et or len(minute_et) < 5:
        return None
    try:
        h, m = int(minute_et[:2]), int(minute_et[3:5])
    except ValueError:
        return None
    t = h + m / 60
    for start, end, name in C.SESSION_WINDOWS:
        if start <= t < end:
            return name
    return None


def session_bars(ticker, date):
    """The 15m bars of one session, oldest first."""
    bars = uw.candles(ticker, candle_size="15m", timeframe="5D", limit=500,
                      end_date=date)
    day = date[:10] if date else None
    if not day:
        return bars
    return [b for b in bars if (b.get("start_time") or "")[:10] == day]
