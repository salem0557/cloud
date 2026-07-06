"""Pick the best CALL option contract for an alerted stock (the strategy
only trades reversal-up setups, so only bullish/long-call contracts apply).

For each signal the bot fetches the Yahoo option chain (nearest expiry up to
OPTIONS_MAX_WEEKS out, ~3 months by default) and selects a single contract
per symbol via a simple filter (delta floor, DTE window, a minimal volume
floor) — see select_best_call() below.

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

# --- شروط اختيار "أفضل عقد" لكل سهم ---
# دلتا أكثر من 0.50 فقط (بدون حد أعلى): عقد ITM بدرجة كافية يتحرك مع السهم
# بقوة. أيام حتى الانتهاء (DTE) بين 1 و120. حجم تداول أكثر من 2 فقط — شرط
# رمزي يستبعد العقود المعدومة الحركة تماماً دون تقييد حقيقي على السيولة. لا
# قيد على العقود المفتوحة (Open Interest) أو التذبذب الضمني (IV) أو سبريد
# العرض/الطلب.
OPTION_FILTERS = {
    "delta_min": 0.50,
    "dte_min": 1, "dte_max": 120,
    "volume_min": 2,
}

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
    # نقطة التعادل لعقد CALL: سعر السهم الذي يجب الوصول إليه عند الانتهاء
    # حتى لا يخسر حامل العقد شيئاً (تنفيذ + بريميوم المدفوع).
    breakeven = round(strike + ask, 2) if ask > 0 else None

    return {
        "strike": strike, "expiry": expiry, "days": days,
        "premium": round(premium, 2), "estimated": estimated,
        "bid": bid, "ask": ask, "spread_pct": spread_pct,
        "volume": vol, "openInterest": oi,
        "iv": iv, "delta": delta, "theta": theta,
        "breakeven": breakeven,
    }


def _add_candidate(candidates, row, spot, expiry, days):
    raw = _raw_candidate(row, spot, expiry, days, is_call=True)
    if raw is not None:
        candidates["call"].append(raw)


def _passes_filters(c: dict) -> bool:
    """يتحقق أن العقد c يحقق شروط OPTION_FILTERS. دلتا غير محسوبة (بيانات
    ناقصة لتقدير Black-Scholes) تعني رفض العقد مباشرة بدل تخمين قيمة له."""
    try:
        delta = c["delta"]
        if delta is None:
            return False
        return (abs(delta) > OPTION_FILTERS["delta_min"]
                and OPTION_FILTERS["dte_min"] <= c["days"] <= OPTION_FILTERS["dte_max"]
                and c["volume"] > OPTION_FILTERS["volume_min"])
    except (KeyError, TypeError):
        return False


def select_best_call(contracts: list[dict]) -> dict | None:
    """يختار أفضل عقد CALL واحد من قائمة عقود سهم معين وفق OPTION_FILTERS.
    العقود المؤهلة تُرتَّب حسب أعلى open_interest أولاً، ثم أقل سعر ask.

    يرجع أفضل عقد، أو None إن لم يحقق أي عقد الشروط."""
    qualified = [c for c in contracts if _passes_filters(c)]
    if not qualified:
        return None
    qualified.sort(key=lambda c: (-c["openInterest"], c["ask"]))
    return qualified[0]


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
    (contract cost = premium * 100), sorted by open interest (desc) then
    ask (asc) — this command searches for cheap contracts specifically, so
    the strict delta/IV filters used by best_options() don't apply here.
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
    cheap.sort(key=lambda c: (-c["openInterest"], c["ask"]))
    out["call"] = cheap[:config.OPTIONS_TOP_N]
    return out


def best_options(symbol: str, spot: float) -> dict:
    """يرجع أفضل عقد CALL واحد لهذا السهم وفق select_best_call(): دلتا أكثر
    من 0.50، أيام انتهاء بين 1 و120، وحجم تداول أكثر من 2 فقط (بلا قيد على
    العقود المفتوحة أو التذبذب الضمني أو السبريد).

    الناتج: {'call': [أفضل عقد] أو []}. قائمة فارغة تعني عدم وجود عقد
    مناسب؛ OptionsFetchError يعني فشل المزوّدَين معاً؛ NoNearTermOptions
    يعني أن للسهم عقوداً لكنها كلها تنتهي بعد OPTIONS_MAX_WEEKS.
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

    try:
        best = select_best_call(candidates["call"])
    except Exception:
        log.exception("select_best_call failed for %s; treating as no pick", symbol)
        best = None

    if best is not None:
        out["call"] = [best]
    return out
