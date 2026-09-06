"""Unusual Whales API client.

Every path and field name below was verified against the official OpenAPI spec
(https://api.unusualwhales.com/api/openapi). UW returns most numbers as JSON
*strings* — normalisation to float happens here, once, so the rest of the code
never has to guess a type.
"""
import datetime
import math
import re
import time

import requests

import config as C

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                                   # pragma: no cover
    _ET = None

_SESSION = requests.Session()
_SESSION.headers.update({
    "Authorization": f"Bearer {C.UW_API_KEY}",
    "Accept": "application/json",
})


class UWError(RuntimeError):
    pass


def _num(v, default=0.0):
    """UW sends numbers as strings ('4.05'), nulls, and real numbers. Coerce."""
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _get(path, params=None, retries=3):
    if not C.UW_API_KEY:
        raise UWError("UW_API_KEY is empty — fill .env before running")
    url = f"{C.UW_BASE}{path}"
    for attempt in range(retries):
        try:
            r = _SESSION.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise UWError(f"{path}: {e}") from e
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429:                      # rate limited
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (401, 403):
            raise UWError(f"{path}: auth failed ({r.status_code}) — check UW_API_KEY")
        if not r.ok:
            if attempt == retries - 1:
                raise UWError(f"{path}: HTTP {r.status_code} {r.text[:200]}")
            time.sleep(2 ** attempt)
            continue
        try:
            return _unwrap(r.json(), path)
        except ValueError as e:
            raise UWError(f"{path}: bad JSON") from e
    raise UWError(f"{path}: exhausted retries")


def _unwrap(payload, path):
    """Pull the rows out of UW's envelope.

    Almost every endpoint wraps its rows in "data". /option-contract/{id}/
    historic wraps them in "chains" instead, so a plain .get("data", []) read
    it as empty — 147 contracts in a row returned nothing and the run blamed
    the subscription. Accept either, and a bare list.
    """
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise UWError(f"{path}: unexpected response type {type(payload).__name__}")
    for key in ("data", "chains", "results"):
        if key in payload:
            rows = payload[key]
            return rows if isinstance(rows, list) else [rows]
    raise UWError(f"{path}: no rows in response (keys: "
                  f"{', '.join(sorted(payload)[:6]) or 'none'})")


# ── OCC symbol parsing (AAPL240202P00185000) ────────────────────
_OCC = re.compile(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")


def parse_occ(symbol):
    """-> {ticker, expiry 'YYYY-MM-DD', type 'call'|'put', strike float} or None."""
    m = _OCC.match(symbol or "")
    if not m:
        return None
    tick, ymd, cp, strike = m.groups()
    return {
        "ticker": tick,
        "expiry": f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}",
        "type": "call" if cp == "C" else "put",
        "strike": int(strike) / 1000.0,
    }


# ── 1) Market-wide unusual options flow ─────────────────────────
def flow_alerts(limit=None):
    """GET /api/option-trades/flow-alerts

    `unusual=true` is UW's own preset for the live-flow screen
    (volume>OI, all-opening, OTM, single-leg, DTE<=60, ask-side>=50%,
    premium>=$10k). Returns normalised dicts.
    """
    raw = _get("/api/option-trades/flow-alerts", {
        "unusual": "true",
        "limit": limit or C.FLOW_ALERT_LIMIT,
    })
    out = []
    for a in raw:
        occ = parse_occ(a.get("option_chain", ""))
        out.append({
            "ticker": (a.get("ticker") or (occ or {}).get("ticker") or "").upper(),
            "alert_rule": a.get("alert_rule", ""),
            "type": (a.get("type") or (occ or {}).get("type") or "").lower(),
            "total_premium": _num(a.get("total_premium")),
            "ask_side_premium": _num(a.get("total_ask_side_prem")),
            "bid_side_premium": _num(a.get("total_bid_side_prem")),
            "has_sweep": bool(a.get("has_sweep")),
            "has_floor": bool(a.get("has_floor")),
            "all_opening": bool(a.get("all_opening_trades")),
            "volume_oi_ratio": _num(a.get("volume_oi_ratio")),
            "open_interest": _num(a.get("open_interest")),
            "underlying_price": _num(a.get("underlying_price")),
            "strike": _num(a.get("strike")),
            "expiry": a.get("expiry", ""),
            "option_symbol": a.get("option_chain", ""),
        })
    return [a for a in out if a["ticker"]]


