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


def _level_window(candles, prior):
    """-> (bars the level may be read from, is_opening_range).

    The most recent LEVEL_LOOKBACK closed bars, cut at this session's open.

    Cutting at the open is the point. Yesterday's high is not a level today's
    move has to clear — a stock that gapped down and is rallying has already
    left it behind, and measuring against it reports "no break" all day.
    """
    recent = prior[-C.LEVEL_LOOKBACK:] if C.LEVEL_LOOKBACK else prior
    if not recent:
        return [], False
    today = (candles[-1].get("date")
             or (candles[-1].get("start_time") or "")[:10])
    if not today:
        return recent, False
    same = [c for c in recent
            if (c.get("date") or (c.get("start_time") or "")[:10]) == today]
    if len(same) >= C.LEVEL_MIN_BARS:
        return same, False
    # Too early for intraday structure. The opening range — the session's first
    # OPENING_RANGE_BARS bars — is the level instead, which is what lets the
    # 09:45 and 10:00 breaks be seen at all. Below that many bars there is not
    # even a range yet, so fall back to the recent window.
    if C.USE_OPENING_RANGE and len(same) >= C.OPENING_RANGE_BARS:
        return same[:C.OPENING_RANGE_BARS], True
    return recent, False


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

    # The LEVEL is measured on a different, shorter window than the ATR.
    #
    # TSLA, 2026-09-08. It closed 376 on Sep 3, gapped down to 361 on Sep 4 and
    # closed 354, then ran 355.80 -> 370.00 the next morning: +3.3% on the day,
    # and the 370 call went 1.09 -> 3.75. The scanner reported "no break" for
    # every minute of it, because a 40-bar window reaches back into Sep 3 and
    # put the level at 384.04 — a price the stock never approached. A gap down
    # followed by a rally is invisible to a level that spans sessions, and that
    # is precisely the move a 0DTE scalp exists to catch.
    #
    # So the level comes from the recent intraday structure only, and never
    # from before this session's open. ATR and the volume average still use the
    # long window: they need the history, and neither is a price to break.
    lvl_window, opening = _level_window(candles, prior)
    lvl_prior = lvl_window or prior

    if direction == "call":
        level = max(c["high"] for c in lvl_prior)
        broke = last["high"] > level
        closed_beyond = last["close"] > level
        distance_atr = (last["close"] - level) / a
        target = level + C.TARGET_ATR_MULT * a
        stop = level - C.STOP_ATR_MULT * a
        entry_rule = f"إغلاق شمعة 15د فوق ${level:.2f}"
    else:
        level = min(c["low"] for c in lvl_prior)
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
        # True when the level came from the opening range rather than from
        # intraday structure. confirms() asks more of the volume here, and the
        # message is tagged, because the open is a different trade.
        "opening_range": bool(opening),
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
    """Did price fall back THROUGH the level after having been above it?

    Only meaningful for a bar that was already beyond the level when it opened.
    The bar that MAKES a breakout starts below the level and ends above it, so
    its low is under the level by construction — reading that as a reversal
    rejected every real breakout on the very bar that made it.

    Measured on BE, 2026-09-08 09:45 ET: opened 270.79, ran to 278.99, closed
    278.44 — the top of its own range, on 4.86x average volume, through a level
    at 274.70. A textbook break. The old test saw low 270.61 < 274.70 and
    called it a reversal. It did that to every breakout, on both paths, which
    is why confirms() had never once returned True.
    """
    if direction == "call":
        return bar["open"] > level and bar["low"] < level
    return bar["open"] < level and bar["high"] > level


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


def reversal(candles, lookback=None):
    """The FAILED break. -> (direction, tech) or None.

    A bar that pierces support and closes back ABOVE it, in the top third of
    its own range, on volume: sellers pushed through the level and could not
    hold it, and everyone who sold the break is trapped above their entry. The
    snap-back is the trade. Mirrored at the top for puts.

    This is the setup Salem pointed at on the MSFT 495 call — 4.23 -> 1.19 on
    the sell-off, then 1.19 -> 2.30 on the bounce. The breakout rule cannot see
    it: while the stock was making its low it was producing a PUT signal, and
    the bottom he wants is that signal failing.

    Returns the same tech shape analyse() does, so every caller downstream —
    the message, the contract picker, the paper book — needs no special case.
    """
    if not C.USE_REVERSAL:
        return None
    lookback = lookback or C.CANDLES_LOOKBACK
    if not candles or len(candles) < max(lookback, C.ATR_PERIOD + 2):
        return None
    prior, _ = _level_window(candles, candles[-lookback:][:-1])
    if len(prior) < C.LEVEL_MIN_BARS:
        return None
    bar = candles[-1]
    rng = bar["high"] - bar["low"]
    if rng <= 0:
        return None
    avg = sum(c["volume"] for c in prior) / len(prior)
    if avg <= 0 or bar["volume"] / avg < C.REVERSAL_VOLUME_RATIO:
        return None
    pos = (bar["close"] - bar["low"]) / rng
    support = min(c["low"] for c in prior)
    resistance = max(c["high"] for c in prior)

    if (bar["low"] < support and bar["close"] > support
            and pos >= 1 - C.REVERSAL_CLOSE_THIRD):
        direction, level, stop = "call", support, bar["low"]
    elif (bar["high"] > resistance and bar["close"] < resistance
          and pos <= C.REVERSAL_CLOSE_THIRD):
        direction, level, stop = "put", resistance, bar["high"]
    else:
        return None

    tech = analyse(candles, direction, lookback)
    if tech is None:
        return None
    a = tech["atr"]
    up = direction == "call"
    target = level + C.TARGET_ATR_MULT * a if up else level - C.TARGET_ATR_MULT * a
    remaining = (target - bar["close"]) / a if up else (bar["close"] - target) / a
    tech.update({
        "reversal": True,
        "level": round(level, 2),
        # The stop is the wick that failed, not an ATR multiple: if price goes
        # back through it the reclaim did not happen and the idea is simply gone.
        "stop": round(stop, 2),
        "target": round(target, 2),
        "remaining_atr": round(remaining, 2),
        "expected_move": round(max(0.0, remaining) * a, 2),
        "entry_rule": (f"استرجع {level:.2f} بعد اختراقه" if up
                       else f"فشل فوق {level:.2f} وأغلق تحته"),
    })
    return direction, tech


