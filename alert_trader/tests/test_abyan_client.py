import json

import pytest

from alert_trader.broker.abyan import (AbyanBroker, BrokerUnavailable, LiveTradingBlocked,
                                       McpHttpClient)


class Resp:
    def __init__(self, status, body=None, headers=None, sse=False):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        if sse:
            self.headers["Content-Type"] = "text/event-stream"
            self.text = f"event: message\ndata: {json.dumps(body)}\n\n"
        else:
            self.headers.setdefault("Content-Type", "application/json")
            self.text = json.dumps(body)

    def json(self):
        return self._body


class Session:
    """Speaks just enough MCP Streamable HTTP to exercise the client."""

    def __init__(self):
        self.sent = []

    def post(self, url, data, headers, timeout):
        body = json.loads(data)
        self.sent.append((body, headers))
        if body["method"] == "initialize":
            return Resp(200, {"jsonrpc": "2.0", "id": body["id"], "result": {}},
                        {"Mcp-Session-Id": "s1"})
        if body["method"] == "notifications/initialized":
            return Resp(202, {})
        name = body["params"]["name"]
        if name == "get_portfolio":
            payload = {"portfolio_id": 1, "buying_power": 125.97}
            return Resp(200, {"jsonrpc": "2.0", "id": body["id"], "result": {
                "content": [{"type": "text", "text": json.dumps(payload)}]}}, sse=True)
        return Resp(200, {"jsonrpc": "2.0", "id": body["id"],
                          "result": {"isError": True, "content": [{"type": "text", "text": "no"}]}})


def test_reads_over_sse_with_session_and_token():
    s = Session()
    broker = AbyanBroker(McpHttpClient("https://x/mcp", "tok", session=s))
    assert broker.account()["buying_power"] == 125.97
    call_headers = s.sent[-1][1]
    assert call_headers["Authorization"] == "Bearer tok"
    assert call_headers["Mcp-Session-Id"] == "s1"
    assert broker.last_ok is not None


def test_write_tools_blocked_before_any_request():
    s = Session()
    broker = AbyanBroker(McpHttpClient("https://x/mcp", "tok", session=s), read_only=True)
    with pytest.raises(LiveTradingBlocked):
        broker.place_limit("AAPL261120C00230000", "buy", 1, 2.5, None)
    with pytest.raises(LiveTradingBlocked):
        broker.cancel("abc")
    assert s.sent == []


def test_auth_failure_is_unavailable():
    class Denied(Session):
        def post(self, url, data, headers, timeout):
            return Resp(401, {})
    broker = AbyanBroker(McpHttpClient("https://x/mcp", None, session=Denied()))
    with pytest.raises(BrokerUnavailable):
        broker.account()
    assert "401" in broker.last_error
