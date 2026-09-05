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


def test_actions_cron_window_covers_both_dst_offsets():
    """The workflow runs 13:00-21:59 UTC; the guard must let EDT and EST
    sessions through and reject the hours that only exist in one of them."""
    utc = datetime.timezone.utc
    edt_open = datetime.datetime(2026, 7, 7, 13, 30, tzinfo=utc)   # 09:30 EDT
    est_open = datetime.datetime(2026, 1, 6, 14, 30, tzinfo=utc)   # 09:30 EST
    assert market.is_open(edt_open.astimezone(market._ET))
    assert market.is_open(est_open.astimezone(market._ET))
    # 13:30 UTC in January is 08:30 EST — premarket, must be rejected
    too_early = datetime.datetime(2026, 1, 6, 13, 30, tzinfo=utc)
    assert not market.is_open(too_early.astimezone(market._ET))
