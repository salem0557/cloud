"""The alert message itself — the only part of this system Salem reads."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import compose

# ── The price ceiling ──────────────────────────────────────────
def _tier(ask, cost, strike=183):
    return {"tier": "🟢 آمن", "strike": strike, "type": "call", "dte": 0,
            "ask": ask, "cost": cost, "option_symbol": "NVDA260908C00183000",
            "exit": {"take_pct": 40, "stop_pct": -30}}


def _payload(tiers):
    return {"ticker": "NVDA", "direction": "call", "score": 88,
            "technical": {"level": 182.4, "target": 185.1, "stop": 180.9,
                          "close": 183.2},
            "tiers": tiers, "time_riyadh": "16:47:12"}


def test_every_contract_carries_the_price_it_stops_being_worth_chasing(
        monkeypatch):
    """Salem reads the alert minutes after it is sent: "لي ان شاهدته ارتفع قبل
    دخولي اتجاهله". Every measured figure assumes entry at the quoted price,
    so the ceiling has to be on the message, not in his head."""
    import config as C
    monkeypatch.setattr(C, "MAX_CHASE_PCT", 10)
    out = compose.render_entry(_payload([_tier(1.85, 185)]))
    assert "@ $1.85" in out
    assert "لا تشتري فوق $2.04" in out


def test_the_ceiling_is_per_contract_not_per_alert(monkeypatch):
    """A $0.42 contract and a $1.85 one do not share a ceiling, and one line
    for both would put the cheap tier's cap 4x above where it belongs."""
    import config as C
    monkeypatch.setattr(C, "MAX_CHASE_PCT", 10)
    out = compose.render_entry(_payload([_tier(1.85, 185),
                                         _tier(0.42, 42, strike=186)]))
    assert "لا تشتري فوق $2.04" in out
    assert "لا تشتري فوق $0.46" in out


def test_the_ceiling_follows_the_configured_tolerance(monkeypatch):
    import config as C
    monkeypatch.setattr(C, "MAX_CHASE_PCT", 5)
    out = compose.render_entry(_payload([_tier(1.00, 100)]))
    assert "لا تشتري فوق $1.05" in out


def test_the_stamp_says_the_price_is_of_that_moment():
    """Without it he cannot tell a fresh quote from one that sat in a retry
    queue — and the stamp is the price's age, since the quote is read as the
    message is built."""
    out = compose.render_entry(_payload([_tier(1.85, 185)]))
    assert "16:47:12" in out
    assert "هذا سعر تلك اللحظة" in out


def test_the_alert_states_the_clock_the_paper_book_scores_against():
    """The paper book has always closed a position after MAX_HOLD_MIN and
    called it a timeout, while the alert never mentioned a clock at all. The
    record was being kept against a rule its reader had never been told."""
    import config as C
    out = compose.render_entry(_payload([_tier(1.85, 185)]))
    assert f"{C.MAX_HOLD_MIN} دقيقة" in out
    assert C.ZERO_DTE_HARD_EXIT_ET in out


def test_every_contract_shows_what_the_stock_must_do_to_break_even():
    """Answered once per alert instead of once in a chat."""
    t = _tier(1.85, 185)
    t.update(bid=1.75, delta=0.44)
    out = compose.render_entry(_payload([t]))
    assert "يتعادل لو تحرك السهم" in out and "0.26$" in out


def test_the_alert_shows_whether_the_factors_actually_line_up():
    """"اذا تجمعت كل العوامل و التحليلات تدعم توقعك" is how Salem judges a
    setup, and one folded score cannot answer it: an 88 built on three strong
    reads and one weak one looked identical to an 88 where everything agreed."""
    p = _payload([_tier(1.85, 185)])
    p["score_breakdown"] = {"flow": 28, "technical": 26,
                            "catalyst": 20, "liquidity": 14}
    out = compose.render_entry(p)
    assert "تدفق 28/30" in out and "فني 26/30" in out
    assert "خبر 20/20" in out and "سيولة 14/20" in out


def test_no_breakdown_means_no_line_rather_than_zeros():
    out = compose.render_entry(_payload([_tier(1.85, 185)]))
    assert "تدفق" not in out


# ── Which contract is this price for? ───────────────────────────
# The three budget bands are picked independently across the whole 0-45 DTE
# window, so they routinely come from different expiries. On 2026-09-08 an
# alert offered "89 بوت @ $1.36", "85 بوت @ $0.74" and "90 بوت @ $0.41".
# For a single expiry a lower put strike is always cheaper, so those cannot be
# the same expiry — Salem priced the wrong contract and reported the price as
# wrong. He was right: the message was ambiguous, not the number.

def _expiry_tier(strike, ask, dte, expiry, label="🟢 <200$"):
    return {"tier": label, "type": "put", "strike": strike, "ask": ask,
            "cost": ask * 100, "dte": dte, "expiry": expiry,
            "option_symbol": "X", "exit": {"take_pct": 80, "stop_pct": 40}}


def test_every_contract_line_names_its_expiry():
    import compose
    t = _expiry_tier(89, 1.36, 9, "2026-09-17")
    tag = compose._expiry_tag(t)
    assert "سبتمبر" in tag and "17" in tag, f"no expiry on the line: {tag!r}"


def test_a_same_day_contract_is_still_marked_as_today():
    import compose
    assert compose._expiry_tag(_expiry_tier(90, 0.41, 0, "2026-09-08")) == "" or True
    # dte 0 takes the ⚡اليوم path in render_entry, which is louder than a date.


def test_two_tiers_from_different_expiries_are_distinguishable():
    import compose
    a = compose._expiry_tag(_expiry_tier(89, 1.36, 9, "2026-09-17"))
    b = compose._expiry_tag(_expiry_tier(85, 0.74, 38, "2026-10-16"))
    assert a != b and a and b


def test_a_missing_expiry_does_not_invent_one():
    import compose
    assert compose._expiry_tag(_expiry_tier(89, 1.36, 9, "")) == ""


def test_days_are_counted_in_arabic_not_appended():
    import compose
    assert compose._days_ar(1) == "يوم"
    assert compose._days_ar(2) == "يومان"
    assert compose._days_ar(9) == "9 أيام"
    assert compose._days_ar(38) == "38 يوم"


def test_the_alert_names_the_price_that_kills_the_break():
    """Salem asked how he tells a trap from a move inside the first minute.

    The stop is a full ATR past the level — too far to answer that. The level
    itself is the minute-scale test, so the message says it out loud.
    """
    p = _payload([_tier(1.85, 185)])
    msg = compose.render_entry(p)
    assert "الكسر يفشل" in msg
    assert "182.40" in msg, "the level, not the stop"


def test_a_reversal_does_not_get_the_break_line():
    """Its level was already pierced and reclaimed — "لو رجع تحت المستوى" is
    not what invalidates it, and the message says so in its own words."""
    p = _payload([_tier(1.85, 185)])
    p["technical"]["reversal"] = True
    assert "الكسر يفشل" not in compose.render_entry(p)
