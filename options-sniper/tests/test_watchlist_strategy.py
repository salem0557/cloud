"""Salem's strategy, 2026-09-09, replacing everything before it.

    "١- فقط الشركات التي بالصورة
     ٢- فقط راقب هذه الشركات والتدفقات على عقودها وان كان هنالك اختراق مقاومة
        او كسر دعم مع سيولة في السهم و العقود على فريم 15 دقيقة ترسل لي افضل
        ثلاث عقود حسب ميزانيتي"

Eleven names. Discovery off. The signal is a 15m break with volume in the
stock and the option flow not pointing the other way — not a score.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import scanner
import technical


def _broke(direction="call"):
    return {"broke_level": True, "remaining_atr": 2.0, "atr": 4.0,
            "close": 100.0, "level": 99.0, "target": 105.0, "stop": 95.0,
            "break_distance_atr": 0.3, "volume_ratio": 3.0,
            "closed_beyond": True, "closed_strong": True, "wick_back": False,
            "bar_high": 100.5, "bar_low": 98.0, "opening_range": False}


# ── The universe ────────────────────────────────────────────────
def test_the_watchlist_is_the_twenty_five_names():
    """Salem, 2026-09-09: "ضيف اكبر الشركات سيولة كملها لل25 + مؤشر spx".

    The eleven he picked from the screenshots, plus fourteen ranked by
    MEASURED 30-day average option volume from UW's stock screener.
    """
    assert len(C.WATCHLIST) == 25
    assert len(set(C.WATCHLIST)) == 25, "a duplicate wastes a scan slot"
    # his own eleven, none of them dropped
    for t in ("MU", "TSLA", "AMZN", "GOOGL", "AAPL", "INTC", "NVDA",
              "QQQ", "META", "MSFT", "F"):
        assert t in C.WATCHLIST, f"{t} was his pick and must not be dropped"


def test_spx_is_not_in_the_watchlist():
    """He asked for it. UW cannot serve it.

    Measured 2026-09-09: the candle endpoint answers an index symbol with
    data:[] and is_index:true under this subscription, at 1m and 15m alike.
    No candles means no level, no break and no signal — a name that can never
    produce an alert only costs requests and hides in the log as "no candles".
    SPY is in the list instead: the same index, a tenth of the price, and the
    most liquid options in the market.
    """
    assert "SPX" not in C.WATCHLIST
    assert "SPY" in C.WATCHLIST


def test_discovery_is_off_by_default():
    assert C.WATCHLIST_ONLY is True


def test_nothing_outside_the_watchlist_is_ever_scanned(monkeypatch, tmp_path):
    """No flow feed, no Finviz movers, no surprises. These names only."""
    seen = []
    monkeypatch.setattr(C, "SHORTLIST_FILE", tmp_path / "s.json")
    monkeypatch.setattr(C, "STATE_FILE", tmp_path / "st.json")
    monkeypatch.setattr(C, "LOCK_FILE", tmp_path / "st.lock")
    monkeypatch.setattr(scanner.market, "is_open", lambda *a: True)
    monkeypatch.setattr(scanner.state, "capacity_left", lambda: 30)
    monkeypatch.setattr(scanner.uw, "spent", lambda: "")
    monkeypatch.setattr(scanner.uw, "ticker_flow_alerts", lambda t: [])
    monkeypatch.setattr(scanner, "evaluate",
                        lambda t, f, d=False: seen.append(t) or None)

    def no_feed(*a, **k):
        raise AssertionError("the market-wide flow feed was called")

    monkeypatch.setattr(scanner.uw, "flow_alerts", no_feed)
    monkeypatch.setattr(scanner.finviz, "movers",
                        lambda **k: (_ for _ in ()).throw(
                            AssertionError("Finviz was called")))
    scanner.main()
    assert set(seen) == set(C.WATCHLIST)


def test_a_watchlist_name_with_no_option_flow_is_still_looked_at(
        monkeypatch, tmp_path):
    """"No unusual alerts today" is the NORMAL state of a mega-cap. It is why
    these names were invisible before, and it must not hide the chart."""
    seen = []
    monkeypatch.setattr(C, "SHORTLIST_FILE", tmp_path / "s.json")
    monkeypatch.setattr(C, "STATE_FILE", tmp_path / "st.json")
    monkeypatch.setattr(C, "LOCK_FILE", tmp_path / "st.lock")
    monkeypatch.setattr(scanner.market, "is_open", lambda *a: True)
    monkeypatch.setattr(scanner.state, "capacity_left", lambda: 30)
    monkeypatch.setattr(scanner.uw, "spent", lambda: "")
    monkeypatch.setattr(scanner.uw, "ticker_flow_alerts", lambda t: [])
    monkeypatch.setattr(scanner, "evaluate",
                        lambda t, f, d=False: seen.append(t) or None)
    scanner.main()
    assert "F" in seen and "INTC" in seen


# ── The signal ──────────────────────────────────────────────────
def test_a_break_with_volume_is_the_signal():
    assert technical.is_signal(_broke(), "call", "call")


def test_no_break_is_not_a_signal_however_good_the_flow():
    quiet = _broke()
    quiet["broke_level"] = False
    assert not technical.is_signal(quiet, "call", "call")


def test_a_break_without_volume_is_not_a_signal():
    thin = _broke()
    thin["volume_ratio"] = C.VOLUME_SPIKE_RATIO / 2
    assert not technical.is_signal(thin, "call", "call")


def test_a_break_that_reversed_inside_its_candle_is_not_a_signal():
    trap = _broke()
    trap["closed_strong"] = False
    assert not technical.is_signal(trap, "call", "call")


def test_option_flow_pointing_the_other_way_vetoes_the_break():
    """"مع سيولة في السهم و العقود" — the money has to be on that side."""
    assert not technical.is_signal(_broke(), "put", "call")


def test_unknown_flow_does_not_veto():
    """UW returns nothing for a name with no unusual activity. "No alerts
    today" is not "the money is on the other side"."""
    assert technical.is_signal(_broke(), None, "call")
    assert technical.is_signal(_broke(), "call", None)


# ── The direction comes from the chart, not the tape ────────────
def test_the_break_picks_the_direction_not_the_flow(monkeypatch):
    """A put break must be measured as a put even when the day's option flow
    happens to be call-heavy. Deriving direction from flow made the flow agree
    with itself and read the wrong side of the chart."""
    monkeypatch.setattr(scanner.uw, "candles", lambda *a, **k: ["bar"] * 60)
    monkeypatch.setattr(scanner.uw, "option_chain", lambda t: [{"strike": 1}])
    monkeypatch.setattr(scanner.uw, "news", lambda t: [])
    monkeypatch.setattr(scanner, "best_contract", lambda *a: {})
    monkeypatch.setattr(scanner, "flow_direction", lambda f: "call")

    def analyse(candles, direction, lookback=None):
        t = _broke(direction)
        t["broke_level"] = direction == "put"      # only the PUT side broke
        return t

    monkeypatch.setattr(scanner.technical, "analyse", analyse)
    cand = scanner.evaluate("NVDA", {"premium_usd": 1e6, "sweep_count": 1,
                                     "vol_oi_ratio": 1.0, "call_premium": 1e6,
                                     "put_premium": 0.0, "ask_side_premium": 1e6,
                                     "bid_side_premium": 0.0,
                                     "underlying_price": 100.0})
    assert cand is not None and cand["direction"] == "put"
