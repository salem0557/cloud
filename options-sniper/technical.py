"""15-minute breakout analysis — computed from real candles, never assumed.

This module replaces the hard-coded placeholder that used to sit in scanner.py:

    tech = {"broke_level": ticker in movers, "break_distance_atr": 0.5,
            "volume_ratio": 2.0, "closed_beyond": False}

`0.5` and `2.0` were invented constants. Every ticker that happened to appear in
the Finviz mover list scored 19/30 on technicals regardless of what price did,
and every ticker that did not scored 0 — which capped the total at 70 and made
the 85 threshold unreachable. Both numbers now come from the candles.
"""
import config as C


def true_range(cur, prev):
    return max(
        cur["high"] - cur["low"],
        abs(cur["high"] - prev["close"]),
        abs(cur["low"] - prev["close"]),
    )


def atr(candles, period=None):
    """Wilder-style ATR over the last `period` completed bars."""
    period = period or C.ATR_PERIOD
    if len(candles) < 2:
        return 0.0
    trs = [true_range(candles[i], candles[i - 1]) for i in range(1, len(candles))]
    window = trs[-period:]
    return sum(window) / len(window) if window else 0.0


def analyse(candles, direction, lookback=None):
    """Measure the 15m frame for one ticker.

    candles   ascending, regular hours, from uw.candles()
    direction 'call' (looking for a break UP) or 'put' (break DOWN)

    Returns a dict with the scoring inputs (broke_level, break_distance_atr,
    volume_ratio, closed_beyond) AND the trade levels the alert message needs
    (level, target, stop, entry_rule). Returns None when there is not enough
    data — the caller must treat that as NO_TRADE, not as a zero score.
    """
    lookback = lookback or C.CANDLES_LOOKBACK
    if not candles or len(candles) < max(lookback, C.ATR_PERIOD + 2):
        return None

    window = candles[-lookback:]
    prior, last = window[:-1], window[-1]

    a = atr(window, C.ATR_PERIOD)
    if a <= 0:
        return None

    avg_vol = sum(c["volume"] for c in prior) / len(prior)
    vol_ratio = (last["volume"] / avg_vol) if avg_vol > 0 else 0.0

    if direction == "call":
        level = max(c["high"] for c in prior)
        broke = last["high"] > level
        closed_beyond = last["close"] > level
        distance_atr = (last["close"] - level) / a
        target = level + C.TARGET_ATR_MULT * a
        stop = level - C.STOP_ATR_MULT * a
        entry_rule = f"إغلاق شمعة 15د فوق ${level:.2f}"
    else:
        level = min(c["low"] for c in prior)
        broke = last["low"] < level
        closed_beyond = last["close"] < level
        distance_atr = (level - last["close"]) / a
        target = level - C.TARGET_ATR_MULT * a
        stop = level + C.STOP_ATR_MULT * a
        entry_rule = f"إغلاق شمعة 15د تحت ${level:.2f}"

    # Room left toward the target, SIGNED by direction. An absolute distance
    # reads a price that has already blown past its target as "still 1 ATR to
    # go", which is how 128 setups scored a 100% hit rate in the backtest: the
    # simulated entry opened beyond the target and registered an instant win.
    # Live it is worse — an alert whose target sits behind the price, with an
    # expected profit computed from a move pointing backwards.
    if direction == "call":
        remaining = (target - last["close"]) / a
    else:
        remaining = (last["close"] - target) / a

    return {
        # scoring inputs
        "broke_level": bool(broke),
        "direction": direction,
        "remaining_atr": round(remaining, 2),
        "break_distance_atr": round(max(0.0, distance_atr), 2),
        "volume_ratio": round(vol_ratio, 2),
        "closed_beyond": bool(closed_beyond),
        # message inputs
        "level": round(level, 2),
        "close": round(last["close"], 2),
        "atr": round(a, 2),
        "bar_high": round(last["high"], 2),
        "bar_low": round(last["low"], 2),
        "closed_strong": _closed_strong(last, direction),
        "wick_back": _wick_back(last, level, direction),
        "target": round(target, 2),
        "stop": round(stop, 2),
        "entry_rule": entry_rule,
        "expected_move": round(max(0.0, remaining) * a, 2),
        "bar_time": last.get("end_time", ""),
        "bars_used": len(window),
    }


def _closed_strong(bar, direction, third=1/3):
    """Did the candle close in the third of its range that agrees with it?

    A call breaking out and then closing at the BOTTOM of its own candle was
    rejected inside the very bar that broke — buyers pushed it up and sellers
    took it straight back. That is the false break Salem asked to filter, and
    it is answerable from the breaking candle alone, without waiting a second
    bar and arriving late.
    """
    rng = bar["high"] - bar["low"]
    if rng <= 0:
        return True                       # a doji-flat bar; nothing to reject
    pos = (bar["close"] - bar["low"]) / rng
    return pos >= 1 - third if direction == "call" else pos <= third


def _wick_back(bar, level, direction):
    """Did price trade back through the level inside the breaking candle?"""
    if direction == "call":
        return bar["low"] < level
    return bar["high"] > level


def holds(tech):
    """True when the break did not reverse inside its own candle.

    Two conditions, both read off the bar that broke: it closed in the third of
    its range that agrees with the direction, and it did not trade back through
    the level. Waiting for a second candle to confirm would be stronger and
    would also cost 15 minutes of a move whose median run to target is three —
    by then the anti-chasing gate would reject the entry anyway.
    """
    if not tech:
        return False
    return bool(tech.get("closed_strong")) and not tech.get("wick_back")


def remaining_atr(tech):
    """How much of the measured move is still ahead of price, in ATRs.

    Negative once price has passed the target — that is a setup with no room
    left, not one with room behind it.
    """
    if not tech:
        return 0.0
    return tech.get("remaining_atr", 0.0)


def is_late(tech):
    """True when the breakout has already run to (or past) its target.

    Alerting here is worse than not alerting: Salem buys the top of the move and
    the delta-based profit estimate reads a meaningless single-digit percentage.
    """
    return remaining_atr(tech) < C.MIN_REMAINING_ATR


def confirms(tech):
    """A break worth alerting on.

    Level broken on a CLOSED candle, volume behind it, room left to the target,
    and — the condition Salem asked for — the break did not reverse inside its
    own candle. A break that closes at the low of the bar that made it is a
    trap, and it used to pass every one of the other four checks.
    """
    return (bool(tech)
            and tech["broke_level"]
            and tech["volume_ratio"] >= C.VOLUME_SPIKE_RATIO
            and holds(tech)
            and not is_late(tech))