def ticker_flow_alerts(ticker, limit=100):
    """GET /api/stock/{ticker}/flow-alerts — one ticker's own flow.

    The market-wide feed is capped, so a ticker with real flow can miss the
    window entirely. scanner.py uses this to fill in Finviz movers that the
    market-wide call did not return, then scores them identically.
    """
    try:
        raw = _get(f"/api/stock/{ticker}/flow-alerts", {"limit": limit})
    except UWError:
        return []
    out = []
    for a in raw:
        occ = parse_occ(a.get("option_chain", ""))
        out.append({
            "ticker": (a.get("ticker") or (occ or {}).get("ticker") or ticker).upper(),
            "alert_rule": a.get("alert_rule", ""),
            "type": (a.get("type") or (occ or {}).get("type") or "").lower(),
            "total_premium": _num(a.get("total_premium")),
            "ask_side_premium": _num(a.get("total_ask_side_prem")),
            "bid_side_premium": _num(a.get("total_bid_side_prem")),
            "has_sweep": bool(a.get("has_sweep")),
            "has_floor": bool(a.get("has_floor")),
            "all_opening": bool(a.get("all_opening_trades")),
            "volume_oi_ratio": _num(a.get("volume_oi_ratio")),
            "open_interest": _num(a.get("open_interest")),
            "underlying_price": _num(a.get("underlying_price")),
            "strike": _num(a.get("strike")),
            "expiry": a.get("expiry", ""),
            "option_symbol": a.get("option_chain", ""),
        })
    return out


# ── 2) News headlines ───────────────────────────────────────────
def news(ticker, limit=20):
    """GET /api/news/headlines?ticker=..."""
    try:
        raw = _get("/api/news/headlines", {"ticker": ticker, "limit": limit})
    except UWError:
        return []
    return [{
        "headline": n.get("headline", ""),
        "source": n.get("source", ""),
        "is_major": bool(n.get("is_major")),
        "sentiment": (n.get("sentiment") or "").lower(),
        "tags": n.get("tags") or [],
        "created_at": n.get("created_at", ""),
    } for n in raw]


def next_earnings_days(ticker):
    """GET /api/stock/{ticker}/earnings -> days until the next report, or None.

    Implied volatility collapses the moment earnings are released. A contract
    bought the day before can be right about direction and still lose money,
    because the IV it was priced with disappears. Nothing in a 15m breakout
    chart shows this coming.
    """
    try:
        raw = _get(f"/api/stock/{ticker}/earnings")
    except UWError:
        return None
    today = datetime.date.today()
    upcoming = []
    for r in raw or []:
        d = r.get("report_date")
        if not d:
            continue
        try:
            day = datetime.date.fromisoformat(d)
        except ValueError:
            continue
        if day >= today:
            upcoming.append((day - today).days)
    return min(upcoming) if upcoming else None


# ── 3) Option chain with greeks ─────────────────────────────────
def _normalise_contract(c):
    """UW field names -> the names scoring.py expects."""
    sym = c.get("option_symbol") or c.get("option_chain") or ""
    occ = parse_occ(sym) or {}
    strike = c.get("strike", occ.get("strike"))
    ctype = c.get("option_type") or occ.get("type") or ""
    expiry = c.get("expires") or c.get("expiry") or occ.get("expiry") or ""
    return {
        "option_symbol": sym,
        "strike": _num(strike),
        "type": str(ctype).lower(),
        "expiry": expiry,
        "bid": _num(c.get("nbbo_bid")),
        "ask": _num(c.get("nbbo_ask")),
        "delta": _num(c.get("delta")),
        "gamma": _num(c.get("gamma")),
        "theta": _num(c.get("theta")),
        "implied_volatility": _num(c.get("implied_volatility")),
        "open_interest": _num(c.get("open_interest")),
        "volume": _num(c.get("volume")),
        "dte": _dte(expiry),
    }


