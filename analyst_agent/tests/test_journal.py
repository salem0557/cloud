"""The journal turns "it missed" into a measured hit rate."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from analyst_agent import config, journal


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "JOURNAL_ENABLED", True)
    monkeypatch.setattr(config, "JOURNAL_MAX_BARS", 30)


AT = datetime(2026, 9, 13, 10, 0, tzinfo=timezone.utc).isoformat(timespec="seconds")


def _bars(low_to, high_to, n=40, tz="UTC"):
    idx = pd.date_range("2026-09-13 10:00", periods=n, freq="5min", tz=tz)
    return pd.DataFrame({
        "Open": 1.0,
        "High": np.linspace(1.0, high_to, n),
        "Low": np.linspace(1.0, low_to, n),
        "Close": np.linspace(1.0, (high_to + low_to) / 2, n),
        "Volume": 1,
    }, index=idx)


def _plan(side="long", entry=1.0, stop=0.98, targets=(1.10,), conviction=70, rr=5.0):
    return {"side": side, "entry": entry, "stop": stop, "targets": list(targets),
            "conviction": conviction, "rr": rr, "conflicts": []}


def _record(symbol="TEST", **kwargs):
    call = journal.record(symbol=symbol, frame="5m", verdict=_plan(**kwargs))
    call.at = AT
    journal._write([c for c in journal._read() if c.id != call.id] + [call])
    return call


def test_a_flat_verdict_is_not_recorded():
    assert journal.record(symbol="X", frame="5m",
                          verdict={"side": "none", "entry": None, "stop": None}) is None


def test_recording_and_reading_round_trip():
    call = _record("NVDA")
    stored = journal._read()
    assert [c.id for c in stored] == [call.id]
    assert stored[0].symbol == "NVDA" and stored[0].outcome == journal.OPEN


def test_target_before_stop_is_a_win(monkeypatch):
    _record("WIN")
    monkeypatch.setattr(journal.market, "fetch", lambda s, f: _bars(0.99, 1.20))
    changed = journal.evaluate()
    assert [c.outcome for c in changed] == [journal.TARGET]
    assert changed[0].r_multiple == 5.0


def test_stop_before_target_is_a_loss(monkeypatch):
    _record("LOSS")
    monkeypatch.setattr(journal.market, "fetch", lambda s, f: _bars(0.80, 1.05))
    changed = journal.evaluate()
    assert changed[0].outcome == journal.STOP
    assert changed[0].r_multiple == -1.0


def test_short_side_is_judged_mirrored(monkeypatch):
    _record("SHORT", side="short", entry=1.0, stop=1.02, targets=(0.90,))
    monkeypatch.setattr(journal.market, "fetch", lambda s, f: _bars(0.85, 1.01))
    assert journal.evaluate()[0].outcome == journal.TARGET


def test_neither_level_reached_is_undecided(monkeypatch):
    _record("FLAT")
    monkeypatch.setattr(journal.market, "fetch", lambda s, f: _bars(0.995, 1.005))
    changed = journal.evaluate()
    assert changed[0].outcome == journal.UNDECIDED
    assert changed[0].r_multiple is not None


def test_a_call_with_no_bars_yet_stays_open(monkeypatch):
    _record("FRESH")
    empty = _bars(0.99, 1.01).head(0)
    monkeypatch.setattr(journal.market, "fetch", lambda s, f: empty)
    assert journal.evaluate() == []
    assert journal._read()[0].outcome == journal.OPEN


def test_only_bars_after_the_call_count(monkeypatch):
    """Bars printed before the call must not resolve it."""
    call = _record("PAST")
    before = _bars(0.50, 1.50)
    before.index = pd.date_range("2026-09-13 06:00", periods=len(before), freq="5min", tz="UTC")
    monkeypatch.setattr(journal.market, "fetch", lambda s, f: before)
    assert journal.evaluate() == []
    assert journal._read()[0].id == call.id


def test_naive_index_is_handled(monkeypatch):
    """Some frames come back without a timezone; the cut still has to work."""
    _record("NAIVE")
    monkeypatch.setattr(journal.market, "fetch", lambda s, f: _bars(0.99, 1.20, tz=None))
    assert journal.evaluate()[0].outcome == journal.TARGET


def test_resolved_calls_are_not_re_judged(monkeypatch):
    _record("ONCE")
    monkeypatch.setattr(journal.market, "fetch", lambda s, f: _bars(0.99, 1.20))
    assert len(journal.evaluate()) == 1
    assert journal.evaluate() == []


def test_stats_before_anything_is_recorded():
    assert "لا توجد توصيات" in journal.stats()


def test_stats_reports_hit_rate_against_the_needed_rate(monkeypatch):
    _record("WIN")
    monkeypatch.setattr(journal.market, "fetch", lambda s, f: _bars(0.99, 1.20))
    journal.evaluate()
    text = journal.stats()
    assert "نسبة الإصابة: 100%" in text
    assert "التعادل يحتاج" in text
    assert "+5.00R" in text or "+5.0R" in text


def test_stats_can_be_filtered_by_source():
    journal.record(symbol="AUTO", frame="5m", verdict=_plan(), source="auto")
    journal.record(symbol="ASK", frame="5m", verdict=_plan(), source="ask")
    assert "الإجمالي: 1" in journal.stats(source="auto")
    assert "الإجمالي: 2" in journal.stats()


def test_a_torn_line_does_not_lose_the_history():
    _record("GOOD")
    with journal.path().open("a") as handle:
        handle.write("{not json\n")
    assert len(journal._read()) == 1


def test_journal_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(config, "JOURNAL_ENABLED", False)
    assert journal.record(symbol="X", frame="5m", verdict=_plan()) is None


def test_a_call_remembers_where_it_was_posted():
    call = _record("XRP-USD")
    journal.attach_message(call.id, chat_id=-1001, message_id=555, topic_id=1773)
    stored = journal._read()[0]
    assert (stored.chat_id, stored.message_id, stored.topic_id) == (-1001, 555, 1773)


def test_only_resolved_and_posted_calls_are_reported(monkeypatch):
    open_call = _record("OPEN")
    journal.attach_message(open_call.id, -1, 1)
    resolved_unposted = _record("UNPOSTED")
    monkeypatch.setattr(journal.market, "fetch", lambda s, f: _bars(0.99, 1.20))
    journal.evaluate()
    pending = journal.pending_notifications()
    assert {c.symbol for c in pending} == {"OPEN"}      # UNPOSTED has no message


def test_a_reported_call_is_not_reported_twice(monkeypatch):
    call = _record("WIN")
    journal.attach_message(call.id, -1001, 555)
    monkeypatch.setattr(journal.market, "fetch", lambda s, f: _bars(0.99, 1.20))
    journal.evaluate()
    assert len(journal.pending_notifications()) == 1
    journal.mark_notified([call.id])
    assert journal.pending_notifications() == []


def test_undecided_is_quiet_unless_asked(monkeypatch):
    call = _record("FLAT")
    journal.attach_message(call.id, -1001, 555)
    monkeypatch.setattr(journal.market, "fetch", lambda s, f: _bars(0.995, 1.005))
    journal.evaluate()
    assert journal.pending_notifications() == []
    monkeypatch.setattr(config, "FOLLOWUP_UNDECIDED", True)
    assert [c.symbol for c in journal.pending_notifications()] == ["FLAT"]


def test_the_target_message_says_what_to_do():
    call = _record("XRP-USD", side="short", entry=1.341, stop=1.345, targets=(1.324,))
    call.outcome, call.r_multiple = journal.TARGET, 4.25
    text = journal.outcome_message(call)
    assert "تحقق الهدف الأول" in text and "1.324" in text
    assert "الستوب لنقطة الدخول" in text


def test_the_stop_message_says_get_out():
    call = _record("XRP-USD", side="short", entry=1.341, stop=1.345, targets=(1.324,))
    call.outcome, call.r_multiple = journal.STOP, -1.0
    text = journal.outcome_message(call)
    assert "وصل الستوب" in text and "اخرج" in text


def test_outcome_messages_name_the_side():
    call = _record("NVDA", side="long", entry=100.0, stop=98.0, targets=(110.0,))
    call.outcome, call.r_multiple = journal.TARGET, 5.0
    assert "شراء" in journal.outcome_message(call)
