"""Exchange access through ccxt, plus a paper broker that fills against live
prices.

Connecting your wallet here means exchange API keys — never a seed phrase or a
private key. Create the key with trade permission only, leave withdrawal
disabled, and IP-restrict it if your exchange allows.

Market data works with no keys at all, so the analyst runs and reports whether
or not the trader is connected.
"""
import logging
import time

from . import config
from .indicators import Candle

log = logging.getLogger(__name__)


class ExchangeError(RuntimeError):
    pass


class Exchange:
    """Thin ccxt wrapper. Public data always; private calls need keys."""

    def __init__(self, exchange_id: str | None = None):
        self.id = exchange_id or config.EXCHANGE_ID
        self._client = None
        self._markets_loaded = False

    # ------------------------------------------------------------- plumbing
    @property
    def client(self):
        if self._client is None:
            try:
                import ccxt  # imported lazily so tests and the analyst run without it
            except ImportError as exc:  # pragma: no cover - env-dependent
                raise ExchangeError(
                    "مكتبة ccxt غير مثبّتة — نفّذ: pip install ccxt") from exc
            if not hasattr(ccxt, self.id):
                raise ExchangeError(f"منصة غير معروفة: {self.id}")
            params = {
                "enableRateLimit": True,
                "options": {"defaultType": config.MARKET_TYPE},
            }
            if config.API_KEY:
                params["apiKey"] = config.API_KEY
                params["secret"] = config.API_SECRET
            if config.API_PASSWORD:
                params["password"] = config.API_PASSWORD
            self._client = getattr(ccxt, self.id)(params)
            if config.TESTNET:
                try:
                    self._client.set_sandbox_mode(True)
                except Exception as exc:  # pragma: no cover - exchange-specific
                    log.warning("sandbox mode unavailable on %s: %s", self.id, exc)
        return self._client

    def load_markets(self) -> dict:
        if not self._markets_loaded:
            self.client.load_markets()
            self._markets_loaded = True
        return self.client.markets

    @property
    def has_keys(self) -> bool:
        return bool(config.API_KEY and config.API_SECRET)

    # ---------------------------------------------------------- market data
    def fetch_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        rows = self.client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        candles = [Candle.from_ccxt(r) for r in rows or []]
        # The final bar is still forming; scoring it means reacting to a candle
        # that can still reverse before it closes.
        return candles[:-1] if len(candles) > 1 else candles

    def fetch_ticker(self, symbol: str) -> dict:
        return self.client.fetch_ticker(symbol)

    def last_price(self, symbol: str) -> float:
        t = self.fetch_ticker(symbol)
        price = t.get("last") or t.get("close") or t.get("bid")
        if not price:
            raise ExchangeError(f"تعذّر جلب سعر {symbol}")
        return float(price)

    def top_symbols(self, n: int, quote: str | None = None) -> list[str]:
        """The n most-traded pairs of the quote asset, by 24h quote volume."""
        quote = (quote or config.QUOTE).upper()
        self.load_markets()
        tickers = self.client.fetch_tickers()
        rows = []
        for sym, t in tickers.items():
            market = self.client.markets.get(sym) or {}
            if market.get("quote") != quote or not market.get("active", True):
                continue
            if config.MARKET_TYPE == "spot" and not market.get("spot", False):
                continue
            qv = t.get("quoteVolume") or 0
            rows.append((qv, sym))
        rows.sort(reverse=True)
        return [s for _, s in rows[:n]]

    def market_meta(self, symbol: str) -> dict:
        self.load_markets()
        return self.client.markets.get(symbol, {})

    def amount_to_precision(self, symbol: str, qty: float) -> float:
        try:
            return float(self.client.amount_to_precision(symbol, qty))
        except Exception:  # pragma: no cover - falls back to raw qty
            return qty

    # --------------------------------------------------------------- private
    def fetch_free_balance(self, asset: str | None = None) -> float:
        asset = (asset or config.QUOTE).upper()
        if not self.has_keys:
            raise ExchangeError("لا توجد مفاتيح API للاتصال بالمحفظة")
        bal = self.client.fetch_balance()
        free = (bal.get("free") or {}).get(asset)
        return float(free or 0.0)

    def create_market_order(self, symbol: str, side: str, qty: float,
                            reduce_only: bool = False) -> dict:
        """Real order. Refuses unless both live-trading switches agree."""
        blocker = config.live_blocker()
        if blocker:
            raise ExchangeError(f"التداول الحقيقي متوقف: {blocker}")
        params = {}
        if reduce_only and config.MARKET_TYPE != "spot":
            params["reduceOnly"] = True
        qty = self.amount_to_precision(symbol, qty)
        log.info("LIVE order %s %s %s", side, qty, symbol)
        order = self.client.create_order(symbol, "market", side, qty, None, params)
        return {
            "price": float(order.get("average") or order.get("price") or 0.0),
            "qty": float(order.get("filled") or qty),
            "fee": float(((order.get("fee") or {}).get("cost")) or 0.0),
            "id": order.get("id"),
            "raw": order,
        }


class PaperBroker:
    """Simulates fills at the live price, charging fee and slippage both ways.

    Optimistic paper results are worse than useless — they make a losing
    strategy look fundable — so costs are always applied against us.
    """

    def __init__(self, exchange: Exchange):
        self.exchange = exchange

    def market_order(self, symbol: str, side: str, qty: float,
                     price: float | None = None) -> dict:
        px = price if price is not None else self.exchange.last_price(symbol)
        slip = config.SLIPPAGE_PCT / 100
        fill = px * (1 + slip) if side == "buy" else px * (1 - slip)
        fee = fill * qty * config.TAKER_FEE_PCT / 100
        return {"price": fill, "qty": qty, "fee": fee,
                "id": f"paper-{int(time.time() * 1000)}", "raw": None}
