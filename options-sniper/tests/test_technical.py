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
