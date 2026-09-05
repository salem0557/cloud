"""Backtest data sourcing: Yahoo for depth, UW for live, and honest coverage."""
import sys, pathlib, datetime, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import history


class FakeIndex:
    """Minimal stand-in for a tz-aware pandas timestamp."""
    def __init__(self, dt):
        self._dt = dt
        self.tzinfo = dt.tzinfo

    def tz_convert(self, _):
        return self._dt.astimezone(datetime.timezone.utc)

    def tz_localize(self, _):
        return self._dt.replace(tzinfo=datetime.timezone.utc)


class FakeDF:
    def __init__(self, rows):
        self._rows = rows
        self.empty = not rows

    def iterrows(self):
        return iter(self._rows)


def _rows(n, start_hour=14):
    base = datetime.datetime(2026, 7, 8, start_hour, 0, tzinfo=datetime.timezone.utc)
    out = []
    for i in range(n):
        ts = FakeIndex(base + datetime.timedelta(minutes=15 * i))
        out.append((ts, {"Open": 100.0, "High": 100.5, "Low": 99.5,
                         "Close": 100.2, "Volume": 1000}))
    return out


def _fake_yf(df, seen=None):
    class T:
        def __init__(self, ticker):
            pass

        def history(self, period, interval, auto_adjust=False):
            if seen is not None:
                seen.append(period)
            return df
    return types.SimpleNamespace(Ticker=T)


# ── Shape must match uw.candles() exactly ───────────────────────
def test_yahoo_rows_match_the_uw_candle_shape(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf(FakeDF(_rows(3))))
    bars = history.fetch("AAPL", "15m", 60, source="yahoo")
    assert len(bars) == 3
    b = bars[0]
    assert set(b) == {"open", "high", "low", "close", "volume",
                      "start_time", "end_time", "closed"}
    assert b["closed"] is True                      # all history is closed
    assert b["start_time"].endswith("Z")
    assert b["end_time"] == "2026-07-08T14:15:00Z"  # 15m after the open


def test_bars_come_back_oldest_first(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf(FakeDF(_rows(5))))
    bars = history.fetch("AAPL", "15m", 60, source="yahoo")
    assert bars == sorted(bars, key=lambda b: b["start_time"])


# ── Coverage is discovered, never assumed ───────────────────────
def test_it_walks_down_to_a_shorter_window(monkeypatch):
    """Yahoo caps intraday depth by interval, and the cap is Yahoo's to change.
    A request for 2y must fall through to a window that returns data."""
    seen = []

    class T:
        def __init__(self, ticker):
            pass

        def history(self, period, interval, auto_adjust=False):
            seen.append(period)
            return FakeDF(_rows(4)) if period == "60d" else FakeDF([])

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=T))
    bars = history.fetch("AAPL", "15m", 730, source="yahoo")
    assert len(bars) == 4
    assert seen[0] == "2y" and "60d" in seen        # asked long, settled short


def test_no_data_anywhere_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf(FakeDF([])))
    try:
        history.fetch("AAPL", "15m", 60, source="yahoo")
    except history.HistoryError as e:
        assert "returned nothing" in str(e)
    else:
        raise AssertionError("should have raised")


def test_missing_yfinance_is_a_clear_error(monkeypatch):
    import builtins
    real = builtins.__import__

    def no_yf(name, *a, **k):
        if name == "yfinance":
            raise ImportError("nope")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_yf)
    try:
        history.fetch("AAPL", "15m", 60, source="yahoo")
    except history.HistoryError as e:
        assert "yfinance" in str(e)
    else:
        raise AssertionError("should have raised")


def test_span_days():
    bars = [{"start_time": "2026-01-01T14:00:00Z"},
            {"start_time": "2026-03-02T14:00:00Z"}]
    assert history.span_days(bars) == 60
    assert history.span_days([]) == 0


# ── Session thirds must follow real ET, not a fixed offset ──────
def test_session_thirds_survive_daylight_saving():
    """A fixed UTC-4 was right in summer and an hour off all winter, which
    filed opening-hour setups as midday and blurred the base rates."""
    assert history.session_third("2026-07-08T14:45:00Z") == "open"    # 10:45 EDT
    assert history.session_third("2026-01-08T14:45:00Z") == "open"    # 09:45 EST
    assert history.session_third("2026-01-08T16:30:00Z") == "midday"  # 11:30 EST
    assert history.session_third("2026-07-08T19:30:00Z") == "close"   # 15:30 EDT


def test_unparseable_timestamp_is_flagged_not_guessed():
    assert history.session_third("") == "unknown"
    assert history.session_third(None) == "unknown"
