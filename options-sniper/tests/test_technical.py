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
