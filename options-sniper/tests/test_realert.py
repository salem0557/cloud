"""A second alert on the same name — but only when it is a different break.

Salem: "كيف افك هذا القيد ليعطيني كسور متكررة اقوى". On 2026-09-08 TSLA broke
at 09:45 and again, harder, hours later; only the first would have reached him
and the second was the better trade.

The lock is not removed, it is made conditional. Removing it outright re-sends
the SAME break every scan — the level barely moves, so the identical setup
clears the gate again ten minutes later.
"""
import datetime
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import state


@pytest.fixture
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(C, "LOCK_FILE", tmp_path / "state.lock")
    monkeypatch.setattr(C, "MAX_ALERTS_PER_DAY", 30)
    monkeypatch.setattr(C, "REALERT", True)
    return tmp_path


def _age(minutes):
    """Backdate the last alert so the cooldown has elapsed."""
    s = state.read()
    for e in s["alerted"].values():
        e["at"] = (datetime.datetime.now()
                   - datetime.timedelta(minutes=minutes)).isoformat(timespec="seconds")
    state.write(s)


# ── The same break must not arrive twice ────────────────────────
def test_the_same_break_ten_minutes_later_is_not_a_second_alert(clean):
    assert state.record_alert("TSLA", level=365.0, direction="call", atr=2.0)
    assert not state.record_alert("TSLA", level=365.0, direction="call", atr=2.0)


def test_a_higher_level_INSIDE_the_cooldown_is_still_refused(clean):
    """One move is one alert. A break that extends within the hour is the same
    trade going well, not a new one."""
    assert state.record_alert("TSLA", level=365.0, direction="call", atr=2.0)
    assert not state.record_alert("TSLA", level=372.0, direction="call", atr=2.0)


def test_a_barely_higher_level_after_the_cooldown_is_still_refused(clean):
    """Beyond the old level by less than REALERT_LEVEL_ATR is drift."""
    assert state.record_alert("TSLA", level=365.0, direction="call", atr=2.0)
    _age(C.REALERT_COOLDOWN_MIN + 5)
    barely = 365.0 + C.REALERT_LEVEL_ATR * 2.0 - 0.2
    assert not state.record_alert("TSLA", level=barely, direction="call", atr=2.0)


# ── A genuinely stronger break does get through ─────────────────
def test_a_higher_level_after_the_cooldown_alerts_again(clean):
    assert state.record_alert("TSLA", level=365.0, direction="call", atr=2.0)
    _age(C.REALERT_COOLDOWN_MIN + 5)
    higher = 365.0 + C.REALERT_LEVEL_ATR * 2.0 + 0.5
    assert state.record_alert("TSLA", level=higher, direction="call", atr=2.0)


def test_a_direction_flip_alerts_immediately(clean):
    """Broke up in the morning, breaks DOWN in the afternoon. That is a
    different trade, not a repeat, and it should not wait out a cooldown."""
    assert state.record_alert("TSLA", level=365.0, direction="call", atr=2.0)
    assert state.record_alert("TSLA", level=360.0, direction="put", atr=2.0)


def test_the_put_side_measures_the_other_way(clean):
    assert state.record_alert("NVDA", level=225.0, direction="put", atr=2.0)
    _age(C.REALERT_COOLDOWN_MIN + 5)
    assert not state.record_alert("NVDA", level=226.0, direction="put", atr=2.0)
    assert state.record_alert("NVDA", level=223.0, direction="put", atr=2.0)


# ── The limits that keep it from becoming spam ──────────────────
def test_one_name_cannot_eat_the_day(clean):
    lvl = 365.0
    sent = 0
    for _ in range(C.MAX_ALERTS_PER_TICKER + 3):
        if state.record_alert("TSLA", level=lvl, direction="call", atr=2.0):
            sent += 1
        _age(C.REALERT_COOLDOWN_MIN + 5)
        lvl += 5.0
    assert sent == C.MAX_ALERTS_PER_TICKER


def test_the_daily_cap_still_binds(clean, monkeypatch):
    monkeypatch.setattr(C, "MAX_ALERTS_PER_DAY", 2)
    assert state.record_alert("A", level=1.0, direction="call", atr=1.0)
    assert state.record_alert("B", level=1.0, direction="call", atr=1.0)
    assert not state.record_alert("C", level=1.0, direction="call", atr=1.0)


# ── Falling back safely ─────────────────────────────────────────
def test_a_caller_that_passes_no_level_keeps_the_old_one_a_day_rule(clean):
    """Any caller not taught the new arguments must get the SAFE behaviour,
    not the permissive one."""
    assert state.record_alert("TSLA")
    assert not state.record_alert("TSLA")


def test_REALERT_off_restores_one_alert_a_day(clean, monkeypatch):
    monkeypatch.setattr(C, "REALERT", False)
    assert state.record_alert("TSLA", level=365.0, direction="call", atr=2.0)
    _age(C.REALERT_COOLDOWN_MIN + 5)
    assert not state.record_alert("TSLA", level=400.0, direction="call", atr=2.0)


def test_it_all_resets_with_the_day(clean):
    import json
    assert state.record_alert("TSLA", level=365.0, direction="call", atr=2.0)
    stale = json.loads(C.STATE_FILE.read_text())
    stale["date"] = "2020-01-01"
    C.STATE_FILE.write_text(json.dumps(stale))
    assert state.record_alert("TSLA", level=365.0, direction="call", atr=2.0)
