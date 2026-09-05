"""Scoring engine. Every number the alert shows is computed HERE, in code.
Claude only formats the message — it never invents or adjusts these values."""
import config as C
from config import (WEIGHTS, BUDGET_TIERS, MAX_SPREAD_PCT, MAX_SPREAD_ABS,
                    MIN_OPEN_INTEREST)


# ── 1) Options-flow score (0-30) ────────────────────────────────
def flow_score(flow: dict) -> float:
    """flow = aggregated UW data for one ticker today:
    {premium_usd, sweep_count, call_premium, put_premium, vol_oi_ratio}"""
    s = 0.0
    s += min(12, flow.get("premium_usd", 0) / 250_000)   # $3M premium = full 12
    s += min(8, flow.get("sweep_count", 0) * 2)          # 4+ sweeps  = full 8
    cp, pp = flow.get("call_premium", 0), flow.get("put_premium", 0)
    total = cp + pp
    if total > 0:
        s += abs(cp - pp) / total * 6                    # directional conviction
    s += min(4, flow.get("vol_oi_ratio", 0) * 2)         # vol/OI >= 2 = full 4

    # Conviction: premium lifted at the ask is someone paying up to get in.
    # Flow that is mostly bid-side is being sold, and scoring it like buying
    # is how a bearish tape reads as a bullish setup.
    ratio = ask_side_ratio(flow)
    if ratio > 0:
        s *= 0.6 + 0.4 * min(1.0, ratio / 0.7)          # 70%+ ask-side = no haircut

    # Multi-leg legs are not directional bets. Premium that is mostly spread
    # volume should not score as conviction in either direction.
    dshare = flow.get("directional_share")
    if dshare is not None:
        s *= max(0.3, min(1.0, dshare / C.MIN_DIRECTIONAL_SHARE))
    return min(WEIGHTS["flow"], round(s, 1))


def flow_direction(flow: dict) -> str:
    """Direction from premium that was actually BOUGHT, not merely traded.

    Comparing call premium to put premium is not enough: a contract hit on the
    bid was sold, not bought. $3M of call premium that is mostly bid-side is
    somebody writing calls — a bearish or neutral position — and reading it as
    bullish points the whole alert the wrong way. UW reports both sides on
    every alert and the first version fetched them and threw them away.

    Ask-side premium is used where the split is available, and the raw premium
    only as a fallback.
    """
    ca = flow.get("call_ask_premium")
    pa = flow.get("put_ask_premium")
    if ca is not None and pa is not None and (ca + pa) > 0:
        return "call" if ca >= pa else "put"
    return "call" if flow.get("call_premium", 0) >= flow.get("put_premium", 0) else "put"


def directional_share(contract: dict) -> float:
    """Fraction of a contract's volume that is NOT part of a multi-leg order.

    A live screen showed SPY 745P with 39,025 of its 39,829 contracts traded as
    legs of spreads — 98%. Read as one-way flow that is a large bearish bet;
    read correctly it is one side of a structure whose other leg says the
    opposite, and it carries no directional information at all. Three of eight
    heavily-traded contracts in that sample were over half multi-leg.

    flow_score counted every contract of premium the same way.
    """
    vol = contract.get("volume", 0)
    if vol <= 0:
        return 0.0
    ml = contract.get("multileg_volume", 0) or 0
    return max(0.0, min(1.0, (vol - ml) / vol))


def ask_side_ratio(flow: dict) -> float:
    """Share of this ticker's premium that was bought at the ask (0..1)."""
    ask = flow.get("ask_premium", 0)
    bid = flow.get("bid_premium", 0)
    total = ask + bid
    return (ask / total) if total > 0 else 0.0


# ── 2) Technical-break score (0-30) ─────────────────────────────
def technical_score(tech: dict) -> float:
    """tech = output of technical.analyse() — real 15m candle measurements."""
    if not tech or not tech.get("broke_level"):
        return 0.0
    s = 10.0                                             # confirmed break base
    s += min(8, tech.get("break_distance_atr", 0) * 8)   # 1 ATR beyond = full 8
    vr = tech.get("volume_ratio", 0)
    s += min(8, max(0.0, vr - 1.0) * 5)                  # 2.6x volume  = full 8
    if tech.get("closed_beyond"):                        # candle CLOSED beyond
        s += 4
    return min(WEIGHTS["technical"], round(s, 1))


