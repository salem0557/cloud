"""Pick the best option contracts for an alerted stock.

For each signal the bot fetches the Yahoo option chain (nearest expiry up to
OPTIONS_MAX_WEEKS out) and selects the top contracts per side (call/put) by a
balanced score — strike near the spot price, real liquidity, and a tight
bid/ask spread — then presents them cheapest-premium first.
"""
import datetime as dt
import logging
import math

import yfinance as yf

from . import config

log = logging.getLogger(__name__)


def _score(row, spot: float):
    """Return (score, premium, estimated) or None if the contract is untradeable.

    estimated=True means the premium comes from the last traded price: outside
    options market hours (9:30-16:00 ET) Yahoo zeroes out bid/ask, which must
    not hide the picks entirely.
    """
    strike = float(row["strike"])
    moneyness = abs(strike - spot) / spot
    if moneyness > config.OPTIONS_MONEYNESS_WINDOW:
        return None
    oi = int(row.get("openInterest") or 0)
    vol = int(row.get("volume") or 0)
    if oi + vol < config.OPTIONS_MIN_ACTIVITY:
        return None

    bid = float(row.get("bid") or 0)
    ask = float(row.get("ask") or 0)
    last = float(row.get("lastPrice") or 0)
    if ask >= bid > 0:
        premium = (bid + ask) / 2
        spread_score = max(0.0, 1 - ((ask - bid) / premium) * 2)  # 0 at 50% spread
        estimated = False
    elif last > 0:
        premium = last
        spread_score = 0.0  # no live quote to judge; rank below quoted contracts
        estimated = True
    else:
        return None

    atm_score = max(0.0, 1 - moneyness / config.OPTIONS_MONEYNESS_WINDOW)
    liq_score = min(1.0, math.log10(1 + oi + 2 * vol) / 4)  # ~1.0 at 10k activity
    score = 0.45 * atm_score + 0.35 * liq_score + 0.20 * spread_score
    return score, premium, estimated


def best_options(symbol: str, spot: float) -> dict[str, list[dict]]:
    """{'call': [top picks cheapest-first], 'put': [...]}; empty lists on failure."""
    out = {"call": [], "put": []}
    try:
        ticker = yf.Ticker(symbol)
        expiries = list(ticker.options or [])
    except Exception:
        log.warning("No options data for %s", symbol)
        return out

    today = dt.date.today()
    cutoff = today + dt.timedelta(weeks=config.OPTIONS_MAX_WEEKS)
    upcoming = []
    for exp in expiries:
        try:
            exp_date = dt.date.fromisoformat(exp)
        except ValueError:
            continue
        if today <= exp_date <= cutoff:
            upcoming.append((exp, (exp_date - today).days))
    upcoming = upcoming[:config.OPTIONS_MAX_EXPIRIES]

    candidates = {"call": [], "put": []}
    for exp, days in upcoming:
        try:
            chain = ticker.option_chain(exp)
        except Exception:
            log.warning("Option chain fetch failed: %s %s", symbol, exp)
            continue
        for side, df in (("call", chain.calls), ("put", chain.puts)):
            for _, row in df.iterrows():
                scored = _score(row, spot)
                if scored is None:
                    continue
                score, premium, estimated = scored
                candidates[side].append({
                    "strike": float(row["strike"]),
                    "expiry": exp,
                    "days": days,
                    "premium": round(premium, 2),
                    "estimated": estimated,
                    "score": score,
                    "activity": int(row.get("openInterest") or 0)
                                + int(row.get("volume") or 0),
                })

    for side, rows in candidates.items():
        top = sorted(rows, key=lambda c: -c["score"])[:config.OPTIONS_TOP_N]
        out[side] = sorted(top, key=lambda c: c["premium"])
    return out
