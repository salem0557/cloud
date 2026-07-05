"""Pick the best option contracts for an alerted stock.

For each signal the bot fetches the Yahoo option chain (nearest expiry up to
OPTIONS_MAX_WEEKS out) and selects the top contracts per side (call/put) by a
balanced score — strike near the spot price, real liquidity, and a tight
bid/ask spread — then presents them cheapest-premium first.
"""
import datetime as dt
import logging
import math
import re
import time

import requests
import yfinance as yf

from . import config

log = logging.getLogger(__name__)

# Fallback provider: CBOE publishes full delayed option chains (all expiries
# in ONE request) with no API key. Independent of Yahoo, so it keeps working
# when our scan traffic gets Yahoo's options endpoint rate-limited.
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"


class OptionsFetchError(Exception):
    """Yahoo could not be reached/answered — distinct from 'no options exist'."""


class NoNearTermOptions(Exception):
    """The symbol has listed options, but none fall within OPTIONS_MAX_WEEKS —
    distinct from 'no options exist at all', so the caller can say so
    accurately instead of claiming the stock has no options."""


# Symbols confirmed (after retries) to have no listed options; re-checked
# after a few hours so a fetch glitch can't mislabel a stock for long.
_no_options: dict[str, float] = {}
NO_OPTIONS_TTL = 6 * 3600


def _expiries_with_retry(ticker, symbol: str) -> list[str]:
    """Yahoo rate-limiting right after a scan burst often returns errors or
    an empty expiry list for stocks that do have options (e.g. DDD); retry
    before concluding anything, and raise on persistent failure."""
    last_exc = None
    for attempt in range(2):
        try:
            expiries = list(ticker.options or [])
            if expiries:
                return expiries
            last_exc = None  # clean empty answer
        except Exception as exc:
            last_exc = exc
        if attempt == 0:
            time.sleep(2)
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


def _add_candidate(candidates, side, row, spot, expiry, days):
    scored = _score(row, spot)
    if scored is None:
        return
    score, premium, estimated = scored
    candidates[side].append({
        "strike": float(row["strike"]),
        "expiry": expiry,
        "days": days,
        "premium": round(premium, 2),
        "estimated": estimated,
        "score": score,
        "activity": int(row.get("openInterest") or 0)
                    + int(row.get("volume") or 0),
    })


def _yahoo_candidates(symbol, spot, today, cutoff):
    """Provider 1. Returns (candidates, has_options); raises OptionsFetchError
    or NoNearTermOptions (listed options exist, just none within `cutoff`)."""
    ticker = yf.Ticker(symbol)
    expiries = _expiries_with_retry(ticker, symbol)
    if not expiries:
        return {"call": [], "put": []}, False

    upcoming = []
    for exp in expiries:
        try:
            exp_date = dt.date.fromisoformat(exp)
        except ValueError:
            continue
        if today <= exp_date <= cutoff:
            upcoming.append((exp, (exp_date - today).days))
    upcoming = upcoming[:config.OPTIONS_MAX_EXPIRIES]

    # Expiries exist (checked above) but none fall in our near-term window —
    # e.g. a stock that only lists monthly options further out than
    # OPTIONS_MAX_WEEKS. Distinct from "no options at all".
    if not upcoming:
        raise NoNearTermOptions(symbol)

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
            log.warning("Yahoo option chain failed: %s %s", symbol, exp)
            continue
        fetched += 1
        for side, df in (("call", chain.calls), ("put", chain.puts)):
            for _, row in df.iterrows():
                _add_candidate(candidates, side, row, spot, exp, days)

    if fetched == 0:
        raise OptionsFetchError(symbol)
    return candidates, True


