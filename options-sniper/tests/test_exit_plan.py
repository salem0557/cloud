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


# ── Daily candles carry no start/end time at all ────────────────
def _daily(monkeypatch, rows):
    monkeypatch.setattr(uw, "_get", lambda path, params=None: rows)


def test_daily_bars_survive_the_closed_filter(monkeypatch):
    """UW documents that 1d and 1w rows have no start_time and no end_time,
    only `date`. Judging them by the intraday end_time rule dropped every one
    of them, which is what made daily_atr() silently return 0 — and left
    atr_to_strike blank on all 5,295 rows of the replication run."""
    rows = [{"date": "2026-08-2%d" % d, "open": "10", "high": "11",
             "low": "9", "close": "10.5", "volume": "1000"} for d in range(1, 8)]
    _daily(monkeypatch, rows)
    got = uw.candles("NVDA", candle_size="1d", timeframe="3M", limit=60)
    assert len(got) == 7
    assert got[0]["date"] < got[-1]["date"]          # ascending


def test_todays_daily_bar_is_still_forming(monkeypatch):
    """The same reason a half-formed 15m candle is excluded."""
    today = uw.datetime.date.today().isoformat()
    _daily(monkeypatch, [{"date": today, "open": "1", "high": "1",
                          "low": "1", "close": "1", "volume": "1"}])
    assert uw.candles("X", candle_size="1d") == []


def test_daily_bars_keep_premarket_field_from_dropping_them(monkeypatch):
    """Daily rows have no market_time either; the regular-hours filter must
    not apply to them."""
    rows = [{"date": "2026-08-1%d" % d, "open": "5", "high": "6", "low": "4",
             "close": "5.5", "volume": "1"} for d in range(1, 6)]
    _daily(monkeypatch, rows)
    assert len(uw.candles("X", candle_size="1d")) == 5


def test_atr_is_wilder_and_matches_a_hand_computed_window(monkeypatch):
    """Ten bars with a true range of exactly 2.0 every day -> ATR 2.0."""
    rows = [{"date": "2026-07-%02d" % d, "open": "100", "high": "101",
             "low": "99", "close": "100", "volume": "1"} for d in range(1, 21)]
    _daily(monkeypatch, rows)
    uw._tech_cache.clear()
    assert uw.daily_atr("X") == 2.0


def test_rsi_of_an_unbroken_rally_pins_at_100(monkeypatch):
    rows = [{"date": "2026-07-%02d" % d, "open": "100", "high": "101",
             "low": "99", "close": str(100 + d), "volume": "1"}
            for d in range(1, 21)]
    _daily(monkeypatch, rows)
    uw._tech_cache.clear()
    assert uw.stock_technicals("X")["rsi"] == 100.0


def test_technicals_are_cached_per_as_of_date_not_per_ticker(monkeypatch):
    """A backtest asks for the same ticker on four screen dates. Caching by
    ticker alone would hand May's entries September's ATR."""
    calls = []

    def fake(path, params=None):
        calls.append((params or {}).get("end_date"))
        return [{"date": "2026-07-%02d" % d, "open": "100", "high": "101",
                 "low": "99", "close": "100", "volume": "1"}
                for d in range(1, 21)]

    monkeypatch.setattr(uw, "_get", fake)
    uw._tech_cache.clear()
    uw.stock_technicals("X", as_of="2026-05-15")
    uw.stock_technicals("X", as_of="2026-07-15")
    uw.stock_technicals("X", as_of="2026-05-15")
    assert calls == ["2026-05-15", "2026-07-15"]


# ── The measuring stick is 15m, not daily ───────────────────────
def _bars15(monkeypatch, n=60, high=101.0, low=99.0, close=100.0):
    """Closed 15m bars, all in the past so the forming-bar filter keeps them."""
    base = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)
    rows = []
    for i in range(n):
        end = base + datetime.timedelta(minutes=15 * (i + 1))
        rows.append({"market_time": "r", "open": "100", "high": str(high),
                     "low": str(low), "close": str(close), "volume": "1000",
                     "start_time": (end - datetime.timedelta(minutes=15)).isoformat(),
                     "end_time": end.isoformat()})
    monkeypatch.setattr(uw, "_get", lambda path, params=None: rows)


def test_session_move_scales_one_bar_to_a_whole_session(monkeypatch):
    """Salem trades same-day contracts, so the distance to a strike has to be
    measured in what the stock can cover before the close — not in daily ATRs.
    A random walk covers sqrt(n) bars of range in n bars, not n."""
    _bars15(monkeypatch)
    uw._intraday_cache.clear()
    t = uw.intraday_technicals("NVDA")
    assert t["atr15"] == 2.0                      # high-low is 2.0 every bar
    assert round(t["session_move"], 2) == round(2.0 * (C.BARS_PER_SESSION ** 0.5), 2)


def test_a_session_is_twenty_six_fifteen_minute_bars():
    """9:30 to 16:00 is 6.5 hours."""
    assert C.BARS_PER_SESSION == 26


def test_the_fifteen_minute_frame_is_what_gets_requested(monkeypatch):
    seen = {}

    def fake(path, params=None):
        seen["path"] = path
        return []

    monkeypatch.setattr(uw, "_get", fake)
    uw._intraday_cache.clear()
    uw.intraday_technicals("NVDA")
    assert seen["path"].endswith("/ohlc/15m")


def test_no_intraday_bars_means_zero_not_a_guess(monkeypatch):
    monkeypatch.setattr(uw, "_get", lambda path, params=None: [])
    uw._intraday_cache.clear()
    t = uw.intraday_technicals("X")
    assert t["session_move"] == 0.0 and t["rsi"] is None
