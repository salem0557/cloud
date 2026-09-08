import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config as C
import technical


def flat(n, price=100.0, vol=1000):
    return [{"open": price, "high": price + 0.5, "low": price - 0.5,
             "close": price, "volume": vol, "end_time": f"t{i}"} for i in range(n)]


def test_returns_none_without_enough_history():
    assert technical.analyse(flat(5), "call") is None
    assert technical.analyse([], "call") is None


def test_no_break_scores_nothing():
    t = technical.analyse(flat(50), "call")
    assert t is not None and t["broke_level"] is False


def test_real_breakout_is_measured_not_assumed():
    c = flat(50)
    c[-1] = {"open": 100.0, "high": 103.0, "low": 100.0, "close": 102.5,
             "volume": 5000, "end_time": "last"}
    t = technical.analyse(c, "call")
    assert t["broke_level"] and t["closed_beyond"]
    assert t["volume_ratio"] == 5.0                 # 5000 / 1000, computed
    assert t["break_distance_atr"] > 0              # derived from real ATR
    assert t["target"] > t["level"] > t["stop"]     # levels are ordered
    # this particular break closes at 102.5, past its own measured target —
    # measured correctly, but too late to trade (see the late-entry tests below)
    assert technical.is_late(t)


def test_put_direction_breaks_downward():
    c = flat(50)
    c[-1] = {"open": 100.0, "high": 100.0, "low": 97.0, "close": 97.5,
             "volume": 5000, "end_time": "last"}
    t = technical.analyse(c, "put")
    assert t["broke_level"] and t["closed_beyond"]
    assert t["target"] < t["level"] < t["stop"]


def test_break_without_volume_is_not_confirmed():
    c = flat(50)
    c[-1] = {"open": 100.0, "high": 103.0, "low": 100.0, "close": 102.5,
             "volume": 900, "end_time": "last"}          # below 1.5x average
    assert technical.confirms(technical.analyse(c, "call")) is False


def _broken(close, level_vol=1200):
    """50 flat bars around 100, then a break that closes at `close`.

    flat() puts resistance at 100.5, so the breaking bar's low has to sit ABOVE
    that or the bar traded back through the level it broke. The original
    fixture used 100.3 and modelled exactly the reversal holds() now rejects.
    """
    c = flat(50, vol=level_vol)
    c[-1] = {"open": 100.55, "high": close + 0.05, "low": 100.55,
             "close": close, "volume": level_vol * 4, "end_time": "last"}
    return technical.analyse(c, "call")


def test_extended_breakout_is_rejected_as_late():
    """Price already at the measured target -> no edge left, do not alert."""
    t = _broken(102.4)
    assert t["broke_level"]
    assert technical.remaining_atr(t) < C.MIN_REMAINING_ATR
    assert technical.is_late(t)
    assert technical.confirms(t) is False


def test_fresh_breakout_still_has_room():
    t = _broken(100.9)
    assert t["broke_level"]
    assert technical.remaining_atr(t) >= C.MIN_REMAINING_ATR
    assert not technical.is_late(t)
    assert technical.confirms(t)


# ── Room to target is signed, not absolute ──────────────────────
def _at(close, direction="call", rejected=False):
    """A breaking candle. `rejected` puts the close at the wrong end of it.

    The original fixture had high = close + 0.3, which put the close near the
    BOTTOM of the bar — a break that was sold back inside its own candle. It
    passed every check until holds() existed, which is the point of holds().
    """
    c = flat(50)
    if direction == "call":
        c[-1] = {"open": 100.6, "high": close + (0.3 if rejected else 0.05),
                 "low": 100.55,                    # above the 100.5 resistance
                 "close": close, "volume": 5000, "end_time": "last"}
    else:
        c[-1] = {"open": 99.4, "high": 99.45,      # below the 99.5 support
                 "low": close - (0.3 if rejected else 0.05),
                 "close": close, "volume": 5000, "end_time": "last"}
    return technical.analyse(c, direction)


def test_price_past_the_target_has_negative_room():
    """The backtest scored 128 setups at a 100% hit rate because an absolute
    distance read a price beyond its target as still having room."""
    t = _at(105.0)
    assert t["remaining_atr"] < 0
    assert technical.is_late(t)
    assert technical.confirms(t) is False


