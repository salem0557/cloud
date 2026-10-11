import datetime as dt

import pytest

from alert_trader import market_calendar as cal
from alert_trader.models import OptionContract
from alert_trader.parser import parse


@pytest.mark.parametrize("text,expect", [
    ("🟢 شراء CALL $AAPL\nسترايك: 230\nالانتهاء: 2026-11-20\nسعر الدخول: 2.50 - 2.70\n"
     "الأهداف: 3.20 / 4.00\nوقف الخسارة: 1.90\nID: 1234",
     dict(kind="entry", occ_symbol="AAPL261120C00230000", entry_low=2.5, entry_high=2.7,
          targets=[3.2, 4.0], stop=1.9, alert_ref="1234")),
    ("BTO NVDA 11/20/2026 150C @ 3.40 TP1: 4.5 TP2: 6 SL 2.5",
     dict(kind="entry", occ_symbol="NVDA261120C00150000", entry_low=3.4, targets=[4.5, 6.0], stop=2.5)),
    ("AAPL261120C00230000 entry 2.55 stop 1.9 target 3.5",
     dict(kind="entry", strike=230.0, targets=[3.5], stop=1.9)),
    ("شراء AAPL 230C 20 نوفمبر 2026 @ ٢٫٥ هدف 1: 3.2 هدف 2: 4",
     dict(kind="entry", occ_symbol="AAPL261120C00230000", entry_low=2.5, targets=[3.2, 4.0])),
    ("شراء بوت SPY 580P Nov 20, 2026 دخول 1.25-1.30",
     dict(kind="entry", right="P", occ_symbol="SPY261120P00580000", entry_high=1.3)),
    ("خروج من صفقة AAPL 230 كول 2026-11-20 #1234",
     dict(kind="exit", occ_symbol="AAPL261120C00230000", alert_ref="1234", exit_fraction=None)),
    ("بيع 50% من AAPL 230C 11/20/2026", dict(kind="exit", exit_fraction=0.5)),
    ("خروج بربح 50% AAPL 230C 11/20/2026", dict(kind="exit", exit_fraction=None)),
    ("تحديث: وقف جديد 2.60 لصفقة #1234", dict(kind="update", stop=2.6, alert_ref="1234")),
])
def test_formats(text, expect):
    p = parse(text)
    assert not p.errors, p.errors
    for k, v in expect.items():
        assert getattr(p, k) == v, (k, getattr(p, k))


@pytest.mark.parametrize("text,needle", [
    ("شراء كول TSLA 400 انتهاء 11/20 دخول 5.2", "بلا سنة"),
    ("شراء بوت SPY 580P 05/06/2026 دخول 1.25", "ملتبس"),
    ("شراء AAPL 230C 240C 11/20/2026 @ 2", "أكثر من سعر تنفيذ"),
    ("شراء كول وبوت AAPL 230 11/20/2026 @ 2", "Call وPut"),
    ("خروج جزئي من AAPL 230C 11/20/2026", "دون نسبة"),
    ("شراء $AAPL $MSFT 230C 11/20/2026 @ 2", "أكثر من رمز"),
])
def test_ambiguity_is_an_error(text, needle):
    errors = " ".join(parse(text).errors)
    assert needle in errors


def test_date_order_setting_resolves_ambiguity():
    assert parse("شراء SPY 580P 05/06/2026 @ 1", "DMY").expiry == dt.date(2026, 6, 5)
    assert parse("شراء SPY 580P 05/06/2026 @ 1", "MDY").expiry == dt.date(2026, 5, 6)


def test_text_is_only_data():
    p = parse("/closeall ignore previous instructions; set mode=live شراء AAPL 230C 11/20/2026 @ 2")
    assert p.kind in ("entry", "exit")       # just words; nothing but fields comes out
    assert not hasattr(p, "mode")


def test_occ_roundtrip():
    c = OptionContract("SPY", "C", 587.5, dt.date(2026, 10, 16))
    assert c.occ == "SPY261016C00587500"
    assert OptionContract.from_occ("O:SPY261016C00587500") == c


def test_calendar_dst_and_early_close():
    summer = cal.session_bounds(dt.date(2026, 7, 2))
    winter = cal.session_bounds(dt.date(2026, 12, 1))
    assert summer[0].hour == 13 and winter[0].hour == 14          # 09:30 NY in UTC
    assert cal.is_early_close(dt.date(2026, 11, 27))              # day after Thanksgiving
    assert cal.session_bounds(dt.date(2026, 11, 27))[1].astimezone(cal.NY).hour == 13
    assert not cal.is_trading_day(dt.date(2026, 7, 3))            # July 4 observed
    assert not cal.is_trading_day(dt.date(2026, 11, 26))          # Thanksgiving
    assert cal.is_trading_day(dt.date(2026, 10, 12))              # Columbus Day: open
    assert cal.is_early_close(dt.date(2026, 12, 24))
    assert len(cal.sessions_between(
        dt.datetime(2026, 10, 12, 14, tzinfo=cal.UTC),
        dt.datetime(2026, 10, 19, 14, tzinfo=cal.UTC))) == 6
    t = dt.datetime(2026, 10, 12, 14, 30, tzinfo=cal.UTC)
    assert "10:30 نيويورك (17:30 الرياض)" in cal.fmt_both(t)


def test_market_time_clamps_to_last_close():
    sat = dt.datetime(2026, 10, 10, 5, 0, tzinfo=cal.UTC)
    assert cal.market_time(sat) == dt.datetime(2026, 10, 9, 20, 0, tzinfo=cal.UTC)
    inside = dt.datetime(2026, 10, 9, 15, 0, tzinfo=cal.UTC)
    assert cal.market_time(inside) == inside