def _dte(expiry):
    """Calendar days until expiry, or None when the date is unusable."""
    try:
        d = datetime.date.fromisoformat(expiry)
    except (TypeError, ValueError):
        return None
    return (d - datetime.date.today()).days


def _in_window(c):
    """Contracts this system is willing to trade.

    The chain endpoint returns every expiry the ticker has - AAPL came back
    with 3,294 contracts running out to 2028. A 2028 LEAP is not a candidate
    for a 15-minute breakout, and a cheap far-dated OTM call can slip through
    the budget filter on price alone, so the DTE window is enforced here
    rather than only in the option_contracts fallback.

    Contracts without a delta are dropped too: expected_profit_pct needs one,
    and roughly half of what UW returns for a full chain has none.
    """
    dte = _dte(c.get("expiry"))
    if dte is None or not (C.MIN_DTE <= dte <= C.MAX_DTE):
        return False
    return abs(_num(c.get("delta"))) > 0   # _num handles None and "" too


def option_chain(ticker):
    """GET /api/stock/{ticker}/option-chains?greeks=true

    IMPORTANT: without `greeks=true` this endpoint returns a plain array of
    option-symbol STRINGS — the original code assumed objects and would have
    produced an empty contract list on every run. With greeks=true each row
    carries strike/expires/option_type/nbbo_bid/nbbo_ask/delta/open_interest.
    Falls back to /option-contracts if the enriched form is unavailable.
    """
    try:
        raw = _get(f"/api/stock/{ticker}/option-chains", {"greeks": "true"})
    except UWError:
        raw = []
    if raw and isinstance(raw[0], dict):
        chain = [_normalise_contract(c) for c in raw]
        usable = [c for c in chain if _in_window(c)]
        if usable:
            return usable
        print(f"[uw] {ticker}: {len(chain)} contracts, none inside "
              f"{C.MIN_DTE}-{C.MAX_DTE} DTE with a delta")
        return []
    # fallback: greeks not served on this plan for option-chains
    return [c for c in option_contracts(ticker) if _in_window(c)]


def option_contracts(ticker):
    """GET /api/stock/{ticker}/option-contracts — greeks + NBBO per contract."""
    try:
        raw = _get(f"/api/stock/{ticker}/option-contracts", {
            "min_dte": C.MIN_DTE,
            "max_dte": C.MAX_DTE,
            "exclude_zero_oi_chains": "true",
            "exclude_zero_vol_chains": "true",
            "limit": 500,
        })
    except UWError:
        return []
    return [_normalise_contract(c) for c in raw if isinstance(c, dict)]


