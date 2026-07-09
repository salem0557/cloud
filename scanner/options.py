"""Pick the best CALL option contracts for an alerted stock (the strategy
only trades reversal-up setups, so only bullish/long-call contracts apply).

For each signal the bot fetches the Yahoo option chain (nearest expiry up to
OPTIONS_MAX_WEEKS out, ~3 months by default), keeps only contracts whose
delta clears DELTA_MIN, and returns the top ones sorted by delta (highest
first) — not by cheapest premium.

Neither Yahoo nor CBOE's free feeds reliably publish delta, so it's derived
from Black-Scholes (spot, strike, days-to-expiry, implied volatility) — a
standard, well-understood approximation (European exercise, no dividend
yield) that's good enough to compare contracts against each other.

Each final pick also carries an IV Rank/Percentile: where its IV sits
against the underlying's own rolling realized volatility over the past
year. True historical IV for a specific contract needs a paid data feed we
don't have, so realized volatility of the underlying (freely available) is
used as the standard proxy for "the usual IV range" — a well-known
approximation, not literal historical option IV.
"""
import datetime as dt
import logging
import math
import re
import time

import numpy as np
import requests
import yfinance as yf

from . import config

log = logging.getLogger(__name__)

RISK_FREE_RATE = 0.045  # ~ current T-bill yield; only used to estimate delta/theta
DELTA_MIN = 0.40  # only filter: a contract must clear this delta to qualify

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


# Per-symbol cache of the underlying's rolling realized-volatility series
# (see module docstring on IV Rank) -- {symbol: (fetched_at, vol_series)}.
# A full year of daily history is a whole extra network call, and the
# result barely changes within a day, so it's cached.
_realized_vol_cache: dict[str, tuple[float, np.ndarray]] = {}


def _realized_vol_series(symbol: str) -> np.ndarray | None:
    """Rolling annualized realized volatility (close-to-close log returns,
    IV_RANK_VOL_WINDOW-day window) over the past year, or None if the price
    history couldn't be fetched or is too short to compute even one window."""
    cached = _realized_vol_cache.get(symbol)
    if cached and cached[0] > time.time() - config.IV_RANK_CACHE_HOURS * 3600:
        return cached[1]
    try:
        hist = yf.Ticker(symbol).history(period="1y", interval="1d")
    except Exception:
        log.warning("Realized-vol history fetch failed for %s", symbol)
        return None
    if hist is None or hist.empty or "Close" not in hist:
        return None
    close = hist["Close"].dropna()
    if len(close) < config.IV_RANK_VOL_WINDOW + 1:
        return None
    log_ret = np.log(close / close.shift(1)).dropna()
    vol_series = (log_ret.rolling(config.IV_RANK_VOL_WINDOW).std()
                 * math.sqrt(252)).dropna().to_numpy()
    if vol_series.size == 0:
        return None
    _realized_vol_cache[symbol] = (time.time(), vol_series)
    return vol_series


def _iv_rank(symbol: str, iv: float | None) -> tuple[float, float] | None:
    """(iv_rank_pct, iv_percentile_pct): how one contract's IV compares to
    the underlying's own realized-volatility range over the past year (see
    module docstring). None if IV_RANK_ENABLED is off, IV is missing, or the
    underlying's history couldn't be fetched/is too short."""
    if not config.IV_RANK_ENABLED or iv is None:
        return None
    vol_series = _realized_vol_series(symbol)
    if vol_series is None:
        return None
    vol_min, vol_max = float(vol_series.min()), float(vol_series.max())
    if vol_max <= vol_min:
        return None
    rank = max(0.0, min(100.0, (iv - vol_min) / (vol_max - vol_min) * 100))
    percentile = float((vol_series < iv).sum()) / vol_series.size * 100
    return rank, percentile


def _attach_iv_rank(symbol: str, picks: list[dict]) -> list[dict]:
    for c in picks:
        result = _iv_rank(symbol, c.get("iv"))
        c["iv_rank"], c["iv_percentile"] = result if result else (None, None)
    return picks


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


def _add_candidate(candidates, row, spot, expiry, days):
    raw = _raw_candidate(row, spot, expiry, days, is_call=True)
    if raw is not None:
        candidates["call"].append(raw)


def _rank_candidates(rows: list[dict], max_premium: float | None = None) -> list[dict]:
    """Keep only contracts whose delta clears DELTA_MIN (and, if given, a
    premium cap), sorted by delta descending (highest first).

    max_premium is separate from OPTIONS_MAX_PREMIUM on purpose: callers
    that already apply their own cap (find_cheap_contracts, with its own
    user-supplied price) don't pass one here, so the two caps never fight.
    """
    qualified = [r for r in rows if r["delta"] is not None and abs(r["delta"]) > DELTA_MIN]
    if max_premium is not None:
        qualified = [r for r in qualified if r["premium"] <= max_premium]
    return sorted(qualified, key=lambda r: -abs(r["delta"]))


