"""The early notice, and the strike money is being lifted into.

Salem's goal from the first message: "أركب موجة ارتفاع سعر العقد من أوله" —
be in before the move, not after a confirmed break has already taken 0.3 ATR.

Everything this system has measured an edge on is the confirmed break. So the
early notice exists, because he asked for it and it is his money — and it says
in its own text that it is not the measured signal. These tests hold that line.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import compose
import uw


def rows(*specs):
    """(strike, call_ask, call_bid, put_ask, put_bid) in millions."""
    return [{"strike": s, "call_ask": ca * 1e6, "call_bid": cb * 1e6,
             "put_ask": pa * 1e6, "put_bid": pb * 1e6,
             "call_volume": 1000, "put_volume": 1000}
            for s, ca, cb, pa, pb in specs]


# ── Net, not gross ──────────────────────────────────────────────
def test_the_magnet_is_where_money_is_ACCUMULATED_not_where_volume_is(monkeypatch):
    """On the real NVDA tape the biggest gross strike was 230 at $61M bought —
    and $56M sold against it. Ranking on gross picks a strike being
    distributed; net picks the one being built."""
    monkeypatch.setattr(uw, "strike_flow", lambda t, d=None: rows(
        (230, 61, 56, 0, 0),      # huge gross, net +5
        (250, 12, 2, 0, 0),       # smaller gross, net +10
    ))
    m = uw.magnet_strike("NVDA", "call", 228.0)
    assert m["strike"] == 250.0
    assert m["net_premium"] == 10_000_000


def test_a_strike_being_sold_is_never_the_magnet(monkeypatch):
    monkeypatch.setattr(uw, "strike_flow", lambda t, d=None: rows(
        (235, 53, 63, 0, 0),      # net NEGATIVE — distribution
    ))
    assert uw.magnet_strike("NVDA", "call", 228.0) is None


def test_only_strikes_price_has_not_reached_count(monkeypatch):
    """A strike already in the money is where the move went, not where it is
    going."""
    monkeypatch.setattr(uw, "strike_flow", lambda t, d=None: rows(
        (200, 90, 1, 0, 0),       # far below spot: already in the money
        (240, 10, 1, 0, 0),
    ))
    assert uw.magnet_strike("NVDA", "call", 228.0)["strike"] == 240.0


def test_puts_look_the_other_way(monkeypatch):
    monkeypatch.setattr(uw, "strike_flow", lambda t, d=None: rows(
        (240, 0, 0, 30, 2),       # above spot — not reachable by a put
        (210, 0, 0, 20, 2),
    ))
    assert uw.magnet_strike("NVDA", "put", 228.0)["strike"] == 210.0


def test_share_says_whether_one_strike_owns_the_day(monkeypatch):
    """One strike taking 40% of the net flow reads differently from one
    taking 4%, and the notice drops anything under MIN_MAGNET_SHARE."""
    monkeypatch.setattr(uw, "strike_flow", lambda t, d=None: rows(
        (240, 10, 2, 0, 0), (245, 10, 2, 0, 0),
        (250, 10, 2, 0, 0), (255, 10, 2, 0, 0),
    ))
    assert uw.magnet_strike("NVDA", "call", 228.0)["share"] == 0.25


def test_no_flow_at_all_is_none_not_a_guess(monkeypatch):
    monkeypatch.setattr(uw, "strike_flow", lambda t, d=None: [])
    assert uw.magnet_strike("NVDA", "call", 228.0) is None
    assert uw.magnet_strike("NVDA", "call", 0) is None


def test_a_failed_request_does_not_take_the_monitor_down(monkeypatch):
    def boom(*a, **k):
        raise uw.UWError("503")
    monkeypatch.setattr(uw, "strike_flow", boom)
    assert uw.magnet_strike("NVDA", "call", 228.0) is None


# ── The notice says what it is ──────────────────────────────────
def watch(**over):
    p = {"ticker": "NVDA", "direction": "call", "time_riyadh": "17:20",
         "technical": {"close": 228.40, "level": 229.10, "atr": 1.20},
         "magnet": {"strike": 250.0, "net_premium": 10_132_661, "share": 0.296,
                    "distance_pct": 9.65, "volume": 116944}}
    p.update(over)
    return compose.render_watch(p)


def test_the_notice_says_twice_that_nothing_has_broken_yet():
    text = watch()
    assert "لسه ما كسر" in text
    assert "مو تنبيه دخول" in text


def test_it_names_the_level_and_what_would_confirm():
    """A heads-up without the trigger is just noise on a phone."""
    text = watch()
    assert "المقاومة 229.10" in text and "باقي 0.70$" in text
    assert "إغلاق شمعة 15د فوق 229.10" in text


def test_the_magnet_is_reported_as_positioning_not_prophecy():
    text = watch()
    assert "إضراب 250" in text and "10.1M$" in text and "30%" in text
    assert "مكان رهانهم، مو وعد إنه يوصله" in text


def test_without_a_magnet_the_notice_still_stands():
    text = watch(magnet=None)
    assert "لسه ما كسر" in text and "💰" not in text


def test_a_breakdown_reads_downward():
    text = watch(direction="put", magnet=None,
                 technical={"close": 101.60, "level": 101.20, "atr": 0.90})
    assert "الدعم 101.20" in text and "تحت 101.20" in text


def test_a_watch_notice_never_goes_through_the_composer():
    """A model rewording 'this has not broken yet' into something that sounds
    like a signal would defeat the point of separating the two."""
    text = compose.compose("watch", {
        "ticker": "X", "direction": "call", "time_riyadh": "10:00",
        "technical": {"close": 100.0, "level": 100.5, "atr": 1.0},
        "magnet": None})
    assert "مو تنبيه دخول" in text
