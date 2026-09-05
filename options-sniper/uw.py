"""Unusual Whales API client.

Every path and field name below was verified against the official OpenAPI spec
(https://api.unusualwhales.com/api/openapi). UW returns most numbers as JSON
*strings* — normalisation to float happens here, once, so the rest of the code
never has to guess a type.
"""
import datetime
import re
import time

import requests

import config as C

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

    Returns candles ASCENDING in time (UW serves them newest-first).
    Fields open/high/low/close arrive as strings -> floats here.
    `market_time` is 'r' (regular), 'pr' (pre) or 'po' (post); technical
    levels are computed on regular-hours candles only.
    """
    size = candle_size or C.CANDLE_SIZE
    try:
        params = {"timeframe": timeframe, "limit": limit}
        if end_date:
            params["end_date"] = end_date       # walk backwards for backtests
        raw = _get(f"/api/stock/{ticker}/ohlc/{size}", params)
    except UWError:
        return []
    rows = []
    for c in raw:
        if C.REGULAR_HOURS_ONLY and c.get("market_time") not in (None, "r"):
            continue
        rows.append({
            "open": _num(c.get("open")),
            "high": _num(c.get("high")),
            "low": _num(c.get("low")),
            "close": _num(c.get("close")),
            "volume": _num(c.get("volume")),
            "start_time": c.get("start_time", ""),
            "end_time": c.get("end_time", ""),
            "closed": _is_closed(c.get("end_time", "")),
        })
    rows.sort(key=lambda r: r["start_time"])          # UW returns newest-first
    rows = [r for r in rows if r["close"] > 0]
    return [r for r in rows if r["closed"]]


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


def screen_contracts(**filters):
    """GET /api/screener/option-contracts — the candidate pool.

    Finds the shape of contract Salem is after: cheap, out of the money, with
    volume that is unusual against its own open interest.

    Raises rather than returning [] on failure: an empty list from a plan that
    does not serve this endpoint is indistinguishable from an empty result,
    and the caller cannot tell the user which one happened.
    """
    params = {k: v for k, v in filters.items() if v is not None}
    params.setdefault("limit", 250)
    raw = _get("/api/screener/option-contracts", params)
    return [_normalise_screener_row(c) for c in raw if isinstance(c, dict)]


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
