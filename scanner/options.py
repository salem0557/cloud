"""Pick the best option contracts for an alerted stock.

For each signal the bot fetches the Yahoo option chain (nearest expiry up to
OPTIONS_MAX_WEEKS out) and selects the top contracts per side (call/put) by a
balanced score — strike near the spot price, real liquidity, and a tight
bid/ask spread — then presents them cheapest-premium first.
"""
import datetime as dt
import logging
import math
import time

import yfinance as yf

from . import config

log = logging.getLogger(__name__)


class OptionsFetchError(Exception):
    """Yahoo could not be reached/answered — distinct from 'no options exist'."""


# Symbols confirmed (after retries) to have no listed options; re-checked
# after a few hours so a fetch glitch can't mislabel a stock for long.
_no_options: dict[str, float] = {}
NO_OPTIONS_TTL = 6 * 3600


def _expiries_with_retry(ticker, symbol: str) -> list[str]:
    """Yahoo rate-limiting right after a scan burst often returns errors or
    an empty expiry list for stocks that do have options (e.g. DDD); retry
    before concluding anything, and raise on persistent failure."""
    last_exc = None
    for attempt in range(3):
        try:
            expiries = list(ticker.options or [])
            if expiries:
                return expiries
            last_exc = None  # clean empty answer
        except Exception as exc:
            last_exc = exc
        time.sleep(2 * (attempt + 1))
    if last_exc is not None:
        raise OptionsFetchError(symbol) from last_exc
    return []


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
    """{'call': [top picks cheapest-first], 'put': [...]}.

    Empty lists mean the stock genuinely has no suitable contracts; a fetch
    problem raises OptionsFetchError instead so the caller can say so.
    """
    out = {"call": [], "put": []}
    if _no_options.get(symbol, 0) > time.time() - NO_OPTIONS_TTL:
        return out
    ticker = yf.Ticker(symbol)
    expiries = _expiries_with_retry(ticker, symbol)
    if not expiries:
        _no_options[symbol] = time.time()
        log.info("%s has no listed options (confirmed after retries)", symbol)
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
    fetched = 0
    for exp, days in upcoming:
        chain = None
        for attempt in range(2):
            try:
                chain = ticker.option_chain(exp)
                break
            except Exception:
                if attempt == 0:
                    time.sleep(2)
        if chain is None:
            log.warning("Option chain fetch failed: %s %s", symbol, exp)
            continue
        fetched += 1
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

    if upcoming and fetched == 0:
        raise OptionsFetchError(symbol)

    for side, rows in candidates.items():
        top = sorted(rows, key=lambda c: -c["score"])[:config.OPTIONS_TOP_N]
        out[side] = sorted(top, key=lambda c: c["premium"])
    return out
