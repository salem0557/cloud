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


def test_heartbeat_slots_are_hourly():
    a = scheduler.slot(et(2026, 9, 5, 10, 0), 60)
    b = scheduler.slot(et(2026, 9, 5, 10, 59), 60)
    c = scheduler.slot(et(2026, 9, 5, 11, 0), 60)
    assert a == b and b != c


# ── The holiday calendar the system did not have ────────────────
def test_labor_day_is_closed():
    """Salem asked whether to wait for Tuesday because Monday is a US holiday.
    The system had no idea: is_open() checked the weekday and the clock and
    nothing else, so it would have scanned all day against Friday's stale
    candles — and stale candles still contain a break."""
    monday = datetime.datetime(2026, 9, 7, 11, 0)
    assert market.is_holiday(monday)
    assert not market.is_open(monday)
    assert market.minutes_to_close(monday) == 0


def test_the_next_session_after_it_is_open():
    assert market.is_open(datetime.datetime(2026, 9, 8, 11, 0))


def test_every_nyse_closure_of_2026_is_known():
    got = sorted(d.isoformat() for d in market.holidays(2026))
    assert got == ["2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
                   "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
                   "2026-11-26", "2026-12-25"]


def test_good_friday_moves_with_easter():
    """Tabulating holidays would expire; Good Friday is two days before
    Gregorian Easter and is computed."""
    assert market.easter(2026) == datetime.date(2026, 4, 5)
    assert market.easter(2027) == datetime.date(2027, 3, 28)
    assert datetime.date(2026, 4, 3) in market.holidays(2026)
    assert datetime.date(2027, 3, 26) in market.holidays(2027)


def test_a_fixed_date_holiday_on_a_weekend_moves():
    """4 July 2026 is a Saturday, so the market closes the Friday before."""
    assert datetime.date(2026, 7, 3) in market.holidays(2026)
    assert datetime.date(2026, 7, 4) not in market.holidays(2026)
    # 2027: the 4th is a Sunday, so the closure moves forward to Monday
    assert datetime.date(2027, 7, 5) in market.holidays(2027)


# ── Half days, which matter to a 0DTE contract ──────────────────
def test_the_day_after_thanksgiving_closes_at_one():
    black_friday = datetime.datetime(2026, 11, 27, 12, 0)
    assert market.closes_at(black_friday) == datetime.time(13, 0)
    assert market.is_open(black_friday)
    assert market.minutes_to_close(black_friday) == 60


def test_a_half_day_is_shut_by_the_afternoon():
    assert not market.is_open(datetime.datetime(2026, 11, 27, 14, 0))


def test_the_hard_exit_moves_ahead_of_an_early_bell():
    """A 0DTE contract on a half-day expires at 13:00. A hard exit written for
    15:30 would fire an hour and a half after the contract stopped existing."""
    assert market.past_hard_exit(datetime.datetime(2026, 11, 27, 12, 35))
    assert not market.past_hard_exit(datetime.datetime(2026, 11, 27, 12, 25))
    # a normal session still uses the configured time
    assert not market.past_hard_exit(datetime.datetime(2026, 9, 8, 15, 0))
    assert market.past_hard_exit(datetime.datetime(2026, 9, 8, 15, 35))
