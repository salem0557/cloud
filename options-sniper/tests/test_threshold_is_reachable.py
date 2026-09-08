"""The alert gate must be a gate, not a wall.

THRESHOLD shipped at 85 out of 100. Nothing was broken: the scanner ran, UW
answered, the tickers were scored — and no alert was ever possible, so no
paper trade was either. A gate set above what the scoring can produce is
silent. It looks exactly like a quiet market.

These tests fail if the threshold drifts back out of reach.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import scoring

PERFECT_FLOW = {"premium_usd": 10_000_000, "sweep_count": 20, "vol_oi_ratio": 5,
                "ask_side_premium": 10_000_000, "bid_side_premium": 0,
                "call_premium": 10_000_000, "put_premium": 0,
                "underlying_price": 100}
PERFECT_TECH = {"broke_level": True, "break_distance_atr": 3,
                "volume_ratio": 6, "closed_beyond": True}
# 2% wide. A real contract is never quoted at zero spread, so scoring the
# ceiling off a 0% spread would flatter the gate by three points.
REAL_CONTRACT = {"bid": 1.00, "ask": 1.02, "open_interest": 100_000, "volume": 5000}


def test_a_setup_with_no_news_can_still_alert():
    """The gate must not make a news catalyst mandatory.

    Salem never asked for that rule, and at 85 it was in the arithmetic:
    flow 30 + technical 30 + catalyst 0 + liquidity 20 = 80 < 85. A clean
    break on heavy one-way flow is a setup whether or not a headline ran.
    """
    best = (scoring.flow_score(PERFECT_FLOW)
            + scoring.technical_score(PERFECT_TECH)
            + scoring.catalyst_score([], "call")
            + scoring.liquidity_score(REAL_CONTRACT))
    assert best >= C.THRESHOLD, (
        f"with no news the best possible score is {best}, below THRESHOLD "
        f"{C.THRESHOLD} — no news would mean no alert, ever")


def test_the_setup_salem_describes_reaches_the_gate():
    """His own words: good news, a buy wave, a clean break of resistance.

    Ordinary strong numbers, not a once-a-year tape. If this cannot alert,
    the system cannot do the job it was built for.
    """
    flow = {"premium_usd": 1_500_000, "sweep_count": 3, "vol_oi_ratio": 1.5,
            "ask_side_premium": 1_125_000, "bid_side_premium": 375_000,
            "call_premium": 1_400_000, "put_premium": 100_000,
            "underlying_price": 100}
    tech = {"broke_level": True, "break_distance_atr": 0.5,
            "volume_ratio": 2.0, "closed_beyond": True}
    news = [{"headline": "Company beats estimates, raises guidance",
             "is_major": True}]
    contract = {"bid": 1.00, "ask": 1.04, "open_interest": 800, "volume": 400}
    total = (scoring.flow_score(flow) + scoring.technical_score(tech)
             + scoring.catalyst_score(news, "call")
             + scoring.liquidity_score(contract))
    assert total >= C.THRESHOLD, (
        f"a strong ordinary setup scores {total}, below THRESHOLD {C.THRESHOLD}")


def test_the_watchlist_floor_leaves_room_for_a_break():
    """A ticker is watched BEFORE it breaks, when technical scores 0.

    If the floor sits at the threshold there is nothing to watch: anything
    that qualifies has already alerted.
    """
    assert C.WATCHLIST_FLOOR < C.THRESHOLD
    assert C.THRESHOLD - C.WATCHLIST_FLOOR >= 15, (
        "the gap is what a fresh break is worth — too narrow and the "
        "watchlist path can never promote anything")


def test_the_threshold_is_inside_the_scale():
    assert 0 < C.THRESHOLD <= sum(C.WEIGHTS.values())