def _cboe_candidates(symbol, spot, today, cutoff):
    """Provider 2 (fallback): whole chain in one keyless request from CBOE."""
    try:
        resp = requests.get(CBOE_URL.format(symbol=symbol.upper()), timeout=20,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 404:
            return {"call": [], "put": []}, False  # not an optionable symbol
        resp.raise_for_status()
        contracts = resp.json()["data"].get("options") or []
    except OptionsFetchError:
        raise
    except Exception as exc:
        raise OptionsFetchError(symbol) from exc
    if not contracts:
        return {"call": [], "put": []}, False

    # Contract names look like DDD261218C00005000: yymmdd, C/P, strike*1000
    pat = re.compile(rf"^{re.escape(symbol.upper())}(\d{{6}})([CP])(\d{{8}})$")
    candidates = {"call": [], "put": []}
    any_in_window = False
    for c in contracts:
        m = pat.match(c.get("option", ""))
        if not m:
            continue
        try:
            exp_date = dt.datetime.strptime(m.group(1), "%y%m%d").date()
        except ValueError:
            continue
        if not today <= exp_date <= cutoff:
            continue
        any_in_window = True
        side = "call" if m.group(2) == "C" else "put"
        row = {
            "strike": int(m.group(3)) / 1000,
            "bid": c.get("bid"),
            "ask": c.get("ask"),
            "lastPrice": c.get("last_trade_price"),
            "openInterest": c.get("open_interest"),
            "volume": c.get("volume"),
        }
        _add_candidate(candidates, side, row, spot,
                       exp_date.isoformat(), (exp_date - today).days)

    # Contracts exist for this symbol, but every one of them expires beyond
    # our near-term window — same distinction as the Yahoo provider above.
    if not any_in_window:
        raise NoNearTermOptions(symbol)
    return candidates, True


def _gather_candidates(symbol: str, spot: float, today, cutoff) -> dict | None:
    """Try both providers in turn; returns the first successful near-term
    candidates dict, or None if both cleanly confirm the symbol has no
    listed options at all.

    Raises OptionsFetchError if both providers failed outright (couldn't be
    reached/answered), or NoNearTermOptions if the symbol does have listed
    options but none fall within `cutoff` — distinct from "no options at
    all", so the caller can word the resulting message accurately instead of
    claiming a stock with options simply doesn't have any.
    """
    providers = (_yahoo_candidates, _cboe_candidates)
    last_error = None
    failures = 0
    near_term_misses = 0
    for provider in providers:
        try:
            candidates, has_options = provider(symbol, spot, today, cutoff)
        except NoNearTermOptions:
            near_term_misses += 1
            continue
        except OptionsFetchError as exc:
            log.warning("Options provider %s failed for %s",
                        provider.__name__, symbol)
            last_error = exc
            failures += 1
            continue
        if has_options:
            return candidates
        # This provider cleanly reports no options; ask the next one to
        # confirm — a throttled Yahoo sometimes answers with emptiness.

    if failures == len(providers):
        raise last_error
    if near_term_misses:
        raise NoNearTermOptions(symbol)
    return None  # both providers cleanly confirm: genuinely no listed options


def find_cheap_contracts(symbol: str, spot: float, max_premium: float,
                         sides=("call", "put")) -> dict[str, list[dict]]:
    """Contracts on `symbol` priced at or under `max_premium` per share
    (contract cost = premium * 100), among the requested sides.

    Reuses the same reliability screen as best_options (moneyness window,
    minimum open-interest+volume, valid bid/ask or last-trade quote) — just
    selects by "cheapest first, under the cap" instead of the balanced
    near-the-money score, since a deliberately cheap/OTM contract is exactly
    what this search is for.
    """
    out = {"call": [], "put": []}
    if _no_options.get(symbol, 0) > time.time() - NO_OPTIONS_TTL:
        return out

    today = dt.date.today()
    cutoff = today + dt.timedelta(weeks=config.OPTIONS_MAX_WEEKS)
    candidates = _gather_candidates(symbol, spot, today, cutoff)
    if candidates is None:
        _no_options[symbol] = time.time()
        return out
    for side in sides:
        cheap = [c for c in candidates[side] if c["premium"] <= max_premium]
        out[side] = sorted(cheap, key=lambda c: c["premium"])[:config.OPTIONS_TOP_N]
    return out


def best_options(symbol: str, spot: float) -> dict[str, list[dict]]:
    """{'call': [top picks cheapest-first], 'put': [...]}.

    Tries Yahoo first, then CBOE's free delayed chain as an independent
    fallback. Empty lists mean the stock genuinely has no suitable
    contracts; OptionsFetchError means both providers failed; NoNearTermOptions
    means the stock has options, just none within OPTIONS_MAX_WEEKS.
    """
    out = {"call": [], "put": []}
    if _no_options.get(symbol, 0) > time.time() - NO_OPTIONS_TTL:
        return out

    today = dt.date.today()
    cutoff = today + dt.timedelta(weeks=config.OPTIONS_MAX_WEEKS)
    candidates = _gather_candidates(symbol, spot, today, cutoff)
    if candidates is None:
        _no_options[symbol] = time.time()
        log.info("%s has no listed options", symbol)
        return out
    for side, rows in candidates.items():
        top = sorted(rows, key=lambda c: -c["score"])[:config.OPTIONS_TOP_N]
        out[side] = sorted(top, key=lambda c: c["premium"])
    return out
