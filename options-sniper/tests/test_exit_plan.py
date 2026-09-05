"""Entry confirmation on closed bars, and the exit plan that ships with it."""
import sys, pathlib, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config as C
import market
import scoring
import uw


# ── Only closed 15m bars may confirm a break ────────────────────
def _iso(seconds_from_now):
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds_from_now)
    return t.isoformat().replace("+00:00", "Z")


def test_forming_bar_is_not_closed():
    """A 15m candle read five minutes in can be above the level and still
    close back under it — the false break the 15m frame exists to filter."""
    assert not uw._is_closed(_iso(600))
    assert not uw._is_closed(_iso(60))


def test_completed_bar_is_closed():
    assert uw._is_closed(_iso(-60))
    assert uw._is_closed(_iso(-3600))


def test_unusable_timestamps_are_not_closed():
    assert not uw._is_closed("")
    assert not uw._is_closed(None)
    assert not uw._is_closed("not-a-date")


# ── Exit rules scale with the contract's remaining life ─────────
def test_zero_dte_takes_profit_earlier_and_cuts_earlier():
    same_day, week = scoring.exit_rule(0), scoring.exit_rule(7)
    assert same_day["take_pct"] < week["take_pct"]
    assert same_day["stop_pct"] > week["stop_pct"]     # -35 is tighter than -40


def test_longer_dated_gets_more_room():
    assert scoring.exit_rule(30)["take_pct"] > scoring.exit_rule(7)["take_pct"]


def test_every_dte_resolves_to_a_rule():
    for d in (0, 1, 3, 7, 8, 21, 45, 400, None):
        r = scoring.exit_rule(d)
        assert r["take_pct"] > 0 > r["stop_pct"]


# ── 0DTE hard time exit ─────────────────────────────────────────
def _at(hh, mm):
    return datetime.datetime(2026, 9, 8, hh, mm, tzinfo=market._ET)


def test_hard_exit_fires_after_the_cutoff():
    assert not market.past_hard_exit(_at(15, 29))
    assert market.past_hard_exit(_at(15, 30))
    assert market.past_hard_exit(_at(15, 55))


def test_hard_exit_is_only_during_the_session():
    assert not market.past_hard_exit(_at(16, 30))      # closed
    assert not market.past_hard_exit(_at(9, 45))       # too early
