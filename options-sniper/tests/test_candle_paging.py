"""One OHLC page is not enough, and that alone stopped every alert.

Measured on META, 2026-09-08 13:00 ET, with diag.py against the live API:

    GET /api/stock/META/ohlc/15m?timeframe=5D&limit=500
      -> 100 raw rows                       (UW caps here; limit=500 ignored)
      -> 100 carry end_time                 OK
      -> 100 priced                         OK
      ->  99 accepted by _is_closed         OK
      ->  40 pass market_time == 'r'        <- 60 are pre/post-market
      ->  39 kept
    technical.analyse() needs 40 -> None

Off by ONE bar, on every liquid ticker in the market, every scan, all day.
evaluate() then dropped each one before it ever priced a contract, which is
why the log showed 60 candle requests and zero option-chain requests.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import uw


def _page(day, n=100, regular=40, forming=True):
    """One UW page, shaped like the live one: n rows for `day`, of which
    `regular` carry market_time 'r'. The newest regular bar is still forming
    unless told otherwise — UW always includes it, and it is the bar that
    turned META's 40 into 39."""
    rows = []
    for i in range(n):
        hh, mm = 4 + (i * 15) // 60, (i * 15) % 60
        last_regular = forming and i == regular - 1
        rows.append({
            "open": "100.0", "high": "101.0", "low": "99.0", "close": "100.5",
            "volume": 1000,
            "start_time": f"2026-09-{day:02d}T{hh:02d}:{mm:02d}:00Z",
            "end_time": ("2099-01-01T00:00:00Z" if last_regular
                         else f"2026-09-{day:02d}T{hh:02d}:{mm:02d}:00Z"),
            "market_time": "r" if i < regular else "pr",
        })
    return rows


def test_one_page_leaves_one_bar_short_and_a_second_page_fixes_it(monkeypatch):
    calls = []

    def fake_get(path, params=None):
        calls.append((params or {}).get("end_date"))
        day = 8 if len(calls) == 1 else 4
        return _page(day)

    monkeypatch.setattr(uw, "_get", fake_get)
    rows = uw.candles("META", timeframe="5D")
    assert len(calls) == 2, "it did not walk back for more history"
    assert calls[1] is not None, "the second page must carry an end_date"
    assert len(rows) >= C.CANDLES_LOOKBACK, (
        f"still only {len(rows)} bars against {C.CANDLES_LOOKBACK} needed")


def test_it_stops_as_soon_as_it_has_enough(monkeypatch):
    """The paging must not cost requests it does not need — 60 tickers a scan
    against a 30,000-a-day allowance."""
    calls = []
    monkeypatch.setattr(uw, "_get",
                        lambda p, params=None: calls.append(1) or _page(8, n=200, regular=120))
    rows = uw.candles("META", timeframe="5D")
    assert len(calls) == 1
    assert len(rows) >= C.CANDLES_LOOKBACK


def test_paging_is_bounded(monkeypatch):
    """A ticker that never has enough history must not loop forever."""
    calls = []
    monkeypatch.setattr(uw, "_get",
                        lambda p, params=None: calls.append(1) or _page(8, n=10, regular=4))
    uw.candles("META", timeframe="5D")
    assert len(calls) == C.CANDLE_PAGES


def test_pages_do_not_duplicate_bars(monkeypatch):
    """If UW hands back rows we already hold, they must not be counted twice —
    that would satisfy the bar count with the same candle repeated."""
    monkeypatch.setattr(uw, "_get", lambda p, params=None: _page(8))
    rows = uw.candles("META", timeframe="5D")
    stamps = [r["start_time"] for r in rows]
    assert len(stamps) == len(set(stamps))


def test_rows_come_back_in_time_order(monkeypatch):
    calls = []

    def fake_get(path, params=None):
        calls.append(1)
        return _page(8 if len(calls) == 1 else 4)

    monkeypatch.setattr(uw, "_get", fake_get)
    rows = uw.candles("META", timeframe="5D")
    assert rows == sorted(rows, key=lambda r: r["start_time"])


def test_a_failed_request_still_returns_empty_and_says_so(monkeypatch, capsys):
    def boom(path, params=None):
        raise uw.UWError("HTTP 500")

    monkeypatch.setattr(uw, "_get", boom)
    assert uw.candles("META", timeframe="5D") == []
    assert "META" in capsys.readouterr().out