# ── 3) Catalyst score (0-20) ────────────────────────────────────
BULLISH = ("upgrade", "beats", "beat", "raises guidance", "raised guidance",
           "approval", "fda approval", "acquisition", "merger", "buyback")
BEARISH = ("downgrade", "misses", "miss", "cuts guidance", "cut guidance",
           "lowers guidance", "investigation", "recall", "halt", "fraud")
NEUTRAL_STRONG = ("earnings", "guidance", "fda", "merger", "acquisition")


def catalyst_score(news: list, direction: str = None) -> float:
    """news = today's headlines [{headline, is_major, ...}, ...]

    Direction-aware: the original version handed a full 20 to any headline
    containing 'downgrade' even when the flow was bullish. A catalyst pointing
    the opposite way to the flow is a reason for caution, not a bonus.
    """
    if not news:
        return 0.0
    text = " ".join((n.get("headline") or "").lower() for n in news)
    major = any(n.get("is_major") for n in news)

    bull = any(k in text for k in BULLISH)
    bear = any(k in text for k in BEARISH)
    hard = any(k in text for k in NEUTRAL_STRONG)

    if direction == "call":
        aligned, opposed = bull, bear
    elif direction == "put":
        aligned, opposed = bear, bull
    else:
        aligned, opposed = (bull or bear), False

    if aligned and not opposed:
        return float(WEIGHTS["catalyst"])                # 20 — aligned catalyst
    if aligned and opposed:
        return 8.0                                       # mixed news
    if opposed:
        return 0.0                                       # catalyst fights the flow
    if hard:
        return 12.0                                      # hard event, no direction
    return 6.0 if major else 4.0                         # background news only


# ── 4) Liquidity score (0-20) — on the contract we would actually buy ──
def liquidity_score(contract: dict) -> float:
    if not contract:
        return 0.0
    bid, ask = contract.get("bid", 0), contract.get("ask", 0)
    mid = (bid + ask) / 2
    if mid <= 0:
        return 0.0
    spread_pct = (ask - bid) / mid * 100
    s = max(0.0, 12 - spread_pct * 1.5)                  # 0% = 12, 8% = 0
    if s == 0 and (ask - bid) <= MAX_SPREAD_ABS:
        s = 4.0                                          # cheap but tight in cents
    s += min(8, contract.get("open_interest", 0) / 250)  # OI 2000+ = full 8
    return min(WEIGHTS["liquidity"], round(s, 1))


def total_score(flow, tech, news, best_contract, direction=None) -> float:
    return round(
        flow_score(flow)
        + technical_score(tech)
        + catalyst_score(news, direction)
        + liquidity_score(best_contract), 1)


# ── Budget filter — THE rule Salem caught: cost = ask x 100 ─────
def contract_cost(c: dict) -> float:
    return c.get("ask", 0) * 100


def passes_liquidity(c: dict) -> bool:
    """Spread test is percentage OR absolute, whichever the contract passes.

    A pure percentage cap is unfair to cheap contracts: at $0.45 a single
    4-cent spread is already 9%, so the 🔴 OTM tier came back empty on nearly
    every scan even when the contract was perfectly tradable.
    """
    bid, ask = c.get("bid", 0), c.get("ask", 0)
    mid = (bid + ask) / 2
    if mid <= 0 or ask <= 0:
        return False
    if c.get("open_interest", 0) < MIN_OPEN_INTEREST:
        return False
    spread = ask - bid
    return (spread / mid * 100 <= MAX_SPREAD_PCT) or (spread <= MAX_SPREAD_ABS)


def _moneyness(c, spot):
    """Signed distance into the money, in dollars (positive = ITM)."""
    if c["type"] == "call":
        return spot - c["strike"]
    return c["strike"] - spot


def contract_quality(contract, spot, expected_move, atr=0.0):
    """How worth buying this contract is, 0..1 — the basis for the pick.

    The first version chose by character: deepest in the money for one tier,
    nearest the money for another, cheapest out of the money for the third.
    None of that asks whether the contract is worth buying. Three things do:

      profit     the delta/gamma/theta estimate, capped — a 900% figure on a
                 far-OTM contract is arithmetic, not an opportunity
      liquidity  spread and open interest. A contract nobody trades back to
                 you is worth its headline number only on paper.
      reach      how many ATRs the stock must travel to the strike. A strike
                 four ATRs away is not a cheaper version of one that is close;
                 it is a different bet, and usually a losing one.
    """
    profit = min(expected_profit_pct(contract, expected_move),
                 C.MAX_PROFIT_CREDIT) / C.MAX_PROFIT_CREDIT
    liq = liquidity_score(contract) / WEIGHTS["liquidity"]

    reach = 0.5
    if atr > 0 and spot > 0:
        gap = abs(contract.get("strike", 0) - spot) / atr
        reach = max(0.0, min(1.0, 1.0 - (gap / (C.REACHABLE_ATR * 2))))

    w = C.QUALITY_WEIGHTS
    return round(w["profit"] * profit + w["liquidity"] * liq + w["reach"] * reach, 4)


