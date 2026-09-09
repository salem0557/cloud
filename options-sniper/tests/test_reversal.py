"""The failed break — the setup Salem pointed at on the MSFT 495 call.

The contract fell 4.23 -> 1.19 as MSFT sold off, then ran 1.19 -> 2.30 (+93%)
on the bounce. "كيف تجعل استراتيجيتنا تعمل لاخذ العقد بالاسفل وبيعه بالاعلى".

The breakout rule cannot see it, and not by accident: while the stock was
making its low it was producing a PUT signal, and the bottom he wants is that
put signal FAILING. So the failed break is its own signal — a bar that pierces
the level and closes back through it, on volume, with the sellers trapped.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import technical


def _tape(last, session_bars=6):
    """40 quiet bars yesterday, then today's session, then `last`."""
    bars = [{"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
             "volume": 1000, "date": "2026-09-08", "end_time": "x",
             "start_time": f"2026-09-08T{13 + i // 4:02d}:{(i % 4) * 15:02d}:00Z",
             "closed": True} for i in range(45)]
    bars += [{"open": 100.0, "high": 100.4, "low": 99.6, "close": 100.0,
              "volume": 1000, "date": "2026-09-09", "end_time": "x",
              "start_time": f"2026-09-09T{13 + i // 4:02d}:{(i % 4) * 15:02d}:00Z",
              "closed": True} for i in range(session_bars)]
    last = dict(last, date="2026-09-09", end_time="x", closed=True,
                start_time="2026-09-09T15:00:00Z")
    return bars + [last]


# ── What a failed breakdown is ──────────────────────────────────
def test_a_pierced_support_reclaimed_on_volume_is_a_call():
    """Support at 99.6. The bar takes 99.0, closes 100.3 at the top of its
    range on triple volume: the sellers who hit 99.0 are trapped."""
    r = technical.reversal(_tape({"open": 99.8, "high": 100.4, "low": 99.0,
                                  "close": 100.3, "volume": 3000}))
    assert r is not None, "the reclaim was not seen"
    direction, tech = r
    assert direction == "call"
    assert tech["reversal"] is True


def test_the_stop_is_the_wick_that_failed_not_an_atr_multiple():
    """If price goes back through that low the reclaim did not happen and the
    idea is simply gone. An ATR stop would sit somewhere with no meaning."""
    _, tech = technical.reversal(_tape({"open": 99.8, "high": 100.4, "low": 99.0,
                                        "close": 100.3, "volume": 3000}))
    assert tech["stop"] == 99.0


def test_a_pierced_resistance_that_fails_is_a_put():
    r = technical.reversal(_tape({"open": 100.2, "high": 101.2, "low": 99.7,
                                  "close": 99.8, "volume": 3000}))
    assert r is not None
    assert r[0] == "put"


# ── What it is not ──────────────────────────────────────────────
def test_a_break_that_HELD_is_not_a_reversal():
    """Closing beyond the level is a breakout. It has its own rule."""
    assert technical.reversal(_tape({"open": 99.8, "high": 100.0, "low": 99.0,
                                     "close": 99.1, "volume": 3000})) is None


def test_a_bar_that_never_pierced_the_level_is_not_a_reversal():
    assert technical.reversal(_tape({"open": 100.0, "high": 100.3, "low": 99.7,
                                     "close": 100.2, "volume": 3000})) is None


def test_a_reclaim_that_closes_weak_is_not_a_reversal():
    """Pierced 99.0 and closed at 99.65 — barely back, in the middle of the
    bar. That is not sellers trapped, that is a bar."""
    assert technical.reversal(_tape({"open": 99.8, "high": 100.4, "low": 99.0,
                                     "close": 99.65, "volume": 3000})) is None


def test_a_reclaim_without_volume_is_not_a_reversal():
    """The reclaim needs buyers behind it, or it is drift."""
    thin = int(1000 * C.REVERSAL_VOLUME_RATIO) - 50
    assert technical.reversal(_tape({"open": 99.8, "high": 100.4, "low": 99.0,
                                     "close": 100.3, "volume": thin})) is None


# ── How it reaches the rest of the system ───────────────────────
def test_a_reversal_is_a_signal_without_having_to_confirm():
    """confirms() asks that the break HELD. A failed break by definition did
    not, so it must not be judged by that rule."""
    _, tech = technical.reversal(_tape({"open": 99.8, "high": 100.4, "low": 99.0,
                                        "close": 100.3, "volume": 3000}))
    assert technical.is_signal(tech)


def test_flow_against_it_still_vetoes():
    _, tech = technical.reversal(_tape({"open": 99.8, "high": 100.4, "low": 99.0,
                                        "close": 100.3, "volume": 3000}))
    assert not technical.is_signal(tech, "put", "call")


def test_the_message_says_it_is_a_failed_break():
    import compose
    _, tech = technical.reversal(_tape({"open": 99.8, "high": 100.4, "low": 99.0,
                                        "close": 100.3, "volume": 3000}))
    out = compose.render_entry({
        "ticker": "MSFT", "score": 61, "direction": "call", "spot": 100.3,
        "technical": tech, "tiers": [], "score_breakdown": {},
        "reasoning": {"links": [], "gaps": []}, "time_riyadh": "16:47:12"})
    assert "🔄" in out and "كسر كاذب" in out


def test_the_paper_book_scores_the_two_setups_apart(monkeypatch, tmp_path):
    """A good setup hidden inside the average of a bad one is not measured.
    The book has to be able to say which of the two earns its place."""
    import paper
    monkeypatch.setattr(paper, "PAPER_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper, "may_open", lambda d: (True, ""))
    _, tech = technical.reversal(_tape({"open": 99.8, "high": 100.4, "low": 99.0,
                                        "close": 100.3, "volume": 3000}))
    tier = {"option_symbol": "X1", "ask": 1.0, "strike": 100, "type": "call",
            "expiry": "2026-09-11", "dte": 2, "cost": 100, "tier": "🟢"}
    pos = paper.record({"ticker": "MSFT", "technical": tech, "tiers": [tier],
                        "direction": "call"})
    assert pos and pos["setup"] == "reversal"

    tier2 = dict(tier, option_symbol="X2")
    pos2 = paper.record({"ticker": "MSFT", "technical": {"broke_level": True},
                         "tiers": [tier2], "direction": "call"})
    assert pos2 and pos2["setup"] == "breakout"


def test_it_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(C, "USE_REVERSAL", False)
    assert technical.reversal(_tape({"open": 99.8, "high": 100.4, "low": 99.0,
                                     "close": 100.3, "volume": 3000})) is None
