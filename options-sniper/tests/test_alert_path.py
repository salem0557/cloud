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


def test_the_shortlist_carries_the_components_the_monitor_cannot_recompute():
    """The monitor re-derives only the technical 30. Without the other three
    a watchlist alert had no breakdown line at all."""
    import inspect
    src = inspect.getsource(scanner.main)
    assert '"base_breakdown"' in src
    assert all(k in src for k in ("flow", "catalyst", "liquidity"))


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
