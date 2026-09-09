"""The alert Salem actually receives, built the way monitor.py builds it.

Every defect these tests cover was live, and every one of them was invisible
because the scanner path — which the tests exercised — was correct while the
watchlist path, the one he designed and uses, was not. A test that runs a
different code path than production is not a test of production.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

import compose
import config as C
import market
import scanner


@pytest.fixture(autouse=True)
def _mid_session(monkeypatch):
    """tradable_chain refuses same-day contracts near the bell, correctly. The
    tests run whenever they run, so the clock is pinned rather than the rule
    being weakened to suit them."""
    monkeypatch.setattr(market, "minutes_to_close", lambda *a, **k: 180)
    monkeypatch.setattr(scanner.market, "minutes_to_close", lambda *a, **k: 180)


def _chain(dte_expiry):
    return [{"option_symbol": f"NVDA260908C00{k}000", "strike": k,
             "type": "call", "expiry": dte_expiry, "ask": a, "bid": a - 0.10,
             "delta": 0.44, "gamma": 0.05, "theta": -0.4,
             "implied_volatility": 0.4, "open_interest": 900, "volume": 500,
             "dte": 0}
            for k, a in ((183, 1.85), (185, 0.95), (186, 0.42))]


def _cand():
    tech = {"level": 182.4, "close": 183.2, "atr": 1.1, "target": 185.1,
            "stop": 180.9, "expected_move": 2.2, "volume_ratio": 2.3,
            "break_distance_atr": 0.2, "closed_beyond": True}
    return {"chain": _chain("2026-09-08"), "direction": "call",
            "spot": 183.2, "technical": tech}


def test_a_watchlist_alert_carries_the_expiry_and_the_exit_plan():
    """monitor.py built its tiers by hand and left out `dte` and `exit`, so a
    same-day contract was presented with no expiry tag, no exit plan, no hold
    clock and no hard-exit line — as if it had all week."""
    tiers = scanner.build_tiers(_cand())
    real = [t for t in tiers if t.get("option_symbol")]
    assert real, "no contract qualified — fixture is wrong, not the code"
    for t in real:
        assert t["dte"] is not None
        assert t["exit"] and "take_pct" in t["exit"]


def test_the_message_from_those_tiers_states_every_exit_rule():
    p = {"ticker": "NVDA", "score": 88, "direction": "call", "spot": 183.2,
         "technical": _cand()["technical"], "tiers": scanner.build_tiers(_cand()),
         "score_breakdown": {"flow": 28, "technical": 26, "catalyst": 20,
                             "liquidity": 14},
         "reasoning": {"links": [{"text": "← كسر وثبت"}], "gaps": []},
         "time_riyadh": "16:47:12"}
    out = compose.render_entry(p)
    assert "⚡اليوم" in out                       # it expires TONIGHT
    assert "بِع عند" in out and "اقطع عند" in out  # the exit plan
    assert f"{C.MAX_HOLD_MIN} دقيقة" in out        # the clock the book scores by
    assert C.ZERO_DTE_HARD_EXIT_ET in out          # out before the close
    assert "تدفق 28/30" in out                     # do the factors line up


def test_the_shortlist_carries_the_components_the_monitor_cannot_recompute(
        monkeypatch, tmp_path):
    """The monitor re-derives only the technical 30. Without the other three
    a watchlist alert had no breakdown line at all.

    Checked on the file the scanner actually writes, not on its source: the
    source test broke the moment the code moved to another function, which is
    the wrong thing for a test to notice.
    """
    import json
    out = tmp_path / "shortlist.json"
    monkeypatch.setattr(C, "SHORTLIST_FILE", out)
    monkeypatch.setattr(C, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(C, "LOCK_FILE", tmp_path / "state.lock")
    monkeypatch.setattr(C, "WATCHLIST_ONLY", False)
    monkeypatch.setattr(C, "MIN_TICKER_PREMIUM", 0)
    monkeypatch.setattr(scanner.market, "is_open", lambda *a: True)
    monkeypatch.setattr(scanner.state, "capacity_left", lambda: 30)
    monkeypatch.setattr(scanner.uw, "flow_alerts", lambda *a, **k: [{"x": 1}])
    monkeypatch.setattr(scanner.uw, "spent", lambda: "")
    monkeypatch.setattr(scanner.finviz, "movers", lambda **k: [])
    monkeypatch.setattr(scanner.uw, "ticker_flow_alerts", lambda t: [])
    monkeypatch.setattr(scanner, "aggregate_flow",
                        lambda a: {"NVDA": {"premium_usd": 5e6}} if a else {})
    cand = dict(_cand(), ticker="NVDA", score=55.0, near_miss=False,
                score_breakdown={"flow": 20.0, "technical": 0.0,
                                 "catalyst": 12.0, "liquidity": 23.0})
    monkeypatch.setattr(scanner, "evaluate", lambda t, f, d=False: cand)
    monkeypatch.setattr(scanner, "to_payload", lambda c: {"ticker": c["ticker"],
                                                          "tiers": []})
    monkeypatch.setattr(scanner.paper, "record", lambda p, tier=None: None)
    monkeypatch.setattr(scanner.technical, "confirms", lambda t: False)
    scanner.main()
    rows = json.loads(out.read_text())
    assert rows, "nothing reached the shortlist"
    assert set(rows[0]["base_breakdown"]) == {"flow", "catalyst", "liquidity"}
    assert "base_score" in rows[0]


def test_the_watchlist_path_records_into_the_paper_book():
    """scanner.py recorded its alerts and monitor.py did not, so the paper
    month was scoring a smaller and different population than the one he
    receives — and the watchlist path is the one he designed."""
    import inspect, monitor
    src = inspect.getsource(monitor.check_shortlist)
    assert "paper.record(payload)" in src


def test_both_paths_use_one_tier_builder():
    """Two copies drifted once and produced a live alert with no exit plan."""
    import inspect, monitor
    assert "build_tiers" in inspect.getsource(monitor.check_shortlist)
    assert "pick_contracts_by_budget" not in inspect.getsource(monitor.check_shortlist)


# ── A scan that scores nothing must say why ─────────────────────
# 2026-09-08: the log read "Tickers worth a data call: [60 names]" and then
# "Shortlist (0): []" and "Alerts sent: 0", with nothing in between. Sixty
# tickers were dropped and not one of them said a word. uw.candles() had
# caught its own request failure and returned [], technical.analyse() saw
# nothing and returned None, and evaluate() dropped the ticker silently.
# A total failure and a quiet market printed exactly the same log.

def test_evaluate_records_why_it_gave_up(monkeypatch):
    import scanner
    monkeypatch.setattr(scanner.uw, "candles", lambda *a, **k: [])
    monkeypatch.setattr(scanner, "flow_direction", lambda f: "call")
    assert scanner.evaluate("META", {"premium_usd": 1e6}) is None
    assert scanner.evaluate.last_skip == "no candles"


def test_a_short_history_is_named_as_such(monkeypatch):
    import scanner
    import config as C
    monkeypatch.setattr(scanner.uw, "candles", lambda *a, **k: ["bar"] * 12)
    monkeypatch.setattr(scanner.technical, "analyse", lambda *a, **k: None)
    monkeypatch.setattr(scanner, "flow_direction", lambda f: "call")
    assert scanner.evaluate("META", {"premium_usd": 1e6}) is None
    assert "12 bars" in scanner.evaluate.last_skip


def test_an_empty_chain_is_named_as_such(monkeypatch):
    import scanner
    tech = {"broke_level": False, "close": 100.0, "atr": 2.0, "level": 99.0,
            "target": 103.0, "stop": 97.0, "remaining_atr": 1.5,
            "break_distance_atr": 0.0, "volume_ratio": 1.0,
            "closed_beyond": False, "expected_move": 3.0}
    monkeypatch.setattr(scanner.uw, "candles", lambda *a, **k: ["bar"] * 60)
    monkeypatch.setattr(scanner.technical, "analyse", lambda *a, **k: tech)
    monkeypatch.setattr(scanner.uw, "option_chain", lambda t: [])
    monkeypatch.setattr(scanner, "flow_direction", lambda f: "call")
    assert scanner.evaluate("META", {"premium_usd": 1e6,
                                     "underlying_price": 100.0}) is None
    assert scanner.evaluate.last_skip == "empty option chain"


def test_uw_candles_reports_a_failed_request_instead_of_returning_empty(capsys):
    """The swallow itself. It may still return [] so a live scan degrades
    rather than crashing — but it may never do it silently again."""
    import uw
    import config as C
    real = C.UW_API_KEY
    C.UW_API_KEY = ""                       # forces UWError inside _get
    try:
        assert uw.candles("META", timeframe="5D") == []
    finally:
        C.UW_API_KEY = real
    out = capsys.readouterr().out
    assert "META" in out and "failed" in out
