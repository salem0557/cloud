"""Computed risk checks: what a summed score cannot see."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config as C
import risk
import scoring
import uw


# ── Direction must follow premium that was BOUGHT ───────────────
def test_bid_side_calls_are_not_bullish():
    """$3M of call premium sold on the bid is somebody WRITING calls. Reading
    it as bullish points the entire alert the wrong way."""
    flow = {"call_premium": 3_000_000, "put_premium": 1_000_000,
            "call_ask_premium": 200_000, "put_ask_premium": 800_000}
    assert scoring.flow_direction(flow) == "put"


def test_ask_side_calls_are_bullish():
    flow = {"call_premium": 3_000_000, "put_premium": 1_000_000,
            "call_ask_premium": 2_600_000, "put_ask_premium": 300_000}
    assert scoring.flow_direction(flow) == "call"


def test_direction_falls_back_without_the_split():
    assert scoring.flow_direction({"call_premium": 2, "put_premium": 1}) == "call"
    assert scoring.flow_direction({"call_premium": 1, "put_premium": 2}) == "put"


def test_bought_flow_scores_above_sold_flow():
    base = {"premium_usd": 3_000_000, "sweep_count": 4, "call_premium": 3_000_000,
            "put_premium": 0, "vol_oi_ratio": 2}
    bought = dict(base, ask_premium=2_700_000, bid_premium=300_000)
    sold = dict(base, ask_premium=300_000, bid_premium=2_700_000)
    assert scoring.flow_score(bought) > scoring.flow_score(sold)


def test_ask_side_ratio():
    assert scoring.ask_side_ratio({"ask_premium": 75, "bid_premium": 25}) == 0.75
    assert scoring.ask_side_ratio({}) == 0.0


# ── Earnings ────────────────────────────────────────────────────
def test_earnings_tomorrow_is_penalised(monkeypatch):
    monkeypatch.setattr(uw, "next_earnings_days", lambda t: 1)
    penalty, note = risk.earnings_risk("NVDA", 7)
    assert penalty == C.EARNINGS_PENALTY and "أرباح" in note


def test_earnings_inside_the_contract_is_half_penalised(monkeypatch):
    monkeypatch.setattr(uw, "next_earnings_days", lambda t: 10)
    penalty, note = risk.earnings_risk("NVDA", 30)
    assert 0 < penalty < C.EARNINGS_PENALTY and note


def test_earnings_after_expiry_is_free(monkeypatch):
    monkeypatch.setattr(uw, "next_earnings_days", lambda t: 60)
    assert risk.earnings_risk("NVDA", 7) == (0.0, None)


def test_unknown_earnings_date_is_not_penalised(monkeypatch):
    monkeypatch.setattr(uw, "next_earnings_days", lambda t: None)
    assert risk.earnings_risk("NVDA", 7) == (0.0, None)


# ── Market regime ───────────────────────────────────────────────
def _spy(pct):
    start = 100.0
    return [{"close": start}] * 4 + [{"close": start * (1 + pct / 100)}]


def test_calls_against_a_falling_market_are_penalised(monkeypatch):
    monkeypatch.setattr(uw, "candles", lambda *a, **k: _spy(-2.0))
    penalty, note = risk.market_regime("call")
    assert penalty == C.REGIME_PENALTY and note


def test_puts_with_a_falling_market_are_free(monkeypatch):
    monkeypatch.setattr(uw, "candles", lambda *a, **k: _spy(-2.0))
    assert risk.market_regime("put") == (0.0, None)


def test_a_quiet_market_penalises_nothing(monkeypatch):
    monkeypatch.setattr(uw, "candles", lambda *a, **k: _spy(0.2))
    assert risk.market_regime("call") == (0.0, None)
    assert risk.market_regime("put") == (0.0, None)


# ── Conviction ──────────────────────────────────────────────────
def test_mostly_sold_premium_is_penalised():
    penalty, note = risk.conviction_risk({"ask_premium": 20, "bid_premium": 80})
    assert penalty == C.CONVICTION_PENALTY and note


def test_mostly_bought_premium_is_free():
    assert risk.conviction_risk({"ask_premium": 80, "bid_premium": 20}) == (0.0, None)


# ── Aggregate ───────────────────────────────────────────────────
def test_penalty_is_capped(monkeypatch):
    monkeypatch.setattr(uw, "next_earnings_days", lambda t: 1)
    monkeypatch.setattr(uw, "candles", lambda *a, **k: _spy(-3.0))
    out = risk.assess("NVDA", "call", {"ask_premium": 5, "bid_premium": 95},
                      [{"dte": 7}])
    assert out["penalty"] == C.MAX_RISK_PENALTY      # 15+8+10 clipped to 20
    assert len(out["flags"]) == 3


def test_clean_setup_has_no_penalty(monkeypatch):
    monkeypatch.setattr(uw, "next_earnings_days", lambda t: 60)
    monkeypatch.setattr(uw, "candles", lambda *a, **k: _spy(0.1))
    out = risk.assess("NVDA", "call", {"ask_premium": 90, "bid_premium": 10},
                      [{"dte": 7}])
    assert out == {"penalty": 0.0, "flags": []}
