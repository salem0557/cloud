"""Guard rails for the rules Salem cares about. Run: python3 -m pytest tests -q"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config as C
from scoring import (contract_cost, contract_quality, passes_liquidity,
                     pick_contracts_by_budget,
                     catalyst_score, liquidity_score, expected_profit_pct,
                     best_contract, flow_score, technical_score)


def mk(sym, strike, ctype, bid, ask, delta, oi=1000):
    return {"option_symbol": sym, "strike": strike, "type": ctype, "bid": bid,
            "ask": ask, "delta": delta, "open_interest": oi, "expiry": "2026-10-16"}


# ── THE rule: cost = ask x 100 ──────────────────────────────────
def test_contract_cost_is_ask_times_100():
    assert contract_cost({"ask": 1.75}) == 175.0


def test_budget_filter_is_strict():
    spot = 100.0
    chain = [mk("A", 95, "call", 2.05, 2.10, 0.70),   # $210 -> over every tier
             mk("B", 100, "call", 0.95, 0.99, 0.50),  # $99
             mk("C", 110, "call", 0.40, 0.45, 0.20)]  # $45
    for _, c in pick_contracts_by_budget(chain, "call", spot):
        if c:
            assert contract_cost(c) <= 200


def test_bands_never_repeat_the_same_contract():
    """Under nested caps a $45 contract qualified for all three tiers."""
    spot = 100.0
    chain = [mk("A", 95, "call", 1.90, 1.95, 0.70),
             mk("B", 100, "call", 0.95, 0.99, 0.50),
             mk("C", 110, "call", 0.40, 0.45, 0.20)]
    picked = [c["option_symbol"] for _, c in pick_contracts_by_budget(chain, "call", spot) if c]
    assert len(picked) == len(set(picked)), picked


def test_a_band_with_nothing_in_it_returns_none():
    """Bands are disjoint ranges. A single $5 contract fills the under-$50
    band and leaves the other two empty rather than being reused."""
    spot = 100.0
    chain = [mk("D", 140, "call", 0.03, 0.05, 0.02, oi=900)]
    picks = dict(pick_contracts_by_budget(chain, "call", spot))
    assert picks["🟢 <200$"] is None
    assert picks["🟡 <100$"] is None
    assert picks["🔴 <50$"] is not None


def test_illiquid_contracts_dropped():
    assert not passes_liquidity(mk("X", 100, "call", 0.10, 0.90, 0.5))   # 160% spread
    assert not passes_liquidity(mk("Y", 100, "call", 1.00, 1.02, 0.5, oi=10))  # OI too low
    assert passes_liquidity(mk("Z", 100, "call", 1.00, 1.02, 0.5, oi=900))


# ── Catalyst must respect direction ─────────────────────────────
def test_downgrade_does_not_reward_a_call():
    news = [{"headline": "Analyst downgrade for XYZ", "is_major": True}]
    assert catalyst_score(news, "call") == 0.0
    assert catalyst_score(news, "put") == 20.0


def test_no_news_is_zero():
    assert catalyst_score([], "call") == 0.0


# ── Liquidity is scored on an affordable contract ───────────────
def test_best_contract_is_affordable():
    spot = 100.0
    leap = mk("LEAP", 100, "call", 19.0, 20.0, 0.55, oi=99999)   # $2000
    cheap = mk("CHEAP", 100, "call", 1.00, 1.02, 0.50, oi=5000)
    assert best_contract([leap, cheap], "call", spot)["option_symbol"] == "CHEAP"


# ── Expected profit uses the measured move ──────────────────────
def test_expected_profit_scales_with_move():
    c = mk("A", 100, "call", 0.95, 1.00, 0.50)
    assert expected_profit_pct(c, 2.0) == 100.0    # 0.5 * 2.00 / 1.00 = 100%
    assert expected_profit_pct(c, 0) == 0.0


# ── Weight ceilings ─────────────────────────────────────────────
def test_scores_respect_their_ceilings():
    huge = {"premium_usd": 9e9, "sweep_count": 99, "call_premium": 9e9,
            "put_premium": 0, "vol_oi_ratio": 99}
    assert flow_score(huge) == C.WEIGHTS["flow"]
    assert technical_score({"broke_level": True, "break_distance_atr": 99,
                            "volume_ratio": 99, "closed_beyond": True}) == C.WEIGHTS["technical"]
    assert liquidity_score(mk("A", 100, "call", 1.00, 1.00, 0.5, oi=999999)) == C.WEIGHTS["liquidity"]
    assert technical_score(None) == 0.0


# ── Cheap contracts must not be failed by a percentage-only spread cap ──
def test_cheap_contract_passes_on_absolute_spread():
    """$0.42/$0.46 is 9.1% wide but only 4 cents — perfectly tradable."""
    c = mk("OTM", 108, "call", 0.42, 0.46, 0.19, oi=3300)
    assert passes_liquidity(c)
    assert liquidity_score(c) > 0


def test_wide_cheap_contract_still_rejected():
    assert not passes_liquidity(mk("BAD", 130, "call", 0.01, 0.30, 0.02, oi=800))


def test_each_band_takes_the_contract_priced_into_it():
    """$198, $98 and $46 fall in one band each — no contract is eligible for
    two, which is what the old nested caps allowed."""
    spot = 102.4
    chain = [mk("A98", 98, "call", 1.90, 1.98, 0.68, oi=4200),
             mk("B102", 102, "call", 0.94, 0.98, 0.44, oi=6100),
             mk("C108", 108, "call", 0.42, 0.46, 0.19, oi=3300)]
    picks = dict(pick_contracts_by_budget(chain, "call", spot))
    assert picks["🟢 <200$"]["option_symbol"] == "A98"
    assert picks["🟡 <100$"]["option_symbol"] == "B102"
    assert picks["🔴 <50$"]["option_symbol"] == "C108"


# ── Inside a band, the pick is by quality, not by character ─────
def test_quality_prefers_a_reachable_strike():
    """A strike four ATRs away is not a cheaper version of a close one; it is
    a different bet. Character-based picking could not see that."""
    spot, atr = 100.0, 2.0
    near = mk("NEAR", 102, "call", 0.44, 0.46, 0.40, oi=5000)
    far = mk("FAR", 112, "call", 0.44, 0.46, 0.40, oi=5000)
    assert (contract_quality(near, spot, 2.0, atr)
            > contract_quality(far, spot, 2.0, atr))


def test_quality_prefers_a_real_book():
    spot, atr = 100.0, 2.0
    liquid = mk("LIQ", 102, "call", 0.45, 0.46, 0.40, oi=8000)
    thin = mk("THIN", 102, "call", 0.35, 0.46, 0.40, oi=400)
    assert (contract_quality(liquid, spot, 2.0, atr)
            > contract_quality(thin, spot, 2.0, atr))


def test_an_absurd_profit_estimate_is_capped():
    """A 900% figure on a far-OTM contract is delta arithmetic, not an edge —
    it must not outweigh a dead book and an unreachable strike."""
    spot, atr = 100.0, 2.0
    lottery = mk("LOTTO", 130, "call", 0.01, 0.03, 0.02, oi=400)
    solid = mk("SOLID", 102, "call", 0.44, 0.46, 0.40, oi=6000)
    assert (contract_quality(solid, spot, 3.0, atr)
            > contract_quality(lottery, spot, 3.0, atr))


def test_quality_is_bounded():
    spot, atr = 100.0, 2.0
    for c in (mk("A", 100, "call", 0.44, 0.46, 0.50, oi=9999),
              mk("B", 150, "call", 0.01, 0.02, 0.01, oi=300)):
        q = contract_quality(c, spot, 2.0, atr)
        assert 0.0 <= q <= 1.0


def test_the_best_in_a_band_wins_not_the_dearest():
    """Two contracts in the same band: the one with a reachable strike and a
    book beats the pricier one without either."""
    spot, atr = 100.0, 2.0
    chain = [mk("GOOD", 102, "call", 0.44, 0.46, 0.40, oi=8000),
             mk("BAD", 118, "call", 0.47, 0.49, 0.08, oi=350)]
    picks = dict(pick_contracts_by_budget(chain, "call", spot,
                                          expected_move=2.0, atr=atr))
    assert picks["🔴 <50$"]["option_symbol"] == "GOOD"