# ── 4) Real-time intraday candles ───────────────────────────────
def candles(ticker, candle_size=None, timeframe="5D", limit=500, end_date=None):
    """GET /api/stock/{ticker}/ohlc/{candle_size}

    Returns candles ASCENDING in time (UW serves intraday newest-first).
    Fields open/high/low/close arrive as strings -> floats here.
    `market_time` is 'r' (regular), 'pr' (pre) or 'po' (post); technical
    levels are computed on regular-hours candles only.

    Daily and weekly candles are a different shape: UW documents that 1d and
    1w rows carry no start_time and no end_time at all, only `date`. Judging
    them with the intraday `end_time` rule dropped every one of them, which is
    what silently made daily_atr() return 0 for months.
    """
    size = candle_size or C.CANDLE_SIZE
    daily = size in ("1d", "1w")
    try:
        params = {"timeframe": timeframe, "limit": limit}
        if end_date:
            params["end_date"] = end_date       # walk backwards for backtests
        raw = _get(f"/api/stock/{ticker}/ohlc/{size}", params)
    except UWError:
        return []
    rows = []
    for c in raw:
        if not daily and C.REGULAR_HOURS_ONLY and c.get("market_time") not in (None, "r"):
            continue
        date = c.get("date", "") or ""
        rows.append({
            "open": _num(c.get("open")),
            "high": _num(c.get("high")),
            "low": _num(c.get("low")),
            "close": _num(c.get("close")),
            "volume": _num(c.get("volume")),
            "date": date,
            "start_time": c.get("start_time", "") or "",
            "end_time": c.get("end_time", "") or "",
            "closed": (_session_done(date, size) if daily
                       else _is_closed(c.get("end_time", ""))),
        })
    # daily rows sort by date, intraday by start_time; UW's order differs per size
    rows.sort(key=lambda r: (r["start_time"] or r["date"]))
    rows = [r for r in rows if r["close"] > 0]
    return [r for r in rows if r["closed"]]


def _session_done(date, size):
    """True once a daily/weekly bar's session has finished.

    Today's daily bar is still forming until the close, and this week's weekly
    bar until Friday, so both are excluded the same way a half-formed 15m
    candle is.
    """
    if not date:
        return False
    today = (datetime.datetime.now(_ET) if _ET else
             datetime.datetime.utcnow() - datetime.timedelta(hours=5)).date()
    try:
        d = datetime.date.fromisoformat(date[:10])
    except ValueError:
        return False
    span = 7 if size == "1w" else 1     # 1w dates the Monday of the ISO week
    return (today - d).days >= span


def _is_closed(end_time):
    """True once the bar's window has actually elapsed.

    UW includes the bar currently forming. Judging a break on it means reading
    a 15-minute candle five minutes in: price can be above the level now and
    close back under it, which is exactly the false break the 15m frame exists
    to filter out. Only completed bars are ever scored.
    """
    if not end_time:
        return False
    try:
        end = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except ValueError:
        return False
    if end.tzinfo is None:
        end = end.replace(tzinfo=datetime.timezone.utc)
    return end <= datetime.datetime.now(datetime.timezone.utc)


def spot(ticker):
    """Latest regular-hours close from the 1m candles."""
    c = candles(ticker, candle_size="1m", timeframe="1D", limit=5)
    return c[-1]["close"] if c else 0.0


def contract_history(option_symbol, limit=500):
    """GET /api/option-contract/{id}/historic — one contract's own daily tape.

    This is what makes explosion.py possible: the contract's price, IV, open
    interest and ask/bid volume split for every day it traded. The stock-level
    backtest could only ask whether price reached a level; this asks the
    question Salem actually cares about — what a contract bought that day went
    on to be worth.
    """
    raw = _get(f"/api/option-contract/{option_symbol}/historic", {"limit": limit})
    rows = []
    for r in raw or []:
        bid, ask = _num(r.get("nbbo_bid")), _num(r.get("nbbo_ask"))
        rows.append({
            "date": r.get("date", ""),
            "open": _num(r.get("open_price")), "high": _num(r.get("high_price")),
            "low": _num(r.get("low_price")), "last": _num(r.get("last_price")),
            "bid": bid, "ask": ask,
            "iv": _num(r.get("implied_volatility")),
            "iv_high": _num(r.get("iv_high")), "iv_low": _num(r.get("iv_low")),
            "open_interest": _num(r.get("open_interest")),
            "volume": _num(r.get("volume")),
            "ask_volume": _num(r.get("ask_volume")),
            "bid_volume": _num(r.get("bid_volume")),
            "sweep_volume": _num(r.get("sweep_volume")),
            "premium": _num(r.get("total_premium")),
        })
    return sorted([r for r in rows if r["date"]], key=lambda r: r["date"])


