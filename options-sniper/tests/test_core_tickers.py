"""The big names are always looked at, whether or not they are "unusual".

The 2026-09-08 13:00 scan evaluated 60 tickers. NVDA, TSLA, AAPL, MSFT, MU,
AMD, SPY and QQQ were in none of them — while NVDA fell 233.24 -> 225.81 (its
225 put 0.25 -> 1.60) and TSLA ran 357.25 -> 370.00 (its 370 call 1.09 -> 3.75).

Discovery is flow-driven, and UW's flow-alert feed lists what is UNUSUAL. A
mega-cap trading its normal enormous volume is never unusual, and Finviz's
movers screen misses it too, because 2% on NVDA is not a mover. So the big
names were not filtered out — they were never candidates at all.
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import scanner


def _flow(prem):
    return {"premium_usd": prem, "sweep_count": 1, "vol_oi_ratio": 1.0,
            "ask_premium": prem, "bid_premium": 0.0, "alerts": 1,
            "call_premium": prem, "put_premium": 0.0, "underlying_price": 100.0,
            "call_ask_premium": prem, "put_ask_premium": 0.0, "rules": []}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(C, "SHORTLIST_FILE", tmp_path / "shortlist.json")
    monkeypatch.setattr(C, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(C, "LOCK_FILE", tmp_path / "state.lock")
    monkeypatch.setattr(C, "CORE_TICKERS", ["NVDA", "TSLA", "MU"])
    monkeypatch.setattr(scanner.market, "is_open", lambda *a: True)
    monkeypatch.setattr(scanner.state, "capacity_left", lambda: 30)
    monkeypatch.setattr(scanner.finviz, "movers", lambda **k: [])
    monkeypatch.setattr(scanner.uw, "flow_alerts", lambda *a, **k: [])
    monkeypatch.setattr(scanner.uw, "spent", lambda: "")
    monkeypatch.setattr(scanner, "evaluate",
                        lambda t, f, d=False: seen.append(t) or None)
    return seen


def test_a_core_name_absent_from_the_feed_is_fetched_and_evaluated(monkeypatch, wired):
    """NVDA on a day nothing about it was flagged as unusual."""
    monkeypatch.setattr(scanner, "aggregate_flow",
                        lambda a: {"NVDA": _flow(5_000_000)} if a else {})
    monkeypatch.setattr(scanner.uw, "ticker_flow_alerts",
                        lambda t: [{"ticker": t}] if t == "NVDA" else [])
    scanner.main()
    assert "NVDA" in wired


def test_a_core_name_is_looked_at_below_the_premium_floor(monkeypatch, wired):
    """The floor stops paying for data on names nobody trades. A mega-cap's
    quiet hour is not that."""
    quiet = C.MIN_TICKER_PREMIUM / 10
    monkeypatch.setattr(scanner, "aggregate_flow", lambda a: {"MU": _flow(quiet)})
    monkeypatch.setattr(scanner.uw, "ticker_flow_alerts", lambda t: [{"ticker": t}])
    scanner.main()
    assert "MU" in wired


def test_a_non_core_name_below_the_floor_is_still_skipped(monkeypatch, wired):
    monkeypatch.setattr(scanner, "aggregate_flow",
                        lambda a: {"XYZ": _flow(C.MIN_TICKER_PREMIUM / 10)})
    monkeypatch.setattr(scanner.uw, "ticker_flow_alerts", lambda t: [])
    scanner.main()
    assert "XYZ" not in wired


def test_core_names_are_not_squeezed_out_by_a_busy_day(monkeypatch, wired):
    """A hundred small caps with bigger premium must not push every large cap
    past MAX_CANDIDATES_PER_SCAN."""
    monkeypatch.setattr(C, "MAX_CANDIDATES_PER_SCAN", 5)
    noisy = {f"SMALL{i}": _flow(50_000_000 + i) for i in range(50)}
    noisy["TSLA"] = _flow(1_000_000)
    monkeypatch.setattr(scanner, "aggregate_flow", lambda a: noisy)
    monkeypatch.setattr(scanner.uw, "ticker_flow_alerts", lambda t: [])
    scanner.main()
    assert "TSLA" in wired
    assert len(wired) == 5, "the cap stopped being a cap"


def test_the_surprises_still_get_in(monkeypatch, wired):
    """Core names take their slots first, they do not take them all. Salem
    asked for the surprises too: 'اريد ايضا المفاجات مثلا اوبر'."""
    monkeypatch.setattr(C, "MAX_CANDIDATES_PER_SCAN", 5)
    agg = {"TSLA": _flow(1_000_000), "UBER": _flow(9_000_000),
           "ABCD": _flow(8_000_000)}
    monkeypatch.setattr(scanner, "aggregate_flow", lambda a: agg)
    monkeypatch.setattr(scanner.uw, "ticker_flow_alerts", lambda t: [])
    scanner.main()
    assert "UBER" in wired and "TSLA" in wired


def test_a_core_name_already_alerted_today_is_not_re_evaluated(monkeypatch, wired):
    monkeypatch.setattr(scanner.state, "read",
                        lambda: {"alerted_tickers": ["NVDA"]})
    monkeypatch.setattr(scanner, "aggregate_flow", lambda a: {"NVDA": _flow(5_000_000)})
    monkeypatch.setattr(scanner.uw, "ticker_flow_alerts", lambda t: [])
    scanner.main()
    assert "NVDA" not in wired


def test_a_failed_core_lookup_does_not_stop_the_scan(monkeypatch, wired, capsys):
    def boom(t):
        if t == "TSLA":
            raise scanner.uw.UWError("HTTP 500")
        return [{"ticker": t}]

    monkeypatch.setattr(scanner, "aggregate_flow",
                        lambda a: {"MU": _flow(5_000_000)})
    monkeypatch.setattr(scanner.uw, "ticker_flow_alerts", boom)
    scanner.main()
    assert "MU" in wired
    assert "TSLA" in capsys.readouterr().out      # reported, not swallowed


# ── The shipped list ────────────────────────────────────────────
def test_the_names_salem_asked_for_are_in_the_default_list():
    """"لا اريد فقط تسلا ونفديا اريد كل الشركات الكبرى تكون مرئية مثل MU و SPX"."""
    for t in ("NVDA", "TSLA", "MU", "SPX", "AAPL", "MSFT", "AMD", "META",
              "AMZN", "GOOGL", "AVGO", "SPY", "QQQ"):
        assert t in C.CORE_TICKERS, f"{t} would still be invisible"


def test_the_list_is_overridable_without_a_deploy():
    """CORE_TICKERS is read from the environment, so a name can be added or
    dropped from Railway rather than through a merge."""
    import importlib
    import os
    os.environ["CORE_TICKERS"] = "abc, def"
    try:
        importlib.reload(C)
        assert C.CORE_TICKERS == ["ABC", "DEF"]
    finally:
        del os.environ["CORE_TICKERS"]
        importlib.reload(C)
