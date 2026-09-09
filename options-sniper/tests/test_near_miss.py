"""Two gates: one for what Salem sees, a looser one for what gets measured.

    ALERT (943)   score >= THRESHOLD          and room >= MIN_REMAINING_ATR
    PAPER (944)   score >= PAPER_THRESHOLD    and room >= PAPER_MIN_REMAINING_ATR
    DROPPED       below either paper floor

The band between them is the point. A setup that is real enough to be worth
measuring but not good enough to send goes into the paper book alone, so the
alert gate can be re-derived from a month of outcomes instead of argued over.

Measured 2026-09-08, the two strongest names in the market: BE at +0.56 ATR
and AMD at +0.71. Both were rejected, the day produced no alert, the gate was
loosened to 0.38 to let them through — and both went on to LOSE. At the
measured 1.00 they are paper-only again, which is where they belonged.

The line that must never bend: nothing in the paper band reaches Salem. No
Telegram, no daily cap, no alert journal. He sees 943; this lives in 944.
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
def test_room_left_but_under_the_alert_gate_is_a_near_miss():
    assert technical.is_near_miss(_tech(0.30))
    assert technical.is_late(_tech(0.30))       # still not an alert


def test_the_two_names_that_produced_no_alert_are_paper_only_again():
    """BE +0.56 and AMD +0.71 on 2026-09-08.

    Loosening the gate to 0.38 turned these into alerts. Both then lost:
    BE 278.44 -> 276 within the hour, AMD 500.00 -> 499. At 1.00 they are
    back in the paper book, where a losing setup costs nothing and still
    gets counted.
    """
    for room in (0.56, 0.71):
        assert technical.is_late(_tech(room))
        assert technical.is_near_miss(_tech(room))


def test_a_break_past_its_target_is_never_a_near_miss():
    """BE's 09:30 bar closed 12 dollars beyond its target: -3.38 ATR.

    That is the top, not a near miss. It must not be taken on paper either —
    a book full of tops would answer the wrong question.
    """
    assert not technical.is_near_miss(_tech(-3.38))
    assert not technical.is_near_miss(_tech(-0.01))
    # Nor one that has all but reached it: below the paper floor there is
    # nothing left for an entry to collect.
    assert not technical.is_near_miss(_tech(C.PAPER_MIN_REMAINING_ATR - 0.01))


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
    monkeypatch.setattr(C, "WATCHLIST_ONLY", False)
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
    monkeypatch.setattr(C, "WATCHLIST_ONLY", False)
    monkeypatch.setattr(scanner.state, "record_alert",
                        lambda t, **kw: reserved.append(t) or True)
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
    monkeypatch.setattr(C, "WATCHLIST_ONLY", False)
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
    # These fixtures hand evaluate() placeholder candles because they patch
    # analyse(). reversal() reads the bars for real, so it is patched too —
    # this file is about the near-miss band, not the failed break.
    monkeypatch.setattr(scanner.technical, "reversal", lambda *a, **k: None)
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
    scanner = _wire_evaluate(monkeypatch, 0.30)
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
    scanner = _wire_evaluate(monkeypatch, 0.30)
    monkeypatch.setattr(C, "PAPER_NEAR_MISS", False)
    assert scanner.evaluate("AMD", FLOW) is None


def test_a_normal_setup_is_not_flagged(monkeypatch):
    scanner = _wire_evaluate(monkeypatch, 2.0)
    monkeypatch.setattr(C, "PAPER_NEAR_MISS", True)
    cand = scanner.evaluate("AMD", FLOW)
    assert cand is not None and cand["near_miss"] is False


# ── The score band between the two gates ────────────────────────
def test_the_two_gates_are_ordered():
    """A paper gate above the alert gate would silence 943 completely."""
    assert C.PAPER_THRESHOLD < C.THRESHOLD
    assert C.PAPER_MIN_REMAINING_ATR < C.MIN_REMAINING_ATR
    assert C.PAPER_MIN_REMAINING_ATR > 0, "a break at its target has nothing left"
    assert C.WATCHLIST_FLOOR <= C.PAPER_THRESHOLD, (
        "a name below the paper gate would be watched forever and never taken")


def _wire_main(monkeypatch, cand, shortlist_name):
    import scanner
    sent, recorded, reserved = [], [], []
    monkeypatch.setattr(C, "WATCHLIST_ONLY", False)  # score gates = discovery mode
    monkeypatch.setattr(scanner, "send", lambda m: sent.append(m) or 1)
    monkeypatch.setattr(scanner.paper, "record",
                        lambda p, tier=None: recorded.append(p) or {})
    monkeypatch.setattr(scanner.state, "record_alert",
                        lambda t, **kw: reserved.append(t) or True)
    monkeypatch.setattr(scanner.market, "is_open", lambda *a: True)
    monkeypatch.setattr(scanner.state, "capacity_left", lambda: 30)
    monkeypatch.setattr(scanner.state, "read", lambda: {"alerted_tickers": []})
    monkeypatch.setattr(scanner.uw, "flow_alerts", lambda *a, **k: [])
    monkeypatch.setattr(scanner.uw, "spent", lambda: "")
    monkeypatch.setattr(scanner.finviz, "movers", lambda **k: [])
    monkeypatch.setattr(scanner, "to_payload",
                        lambda c: {"ticker": c["ticker"], "tiers": [],
                                   "score": c["score"]})
    monkeypatch.setattr(scanner, "aggregate_flow",
                        lambda a: {cand["ticker"]: {"premium_usd": 1e6}})
    monkeypatch.setattr(scanner, "evaluate", lambda t, f, d=False: cand)
    monkeypatch.setattr(scanner, "compose", lambda kind, p: "alert text")
    monkeypatch.setattr(scanner.mine, "remember_alert", lambda mid, p: None)
    monkeypatch.setattr(scanner.journal, "log_alert", lambda p, kind=None: None)
    monkeypatch.setattr(C, "MIN_TICKER_PREMIUM", 0)
    monkeypatch.setattr(C, "SHORTLIST_FILE", pathlib.Path(shortlist_name))
    scanner.main()
    return sent, recorded, reserved


def _cand(score, room):
    return {"ticker": "AMD", "score": score, "direction": "call", "spot": 500.0,
            "near_miss": False,
            "score_breakdown": {"flow": 10.2, "technical": 27.9,
                                "catalyst": 12.0, "liquidity": 10.0},
            "technical": _tech(room)}


def test_a_score_inside_the_band_goes_to_paper_and_not_to_telegram(monkeypatch):
    """Real enough to measure, not good enough to send."""
    mid = (C.PAPER_THRESHOLD + C.THRESHOLD) / 2
    sent, recorded, reserved = _wire_main(monkeypatch, _cand(mid, 2.0), "/tmp/_b1.json")
    assert sent == [], "a setup below the alert gate reached Telegram"
    assert reserved == [], "it consumed one of the day's alert slots"
    assert len(recorded) == 1 and recorded[0]["near_miss"] is True


def test_a_score_above_the_alert_gate_is_sent(monkeypatch):
    sent, recorded, _ = _wire_main(monkeypatch, _cand(C.THRESHOLD + 5, 2.0),
                                   "/tmp/_b2.json")
    assert len(sent) == 1, "a qualifying setup was not sent"
    assert len(recorded) == 1 and not recorded[0].get("near_miss")


def test_a_score_below_the_paper_gate_is_taken_nowhere(monkeypatch):
    sent, recorded, _ = _wire_main(monkeypatch, _cand(C.PAPER_THRESHOLD - 1, 2.0),
                                   "/tmp/_b3.json")
    assert sent == [] and recorded == []


def test_the_band_closes_when_the_paper_book_is_off(monkeypatch):
    """PAPER_NEAR_MISS=0 and the scanner stops at the alert gate, as before."""
    monkeypatch.setattr(C, "PAPER_NEAR_MISS", False)
    mid = (C.PAPER_THRESHOLD + C.THRESHOLD) / 2
    sent, recorded, _ = _wire_main(monkeypatch, _cand(mid, 2.0), "/tmp/_b4.json")
    assert sent == [] and recorded == []


# ── A confirmed break is judged by its own gate ─────────────────
# Salem: "لا انا اريد رسائل تنبيه اكثر... انا ارى انك تضيع علي فرص كثيرة".
# The measurement on the live 16:56Z scan (41 real scores) said where the
# alerts were going: with a break adding a realistic 25 technical points,
# gate 70 let 10 of 41 through and gate 50 let 34 through.
#
# So a setup where PRICE has already broken and held is judged by
# BREAK_THRESHOLD, and one built only on flow and a headline still has to
# clear THRESHOLD. Price is the one input that cannot be talked into agreeing.

def _broke():
    """A tech dict that confirms(): broken, on volume, held, with room."""
    return {"broke_level": True, "remaining_atr": 2.0, "atr": 4.0,
            "close": 100.0, "level": 99.0, "target": 105.0, "stop": 95.0,
            "break_distance_atr": 0.3, "volume_ratio": 3.0,
            "closed_beyond": True, "closed_strong": True, "wick_back": False,
            "bar_high": 100.5, "bar_low": 98.0, "opening_range": False}


def test_a_confirmed_break_uses_the_lower_gate():
    assert technical.confirms(_broke())
    assert technical.alert_gate(_broke()) == C.BREAK_THRESHOLD


def test_a_setup_with_no_break_still_has_to_clear_the_high_gate():
    quiet = _broke()
    quiet["broke_level"] = False
    assert technical.alert_gate(quiet) == C.THRESHOLD


def test_a_break_that_reversed_inside_its_candle_gets_no_discount():
    """holds() is what makes the break real. Without it the lower gate would
    be handed to exactly the false breaks the filter exists to catch."""
    trap = _broke()
    trap["closed_strong"] = False
    assert not technical.confirms(trap)
    assert technical.alert_gate(trap) == C.THRESHOLD


def test_a_score_between_the_two_gates_is_sent_on_a_break(monkeypatch):
    """The whole point: this used to be silence."""
    mid = (C.BREAK_THRESHOLD + C.THRESHOLD) / 2
    cand = _cand(mid, 2.0)
    cand["technical"] = _broke()
    sent, recorded, _ = _wire_main(monkeypatch, cand, "/tmp/_g1.json")
    assert len(sent) == 1, "a confirmed break inside the band was not sent"
    assert not recorded[0].get("near_miss")


def test_the_same_score_without_a_break_is_not_sent(monkeypatch):
    mid = (C.BREAK_THRESHOLD + C.THRESHOLD) / 2
    quiet = _broke()
    quiet["broke_level"] = False
    cand = _cand(mid, 2.0)
    cand["technical"] = quiet
    sent, recorded, _ = _wire_main(monkeypatch, cand, "/tmp/_g2.json")
    assert sent == [], "a forecast with no break got the break gate"
    assert len(recorded) == 1 and recorded[0]["near_miss"] is True
