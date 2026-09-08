"""A break the alert rule rejects by a hair, taken on paper only.

Measured 2026-09-08 on the two strongest names in the market, both rejected
and both of which would have lost money:

    BE  09:45   +0.56 ATR left   close 278.44 -> 276 within the hour
    AMD 10:15   +0.71 ATR left   close 500.00 -> 499

MIN_REMAINING_ATR is 0.75. One session cannot say whether that number earns
its place, so the rejected setups go into the paper book and a month of real
outcomes answers it.

The rule that must never bend: none of this reaches Salem. No Telegram, no
daily cap, no alert journal. He sees 943; this lives in 944.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import technical


def _tech(remaining, broke=True):
    return {"broke_level": broke, "remaining_atr": remaining, "atr": 4.0,
            "close": 100.0, "level": 99.0, "target": 105.0, "stop": 95.0,
            "break_distance_atr": 0.3, "volume_ratio": 2.0,
            "closed_beyond": True, "bar_high": 100.5, "bar_low": 98.0}


# ── What counts as a near miss ──────────────────────────────────
def test_room_left_but_under_the_rule_is_a_near_miss():
    """AMD's 10:15 bar: 0.71 ATR left where the rule wants 0.75."""
    assert technical.is_near_miss(_tech(0.71))
    assert technical.is_late(_tech(0.71))       # still not an alert


def test_a_break_past_its_target_is_never_a_near_miss():
    """BE's 09:30 bar closed 12 dollars beyond its target: -3.38 ATR.

    That is the top, not a near miss. It must not be taken on paper either —
    a book full of tops would answer the wrong question.
    """
    assert not technical.is_near_miss(_tech(-3.38))
    assert not technical.is_near_miss(_tech(-0.01))


def test_a_setup_with_enough_room_is_a_normal_alert_not_a_test():
    assert not technical.is_near_miss(_tech(C.MIN_REMAINING_ATR))
    assert not technical.is_near_miss(_tech(2.0))


def test_a_ticker_that_never_broke_is_not_a_near_miss():
    assert not technical.is_near_miss(_tech(0.5, broke=False))
    assert not technical.is_near_miss(None)


# ── The line that must never be crossed ─────────────────────────
def test_the_scanner_sends_a_near_miss_to_paper_and_never_to_telegram(monkeypatch):
    import scanner
    sent, recorded = [], []
    monkeypatch.setattr(scanner, "send", lambda m: sent.append(m) or 1)
    monkeypatch.setattr(scanner.paper, "record", lambda p, tier=None: recorded.append(p) or {})
    monkeypatch.setattr(scanner.market, "is_open", lambda *a: True)
    monkeypatch.setattr(scanner.state, "capacity_left", lambda: 30)
    monkeypatch.setattr(scanner.state, "read", lambda: {"alerted_tickers": []})
    monkeypatch.setattr(scanner.uw, "flow_alerts", lambda *a, **k: [])
    monkeypatch.setattr(scanner.finviz, "movers", lambda **k: [])

    cand = {"ticker": "AMD", "score": 88.0, "near_miss": True,
            "direction": "call", "spot": 500.0,
            "score_breakdown": {"flow": 10.2, "technical": 27.9,
                                "catalyst": 20.0, "liquidity": 20.0},
            "technical": _tech(0.71), "flow": {}, "chain": [], "news": [],
            "flow_reason": "", "risk": {"penalty": 0.0, "flags": []},
            "raw_score": 88.0}
    monkeypatch.setattr(scanner, "to_payload", lambda c: {"ticker": c["ticker"],
                                                          "tiers": [], "score": c["score"]})
    monkeypatch.setattr(scanner, "aggregate_flow", lambda a: {"AMD": {"premium_usd": 1e6}})
    monkeypatch.setattr(scanner, "evaluate", lambda t, f, d=False: cand)
    monkeypatch.setattr(C, "MIN_TICKER_PREMIUM", 0)
    monkeypatch.setattr(scanner.uw, "spent", lambda: "")
    monkeypatch.setattr(C, "SHORTLIST_FILE", pathlib.Path("/tmp/_nm_shortlist.json"))

    assert scanner.main() == 0            # nothing "sent"
    assert sent == [], "a near miss reached Telegram"
    assert len(recorded) == 1, "a near miss did not reach the paper book"
    assert recorded[0]["near_miss"] is True


