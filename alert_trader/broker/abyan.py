"""Abyan Trading through its MCP endpoint (the interface Abyan publishes for
AI assistants), spoken directly as JSON-RPC over Streamable HTTP.

What was verified (2026-10-11, through the Claude connector): the tool names,
arguments and response fields used here - get_portfolio, list_positions,
list_orders, browse_options_chain, get_current_price - and that place_order,
cancel_order and replace_order exist.

What was NOT verified, and blocks a server-side bot until Abyan confirms it:
* that a standalone program (not claude.ai) may call the endpoint, and how it
  obtains ABYAN_MCP_URL and a token (ABYAN_MCP_TOKEN). The Claude connector's
  OAuth session cannot be exported to this bot.
* Abyan documents that orders from this interface are drafts the user must
  confirm in the Abyan app. Unattended live execution through it is therefore
  not available as documented.

The client is read-only unless constructed with read_only=False, and in
read-only mode it refuses every write tool before anything leaves the process.
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import threading

import requests

from ..models import OptionContract
from ..storage import now_utc

WRITE_TOOLS = frozenset({"place_order", "cancel_order", "replace_order"})
PROTOCOL = "2025-06-18"


class BrokerError(Exception):
    pass


class BrokerUnavailable(BrokerError):
    """Network or auth failure: the state of anything just sent is unknown."""


class LiveTradingBlocked(BrokerError):
    """A write was attempted while the system is not in armed live mode."""


class McpHttpClient:
    def __init__(self, url: str, token: str | None, timeout: float = 20, session=None):
        self.url, self.token, self.timeout = url, token, timeout
        self.http = session or requests.Session()
        self._session_id: str | None = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": PROTOCOL}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _post(self, body: dict) -> requests.Response:
        try:
            return self.http.post(self.url, data=json.dumps(body), headers=self._headers(),
                                  timeout=self.timeout)
        except requests.RequestException as e:
            raise BrokerUnavailable(f"تعذر الاتصال بأبيان: {type(e).__name__}") from None

    @staticmethod
    def _decode(resp: requests.Response, want_id: int) -> dict:
        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" in ctype:
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    try:
                        msg = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if msg.get("id") == want_id:
                        return msg
            raise BrokerError("رد أبيان لا يحتوي على نتيجة الطلب")
        return resp.json()

    def _initialize(self) -> None:
        rid = next(self._ids)
        resp = self._post({"jsonrpc": "2.0", "id": rid, "method": "initialize",
                           "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                                      "clientInfo": {"name": "alert-trader", "version": "1.0"}}})
        if resp.status_code in (401, 403):
            raise BrokerUnavailable(f"أبيان رفض الصلاحية (HTTP {resp.status_code})")
        if resp.status_code != 200:
            raise BrokerUnavailable(f"أبيان HTTP {resp.status_code} عند التهيئة")
        self._session_id = resp.headers.get("Mcp-Session-Id")
        msg = self._decode(resp, rid)
        if "error" in msg:
            raise BrokerError(f"أبيان: {msg['error'].get('message')}")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, tool: str, args: dict) -> dict:
        with self._lock:
            if self._session_id is None:
                self._initialize()
            for attempt in (1, 2):
                rid = next(self._ids)
                resp = self._post({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                                   "params": {"name": tool, "arguments": args}})
                if resp.status_code == 404 and attempt == 1:   # session expired
                    self._session_id = None
                    self._initialize()
                    continue
                if resp.status_code in (401, 403):
                    raise BrokerUnavailable(f"أبيان رفض الصلاحية (HTTP {resp.status_code})")
                if resp.status_code >= 500:
                    raise BrokerUnavailable(f"أبيان HTTP {resp.status_code}")
                if resp.status_code != 200:
                    raise BrokerError(f"أبيان HTTP {resp.status_code}")
                msg = self._decode(resp, rid)
                break
        if "error" in msg:
            raise BrokerError(f"أبيان: {msg['error'].get('message')}")
        result = msg.get("result") or {}
        if result.get("isError"):
            text = " ".join(c.get("text", "") for c in result.get("content", []))
            raise BrokerError(f"أبيان رفض {tool}: {text[:300]}")
        if isinstance(result.get("structuredContent"), dict):
            return result["structuredContent"]
        for c in result.get("content", []):
            if c.get("type") == "text":
                try:
                    return json.loads(c["text"])
                except ValueError:
                    return {"text": c["text"]}
        return {}


class AbyanBroker:
    """Read access to the account and option chains; writes only when armed."""

    name = "abyan"

    def __init__(self, client, portfolio_id: int | None = None, read_only: bool = True):
        self.client = client
        self.portfolio_id = portfolio_id
        self.read_only = read_only
        self.last_ok: dt.datetime | None = None
        self.last_error: str | None = None

    def _call(self, tool: str, args: dict) -> dict:
        if tool in WRITE_TOOLS and self.read_only:
            raise LiveTradingBlocked(f"محظور: {tool} في وضع القراءة فقط (الوضع الورقي)")
        args = {k: v for k, v in args.items() if v is not None}
        try:
            out = self.client.call(tool, args)
        except BrokerError as e:
            self.last_error = str(e)
            raise
        self.last_ok = now_utc()
        self.last_error = None
        return out

    # --- reads -------------------------------------------------------------
    def account(self) -> dict:
        return self._call("get_portfolio", {"portfolio_id": self.portfolio_id})

    def positions(self) -> list[dict]:
        out, offset = [], 0
        while True:
            page = self._call("list_positions", {"portfolio_id": self.portfolio_id,
                                                 "limit": 100, "offset": offset})
            out += page.get("positions", [])
            if not page.get("has_more"):
                return out
            offset += 100

    def orders(self, symbol: str | None = None, status: str | None = None,
               limit: int = 100) -> list[dict]:
        page = self._call("list_orders", {"portfolio_id": self.portfolio_id, "symbol": symbol,
                                          "status": status, "limit": limit})
        return page.get("orders", [])

    def expirations(self, underlying: str) -> list[dt.date]:
        page = self._call("browse_options_chain", {"symbol": underlying})
        return [dt.date.fromisoformat(d) for d in page.get("expiration_dates", [])]

    def contracts(self, underlying: str, expiry: dt.date, right: str) -> list[dict]:
        out, offset = [], 0
        while True:
            page = self._call("browse_options_chain", {
                "symbol": underlying, "expiration_date": expiry.isoformat(),
                "type": "call" if right == "C" else "put", "limit": 250, "offset": offset})
            out += page.get("contracts", [])
            if not page.get("has_more"):
                return out
            offset += 250

    def fmv(self, contract: OptionContract) -> float | None:
        page = self._call("get_current_price", {
            "symbol": contract.underlying, "asset_class": "us_option",
            "expiration_date": contract.expiry.isoformat(),
            "option_type": contract.right_name, "strike_price": contract.strike})
        value = page.get("fmv")
        return float(value) if value is not None else None

    def held_qty(self, occ: str) -> int:
        return sum(int(float(p.get("qty", 0))) for p in self.positions()
                   if p.get("symbol") in (occ, f"O:{occ}"))

    # --- writes (blocked unless read_only=False) -------------------------------
    def place_limit(self, occ: str, side: str, qty: int, limit: float, fmv: float | None) -> dict:
        return self._call("place_order", {
            "symbol": occ, "asset_class": "us_option", "side": side, "qty": str(qty),
            "type": "limit", "limit_price": f"{limit:.2f}", "time_in_force": "day",
            "symbol_nav": fmv, "portfolio_id": self.portfolio_id})

    def cancel(self, broker_order_id: str) -> dict:
        return self._call("cancel_order", {"order_id": broker_order_id,
                                           "portfolio_id": self.portfolio_id})


def build(env, read_only: bool = True) -> AbyanBroker | None:
    if not env.abyan_mcp_url:
        return None
    return AbyanBroker(McpHttpClient(env.abyan_mcp_url, env.abyan_mcp_token),
                       env.abyan_portfolio_id, read_only=read_only)
