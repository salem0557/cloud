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