def test_a_near_miss_does_not_consume_the_daily_alert_cap(monkeypatch):
    """30 alerts a day is what Salem SEES. A silent paper trade is not one."""
    import scanner
    reserved = []
    monkeypatch.setattr(scanner.state, "record_alert", lambda t: reserved.append(t) or True)
    monkeypatch.setattr(scanner, "send", lambda m: 1)
    monkeypatch.setattr(scanner.paper, "record", lambda p, tier=None: {})
    monkeypatch.setattr(scanner.market, "is_open", lambda *a: True)
    monkeypatch.setattr(scanner.state, "capacity_left", lambda: 30)
    monkeypatch.setattr(scanner.state, "read", lambda: {"alerted_tickers": []})
    monkeypatch.setattr(scanner.uw, "flow_alerts", lambda *a, **k: [])
    monkeypatch.setattr(scanner.finviz, "movers", lambda **k: [])
    monkeypatch.setattr(scanner.uw, "spent", lambda: "")
    monkeypatch.setattr(C, "MIN_TICKER_PREMIUM", 0)
    monkeypatch.setattr(C, "SHORTLIST_FILE", pathlib.Path("/tmp/_nm_shortlist2.json"))
    cand = {"ticker": "BE", "score": 90.0, "near_miss": True, "direction": "call",
            "score_breakdown": {"technical": 29.5}, "technical": _tech(0.56)}
    monkeypatch.setattr(scanner, "to_payload", lambda c: {"ticker": c["ticker"], "tiers": []})
    monkeypatch.setattr(scanner, "aggregate_flow", lambda a: {"BE": {"premium_usd": 1e6}})
    monkeypatch.setattr(scanner, "evaluate", lambda t, f, d=False: cand)

    scanner.main()
    assert reserved == [], "a near miss reserved one of the day's alert slots"


def test_a_near_miss_stays_off_the_watchlist(monkeypatch):
    """The watchlist is for names that have NOT broken yet. This one has."""
    import json
    import scanner
    out = pathlib.Path("/tmp/_nm_shortlist3.json")
    monkeypatch.setattr(C, "SHORTLIST_FILE", out)
    monkeypatch.setattr(C, "MIN_TICKER_PREMIUM", 0)
    monkeypatch.setattr(scanner.market, "is_open", lambda *a: True)
    monkeypatch.setattr(scanner.state, "capacity_left", lambda: 30)
    monkeypatch.setattr(scanner.state, "read", lambda: {"alerted_tickers": []})
    monkeypatch.setattr(scanner.uw, "flow_alerts", lambda *a, **k: [])
    monkeypatch.setattr(scanner.finviz, "movers", lambda **k: [])
    monkeypatch.setattr(scanner.uw, "spent", lambda: "")
    monkeypatch.setattr(scanner.paper, "record", lambda p, tier=None: {})
    monkeypatch.setattr(scanner, "send", lambda m: 1)
    cand = {"ticker": "BE", "score": 90.0, "near_miss": True, "direction": "call",
            "spot": 278.44, "score_breakdown": {"technical": 29.5},
            "technical": _tech(0.56)}
    monkeypatch.setattr(scanner, "to_payload", lambda c: {"ticker": c["ticker"], "tiers": []})
    monkeypatch.setattr(scanner, "aggregate_flow", lambda a: {"BE": {"premium_usd": 1e6}})
    monkeypatch.setattr(scanner, "evaluate", lambda t, f, d=False: cand)

    scanner.main()
    assert json.loads(out.read_text()) == []


# ── evaluate() itself, where the branch lives ───────────────────
def _wire_evaluate(monkeypatch, remaining):
    import scanner
    monkeypatch.setattr(scanner.uw, "candles", lambda *a, **k: ["bar"] * 60)
    monkeypatch.setattr(scanner.technical, "analyse",
                        lambda *a, **k: _tech(remaining))
    monkeypatch.setattr(scanner.uw, "option_chain", lambda t: [{"strike": 500}])
    monkeypatch.setattr(scanner.uw, "news", lambda t: [])
    monkeypatch.setattr(scanner, "best_contract", lambda *a: {})
    monkeypatch.setattr(scanner, "flow_direction", lambda f: "call")
    return scanner


FLOW = {"premium_usd": 1_000_000, "underlying_price": 500.0,
        "sweep_count": 3, "vol_oi_ratio": 1.5, "call_premium": 900_000,
        "put_premium": 100_000, "ask_side_premium": 750_000,
        "bid_side_premium": 250_000, "alerts": 4, "rules": []}


def test_evaluate_flags_a_near_miss_instead_of_dropping_it(monkeypatch):
    scanner = _wire_evaluate(monkeypatch, 0.71)
    monkeypatch.setattr(C, "PAPER_NEAR_MISS", True)
    cand = scanner.evaluate("AMD", FLOW)
    assert cand is not None, "the near miss was dropped before the paper book"
    assert cand["near_miss"] is True


def test_evaluate_drops_a_break_past_its_target_even_with_the_flag_on(monkeypatch):
    scanner = _wire_evaluate(monkeypatch, -3.38)
    monkeypatch.setattr(C, "PAPER_NEAR_MISS", True)
    assert scanner.evaluate("BE", FLOW) is None


def test_turning_the_flag_off_restores_the_old_behaviour(monkeypatch):
    """PAPER_NEAR_MISS=0 in the environment and the near miss is dropped
    exactly as it was before, with nothing reaching the paper book."""
    scanner = _wire_evaluate(monkeypatch, 0.71)
    monkeypatch.setattr(C, "PAPER_NEAR_MISS", False)
    assert scanner.evaluate("AMD", FLOW) is None


def test_a_normal_setup_is_not_flagged(monkeypatch):
    scanner = _wire_evaluate(monkeypatch, 2.0)
    monkeypatch.setattr(C, "PAPER_NEAR_MISS", True)
    cand = scanner.evaluate("AMD", FLOW)
    assert cand is not None and cand["near_miss"] is False
