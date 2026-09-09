"""The index read that goes to Salem's SPX topic.

Salem, 2026-09-09: "عندي القسم هذا خاص ب spx كيف استفيد منه انك تتابع المؤشر
وتحلله وتشوف اتجاهه؟"

The constraint these tests exist to hold: SPX has NO candles under this UW
subscription (measured — data:[] with is_index:true at every size), so the
report is built from the options side plus SPY's tape, and every number in it
comes from a response. A report that guesses the index price is worse than no
report, because every distance in it is measured from that number.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import spx


def _tide(pairs):
    return [{"timestamp": f"t{i}", "net_call_premium": c,
             "net_put_premium": p, "net_volume": 0}
            for i, (c, p) in enumerate(pairs)]


def _data(**kw):
    d = {"spx": 7673.52, "spy": 765.96, "ratio": 7673.52 / 765.96,
         "at": "22:35", "spy_candles": [],
         "levels": {"call_wall": 7680.0, "put_wall": 7670.0,
                    "gamma_flip": 7694.98, "gamma_magnet": 7675.0},
         "tide": _tide([(10e6, 2e6)] * 13 + [(-85e6, 67e6)])}
    d.update(kw)
    return d


# ── The tide, which is the direction ────────────────────────────
def test_money_moving_to_calls_and_still_rising_reads_up():
    now, before, label = spx._tide_direction(_tide([(1e6, 5e6)] * 13 + [(50e6, 1e6)]))
    assert now > 0 and "صاعد" in label and "يضعف" not in label


def test_money_on_calls_but_fading_is_not_the_same_market():
    """A tide that is positive and falling is a different day from one that is
    positive and rising, and one number cannot tell them apart."""
    now, before, label = spx._tide_direction(_tide([(90e6, 1e6)] * 13 + [(20e6, 1e6)]))
    assert now > 0 and "يضعف" in label


def test_money_moving_to_puts_reads_down():
    now, before, label = spx._tide_direction(_tide([(10e6, 2e6)] * 13 + [(-85e6, 67e6)]))
    assert now < 0 and "هابط" in label


def test_no_tide_is_not_a_direction():
    assert spx._tide_direction([]) is None
    assert "UW ما أعطى" in spx.message(_data(tide=[]))


# ── The report ──────────────────────────────────────────────────
def test_the_report_carries_the_index_price_and_every_wall():
    out = spx.message(_data())
    assert "7,673.52" in out and "765.96" in out
    for wall in ("7,680", "7,670", "7,675", "7,695"):
        assert wall in out, f"{wall} missing from the report"


def test_the_report_says_it_is_not_a_trade():
    assert "مو توصية عقد" in spx.message(_data())


def test_missing_levels_are_said_out_loud_not_left_blank():
    out = spx.message(_data(levels=None))
    assert "ما أعطى مستويات" in out


def test_an_unpriced_index_produces_no_report(monkeypatch):
    """Every distance in the message is measured from the index price. With no
    price there is no smaller report — there is a wrong one."""
    monkeypatch.setattr(spx.uw, "index_close", lambda *a, **k: 0.0)
    monkeypatch.setattr(spx.uw, "spot", lambda *a, **k: 765.96)
    assert spx.read() is None
    assert spx.message() == spx.NO_DATA


def test_nothing_is_sent_when_the_market_is_closed(monkeypatch):
    """The walls move with the session's own flow; a report at 3am describes
    yesterday and reads like today."""
    monkeypatch.setattr(spx.market, "is_open", lambda *a, **k: False)
    monkeypatch.setattr(spx, "message", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no report may be built on a closed market")))
    assert spx.send_report() is False