def pick_contracts_by_budget(chain: list, direction: str, spot: float,
                             expected_move: float = 0.0, atr: float = 0.0) -> list:
    """The best contract in each budget BAND, by quality.

    Bands are disjoint ranges, not nested caps: $100-$200, $50-$100, under $50.
    Under the old nested caps a $45 contract qualified for all three tiers, so
    which one it landed in depended on the order they were checked rather than
    on what it cost — and Salem's budgets are ranges he chooses between, not
    three names for the same contract.

    Inside a band the pick is by contract_quality, so a cheaper contract with a
    reachable strike and a real book beats a dearer one with neither.
    """
    side = [c for c in chain
            if c.get("type") == direction and passes_liquidity(c)]

    picks, taken = [], set()
    for label, floor, ceiling in BUDGET_TIERS:
        band = [c for c in side
                if floor < contract_cost(c) <= ceiling
                and c.get("option_symbol") not in taken]
        if not band:
            picks.append((label, None))
            continue
        best = max(band, key=lambda c: contract_quality(c, spot, expected_move, atr))
        taken.add(best.get("option_symbol"))
        picks.append((label, best))
    return picks


def best_contract(chain, direction, spot):
    """The contract the liquidity score is computed on: the most liquid one we
    could actually afford, not the highest-OI contract in the whole chain
    (which is often a $2,000 LEAP Salem can never buy)."""
    top_budget = max(ceiling for _, _, ceiling in BUDGET_TIERS)
    affordable = [c for c in chain
                  if c.get("type") == direction
                  and passes_liquidity(c)
                  and 0 < contract_cost(c) <= top_budget]
    if not affordable:
        return None
    return max(affordable, key=lambda c: c.get("open_interest", 0))


# ── Exit plan per contract ──────────────────────────────────────
def exit_rule(dte):
    """The take/stop pair that fits this contract's remaining life."""
    d = 0 if dte is None else max(0, int(dte))
    for max_dte, take, stop, note in C.EXIT_RULES:
        if d <= max_dte:
            return {"take_pct": take, "stop_pct": stop, "note": note, "dte": d}
    take, stop, note = C.PROFIT_TAKE_PCT, C.STOP_LOSS_PCT, ""
    return {"take_pct": take, "stop_pct": stop, "note": note, "dte": d}


# ── Expected profit ─────────────────────────────────────────────
def expected_profit_pct(contract: dict, expected_move: float) -> float:
    """Estimate the contract's return if the stock reaches the measured target.

    expected_move = |target - close| in dollars, from the 15m level.

    Delta alone is not good enough once same-day expiries are in scope. A pure
    delta estimate ignores both of the terms that dominate a 0DTE contract:

      gamma  delta itself rises as the move happens, so the delta-only figure
             understates a winning breakout
      theta  a same-day contract bleeds its whole remaining value into the
             close, so the delta-only figure overstates every trade, and by
             far the most on the ones Salem now wants to take

    Second-order estimate:  ΔP ≈ δ·m + ½·γ·m² − θ·(held/6.5)

    Still an estimate — it assumes a static IV, and a 0DTE contract's real
    path depends on when in the session the move arrives. Labelled as a
    تقدير in every message.
    """
    ask = contract.get("ask", 0)
    delta = abs(contract.get("delta", 0))
    if ask <= 0 or delta <= 0 or expected_move <= 0:
        return 0.0

    gamma = abs(contract.get("gamma", 0) or 0)
    theta = abs(contract.get("theta", 0) or 0)

    intrinsic_gain = delta * expected_move + 0.5 * gamma * expected_move ** 2
    decay = theta * (C.HOLD_HOURS / C.TRADING_HOURS_PER_DAY)

    net = intrinsic_gain - decay
    if net <= 0:
        return 0.0                    # theta eats the move — not a candidate
    return round(net / ask * 100, 0)
