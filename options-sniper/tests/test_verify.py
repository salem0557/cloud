"""The gate that stands between a bug and Salem's money.

Salem, 2026-09-09:

    "لا اريد ان اشتري عقد ترسل تنبيه عليه ويكون مقلب بسبب خطاء برمجي ثم
     ارسل لك تقول اوووه اكتشفت خطاء بالكود"

Not "have no bugs" — nobody can promise that. "When you are broken, send me
nothing instead of something wrong."

Each test below is one shape of defect this project has actually shipped or
could ship, injected on purpose. If the gate stops noticing one of them, the
test fails here rather than in his account.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import verify


def _tier(**kw):
    t = {"tier": "🟢", "option_symbol": "AMD260918C00500000", "strike": 500,
         "type": "call", "expiry": "2099-09-18", "ask": 1.85, "bid": 1.75,
         "cost": 185.0, "delta": 0.44, "open_interest": 900, "dte": 9}
    t.update(kw)
    return t


def _payload(tech=None, **kw):
    p = {"ticker": "AMD", "direction": "call", "spot": 500.0, "score": 80,
         "technical": tech or {"level": 99.0, "target": 105.0, "stop": 95.0,
                               "atr": 4.0, "remaining_atr": 2.0},
         "tiers": [_tier()]}
    p.update(kw)
    return p


MSG = "alert text 500 @ $1.85"


# ── A coherent alert passes ─────────────────────────────────────
def test_a_sound_alert_is_not_blocked():
    assert verify.problems(_payload(), MSG) == []
    assert verify.blocks(_payload(), MSG) is None


# ── Geometry: the defect class of an inverted comparison ────────
def test_a_call_whose_target_sits_below_its_level_is_blocked():
    """_wick_back shipped with exactly this shape of error — a comparison the
    wrong way round, passing every other check."""
    bad = {"level": 99.0, "target": 90.0, "stop": 95.0, "atr": 4.0,
           "remaining_atr": 2.0}
    assert verify.blocks(_payload(bad), MSG)


def test_a_call_whose_stop_sits_above_its_level_is_blocked():
    bad = {"level": 99.0, "target": 105.0, "stop": 101.0, "atr": 4.0,
           "remaining_atr": 2.0}
    assert verify.blocks(_payload(bad), MSG)


def test_a_put_is_checked_the_other_way_round():
    good = {"level": 99.0, "target": 93.0, "stop": 103.0, "atr": 4.0,
            "remaining_atr": 2.0}
    p = _payload(good, direction="put", tiers=[_tier(type="put", delta=-0.44)])
    assert verify.blocks(p, MSG) is None
    # and the same numbers as a CALL are impossible
    assert verify.blocks(_payload(good), MSG)


# ── The anti-chasing gate, re-checked at the door ───────────────
def test_a_top_that_slipped_past_is_late_check_is_blocked():
    """A gate that lives in one place is a gate one refactor walks around."""
    late = {"level": 99.0, "target": 105.0, "stop": 95.0, "atr": 4.0,
            "remaining_atr": C.MIN_REMAINING_ATR - 0.01}
    assert verify.blocks(_payload(late), MSG)


# ── ATR: the defect class of a number from another calculation ──
def test_a_target_built_with_a_different_atr_is_blocked():
    """level+target say the ATR was 6.0; the payload says 4.0. One of the two
    is wrong and the message quotes both."""
    bad = {"level": 99.0, "target": 108.0, "stop": 95.0, "atr": 4.0,
           "remaining_atr": 2.0}
    assert verify.blocks(_payload(bad), MSG)


def test_a_zero_atr_is_blocked():
    bad = {"level": 99.0, "target": 105.0, "stop": 95.0, "atr": 0.0,
           "remaining_atr": 2.0}
    assert verify.blocks(_payload(bad), MSG)


# ── The contract has to be the trade the alert describes ────────
def test_a_call_alert_carrying_a_put_contract_is_blocked():
    assert verify.blocks(_payload(tiers=[_tier(type="put")]), MSG)


def test_a_call_with_a_negative_delta_is_blocked():
    assert verify.blocks(_payload(tiers=[_tier(delta=-0.44)]), MSG)


def test_an_expired_contract_is_blocked():
    assert verify.blocks(_payload(tiers=[_tier(expiry="2020-01-17")]), MSG)


def test_a_contract_with_no_expiry_is_blocked():
    assert verify.blocks(_payload(tiers=[_tier(expiry=None)]), MSG)


def test_a_cost_that_does_not_match_the_price_is_blocked():
    """Salem, 2026-09-08: "سعر العقد اللي جبته غير صحيح". $1.85 a contract is
    $185, and any other number in that line is a bug he pays for."""
    assert verify.blocks(_payload(tiers=[_tier(cost=18.5)]),
                         "alert text 500 @ $1.85")


def test_a_bid_above_the_ask_is_blocked():
    assert verify.blocks(_payload(tiers=[_tier(bid=2.50)]), MSG)


def test_a_free_contract_is_blocked():
    assert verify.blocks(_payload(tiers=[_tier(ask=0.0)]), MSG)


def test_an_alert_with_no_contract_at_all_is_blocked():
    assert verify.blocks(_payload(tiers=[]), MSG)
    assert verify.blocks(_payload(tiers=[{"tier": "🟢", "option_symbol": None}]),
                         MSG)


def test_illiquid_open_interest_is_blocked():
    assert verify.blocks(_payload(tiers=[_tier(open_interest=1)]), MSG)


# ── The message must carry the numbers it was built from ────────
def test_a_message_showing_a_price_the_payload_does_not_hold_is_blocked():
    """Every payload check passes here. Only the rendered text is wrong, which
    is the one thing Salem actually reads."""
    assert verify.blocks(_payload(), "alert text 500 @ $18.50")


def test_a_message_showing_the_wrong_strike_is_blocked():
    assert verify.blocks(_payload(), "alert text 480 @ $1.85")


def test_the_message_check_is_skipped_when_there_is_no_message():
    """verify runs on the payload alone in places that have not rendered yet;
    a missing message is not a defect."""
    assert verify.problems(_payload()) == []


# ── The refusal itself ──────────────────────────────────────────
def test_an_unknown_direction_stops_everything_immediately():
    assert verify.blocks(_payload(direction=None), MSG)
    assert verify.blocks(_payload(direction="both"), MSG)


def test_the_reason_names_what_it_found():
    """A blocked alert that says only "blocked" costs an hour of reading logs
    to find out what happened."""
    why = verify.blocks(_payload(tiers=[_tier(type="put")]), MSG)
    assert "بوت" in why or "put" in why


def test_it_never_edits_the_alert_into_shape():
    """A message that had to be corrected to be sent is one nobody checked."""
    p = _payload(tiers=[_tier(cost=18.5)])
    before = dict(p["tiers"][0])
    verify.blocks(p, MSG)
    assert p["tiers"][0] == before


# ── Both send paths are gated, not just one ─────────────────────
def test_the_scanner_and_the_monitor_both_call_it():
    import inspect
    import monitor
    import scanner
    for mod in (scanner, monitor):
        assert "verify.blocks" in inspect.getsource(mod), (
            f"{mod.__name__} can reach Telegram without the gate")
