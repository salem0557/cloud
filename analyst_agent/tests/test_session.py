import datetime as dt

import pytest

from analyst_agent import session


def _at(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text).replace(tzinfo=session.NY)


@pytest.mark.parametrize("when,phase", [
    ("2026-09-14 10:15", "regular"),   # Monday, mid-session
    ("2026-09-14 09:29", "pre"),
    ("2026-09-14 04:00", "pre"),
    ("2026-09-14 16:00", "after"),
    ("2026-09-14 19:59", "after"),
    ("2026-09-14 22:00", "closed"),
    ("2026-09-14 02:00", "closed"),
    ("2026-09-12 11:00", "weekend"),   # Saturday
    ("2026-09-13 11:00", "weekend"),   # Sunday
    ("2026-07-03 11:00", "holiday"),   # Independence Day (observed)
    ("2026-12-25 11:00", "holiday"),
])
def test_phase(when, phase):
    assert session.state(_at(when))["phase"] == phase


def test_regular_session_counts_down_to_the_close():
    state = session.state(_at("2026-09-14 15:30"))
    assert state["is_open"] is True
    assert state["minutes_to_close"] == 30
    assert "مفتوحة" in state["phase_ar"]


def test_premarket_counts_up_to_the_open():
    assert session.state(_at("2026-09-14 09:00"))["minutes_to_open"] == 30


def test_every_phase_has_arabic_wording():
    for phase, label in session.LABELS.items():
        assert label and phase


def test_fresh_bar_has_no_warning():
    now = dt.datetime.now(dt.timezone.utc)
    fresh = session.bar_freshness(now - dt.timedelta(minutes=10), 15)
    assert fresh["last_bar_age_in_bars"] < 1
    assert "stale_note" not in fresh


def test_stale_bar_is_flagged():
    now = dt.datetime.now(dt.timezone.utc)
    stale = session.bar_freshness(now - dt.timedelta(minutes=300), 15)
    assert stale["last_bar_age_in_bars"] == 20.0
    assert "stale_note" in stale


def test_daily_bar_from_yesterday_is_not_stale():
    now = dt.datetime.now(dt.timezone.utc)
    daily = session.bar_freshness(now - dt.timedelta(hours=20), 1440)
    assert "stale_note" not in daily


def test_missing_timestamp_is_handled():
    assert session.bar_freshness(None, 60) == {}
