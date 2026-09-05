"""Computed risk checks — the things a summed score cannot see.

The four scoring components measure whether a setup looks good. They do not
notice when the pieces fail to tell one story, and each check below is a case
where a high score is misleading for a reason that is nowhere in the numbers:

  earnings   IV collapses the moment a report lands. A contract bought the day
             before can be right about direction and still lose, because the
             volatility it was priced with is gone. No 15m chart shows this.
  regime     A bullish break while the whole market sells off is swimming
             against the tide; the same pattern has a different base rate.
  conviction Premium hit on the bid was sold, not bought. Flow that is mostly
             bid-side is not the buying pressure the flow score implies.

Every deduction here is arithmetic on fetched data — no judgement, no
invented number — and each one is reported by name so the alert says why.
"""
import config as C
import technical
import uw
from scoring import ask_side_ratio


def earnings_risk(ticker, max_dte):
    """Penalise a contract that expires across an earnings report."""
    days = uw.next_earnings_days(ticker)
    if days is None:
        return 0.0, None
    if days <= C.EARNINGS_BLOCK_DAYS:
        return (C.EARNINGS_PENALTY,
                f"أرباح بعد {days} يوم — انهيار IV سيأكل العقد حتى لو تحرك السهم")
    if max_dte is not None and days <= max_dte:
        return (C.EARNINGS_PENALTY / 2,
                f"أرباح بعد {days} يوم، قبل انتهاء العقد")
    return 0.0, None


def market_regime(direction):
    """Is the broad market moving with this trade or against it?"""
    try:
        spy = uw.candles(C.REGIME_TICKER, timeframe="1D", limit=60)
    except uw.UWError:
        return 0.0, None
    if len(spy) < 5:
        return 0.0, None
    first, last = spy[0]["close"], spy[-1]["close"]
    if first <= 0:
        return 0.0, None
    move_pct = (last - first) / first * 100
    against = (direction == "call" and move_pct <= -C.REGIME_MOVE_PCT) or \
              (direction == "put" and move_pct >= C.REGIME_MOVE_PCT)
    if against:
        way = "ينزل" if move_pct < 0 else "يصعد"
        return (C.REGIME_PENALTY,
                f"السوق {way} {abs(move_pct):.1f}% اليوم — الإشارة ضد التيار")
    return 0.0, None


def conviction_risk(flow):
    """Flow that was sold rather than bought."""
    ratio = ask_side_ratio(flow)
    if ratio and ratio < C.MIN_ASK_SIDE_RATIO:
        return (C.CONVICTION_PENALTY,
                f"{ratio*100:.0f}% فقط من العلاوة اشتُريت عند الطلب — "
                "التدفق بيع لا شراء")
    return 0.0, None


def assess(ticker, direction, flow, chain):
    """-> {penalty, flags[]}. Penalty is subtracted from the computed score."""
    dtes = [c.get("dte") for c in chain if c.get("dte") is not None]
    max_dte = max(dtes) if dtes else None

    penalty, flags = 0.0, []
    for amount, note in (earnings_risk(ticker, max_dte),
                         market_regime(direction),
                         conviction_risk(flow)):
        if amount:
            penalty += amount
            flags.append(note)
    return {"penalty": round(min(penalty, C.MAX_RISK_PENALTY), 1), "flags": flags}