def _yahoo_candidates(symbol, spot, today, cutoff):
    """Provider 1. Returns (candidates, has_options); raises OptionsFetchError
    or NoNearTermOptions (listed options exist, just none within `cutoff`)."""
    ticker = yf.Ticker(symbol)
    expiries = _expiries_with_retry(ticker, symbol)
    if not expiries:
        return {"call": []}, False

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

    candidates = {"call": []}
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
        for _, row in chain.calls.iterrows():
            _add_candidate(candidates, row, spot, exp, days)

    if fetched == 0:
        raise OptionsFetchError(symbol)
    return candidates, True


def _cboe_candidates(symbol, spot, today, cutoff):
    """Provider 2 (fallback): whole chain in one keyless request from CBOE."""
    try:
        resp = requests.get(CBOE_URL.format(symbol=symbol.upper()), timeout=20,
                            headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 404:
            return {"call": []}, False  # not an optionable symbol
        resp.raise_for_status()
        contracts = resp.json()["data"].get("options") or []
    except OptionsFetchError:
        raise
    except Exception as exc:
        raise OptionsFetchError(symbol) from exc
    if not contracts:
        return {"call": []}, False

    # Contract names look like DDD261218C00005000: yymmdd, C/P, strike*1000
    pat = re.compile(rf"^{re.escape(symbol.upper())}(\d{{6}})([CP])(\d{{8}})$")
    candidates = {"call": []}
    any_in_window = False
    for c in contracts:
        m = pat.match(c.get("option", ""))
        if not m or m.group(2) != "C":  # only calls: this strategy is long-only
            continue
        try:
            exp_date = dt.datetime.strptime(m.group(1), "%y%m%d").date()
        except ValueError:
            continue
        if not today <= exp_date <= cutoff:
            continue
        any_in_window = True
        row = {
            "strike": int(m.group(3)) / 1000,
            "bid": c.get("bid"),
            "ask": c.get("ask"),
            "lastPrice": c.get("last_trade_price"),
            "openInterest": c.get("open_interest"),
            "volume": c.get("volume"),
            "iv": c.get("iv"),
        }
        _add_candidate(candidates, row, spot,
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


def find_cheap_contracts(symbol: str, spot: float, max_premium: float) -> dict[str, list[dict]]:
    """CALL contracts on `symbol` priced at or under `max_premium` per share
    (contract cost = premium * 100).

    Reuses the same reliability screen and delta filter/ranking as
    best_options — just pre-filtered to the price cap first; among the ones
    under the cap, the highest delta (not just the cheapest) comes first.
    """
    out = {"call": []}
    if _no_options.get(symbol, 0) > time.time() - NO_OPTIONS_TTL:
        return out

    today = dt.date.today()
    cutoff = today + dt.timedelta(weeks=config.OPTIONS_MAX_WEEKS)
    candidates = _gather_candidates(symbol, spot, today, cutoff)
    if candidates is None:
        _no_options[symbol] = time.time()
        return out
    cheap = [c for c in candidates["call"] if c["premium"] <= max_premium]
    out["call"] = _rank_candidates(cheap)[:config.OPTIONS_TOP_N]
    return out


def best_options(symbol: str, spot: float) -> dict[str, list[dict]]:
    """{'call': [top picks, highest delta first]}, each also carrying
    iv_rank/iv_percentile (see module docstring; None if unavailable).

    Filters: delta must clear DELTA_MIN, and premium must not exceed
    OPTIONS_MAX_PREMIUM per share (contract cost = premium * 100) — not by
    cheapest premium otherwise. Tries Yahoo first, then CBOE's free delayed
    chain as an independent fallback. Empty list means no contract cleared
    both filters (or the stock genuinely has no suitable contracts) —
    which suppresses that stock's alert entirely (see bot.py's
    filter_by_options); OptionsFetchError means both providers failed;
    NoNearTermOptions means the stock has options, just none within
    OPTIONS_MAX_WEEKS.
    """
    out = {"call": []}
    if _no_options.get(symbol, 0) > time.time() - NO_OPTIONS_TTL:
        return out

    today = dt.date.today()
    cutoff = today + dt.timedelta(weeks=config.OPTIONS_MAX_WEEKS)
    candidates = _gather_candidates(symbol, spot, today, cutoff)
    if candidates is None:
        _no_options[symbol] = time.time()
        log.info("%s has no listed options", symbol)
        return out
    picks = _rank_candidates(candidates["call"],
                             max_premium=config.OPTIONS_MAX_PREMIUM)[:config.OPTIONS_TOP_N]
    # IV Rank only for the final picks that actually get shown, and only
    # here (not find_cheap_contracts) -- that command already loops over
    # hundreds/thousands of symbols, and a full extra year of daily history
    # per symbol would multiply its already-long runtime.
    out["call"] = _attach_iv_rank(symbol, picks)
    return out
