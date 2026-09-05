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

    return {
        # scoring inputs
        "broke_level": bool(broke),
        "break_distance_atr": round(max(0.0, distance_atr), 2),
        "volume_ratio": round(vol_ratio, 2),
        "closed_beyond": bool(closed_beyond),
        # message inputs
        "level": round(level, 2),
        "close": round(last["close"], 2),
        "atr": round(a, 2),
        "target": round(target, 2),
        "stop": round(stop, 2),
        "entry_rule": entry_rule,
        "expected_move": round(abs(target - last["close"]), 2),
        "bar_time": last.get("end_time", ""),
        "bars_used": len(window),
    }


def remaining_atr(tech):
    """How much of the measured move is still ahead of price, in ATRs."""
    if not tech or tech["atr"] <= 0:
        return 0.0
    return abs(tech["target"] - tech["close"]) / tech["atr"]


def is_late(tech):
    """True when the breakout has already run to (or past) its target.

    Alerting here is worse than not alerting: Salem buys the top of the move and
    the delta-based profit estimate reads a meaningless single-digit percentage.
    """
    return remaining_atr(tech) < C.MIN_REMAINING_ATR


def confirms(tech):
    """A break worth alerting on: level broken, volume spike, and room left."""
    return (bool(tech)
            and tech["broke_level"]
            and tech["volume_ratio"] >= C.VOLUME_SPIKE_RATIO
            and not is_late(tech))
