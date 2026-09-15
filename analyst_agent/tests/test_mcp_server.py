"""The MCP surface: the same engine, reachable as tools."""
import asyncio

import pytest

pytest.importorskip("fastmcp", reason="fastmcp not installed")


def _server(monkeypatch, token="test-token"):
    import importlib

    monkeypatch.setenv("MCP_TOKEN", token)
    import mcp_server

    return importlib.reload(mcp_server)


def _tools(server):
    from fastmcp import Client

    async def collect():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    return asyncio.run(collect())


def test_every_tool_is_registered(monkeypatch):
    names = {t.name for t in _tools(_server(monkeypatch))}
    assert names == {"analyze", "snapshot", "expirations", "option_chain",
                     "performance", "scan"}


def test_tools_describe_themselves_in_arabic(monkeypatch):
    """The descriptions are what Claude reads to pick a tool."""
    for tool in _tools(_server(monkeypatch)):
        assert tool.description and len(tool.description) > 20


def test_a_token_turns_on_authentication(monkeypatch):
    server = _server(monkeypatch, token="secret")
    assert server.auth is not None
    assert server.mcp.auth is not None


def test_without_a_token_the_endpoint_is_open(monkeypatch):
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    import importlib

    import mcp_server

    server = importlib.reload(mcp_server)
    assert server.auth is None          # logged as a warning on startup


def test_the_path_is_configurable(monkeypatch):
    monkeypatch.setenv("MCP_PATH", "/mcp/unguessable")
    import importlib

    import mcp_server

    assert importlib.reload(mcp_server).PATH == "/mcp/unguessable"


def test_analyze_returns_the_agent_answer(monkeypatch):
    """The MCP tool is a thin shell over analyst.analyze — no second engine."""
    from analyst_agent import analyst

    server = _server(monkeypatch)
    monkeypatch.setattr(server.analyst, "analyze", lambda request, with_news=True:
                        analyst.Answer(True, "تحليل", headline="NVDA • يومي",
                                       symbol="NVDA", frame_key="1d"))
    from fastmcp import Client

    async def call():
        async with Client(server.mcp) as client:
            return await client.call_tool("analyze", {"symbol": "NVDA", "frame": "1d"})

    result = asyncio.run(call())
    assert result.data["symbol"] == "NVDA"
    assert result.data["text"] == "تحليل"


def test_snapshot_reports_missing_data_instead_of_raising(monkeypatch):
    server = _server(monkeypatch)
    monkeypatch.setattr(server.market, "load", lambda *a, **k: None)
    from fastmcp import Client

    async def call():
        async with Client(server.mcp) as client:
            return await client.call_tool("snapshot", {"symbol": "NOPE"})

    assert "error" in asyncio.run(call()).data
