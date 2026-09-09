"""Salem: "وش الطرق اللي تزود نسبة الضمان لكن ماتقلل التنبيهات كثير".

Every other way of raising confidence costs signals. This one costs a
request: before the alert goes out, ask the 1m tape whether price is still on
the far side of the level the 15m bar broke.

Measured on NVDA, five sessions: it caught the one signal that went 1.87 ATR
against the entry and never recovered, and kept all three that worked.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import technical


def _tech(direction="call", level=100.0):
    return {"direction": direction, "level": level, "broke_level": True}


def test_price_still_beyond_the_level_is_confirmed():
    assert technical.still_beyond(_tech(), {"close": 100.5}) is True
    assert technical.still_beyond(_tech("put"), {"close": 99.5}) is True


def test_price_back_through_the_level_is_not():
    assert technical.still_beyond(_tech(), {"close": 99.5}) is False
    assert technical.still_beyond(_tech("put"), {"close": 100.5}) is False


def test_the_nvda_trap_is_the_one_it_rejects():
    """2026-09-04 14:15, level 234.65, the next minute closed 234.01.

    MFE +0.01 ATR, MAE +1.87, and it never came back. Every other signal in
    the sample closed on the right side of its level and was kept.
    """
    assert technical.still_beyond(_tech("call", 234.65), {"close": 234.01}) is False
    assert technical.still_beyond(_tech("call", 229.99), {"close": 230.06}) is True
    assert technical.still_beyond(_tech("put", 228.06), {"close": 227.50}) is True
    assert technical.still_beyond(_tech("put", 226.34), {"close": 226.26}) is True


def test_a_missing_bar_is_not_a_veto():
    """UW unreachable must never silently cancel a setup — that is the failure
    mode that produced a whole session of nothing with no line in the log."""
    assert technical.still_beyond(_tech(), None) is None
    assert technical.still_beyond(_tech(), {"close": 0}) is None
    assert technical.still_beyond({"direction": "call"}, {"close": 100.5}) is None
    assert technical.still_beyond(None, {"close": 100.5}) is None


# ── The gate the scanner actually calls ─────────────────────────
def _cand(level=100.0, direction="call"):
    return {"ticker": "NVDA",
            "technical": {"direction": direction, "level": level,
                          "broke_level": True}}


def test_the_scanner_sends_a_break_that_is_still_holding(monkeypatch):
    import config as C
    import scanner
    monkeypatch.setattr(C, "USE_MINUTE_CONFIRM", True)
    monkeypatch.setattr(scanner.uw, "last_closed_minute", lambda t: {"close": 100.5})
    assert scanner.still_holding(_cand()) is True


def test_the_scanner_holds_back_a_break_that_gave_the_level_up(monkeypatch):
    import config as C
    import scanner
    monkeypatch.setattr(C, "USE_MINUTE_CONFIRM", True)
    monkeypatch.setattr(scanner.uw, "last_closed_minute", lambda t: {"close": 99.5})
    assert scanner.still_holding(_cand()) is False


def test_an_unreachable_uw_still_sends(monkeypatch):
    """The failure mode this project has already lived through once: a request
    that fails quietly and takes a whole session of alerts with it."""
    import config as C
    import scanner
    monkeypatch.setattr(C, "USE_MINUTE_CONFIRM", True)
    monkeypatch.setattr(scanner.uw, "last_closed_minute", lambda t: None)
    assert scanner.still_holding(_cand()) is True


def test_the_rule_can_be_turned_off_without_touching_the_code(monkeypatch):
    import config as C
    import scanner
    monkeypatch.setattr(C, "USE_MINUTE_CONFIRM", False)
    def _boom(_t):
        raise AssertionError("no request may be made when the rule is off")
    monkeypatch.setattr(scanner.uw, "last_closed_minute", _boom)
    assert scanner.still_holding(_cand(level=999.0)) is True
