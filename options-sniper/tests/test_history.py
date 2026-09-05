"""Backtest data sourcing: Yahoo's chart JSON read directly, no heavy deps."""
import sys, pathlib, datetime, types
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import history


def chart_json(n=4, start=1757340000, gaps=()):
    """Yahoo's column-major shape, with nulls where it pads gaps."""
    ts = [start + 900 * i for i in range(n)]
    def col(v):
        return [None if i in gaps else v for i in range(n)]
    return {"chart": {"error": None, "result": [{
        "timestamp": ts,
        "indicators": {"quote": [{"open": col(100.0), "high": col(100.5),
                                  "low": col(99.5), "close": col(100.2),
                                  "volume": col(1000)}]},
    }]}}


class Resp:
    def __init__(self, payload, ok=True, status=200):
        self._p, self.ok, self.status_code = payload, ok, status

    def json(self):
        return self._p


def patch_get(monkeypatch, fn):
    monkeypatch.setattr(history.requests, "get", fn)


# ── Shape must match uw.candles() exactly ───────────────────────
def test_chart_json_becomes_uw_shaped_candles(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: Resp(chart_json(3)))
    bars = history.fetch("AAPL", "15m", 60, source="yahoo")
    assert len(bars) == 3
    b = bars[0]
    assert set(b) == {"open", "high", "low", "close", "volume",
                      "start_time", "end_time", "closed"}
    assert b["closed"] is True
    assert b["start_time"].endswith("Z")
    # end_time is one interval after start
    start = datetime.datetime.strptime(b["start_time"], "%Y-%m-%dT%H:%M:%SZ")
    end = datetime.datetime.strptime(b["end_time"], "%Y-%m-%dT%H:%M:%SZ")
    assert (end - start).total_seconds() == 900


def test_interval_sets_the_bar_length(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: Resp(chart_json(2)))
    bars = history.fetch("AAPL", "1h", 700, source="yahoo")
    start = datetime.datetime.strptime(bars[0]["start_time"], "%Y-%m-%dT%H:%M:%SZ")
    end = datetime.datetime.strptime(bars[0]["end_time"], "%Y-%m-%dT%H:%M:%SZ")
    assert (end - start).total_seconds() == 3600


def test_bars_come_back_oldest_first(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: Resp(chart_json(6)))
    bars = history.fetch("AAPL", "15m", 60, source="yahoo")
    assert bars == sorted(bars, key=lambda b: b["start_time"])


def test_null_padded_bars_are_dropped(monkeypatch):
    """Yahoo pads missing periods with nulls; a bar missing a leg is not a bar."""
    patch_get(monkeypatch, lambda *a, **k: Resp(chart_json(5, gaps=(1, 3))))
    assert len(history.fetch("AAPL", "15m", 60, source="yahoo")) == 3


# ── Coverage is discovered, never assumed ───────────────────────
def test_it_walks_down_to_a_shorter_window(monkeypatch):
    """Yahoo caps intraday depth by interval and the cap is Yahoo's to change."""
    seen = []

    def get(url, params=None, **k):
        seen.append(params["range"])
        return Resp(chart_json(4)) if params["range"] == "60d" \
            else Resp({"chart": {"result": [], "error": None}})

    patch_get(monkeypatch, get)
    bars = history.fetch("AAPL", "15m", 730, source="yahoo")
    assert len(bars) == 4
    assert seen[0] == "2y" and "60d" in seen        # asked long, settled short


def test_http_error_is_reported(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: Resp(None, ok=False, status=429))
    try:
        history.fetch("AAPL", "15m", 60, source="yahoo")
    except history.HistoryError as e:
        assert "429" in str(e)
    else:
        raise AssertionError("should have raised")


def test_unknown_ticker_is_reported(monkeypatch):
    patch_get(monkeypatch, lambda *a, **k: Resp(None, ok=False, status=404))
    try:
        history.fetch("NOTATICKER", "15m", 60, source="yahoo")
    except history.HistoryError as e:
        assert "NOTATICKER" in str(e)
    else:
        raise AssertionError("should have raised")


def test_network_error_is_reported(monkeypatch):
    def boom(*a, **k):
        raise history.requests.RequestException("connection reset")
    patch_get(monkeypatch, boom)
    try:
        history.fetch("AAPL", "15m", 60, source="yahoo")
    except history.HistoryError as e:
        assert "AAPL" in str(e)
    else:
        raise AssertionError("should have raised")


def test_span_days():
    assert history.span_days([{"start_time": "2026-01-01T14:00:00Z"},
                              {"start_time": "2026-03-02T14:00:00Z"}]) == 60
    assert history.span_days([]) == 0


# ── Session thirds must follow real ET, not a fixed offset ──────
def test_session_thirds_survive_daylight_saving():
    assert history.session_third("2026-07-08T14:45:00Z") == "open"    # 10:45 EDT
    assert history.session_third("2026-01-08T14:45:00Z") == "open"    # 09:45 EST
    assert history.session_third("2026-01-08T16:30:00Z") == "midday"  # 11:30 EST
    assert history.session_third("2026-07-08T19:30:00Z") == "close"   # 15:30 EDT


def test_unparseable_timestamp_is_flagged_not_guessed():
    assert history.session_third("") == "unknown"
    assert history.session_third(None) == "unknown"
