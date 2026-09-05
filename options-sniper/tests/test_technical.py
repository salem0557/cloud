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
    """50 flat bars around 100, then a break that closes at `close`."""
    c = flat(50, vol=level_vol)
    c[-1] = {"open": 100.4, "high": close + 0.5, "low": 100.3,
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
def _at(close, direction="call"):
    c = flat(50)
    if direction == "call":
        c[-1] = {"open": 100.4, "high": close + 0.3, "low": 100.3,
                 "close": close, "volume": 5000, "end_time": "last"}
    else:
        c[-1] = {"open": 99.6, "high": 99.7, "low": close - 0.3,
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
