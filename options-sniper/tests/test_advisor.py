"""The adviser: watch a position Salem is IN and say when to get out.

Two words in his request are not buildable and the code says so rather than
pretending. "بالثانية" — UW serves one-minute bars, so a minute is the honest
cadence. "سيولة قادمة" — nothing here sees the future; what it reads is who
is trading it NOW.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

import advisor
import config as C

POS = {"ticker": "NVDA", "strike": 183, "type": "call", "direction": "call",
       "entry_price": 1.85, "option_symbol": "N183",
       "entry_date": "2026-09-08"}


def _f(**over):
    base = {"ticker": "NVDA", "strike": 183, "is_call": True, "entry": 1.85,
            "price": 1.90, "pct": 2.7, "pressure": {"ask_share": 0.7,
                                                    "volume": 400,
                                                    "minutes": 10},
            "strike_net": 5e6, "minutes_to_close": 180, "held_min": 6,
            "tech": {"level": 182.4, "close": 183.2}}
    base.update(over)
    return base


# ── the idea being dead outranks everything ────────────────────
def test_the_stock_falling_back_through_the_level_ends_it():
    """A target reached on a setup that has already broken is a number about
    to be given back, so this outranks the profit."""
    action, why = advisor.verdict(_f(pct=80.0,
                                     tech={"level": 182.4, "close": 181.0}))
    assert action == "اخرج"
    assert "الفكرة انتهت" in why[0]


def test_a_put_is_dead_when_the_stock_goes_back_UP_through_the_level():
    action, _ = advisor.verdict(_f(is_call=False,
                                   tech={"level": 182.4, "close": 183.9}))
    assert action == "اخرج"


# ── the clock ──────────────────────────────────────────────────
def test_a_same_day_contract_is_called_in_before_the_bell():
    action, why = advisor.verdict(_f(minutes_to_close=10))
    assert action == "اخرج" and "الإغلاق" in why[0]


def test_the_clock_does_not_fire_when_the_market_is_already_closed():
    """minutes_to_close of 0 means shut, not urgent."""
    action, _ = advisor.verdict(_f(minutes_to_close=0))
    assert action != "اخرج"


# ── pressure, which is a fact about now and not a forecast ─────
def test_buyers_leaving_turns_it_to_watch():
    action, why = advisor.verdict(_f(pressure={"ask_share": 0.30,
                                               "volume": 300, "minutes": 10}))
    assert action == "راقب"
    assert any("الشراء خف" in w for w in why)


def test_a_tape_too_thin_to_read_is_named_not_assumed_calm():
    """Unknown is never reported as calm."""
    _a, why = advisor.verdict(_f(pressure=None))
    assert any("ما أقدر أقرأ الضغط" in w for w in why)


def test_the_strike_being_sold_today_turns_it_to_watch():
    action, why = advisor.verdict(_f(strike_net=-3e6))
    assert action == "راقب" and any("يبيعون" in w for w in why)


# ── the profit ─────────────────────────────────────────────────
def test_reaching_the_configured_target_says_get_out():
    take = C.EXIT_RULES[0][1]
    action, why = advisor.verdict(_f(pct=take + 1))
    assert action == "اخرج" and any(f"الهدف {take}%" in w for w in why)


def test_a_position_that_is_merely_fine_says_nothing():
    """"لا انا لا اريدك ترسل تلقائي عن حالة العقد" — a status report is not
    what he asked for. 'امسك' is the silent verdict and the caller sends
    nothing on it."""
    action, _why = advisor.verdict(_f())
    assert action == "امسك"


# ── something GOOD, and only when it is new ────────────────────
def test_crossing_a_step_for_the_first_time_is_worth_saying():
    action, why = advisor.verdict(_f(pct=22.0, peak_pct=8.0))
    assert action == "فرصة"
    assert any("تجاوز +20%" in w for w in why)


def test_the_same_step_is_not_reported_twice():
    """A contract that crossed +40% ten minutes ago and is still there is not
    news."""
    action, _ = advisor.verdict(_f(pct=22.0, peak_pct=21.0))
    assert action == "امسك"


def test_strong_buying_is_added_to_the_good_news_not_invented_as_it():
    """Below the take target, so this is news rather than an exit."""
    action, why = advisor.verdict(_f(pct=25.0, peak_pct=5.0,
                                     pressure={"ask_share": 0.82,
                                               "volume": 900, "minutes": 10}))
    assert action == "فرصة"
    assert any("تجاوز +20%" in w for w in why)
    assert any("الشراء قوي" in w for w in why)


def test_reaching_the_target_outranks_the_good_news():
    """+45% crosses the +40% step AND the exit target. The exit wins: a step
    is information, a target is a decision."""
    action, why = advisor.verdict(_f(pct=45.0, peak_pct=5.0))
    assert action == "اخرج"
    assert any("الهدف" in w for w in why)


def test_a_step_crossed_while_the_idea_is_dead_still_says_get_out():
    """Good news does not outrank a broken setup."""
    action, _ = advisor.verdict(_f(pct=45.0, peak_pct=5.0,
                                   tech={"level": 182.4, "close": 180.0}))
    assert action == "اخرج"


# ── the message ────────────────────────────────────────────────
def test_the_message_leads_with_the_verdict_and_the_move():
    f = _f(pct=45.0, price=2.68)
    action, why = advisor.verdict(f)
    msg = advisor.message(f, action, why)
    assert msg.startswith("🔴 اخرج — NVDA 183 كول")
    assert "$1.85 ← $2.68 (+45.0%)" in msg
    assert "القرار قرارك" in msg


def test_an_answer_he_asked_for_does_not_add_the_decision_line():
    f = _f(pct=45.0)
    action, why = advisor.verdict(f)
    assert "القرار قرارك" not in advisor.message(f, action, why, asked=True)


# ── the limits, stated in the code rather than promised away ───
def test_the_module_says_what_it_cannot_do():
    import inspect
    doc = inspect.getdoc(advisor)
    assert "بالثانية" in doc and "ONE MINUTE" in doc
    assert "سيولة قادمة" in doc and "does not make forecasts" in doc
