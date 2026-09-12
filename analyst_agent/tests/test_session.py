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


@pytest.mark.parametrize("symbol,kind", [
    ("NVDA", "us_equity"), ("^GSPC", "us_equity"), ("SPY", "us_equity"),
    ("BTC-USD", "crypto"), ("ETH-USD", "crypto"),
    ("EURUSD=X", "fx"), ("GC=F", "futures"), (None, "us_equity"),
])
def test_asset_class(symbol, kind):
    assert session.asset_class(symbol) == kind


def test_crypto_is_never_closed():
    """Saying "market closed" about Bitcoin would be wrong at any hour."""
    for when in ("2026-09-12 23:30", "2026-09-14 04:00", "2026-07-03 11:00"):
        state = session.state_for("BTC-USD", _at(when))
        assert state["is_open"] is True
        assert state["phase"] == "crypto_24h"
        assert "24" in state["phase_ar"]


def test_equities_still_use_the_nyse_clock():
    assert session.state_for("NVDA", _at("2026-09-12 23:30"))["phase"] == "weekend"
    assert session.state_for("NVDA", _at("2026-09-14 10:15"))["phase"] == "regular"


def test_fx_closes_only_for_the_weekend():
    assert session.state_for("EURUSD=X", _at("2026-09-16 03:00"))["is_open"] is True
    assert session.state_for("EURUSD=X", _at("2026-09-12 12:00"))["is_open"] is False
    assert session.state_for("EURUSD=X", _at("2026-09-11 18:00"))["is_open"] is False
    assert session.state_for("EURUSD=X", _at("2026-09-13 20:00"))["is_open"] is True


def test_futures_take_the_daily_break():
    assert session.state_for("GC=F", _at("2026-09-16 17:30"))["is_open"] is False
    assert session.state_for("GC=F", _at("2026-09-16 19:00"))["is_open"] is True


def test_every_state_names_its_asset_class():
    for symbol in ("NVDA", "BTC-USD", "EURUSD=X", "GC=F"):
        assert session.state_for(symbol)["asset_class"] == session.asset_class(symbol)