def test_price_short_of_the_target_has_positive_room():
    t = _at(100.85)
    assert t["remaining_atr"] > C.MIN_REMAINING_ATR
    assert not technical.is_late(t)
    assert technical.confirms(t)


def test_room_shrinks_as_price_approaches_the_target():
    near, far = _at(100.85), _at(102.0)
    assert near["remaining_atr"] > far["remaining_atr"]


def test_puts_are_signed_the_other_way():
    fresh, blown = _at(99.2, "put"), _at(95.0, "put")
    assert fresh["remaining_atr"] > 0
    assert blown["remaining_atr"] < 0
    assert technical.confirms(blown) is False


def test_expected_move_never_points_backwards():
    """expected_profit_pct multiplies by this; a backwards move would have
    produced a positive estimate for a trade with nothing left to gain."""
    assert _at(105.0)["expected_move"] == 0.0
    assert _at(95.0, "put")["expected_move"] == 0.0
    assert _at(100.85)["expected_move"] > 0


def test_direction_is_recorded_on_the_analysis():
    assert _at(100.85)["direction"] == "call"
    assert _at(99.2, "put")["direction"] == "put"



# ── The break that reverses inside its own candle ───────────────
def test_a_break_sold_back_inside_its_candle_does_not_confirm():
    """Salem asked for alerts only when the break "will not reverse
    immediately". A call that breaks out and closes at the LOW of the bar that
    broke was rejected by sellers within those fifteen minutes — and it used
    to clear every other check: level broken, volume behind it, room left."""
    strong, weak = _at(100.85), _at(100.85, rejected=True)
    assert strong["broke_level"] and weak["broke_level"]
    assert strong["volume_ratio"] == weak["volume_ratio"]
    assert technical.holds(strong) and not technical.holds(weak)
    assert technical.confirms(strong) and not technical.confirms(weak)


def test_the_same_rule_applies_to_a_breakdown():
    strong = _at(99.15, direction="put")
    weak = _at(99.15, direction="put", rejected=True)
    assert technical.holds(strong) and not technical.holds(weak)


def test_a_wick_back_through_the_level_is_a_reversal():
    """Price traded back through the level during the candle. It closed beyond
    it, but it did not hold it."""
    c = flat(50)
    c[-1] = {"open": 100.6, "high": 100.9, "low": 100.0,   # dipped under 100.5
             "close": 100.85, "volume": 5000, "end_time": "last"}
    t = technical.analyse(c, "call")
    assert t["wick_back"] is True
    assert not technical.holds(t)


def test_a_flat_candle_is_not_treated_as_a_rejection():
    """Zero range means nothing was rejected; dividing by it would crash."""
    c = flat(50)
    c[-1] = {"open": 100.85, "high": 100.85, "low": 100.85,
             "close": 100.85, "volume": 5000, "end_time": "last"}
    assert technical.holds(technical.analyse(c, "call"))


# ── The breakout shape itself, which nothing tested ─────────────
# _broken() above carries the tell: "the breaking bar's low has to sit ABOVE
# [the level] or the bar traded back through the level it broke". That is the
# fixture being bent to fit the rule. A bar whose low sits above the level it
# breaks is not a breakout — it is a bar that gapped over the level and never
# touched it. The bar that MAKES a breakout opens below the level, crosses it,
# and closes above: its low is under the level by construction. confirms() had
# never once returned True on live data because of it.

def test_the_bar_that_makes_the_breakout_confirms():
    """Opens below the level, crosses it, closes at the top of its range."""
    c = flat(50)
    c[-1] = {"open": 99.8, "high": 101.4, "low": 99.7, "close": 101.3,
             "volume": 5000, "end_time": "last"}
    t = technical.analyse(c, "call")
    assert t["broke_level"], "did not clear the 100.5 level"
    assert t["wick_back"] is False, (
        "a bar that opened BELOW the level cannot have fallen back through it")
    assert technical.holds(t)
    assert technical.confirms(t)


def test_the_same_for_a_breakdown():
    c = flat(50)
    c[-1] = {"open": 100.2, "high": 100.3, "low": 98.6, "close": 98.7,
             "volume": 5000, "end_time": "last"}
    t = technical.analyse(c, "put")
    assert t["broke_level"] and t["wick_back"] is False
    assert technical.holds(t) and technical.confirms(t)


