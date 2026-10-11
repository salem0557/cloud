"""In-memory stand-in for AbyanBroker, for tests and offline replay."""
from __future__ import annotations

import datetime as dt

from ..broker.abyan import BrokerUnavailable, LiveTradingBlocked, WRITE_TOOLS


class FakeBroker:
    name = "fake"

    def __init__(self, read_only: bool = True):
        self.read_only = read_only
        self.chain: dict[str, dict[dt.date, list[dict]]] = {}
        self.fmvs: dict[str, float] = {}
        self.down = False
        self.last_ok = None
        self.last_error = None
        self.calls: list[str] = []
        self.placed: list[dict] = []
        self.order_rows: list[dict] = []
        self.position_rows: list[dict] = []
        self.buying_power = 1000.0

    def list_contract(self, occ_contract) -> None:
        c = occ_contract
        self.chain.setdefault(c.underlying, {}).setdefault(c.expiry, []).append(
            {"symbol": c.occ, "underlying_symbol": c.underlying,
             "type": c.right_name, "strike_price": c.strike})

    def _guard(self, tool):
        self.calls.append(tool)
        if self.down:
            self.last_error = "down"
            raise BrokerUnavailable("أبيان غير متاح (اختبار)")
        if tool in WRITE_TOOLS and self.read_only:
            raise LiveTradingBlocked(f"محظور: {tool}")

    def account(self):
        self._guard("get_portfolio")
        return {"buying_power": self.buying_power}

    def positions(self):
        self._guard("list_positions")
        return list(self.position_rows)

    def orders(self, symbol=None, status=None, limit=100):
        self._guard("list_orders")
        return [o for o in self.order_rows if symbol is None or o.get("symbol") == symbol]

    def expirations(self, underlying):
        self._guard("browse_options_chain")
        return sorted(self.chain.get(underlying, {}))

    def contracts(self, underlying, expiry, right):
        self._guard("browse_options_chain")
        name = "call" if right == "C" else "put"
        return [r for r in self.chain.get(underlying, {}).get(expiry, []) if r["type"] == name]

    def fmv(self, contract):
        self._guard("get_current_price")
        return self.fmvs.get(contract.occ)

    def place_limit(self, occ, side, qty, limit, fmv):
        self._guard("place_order")
        self.placed.append({"occ": occ, "side": side, "qty": qty, "limit": limit})
        return {"client_order_id": f"live-{len(self.placed)}", "status_raw": "accepted"}

    def cancel(self, broker_order_id):
        self._guard("cancel_order")
        return {}