def _normalise_screener_row(c):
    """The screener speaks a different dialect from the chain endpoints.

    It reports `close` rather than an NBBO pair, `ask_side_volume` rather than
    `ask_volume`, and carries no greeks at all. Running it through
    _normalise_contract produced ask=0 on every row, so a price filter of
    $0.02-$1.00 rejected the entire result and the run reported "returned
    nothing in that price band" for a screener that had answered fine.
    """
    sym = c.get("option_symbol") or ""
    occ = parse_occ(sym) or {}
    ask_v, bid_v = _num(c.get("ask_side_volume")), _num(c.get("bid_side_volume"))
    return {
        "option_symbol": sym,
        "strike": _num(c.get("strike", occ.get("strike"))),
        "type": str(c.get("option_type") or occ.get("type") or "").lower(),
        "expiry": c.get("expiry") or occ.get("expiry") or "",
        "price": _num(c.get("close")),          # what the contract last traded at
        "high": _num(c.get("high")), "low": _num(c.get("low")),
        "open": _num(c.get("open")),
        "prev_close": _num(c.get("chain_prev_close")),
        "volume": _num(c.get("volume")),
        "open_interest": _num(c.get("open_interest")),
        "ask_volume": ask_v, "bid_volume": bid_v,
        "sweep_volume": _num(c.get("sweep_volume")),
        "premium": _num(c.get("premium")),
        "multileg_volume": _num(c.get("multileg_volume")),
        "days_of_oi_increases": _num(c.get("days_of_oi_increases")),
        "stock_price": _num(c.get("stock_price")),
        "next_earnings_date": c.get("next_earnings_date") or "",
        "sector": c.get("sector") or "",
    }


def contract_intraday(option_symbol, date=None):
    """GET /api/option-contract/{id}/intraday — one contract, minute by minute.

    The daily tape in contract_history() cannot describe a same-day trade: a
    0DTE contract has one row there, and Salem's trade is bought and sold
    inside a single session. This is the only endpoint that can measure it.

    UW does not publish the row shape, so field names are taken defensively
    and the raw keys of the first row are exposed as `_keys` for the caller to
    report. Rows are returned ASCENDING in time.
    """
    params = {"date": date} if date else {}
    raw = _get(f"/api/option-contract/{option_symbol}/intraday", params)
    rows = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        close = _num(_first(r, "close", "price", "last", "last_price"))
        # UW names these volume_ask_side / volume_bid_side here, not
        # ask_volume / bid_volume as the daily tape does. Reading the wrong
        # name is a silent zero: it made minute volume and ask share blank on
        # every one of 6,867 trades in the first 0DTE run.
        ask_v = _num(_first(r, "volume_ask_side", "ask_volume", "ask_side_volume"))
        bid_v = _num(_first(r, "volume_bid_side", "bid_volume", "bid_side_volume"))
        mid_v = _num(_first(r, "volume_mid_side", "mid_volume"))
        no_v = _num(_first(r, "volume_no_side", "no_side_volume", "neutral_volume"))
        total_v = _num(_first(r, "volume", "total_volume"))
        rows.append({
            "time": _first(r, "start_time", "tape_time", "timestamp", "time",
                           default=""),
            "open": _num(_first(r, "open", "price")),
            "high": _num(_first(r, "high", "price")),
            "low": _num(_first(r, "low", "price")),
            "close": close,
            "avg_price": _num(_first(r, "avg_price")),
            # This endpoint serves no NBBO at all — the caller must charge a
            # spread explicitly rather than pretend the trade price is free.
            "bid": _num(_first(r, "nbbo_bid", "bid", "bid_price")),
            "ask": _num(_first(r, "nbbo_ask", "ask", "ask_price")),
            "volume": total_v or (ask_v + bid_v + mid_v + no_v),
            "ask_volume": ask_v,
            "bid_volume": bid_v,
            "iv": _num(_first(r, "iv_high", "implied_volatility", "iv")),
            # premium is dollars, volume is contracts, so premium/(volume*100)
            # is the average price of the trades that went off on that side.
            # The gap between the two IS the spread, measured from real prints
            # — the only way to get it here, since no NBBO is served.
            "ask_px": (_num(_first(r, "premium_ask_side")) / (ask_v * 100)
                       if ask_v > 0 else 0.0),
            "bid_px": (_num(_first(r, "premium_bid_side")) / (bid_v * 100)
                       if bid_v > 0 else 0.0),
            "delta": _num(_first(r, "delta")),
            "_keys": sorted(r.keys()),
        })
    rows.sort(key=lambda r: r["time"])
    return [r for r in rows if r["close"] > 0 or r["ask"] > 0]


