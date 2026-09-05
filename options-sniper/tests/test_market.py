import sys, pathlib, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import market


def et(y, m, d, hh, mm):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=market._ET)


def test_regular_session_is_open():
    assert market.is_open(et(2026, 9, 8, 10, 0))     # Tuesday 10:00 ET
    assert market.is_open(et(2026, 9, 8, 9, 30))     # the opening bell
    assert market.is_open(et(2026, 9, 8, 16, 0))     # the closing bell


def test_outside_the_session_is_closed():
    assert not market.is_open(et(2026, 9, 8, 9, 29))   # one minute early
    assert not market.is_open(et(2026, 9, 8, 16, 1))   # one minute late
    assert not market.is_open(et(2026, 9, 8, 4, 0))    # premarket


def test_weekend_is_closed():
    assert not market.is_open(et(2026, 9, 5, 12, 0))   # Saturday
    assert not market.is_open(et(2026, 9, 6, 12, 0))   # Sunday


def test_guard_follows_daylight_saving():
    """09:30 ET is 13:30 UTC in summer and 14:30 UTC in winter. The guard must
    open at the right moment in both, with no seasonal edit."""
    utc = datetime.timezone.utc
    edt_open = datetime.datetime(2026, 7, 7, 13, 30, tzinfo=utc)   # 09:30 EDT
    est_open = datetime.datetime(2026, 1, 6, 14, 30, tzinfo=utc)   # 09:30 EST
    assert market.is_open(edt_open.astimezone(market._ET))
    assert market.is_open(est_open.astimezone(market._ET))
    # 13:30 UTC in January is 08:30 EST — premarket, must be rejected
    too_early = datetime.datetime(2026, 1, 6, 13, 30, tzinfo=utc)
    assert not market.is_open(too_early.astimezone(market._ET))


# ── scheduler slot keys ─────────────────────────────────────────
import scheduler


def test_slot_is_stable_inside_its_window():
    """Two ticks in the same 30-minute window must not scan twice."""
    a = scheduler.slot(et(2026, 9, 8, 10, 0), 30)
    b = scheduler.slot(et(2026, 9, 8, 10, 29), 30)
    assert a == b


def test_slot_changes_at_the_window_boundary():
    a = scheduler.slot(et(2026, 9, 8, 10, 29), 30)
    b = scheduler.slot(et(2026, 9, 8, 10, 30), 30)
    assert a != b


def test_slot_does_not_collide_across_days():
    a = scheduler.slot(et(2026, 9, 8, 10, 0), 30)
    b = scheduler.slot(et(2026, 9, 9, 10, 0), 30)
    assert a != b


def test_five_minute_slots_are_distinct():
    keys = {scheduler.slot(et(2026, 9, 8, 10, m), 5) for m in range(0, 30)}
    assert len(keys) == 6