def is_signal(tech, flow_direction=None, direction=None):
    """Salem's rule, stated in his own words on 2026-09-09:

        "ان كان هنالك اختراق مقاومة او كسر دعم مع سيولة في السهم و العقود
         على فريم 15 دقيقة ترسل لي افضل ثلاث عقود"

    A 15m break, volume in the STOCK behind it, and money on that side in the
    OPTIONS. confirms() is the first two — the level broken on a closed candle,
    volume above the average, the break held, and room left to the target. The
    third is the option flow pointing the same way as the break.

    Flow that is unknown does not veto: UW returns nothing for a name with no
    unusual activity, and "no alerts today" is not "the money is on the other
    side". Flow that actively disagrees does veto.
    """
    # A failed break is its own signal and does not have to confirm() — it is
    # by definition a break that did NOT hold.
    if not (tech or {}).get("reversal") and not confirms(tech):
        return False
    if flow_direction and direction and flow_direction != direction:
        return False
    return True


def alert_gate(tech):
    """The score this setup must reach to be SENT.

    A confirmed break is price agreeing, and price is the only input that
    cannot be talked into it. A setup with no break is a forecast built from
    flow and a headline, and it has to clear a much higher bar to be worth
    Salem's attention.
    """
    return C.BREAK_THRESHOLD if confirms(tech) else C.THRESHOLD


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


def has_setup(tech):
    """True when tech describes an entry with a level, a target and room.

    Two shapes qualify: a break of the level, and a reversal — the failed
    break that reclaimed it. Both carry a target, so both can be too late,
    and the room gate has to see both. Before this, a reversal skipped the
    gate entirely because it never sets broke_level.
    """
    return bool(tech) and bool(tech.get("broke_level") or tech.get("reversal"))


def is_near_miss(tech):
    """Broke its level, still has room toward the target — but less than the
    rule demands. Not an alert. A paper test of the rule itself.

    Inside [PAPER_MIN_REMAINING_ATR, MIN_REMAINING_ATR): a break that has
    reached or passed its target has no room worth buying and is never a near
    miss, it is the top.
    """
    if not has_setup(tech):
        return False
    return C.PAPER_MIN_REMAINING_ATR <= remaining_atr(tech) < C.MIN_REMAINING_ATR


def still_beyond(tech, minute_bar):
    """Is the break STILL holding, at 1-minute resolution? -> True/False/None.

    Salem, 2026-09-09, after being told no entry can be guaranteed:
    "وش الطرق اللي تزود نسبة الضمان لكن ماتقلل التنبيهات كثير".

    This is the one answer that costs seconds instead of signals. The 15m bar
    closed beyond the level; the question here is whether price is still on
    that side of it now. It rejects nothing that was ever working — only a
    break that has already given the level back before he has read the alert.

    Measured on NVDA, five sessions, against the 1m tape:

      signal              1m close   holds   MFE     MAE
      09-03 17:45 call     230.06    yes    +0.36   +0.50
      09-04 14:15 call     234.01    NO     +0.01   +1.87   <- the trap
      09-08 14:15 put      227.50    yes    +1.21   +0.00
      09-08 15:45 put      226.26    yes    +0.59   +0.18

    It caught the only trap and kept all three that worked. Four signals is
    not a result — it is the reason the rejected ones go to the paper book
    instead of being thrown away.

    None when the bar is missing or the setup has no level: unknown is not a
    veto.
    """
    if not tech or not minute_bar:
        return None
    level = tech.get("level")
    close = minute_bar.get("close")
    if level is None or not close:
        return None
    return close > level if tech.get("direction") == "call" else close < level


def confirms(tech):
    """A break worth alerting on.

    Level broken on a CLOSED candle, volume behind it, room left to the target,
    and — on an opening-range break — more volume than the rest of the day
    needs, because the first half hour prints moves that do not survive.
    and — the condition Salem asked for — the break did not reverse inside its
    own candle. A break that closes at the low of the bar that made it is a
    trap, and it used to pass every one of the other four checks.
    """
    if not tech:
        return False
    need = (C.OPENING_VOLUME_RATIO if tech.get("opening_range")
            else C.VOLUME_SPIKE_RATIO)
    return (tech["broke_level"]
            # The bar has to CLOSE on the far side of the level. broke_level
            # only asks whether the wick touched it, so without this a bar
            # that pierced the level and closed back inside counted as a
            # break — while the message printed "إغلاق شمعة 15د تحت X" as the
            # entry rule. NVDA 2026-09-08 09:30: level 229.63, low 229.46,
            # close 229.76. The code called it a breakdown; the close was
            # above support, which is the definition of a failed break.
            and tech["closed_beyond"]
            and tech["volume_ratio"] >= need
            and holds(tech)
            and not is_late(tech))