def _first(row, *names, default=None):
    """The first of `names` the row actually carries — UW's field naming
    differs between endpoints and guessing wrong reads as a zero."""
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return default


def screen_contracts(**filters):
    """GET /api/screener/option-contracts — the candidate pool.

    Finds the shape of contract Salem is after: cheap, out of the money, with
    volume that is unusual against its own open interest.

    Raises rather than returning [] on failure: an empty list from a plan that
    does not serve this endpoint is indistinguishable from an empty result,
    and the caller cannot tell the user which one happened.
    """
    params = {k: v for k, v in filters.items() if v is not None}
    # The screener names its array filters with a literal "[]" suffix, which is
    # not a valid Python keyword, so callers pass the bare name and it is
    # translated here. `expiry_dates` is the only reliable way to ask for a
    # specific expiry: min_dte/max_dte are measured from TODAY, so asking for
    # dte 0 on a past session returns nothing at all.
    for name in ("expiry_dates", "sectors", "issue_types"):
        if name in params:
            params[f"{name}[]"] = params.pop(name)
    params.setdefault("limit", 250)
    raw = _get("/api/screener/option-contracts", params)
    return [_normalise_screener_row(c) for c in raw if isinstance(c, dict)]


_gex_cache, _tech_cache, _intraday_cache = {}, {}, {}


def gex_levels(ticker, date=None):
    """GET /api/stock/{ticker}/gex-levels — where dealer hedging changes sign.

    Below the gamma flip dealers are short gamma: they sell rallies and buy
    dips, which suppresses movement. Above it they buy strength, which
    amplifies it. That is a mechanical reason a breakout continues or dies,
    and nothing in a contract's own tape contains it.
    """
    key = (ticker, date)
    if key in _gex_cache:
        return _gex_cache[key]
    try:
        raw = _get(f"/api/stock/{ticker}/gex-levels",
                   {"source": "vol", **({"date": date} if date else {})})
    except UWError:
        _gex_cache[key] = None
        return None
    row = raw[0] if isinstance(raw, list) and raw else raw
    out = None
    if isinstance(row, dict):
        out = {k: _num(row.get(k)) or None for k in
               ("call_wall", "put_wall", "gamma_flip", "gamma_magnet")}
    _gex_cache[key] = out
    return out


def stock_technicals(ticker, as_of=None, period=14):
    """Daily ATR, RSI and distance from the 20-day average, from one pull.

    All three describe whether the stock is stretched or coiled, and whether it
    can travel to the strike at all — the one thing a contract's own tape can
    never say. They are computed here rather than read off Finviz for two
    reasons: Finviz serves only *today's* value, which is lookahead in a
    backtest of last year's entries, and one daily OHLC request already carries
    everything needed.

    `as_of` (YYYY-MM-DD) walks the window back, so a backtest sees what was
    knowable on the entry date and nothing after it.
    """
    key = (ticker, as_of or "", period)
    if key in _tech_cache:
        return _tech_cache[key]
    bars = candles(ticker, candle_size="1d", timeframe="6M", limit=120,
                   end_date=as_of)
    out = {"atr": 0.0, "rsi": None, "sma20": None, "vs_sma20": None,
           "close": bars[-1]["close"] if bars else 0.0}
    if len(bars) > period:
        out["atr"] = _wilder_atr(bars, period)
        out["rsi"] = _wilder_rsi([b["close"] for b in bars], period)
    if len(bars) >= 20:
        sma = sum(b["close"] for b in bars[-20:]) / 20
        out["sma20"] = round(sma, 2)
        if sma > 0:
            out["vs_sma20"] = round((bars[-1]["close"] - sma) / sma * 100, 2)
    _tech_cache[key] = out
    return out


