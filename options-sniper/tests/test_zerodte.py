"""0DTE handling: same-day contracts are allowed, but priced and gated for it."""
import sys, pathlib, datetime
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config as C
import market
import scanner
import scoring


def contract(ask, delta, gamma=0.0, theta=0.0, dte=0):
    return {"option_symbol": f"X{dte}", "strike": 100, "type": "call",
            "bid": ask - 0.02, "ask": ask, "delta": delta, "gamma": gamma,
            "theta": theta, "open_interest": 5000, "dte": dte,
            "expiry": (datetime.date.today() + datetime.timedelta(days=dte)).isoformat()}


# ── Profit estimate ─────────────────────────────────────────────
def test_gamma_lifts_the_estimate():
    """Delta rises as the move happens; a delta-only figure understates it."""
    flat = scoring.expected_profit_pct(contract(1.00, 0.40), 2.0)
    curved = scoring.expected_profit_pct(contract(1.00, 0.40, gamma=0.05), 2.0)
    assert curved > flat


def test_theta_reduces_the_estimate():
    clean = scoring.expected_profit_pct(contract(1.00, 0.40), 2.0)
    decayed = scoring.expected_profit_pct(contract(1.00, 0.40, theta=0.30), 2.0)
    assert decayed < clean


def test_theta_larger_than_the_move_gives_zero():
    """A 0DTE contract bleeding faster than the expected move is not a trade."""
    assert scoring.expected_profit_pct(
        contract(1.00, 0.10, theta=2.00), 0.5) == 0.0


def test_estimate_still_zero_without_a_delta():
    assert scoring.expected_profit_pct(contract(1.00, 0.0), 2.0) == 0.0
    assert scoring.expected_profit_pct(contract(1.00, 0.40), 0) == 0.0


# ── Late-session gate ───────────────────────────────────────────
def _at(hh, mm):
    return datetime.datetime(2026, 9, 8, hh, mm, tzinfo=market._ET)


def test_minutes_to_close():
    assert market.minutes_to_close(_at(15, 0)) == 60
    assert market.minutes_to_close(_at(15, 45)) == 15
    assert market.minutes_to_close(_at(16, 30)) == 0     # session over


def test_zero_dte_dropped_near_the_bell(monkeypatch):
    monkeypatch.setattr(market, "minutes_to_close", lambda *a: 20)
    chain = [contract(0.30, 0.35, dte=0), contract(1.10, 0.45, dte=7)]
    kept = scanner.tradable_chain(chain)
    assert [c["dte"] for c in kept] == [7]


def test_zero_dte_kept_with_time_left(monkeypatch):
    monkeypatch.setattr(market, "minutes_to_close", lambda *a: 180)
    chain = [contract(0.30, 0.35, dte=0), contract(1.10, 0.45, dte=7)]
    assert len(scanner.tradable_chain(chain)) == 2


def test_cutoff_can_be_disabled(monkeypatch):
    monkeypatch.setattr(C, "MIN_MINUTES_TO_CLOSE", 0)
    monkeypatch.setattr(market, "minutes_to_close", lambda *a: 1)
    chain = [contract(0.30, 0.35, dte=0)]
    assert len(scanner.tradable_chain(chain)) == 1
