"""One heads-up per name per day, and it must survive a scan.

HWM's watch notice went out twice on 2026-09-08 — 17:01:15 and 17:05:46, one
monitor beat either side of a scan — with identical text. The flag that was
supposed to stop that lived on the shortlist row:

    if ... and not item.get("watch_sent"):
        ...
        item["watch_sent"] = True
        save_json(C.SHORTLIST_FILE, shortlist)

and scanner.main() rebuilds SHORTLIST_FILE from scratch every 10 minutes,
carrying nothing over. So the flag was wiped on every scan and the next
monitor pass re-sent the same message. The reservation now lives in the day's
state, which no scan rewrites.
"""
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

import config as C
import state


@pytest.fixture
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(C, "LOCK_FILE", tmp_path / "state.lock")
    monkeypatch.setattr(C, "SHORTLIST_FILE", tmp_path / "shortlist.json")
    return tmp_path


def test_a_ticker_gets_one_heads_up(clean):
    assert state.record_watch("HWM") is True
    assert state.record_watch("HWM") is False


def test_other_tickers_are_unaffected(clean):
    assert state.record_watch("HWM") is True
    assert state.record_watch("META") is True


def test_a_scan_rewriting_the_shortlist_does_not_reopen_it(clean):
    """The actual failure. The scanner writes this file from scratch; the
    reservation must not live anywhere the scanner can overwrite."""
    assert state.record_watch("HWM") is True
    C.SHORTLIST_FILE.write_text(json.dumps(
        [{"ticker": "HWM", "score": 30.0, "direction": "put"}]))
    assert state.record_watch("HWM") is False, (
        "the scan wiped the reservation and HWM would be sent again")


def test_a_send_that_failed_gives_the_reservation_back(clean):
    """Telegram down must not cost the ticker its one notice for the day."""
    assert state.record_watch("HWM") is True
    state.release_watch("HWM")
    assert state.record_watch("HWM") is True


def test_releasing_a_ticker_that_never_reserved_is_harmless(clean):
    state.release_watch("NOPE")
    assert state.record_watch("NOPE") is True


def test_the_reservation_resets_with_the_day(clean, monkeypatch):
    assert state.record_watch("HWM") is True
    stale = json.loads(C.STATE_FILE.read_text())
    stale["date"] = "2020-01-01"
    C.STATE_FILE.write_text(json.dumps(stale))
    assert state.record_watch("HWM") is True


def test_the_watch_reservation_does_not_consume_an_alert_slot(clean):
    """A heads-up is explicitly not an alert — the message says so."""
    before = state.capacity_left()
    state.record_watch("HWM")
    assert state.capacity_left() == before
