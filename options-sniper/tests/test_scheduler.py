"""The entrypoint Railway actually runs.

This file exists because scheduler.py had no test and shipped a NameError in
its heartbeat: `state.capacity_left()` with `state` never imported. Every
other test passed, the scanner and monitor were correct, and the service was
dead — it crashed on the first hourly heartbeat after the open, restarted,
and crashed again. No alert and no paper trade for a whole session, with
nothing in the chat to say so. These tests run the loop's real tick.
"""
import datetime
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import scheduler


class Spy:
    """Records which jobs a tick dispatched."""

    def __init__(self):
        self.ran = []

    def job(self, name):
        def fn():
            self.ran.append(name)
        return fn


def _wire(monkeypatch, spy, is_open, now):
    monkeypatch.setattr(scheduler.market, "now_et", lambda: now)
    monkeypatch.setattr(scheduler.market, "is_open", lambda *_a: is_open)
    monkeypatch.setattr(scheduler.market, "reason", lambda: "test")
    monkeypatch.setattr(scheduler.mine, "poll_and_apply", spy.job("inbox"))
    monkeypatch.setattr(scheduler.monitor, "watch_mine", spy.job("watch"))
    monkeypatch.setattr(scheduler.monitor, "main", spy.job("monitor"))
    monkeypatch.setattr(scheduler.scanner, "main", spy.job("scanner"))


def _marks():
    return {"scan": None, "monitor": None, "beat": None,
            "watch": None, "open": None, "close": None}


def _et(hour, minute):
    return datetime.datetime(2026, 9, 8, hour, minute)


# ── The bug itself ──────────────────────────────────────────────
def test_heartbeat_with_the_market_open_does_not_raise(monkeypatch):
    """The exact line that took the service down. `state` must be reachable."""
    spy = Spy()
    _wire(monkeypatch, spy, True, _et(10, 0))
    monkeypatch.setattr(scheduler.state, "capacity_left", lambda: 7)
    scheduler.tick(_marks())          # raised NameError before the fix


def test_scheduler_imports_every_module_it_names():
    """A module used but not imported is invisible until the line runs."""
    for name in ("state", "market", "mine", "monitor", "scanner"):
        assert hasattr(scheduler, name), f"scheduler.py uses {name} without importing it"


# ── Dispatch ────────────────────────────────────────────────────
def test_open_market_dispatches_every_job(monkeypatch):
    spy = Spy()
    _wire(monkeypatch, spy, True, _et(10, 0))
    monkeypatch.setattr(scheduler.state, "capacity_left", lambda: 7)
    scheduler.tick(_marks())
    assert set(spy.ran) == {"inbox", "watch", "scanner", "monitor"}


def test_after_the_bell_the_day_is_reported(monkeypatch):
    """Salem, 2026-09-09: "انت ما اعطيتني تقريرك ليوم الامس الورقي و الحقيقي".

    He never got one, on any day. Both daily summaries live inside
    monitor.main() and both refuse to send while the market is open — and the
    closed-market branch used to `return` above the line that calls it. The
    reports were unreachable code.
    """
    spy = Spy()
    _wire(monkeypatch, spy, False, _et(18, 0))
    scheduler.tick(_marks())
    assert spy.ran == ["inbox", "monitor"]


def test_the_day_is_reported_once_and_not_every_tick(monkeypatch):
    spy = Spy()
    marks = _marks()
    _wire(monkeypatch, spy, False, _et(18, 0))
    scheduler.tick(marks)
    spy.ran.clear()
    scheduler.tick(marks)
    assert spy.ran == ["inbox"]


def test_nothing_is_scanned_or_alerted_after_the_bell(monkeypatch):
    """The report is the ONLY thing the closed branch may do."""
    spy = Spy()
    _wire(monkeypatch, spy, False, _et(18, 0))
    scheduler.tick(_marks())
    assert "scanner" not in spy.ran and "watch" not in spy.ran


def test_before_the_bell_there_is_no_day_to_report(monkeypatch):
    """Pre-market: the market is closed, but the session has not happened."""
    spy = Spy()
    _wire(monkeypatch, spy, False, _et(7, 0))
    scheduler.tick(_marks())
    assert spy.ran == ["inbox"]


def test_no_report_on_a_weekend(monkeypatch):
    """`not is_open` is also true all weekend. after_bell() is not."""
    spy = Spy()
    _wire(monkeypatch, spy, False, datetime.datetime(2026, 9, 12, 18, 0))
    scheduler.tick(_marks())
    assert spy.ran == ["inbox"]


def test_a_job_does_not_fire_twice_inside_its_own_window(monkeypatch):
    spy = Spy()
    marks = _marks()
    _wire(monkeypatch, spy, True, _et(10, 0))
    monkeypatch.setattr(scheduler.state, "capacity_left", lambda: 7)
    scheduler.tick(marks)
    spy.ran.clear()
    scheduler.tick(marks)             # same minute, same 10m slot
    assert spy.ran == ["inbox"]       # the inbox is read every tick, by design


def test_the_scanner_fires_again_in_the_next_window(monkeypatch):
    spy = Spy()
    marks = _marks()
    _wire(monkeypatch, spy, True, _et(10, 0))
    monkeypatch.setattr(scheduler.state, "capacity_left", lambda: 7)
    scheduler.tick(marks)
    spy.ran.clear()
    monkeypatch.setattr(scheduler.market, "now_et", lambda: _et(10, 10))
    scheduler.tick(marks)
    assert "scanner" in spy.ran


# ── Nothing may take the loop down ──────────────────────────────
def test_a_failing_job_does_not_stop_the_others(monkeypatch):
    """run() catches per-job failures: one bad scan must not cost the monitor."""
    spy = Spy()
    _wire(monkeypatch, spy, True, _et(10, 0))
    monkeypatch.setattr(scheduler.state, "capacity_left", lambda: 7)

    def boom():
        raise RuntimeError("UW timed out")

    monkeypatch.setattr(scheduler.scanner, "main", boom)
    scheduler.tick(_marks())
    assert "monitor" in spy.ran and "watch" in spy.ran


def test_a_raising_tick_does_not_kill_the_loop(monkeypatch):
    """The outage was a crash OUTSIDE run(). The loop must absorb it."""
    calls = []

    def boom(_marks):
        calls.append(1)
        if len(calls) >= 3:
            scheduler._stop = True
        raise NameError("whatever breaks next")

    monkeypatch.setattr(scheduler, "tick", boom)
    monkeypatch.setattr(scheduler.time, "sleep", lambda _s: None)
    monkeypatch.setattr(scheduler, "_stop", False)
    monkeypatch.setattr(C, "UW_API_KEY", "x")
    monkeypatch.setattr(C, "TELEGRAM_TOKEN", "x")
    monkeypatch.setattr(C, "TELEGRAM_CHAT_ID", "x")
    try:
        assert scheduler.main() == 0
    finally:
        scheduler._stop = False
    assert len(calls) == 3        # it kept going after each crash


def test_missing_credentials_park_instead_of_scanning(monkeypatch):
    monkeypatch.setattr(C, "UW_API_KEY", "")
    monkeypatch.setattr(scheduler, "park", lambda why: ("parked", why))
    monkeypatch.setattr(C, "TELEGRAM_TOKEN", "x")
    monkeypatch.setattr(C, "TELEGRAM_CHAT_ID", "x")
    result = scheduler.main()
    assert result[0] == "parked" and "UW_API_KEY" in result[1]