def _wilder_atr(bars, period=14):
    """Wilder's smoothed ATR — the same definition UW and Finviz publish, so a
    number here can be checked against either."""
    trs = [max(b["high"] - b["low"], abs(b["high"] - a["close"]),
               abs(b["low"] - a["close"]))
           for a, b in zip(bars, bars[1:])]
    if len(trs) < period:
        return 0.0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 4)


def _wilder_rsi(closes, period=14):
    """Wilder's RSI on daily closes. None when there is not enough history."""
    if len(closes) <= period:
        return None
    deltas = [b - a for a, b in zip(closes, closes[1:])]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0 if avg_g > 0 else None
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 2)


def intraday_technicals(ticker, as_of=None, period=14):
    """The same three readings as stock_technicals, but on 15m bars.

    This is the measuring stick a same-day trader needs. A daily ATR says what
    the stock does in a week of sessions; a 0DTE contract has hours. ATR(14) on
    15m bars reads the last three and a half hours, so it reflects today's
    regime rather than a fortnight's average.

    `session_move` scales one bar's ATR to a whole session by the square root
    of the bar count — a random walk covers sqrt(n) bars of range in n bars,
    not n. It is the honest answer to "how far can this stock travel before the
    close", which is the only distance a same-day contract can use.
    """
    key = (ticker, as_of or "", period)
    if key in _intraday_cache:
        return _intraday_cache[key]
    # limit=500, not 200: UW applies the limit to the raw rows, which include
    # pre- and post-market bars, and REGULAR_HOURS_ONLY drops those afterwards.
    # 200 raw rows is about three sessions of extended hours -> only 78 usable
    # bars, against the 130 the rest of the code reads on the same request.
    bars = candles(ticker, candle_size="15m", timeframe="5D", limit=500,
                   end_date=as_of)
    out = {"atr15": 0.0, "rsi": None, "session_move": 0.0,
           "close": bars[-1]["close"] if bars else 0.0, "bars": len(bars)}
    if len(bars) > period:
        out["atr15"] = _wilder_atr(bars, period)
        out["rsi"] = _wilder_rsi([b["close"] for b in bars], period)
        out["session_move"] = round(out["atr15"] * math.sqrt(C.BARS_PER_SESSION), 4)
    _intraday_cache[key] = out
    return out


def session_move(ticker, as_of=None):
    """How far the stock can travel in one full session, from 15m volatility."""
    return intraday_technicals(ticker, as_of=as_of)["session_move"]


def daily_atr(ticker, as_of=None, period=14):
    """Average true range on daily bars — the unit a move is measured in."""
    return stock_technicals(ticker, as_of=as_of, period=period)["atr"]


def contract_quote(option_symbol):
    """GET /api/option-contract/{id}/intraday — latest price for one contract.
    Used by monitor.py for exit alerts (replaces the delayed yfinance quote)."""
    try:
        raw = _get(f"/api/option-contract/{option_symbol}/intraday", {"limit": 1})
    except UWError:
        return None
    if not raw:
        return None
    row = raw[-1] if isinstance(raw, list) else raw
    bid, ask = _num(row.get("nbbo_bid")), _num(row.get("nbbo_ask"))
    mid = (bid + ask) / 2 if (bid and ask) else 0.0
    price = mid or _num(row.get("last_price")) or _num(row.get("close"))
    return {"price": price, "bid": bid, "ask": ask,
            "volume": _num(row.get("volume")),
            "open_interest": _num(row.get("open_interest"))}
