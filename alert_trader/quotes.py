"""Bid/ask sources for validation and paper fills, behind one interface.

Abyan's connector returns an indicative fair-market value only, with no bid,
ask, size or timestamp, so it cannot price a fill. Bid/ask comes from one of
these providers, and every quote carries its source, its market timestamp and
whether the feed is delayed. Using one of them is never reported as an Abyan
check.

* cboe     - CBOE's public delayed feed: free, keyless, 15 minutes behind.
             Its quotes will fail any freshness limit tighter than that.
* polygon  - Polygon/Massive options snapshot (POLYGON_API_KEY); real-time or
             delayed depending on the plan, reported per quote.
* tradier  - Tradier markets API (TRADIER_TOKEN); real-time for funded
             brokerage accounts, delayed for sandbox (TRADIER_DELAYED).
"""
from __future__ import annotations

import datetime as dt
import threading
import time

import requests

from .market_calendar import NY, UTC, market_time
from .models import OptionContract, Quote

TIMEOUT = 15


class QuoteError(Exception):
    pass


class QuoteProvider:
    name = "base"

    def quote(self, contract: OptionContract) -> Quote:
        raise NotImplementedError


class CboeDelayedProvider(QuoteProvider):
    name = "cboe-delayed-15m"
    URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
    DELAY = dt.timedelta(minutes=15)

    def __init__(self, cache_sec: float = 10.0, session=None):
        self.cache_sec = cache_sec
        self.http = session or requests.Session()
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def _chain(self, underlying: str) -> dict:
        with self._lock:
            hit = self._cache.get(underlying)
            if hit and time.monotonic() - hit[0] < self.cache_sec:
                return hit[1]
        resp = self.http.get(self.URL.format(symbol=underlying.upper()), timeout=TIMEOUT)
        if resp.status_code != 200:
            raise QuoteError(f"CBOE HTTP {resp.status_code}")
        payload = resp.json()
        with self._lock:
            self._cache[underlying] = (time.monotonic(), payload)
        return payload

    def quote(self, contract: OptionContract) -> Quote:
        payload = self._chain(contract.underlying)
        data = payload.get("data") or {}
        row = next((o for o in data.get("options", []) if o.get("option") == contract.occ), None)
        if row is None:
            raise QuoteError(f"العقد {contract.occ} غير موجود في بيانات CBOE")
        stamp = payload.get("timestamp")
        quote_time = None
        if stamp:
            # The snapshot stamp is New York time; the prices in it are 15 min older.
            # Outside the session the snapshot is a cache of the last close.
            quote_time = market_time(dt.datetime.fromisoformat(stamp).replace(tzinfo=NY)
                                     .astimezone(UTC) - self.DELAY)
        return Quote(
            symbol=contract.occ, bid=_pos(row.get("bid")), ask=_pos(row.get("ask")),
            bid_size=_int(row.get("bid_size")), ask_size=_int(row.get("ask_size")),
            last=_pos(row.get("last_trade_price")), volume=_int(row.get("volume")),
            open_interest=_int(row.get("open_interest")), quote_time=quote_time,
            source=self.name, delayed=True,
            underlying_price=_pos(data.get("current_price")),
        )


class PolygonProvider(QuoteProvider):
    name = "polygon"
    URL = "https://api.polygon.io/v3/snapshot/options/{underlying}/O:{occ}"

    def __init__(self, api_key: str, session=None):
        self.api_key = api_key
        self.http = session or requests.Session()

    def quote(self, contract: OptionContract) -> Quote:
        resp = self.http.get(self.URL.format(underlying=contract.underlying, occ=contract.occ),
                             params={"apiKey": self.api_key}, timeout=TIMEOUT)
        if resp.status_code != 200:
            raise QuoteError(f"Polygon HTTP {resp.status_code}")
        res = resp.json().get("results") or {}
        lq = res.get("last_quote") or {}
        if not lq:
            raise QuoteError("Polygon: لا يوجد عرض/طلب لهذا العقد في خطتك")
        updated = lq.get("last_updated")
        quote_time = (dt.datetime.fromtimestamp(updated / 1e9, tz=UTC) if updated else None)
        day = res.get("day") or {}
        return Quote(
            symbol=contract.occ, bid=_pos(lq.get("bid")), ask=_pos(lq.get("ask")),
            bid_size=_int(lq.get("bid_size")), ask_size=_int(lq.get("ask_size")),
            last=_pos((res.get("last_trade") or {}).get("price")),
            volume=_int(day.get("volume")), open_interest=_int(res.get("open_interest")),
            quote_time=quote_time, source=self.name,
            delayed=(lq.get("timeframe") or "").upper() != "REAL-TIME",
            underlying_price=_pos((res.get("underlying_asset") or {}).get("price")),
        )


class TradierProvider(QuoteProvider):
    name = "tradier"

    def __init__(self, token: str, base_url: str, delayed: bool, session=None):
        self.token, self.base_url, self.delayed = token, base_url.rstrip("/"), delayed
        self.http = session or requests.Session()

    def quote(self, contract: OptionContract) -> Quote:
        resp = self.http.get(f"{self.base_url}/v1/markets/quotes",
                             params={"symbols": contract.occ, "greeks": "false"},
                             headers={"Authorization": f"Bearer {self.token}",
                                      "Accept": "application/json"}, timeout=TIMEOUT)
        if resp.status_code != 200:
            raise QuoteError(f"Tradier HTTP {resp.status_code}")
        q = ((resp.json().get("quotes") or {}).get("quote")) or {}
        if isinstance(q, list):
            q = q[0] if q else {}
        if not q:
            raise QuoteError("Tradier: العقد غير موجود")
        stamps = [s for s in (q.get("bid_date"), q.get("ask_date")) if s]
        quote_time = dt.datetime.fromtimestamp(min(stamps) / 1000, tz=UTC) if stamps else None
        return Quote(
            symbol=contract.occ, bid=_pos(q.get("bid")), ask=_pos(q.get("ask")),
            bid_size=_int(q.get("bidsize")), ask_size=_int(q.get("asksize")),
            last=_pos(q.get("last")), volume=_int(q.get("volume")),
            open_interest=_int(q.get("open_interest")), quote_time=quote_time,
            source=f"{self.name}{'-delayed' if self.delayed else ''}", delayed=self.delayed,
        )


class StaticProvider(QuoteProvider):
    """Test/replay provider: quotes set by hand."""
    name = "static"

    def __init__(self):
        self.quotes: dict[str, Quote] = {}
        self.fail = False

    def set(self, q: Quote) -> None:
        self.quotes[q.symbol] = q

    def quote(self, contract: OptionContract) -> Quote:
        if self.fail:
            raise QuoteError("مصدر الأسعار غير متاح")
        if contract.occ not in self.quotes:
            raise QuoteError(f"لا يوجد سعر للعقد {contract.occ}")
        return self.quotes[contract.occ]


def _pos(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def build(env) -> QuoteProvider:
    if env.quote_provider == "polygon":
        if not env.polygon_api_key:
            raise ValueError("QUOTE_PROVIDER=polygon يتطلب POLYGON_API_KEY")
        return PolygonProvider(env.polygon_api_key)
    if env.quote_provider == "tradier":
        if not env.tradier_token:
            raise ValueError("QUOTE_PROVIDER=tradier يتطلب TRADIER_TOKEN")
        return TradierProvider(env.tradier_token, env.tradier_base_url, env.tradier_delayed)
    return CboeDelayedProvider()
