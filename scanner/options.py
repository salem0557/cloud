"""Pick the best option contracts for an alerted stock.

For each signal the bot fetches the Yahoo option chain (nearest expiry up to
OPTIONS_MAX_WEEKS out, ~3 months by default) and ranks contracts per side
(call/put) by a composite score across bid/ask spread quality, volume, open
interest, implied volatility, delta, and theta — not by cheapest premium.
Liquidity (spread/volume/OI) is weighted higher than the Greeks, since it
determines whether a contract can actually be traded at a sane price at all.

Neither Yahoo nor CBOE's free feeds reliably publish delta/theta, so both are
derived from Black-Scholes (spot, strike, days-to-expiry, implied volatility)
— a standard, well-understood approximation (European exercise, no dividend
yield) that's good enough to compare contracts against each other.
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

RISK_FREE_RATE = 0.045  # ~ current T-bill yield; only used to estimate delta/theta
# Preferred delta magnitude band for a directional pick: enough sensitivity
# to the stock's move without paying for a deep-ITM contract.
DELTA_TARGET_LOW = 0.30
DELTA_TARGET_HIGH = 0.50
# Composite score weights: liquidity (spread+volume+OI) outweighs the Greeks,
# since it determines whether a contract is actually tradeable at a sane
# price at all; the Greeks then decide between similarly-liquid contracts.
WEIGHT_SPREAD = 0.25
WEIGHT_VOLUME = 0.20
WEIGHT_OI = 0.20
WEIGHT_IV = 0.12
WEIGHT_DELTA = 0.12
WEIGHT_THETA = 0.11

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


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def _bs_delta_theta(spot: float, strike: float, days: int, iv: float | None,
                    is_call: bool) -> tuple[float | None, float | None]:
    """Black-Scholes delta and per-day theta, or (None, None) if IV/days
    aren't usable. See module docstring for why these are computed rather
    than read from the feed."""
    if not days or not iv or iv <= 0 or spot <= 0 or strike <= 0:
        return None, None
    t = days / 365.0
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (RISK_FREE_RATE + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    pdf_d1 = _norm_pdf(d1)
    if is_call:
        delta = _norm_cdf(d1)
        theta_year = (-(spot * pdf_d1 * iv) / (2 * sqrt_t)
                     - RISK_FREE_RATE * strike * math.exp(-RISK_FREE_RATE * t) * _norm_cdf(d2))
    else:
        delta = _norm_cdf(d1) - 1
        theta_year = (-(spot * pdf_d1 * iv) / (2 * sqrt_t)
                     + RISK_FREE_RATE * strike * math.exp(-RISK_FREE_RATE * t) * _norm_cdf(-d2))
    return delta, theta_year / 365.0


def _raw_candidate(row, spot: float, expiry: str, days: int, is_call: bool) -> dict | None:
    """Pull the raw fields needed for ranking from one option chain row, or
    None if the contract fails a hard floor (too illiquid, no usable quote).

    estimated=True means the premium comes from the last traded price: outside
    options market hours (9:30-16:00 ET) Yahoo zeroes out bid/ask, which must
    not hide the picks entirely.
    """
    oi = int(row.get("openInterest") or 0)
    vol = int(row.get("volume") or 0)
    if oi + vol < config.OPTIONS_MIN_ACTIVITY:
        return None

    bid = float(row.get("bid") or 0)
    ask = float(row.get("ask") or 0)
    last = float(row.get("lastPrice") or 0)
    if ask >= bid > 0:
        premium = (bid + ask) / 2
        spread_pct = (ask - bid) / premium
        estimated = False
    elif last > 0:
        premium, bid, ask = last, last, last
        spread_pct = None  # no live quote to judge
        estimated = True
    else:
        return None

    strike = float(row["strike"])
    iv_raw = row.get("impliedVolatility", row.get("iv"))
    iv = float(iv_raw) if iv_raw not in (None, "") else None
    delta, theta = _bs_delta_theta(spot, strike, days, iv, is_call)

    return {
        "strike": strike, "expiry": expiry, "days": days,
        "premium": round(premium, 2), "estimated": estimated,
        "bid": bid, "ask": ask, "spread_pct": spread_pct,
        "volume": vol, "openInterest": oi,
        "iv": iv, "delta": delta, "theta": theta,
    }


def _add_candidate(candidates, side, row, spot, expiry, days):
    raw = _raw_candidate(row, spot, expiry, days, is_call=(side == "call"))
    if raw is not None:
        candidates[side].append(raw)


def _rank_candidates(rows: list[dict]) -> list[dict]:
    """Composite score, best first: bid/ask spread + volume + open interest
    (65% combined) and IV + delta + theta (35% combined) — replacing the old
    "cheapest, near-the-money" ranking entirely.

    IV and theta are scored relative to the other contracts fetched for the
    same symbol/side (their usable range varies too much across underlyings
    for one fixed global threshold to mean anything); delta is scored against
    a fixed 0.30-0.50 target band, since that's a stated preference, not
    something relative to peers.
    """
    if not rows:
        return rows

    ivs = [r["iv"] for r in rows if r["iv"] is not None]
    iv_lo, iv_hi = (min(ivs), max(ivs)) if ivs else (None, None)
    thetas = [abs(r["theta"]) / r["premium"] for r in rows
             if r["theta"] is not None and r["premium"]]
    th_lo, th_hi = (min(thetas), max(thetas)) if thetas else (None, None)

    for r in rows:
        if r["spread_pct"] is not None:
            spread_score = max(0.0, 1 - r["spread_pct"] * 2)  # 0 at a 50% spread
        else:
            spread_score = 0.3  # after-hours last-trade quote: can't judge, partial credit

        volume_score = min(1.0, math.log10(1 + r["volume"]) / 3.0)   # ~1.0 at 1,000 volume
        oi_score = min(1.0, math.log10(1 + r["openInterest"]) / 4.0)  # ~1.0 at 10,000 OI

        if r["iv"] is not None and iv_hi is not None and iv_hi > iv_lo:
            iv_score = 1 - (r["iv"] - iv_lo) / (iv_hi - iv_lo)  # lower IV wins
        else:
            iv_score = 0.5  # no peer spread to compare against -> neutral

        if r["delta"] is not None:
            d = abs(r["delta"])
            if DELTA_TARGET_LOW <= d <= DELTA_TARGET_HIGH:
                delta_score = 1.0
            else:
                dist = (DELTA_TARGET_LOW - d) if d < DELTA_TARGET_LOW else (d - DELTA_TARGET_HIGH)
                delta_score = max(0.0, 1 - dist / DELTA_TARGET_LOW)
        else:
            delta_score = 0.5

        if r["theta"] is not None and r["premium"] and th_hi is not None and th_hi > th_lo:
            theta_pct = abs(r["theta"]) / r["premium"]
            theta_score = 1 - (theta_pct - th_lo) / (th_hi - th_lo)  # slower decay wins
        else:
            theta_score = 0.5

        r["score"] = (WEIGHT_SPREAD * spread_score + WEIGHT_VOLUME * volume_score
                      + WEIGHT_OI * oi_score + WEIGHT_IV * iv_score
                      + WEIGHT_DELTA * delta_score + WEIGHT_THETA * theta_score)

    return sorted(rows, key=lambda r: -r["score"])


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
            "iv": c.get("iv"),
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

    Reuses the same reliability screen and composite ranking as best_options
    — just pre-filtered to the price cap first; among the ones under the
    cap, the best-scored (not just the cheapest) come first.
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
        out[side] = _rank_candidates(cheap)[:config.OPTIONS_TOP_N]
    return out


def best_options(symbol: str, spot: float) -> dict[str, list[dict]]:
    """{'call': [top picks, best composite score first], 'put': [...]}.

    Ranks by a composite score across bid/ask spread, volume, open interest,
    implied volatility, delta, and theta (see module docstring) — not by
    cheapest premium. Tries Yahoo first, then CBOE's free delayed chain as
    an independent fallback. Empty lists mean the stock genuinely has no
    suitable contracts; OptionsFetchError means both providers failed;
    NoNearTermOptions means the stock has options, just none within
    OPTIONS_MAX_WEEKS.
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
        out[side] = _rank_candidates(rows)[:config.OPTIONS_TOP_N]
    return out