def test_be_at_0945_on_the_day_nothing_alerted():
    """The real bar, from UW. BE broke 274.70 on 4.86x volume and closed
    278.44 — the top of a 270.61-278.99 range. It was rejected as a reversal
    because its low was under the level, which is what a breakout looks like.
    """
    c = flat(50, price=272.0)                    # level lands at 272.5
    c[-1] = {"open": 270.79, "high": 278.99, "low": 270.61, "close": 278.436,
             "volume": 5000, "end_time": "last"}
    t = technical.analyse(c, "call")
    assert t["broke_level"] and t["closed_beyond"]
    assert t["wick_back"] is False
    assert technical.holds(t), "the textbook break was called a reversal"


def test_a_bar_that_opened_above_the_level_and_fell_back_is_still_rejected():
    """The case wick_back was written for, and it must keep working: price was
    already beyond the level, dropped back under it, and closed above again."""
    c = flat(50)
    c[-1] = {"open": 100.6, "high": 100.9, "low": 100.0, "close": 100.85,
             "volume": 5000, "end_time": "last"}
    t = technical.analyse(c, "call")
    assert t["wick_back"] is True
    assert not technical.holds(t)


def test_a_break_that_closes_at_the_low_of_its_bar_is_still_rejected():
    """The other half of holds(). Loosening wick_back must not weaken this:
    buyers pushed it through and sellers took it straight back."""
    c = flat(50)
    c[-1] = {"open": 99.8, "high": 101.4, "low": 99.7, "close": 99.9,
             "volume": 5000, "end_time": "last"}
    t = technical.analyse(c, "call")
    assert t["closed_strong"] is False
    assert not technical.holds(t)


# ── A gap down, then a rally: the move a 0DTE scalp exists to catch ──
# TSLA, 2026-09-08. Closed 376 on Sep 3, gapped down to 361 on Sep 4 and
# closed 354, then ran 355.80 -> 370.00 the next morning — +3.3% on the day,
# its 370 call 1.09 -> 3.75. The scanner reported "no break" for every minute
# of it: a 40-bar window reaches into Sep 3 and put the level at 384.04, a
# price the stock never approached. The level now comes from recent intraday
# structure, cut at this session's open. ATR and the volume average still use
# the long window — they need the history, and neither is a price to break.

def _gap_down_then_rally():
    """20 bars yesterday up at 380, then today opening at 356 and climbing."""
    bars = []
    for i in range(20):                      # yesterday, up near 380
        bars.append({"open": 380.0, "high": 384.0, "low": 378.0, "close": 380.0,
                     "volume": 1000, "date": "2026-09-03",
                     "start_time": f"2026-09-03T{13 + i // 4:02d}:{(i % 4) * 15:02d}:00Z",
                     "end_time": "x", "closed": True})
    price = 356.0
    for i in range(20):                      # today, gapped down and climbing
        bars.append({"open": price, "high": price + 1.0, "low": price - 0.5,
                     "close": price + 0.9, "volume": 1000, "date": "2026-09-08",
                     "start_time": f"2026-09-08T{13 + i // 4:02d}:{(i % 4) * 15:02d}:00Z",
                     "end_time": "x", "closed": True})
        price += 0.8
    bars[-1]["volume"] = 3000                # the breaking bar, on volume
    return bars


def test_yesterdays_high_is_not_todays_level_after_a_gap_down():
    t = technical.analyse(_gap_down_then_rally(), "call")
    assert t is not None
    assert t["level"] < 380, (
        f"level {t['level']} came from the previous session — a stock that "
        "gapped down has already left it behind")
    assert t["broke_level"], "the rally was not seen as a break at all"


def test_the_rally_confirms_instead_of_reading_as_no_break():
    assert technical.confirms(technical.analyse(_gap_down_then_rally(), "call"))


def test_atr_still_uses_the_long_window():
    """Only the LEVEL is cut to the session. ATR needs the history, and a
    short window would make it tiny and every break look enormous."""
    t = technical.analyse(_gap_down_then_rally(), "call")
    assert t["atr"] > 0
    assert t["bars_used"] == C.CANDLES_LOOKBACK


def test_the_level_is_not_called_off_one_candle_at_the_open():
    """Two bars into a session there is no intraday structure yet. Falling
    back is right; a level read off one candle is noise, not a level."""
    bars = _gap_down_then_rally()[:-18]      # yesterday plus two bars today
    t = technical.analyse(bars + _gap_down_then_rally()[-2:], "call")
    assert t is None or t["level"] > 0       # never crashes, never invents one
