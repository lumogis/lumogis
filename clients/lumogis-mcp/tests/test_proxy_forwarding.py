# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis

"""Unit tests for proxy forwarding (mocked upstream)."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import pytest
from mcp.types import CallToolResult
from mcp.types import ListToolsResult
from mcp.types import TextContent
from mcp.types import Tool

from lumogis_mcp.config import BridgeConfig
from lumogis_mcp.proxy import _upstream_headers
from lumogis_mcp.proxy import run_stdio_proxy


@pytest.fixture
def bridge_config():
    return BridgeConfig(url="http://127.0.0.1:8000/mcp/", token="lmcp_secret")


@pytest.mark.asyncio
async def test_proxy_list_tools_delegates(bridge_config):
    upstream_result = ListToolsResult(tools=[Tool(name="recall", description="d", inputSchema={})])
    mock_session = AsyncMock()
    mock_session.list_tools.return_value = upstream_result
    mock_session.initialize = AsyncMock()

    server_handlers: dict = {}

    class FakeServer:
        def __init__(self, name):
            self.name = name

        def list_tools(self):
            def deco(fn):
                server_handlers["list_tools"] = fn
                return fn

            return deco

        def call_tool(self, **kwargs):
            def deco(fn):
                server_handlers["call_tool"] = fn
                return fn

            return deco

        def create_initialization_options(self):
            return MagicMock()

        async def run(self, *args, **kwargs):
            return None

    shutdown = __import__("asyncio").Event()
    shutdown.set()

    with (
        patch("lumogis_mcp.proxy.Server", FakeServer),
        patch("lumogis_mcp.proxy.stdio_server") as mock_stdio,
        patch("lumogis_mcp.proxy.streamablehttp_client") as mock_http,
        patch("lumogis_mcp.proxy.ClientSession") as mock_cs,
    ):
        mock_stdio.return_value.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_stdio.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.return_value.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), lambda: None))
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

        task = __import__("asyncio").create_task(run_stdio_proxy(bridge_config))
        for _ in range(50):
            if "list_tools" in server_handlers:
                break
            await __import__("asyncio").sleep(0.02)
        assert "list_tools" in server_handlers
        result = await server_handlers["list_tools"]()
        assert result == upstream_result
        mock_session.list_tools.assert_awaited_once()
        task.cancel()
        with pytest.raises(__import__("asyncio").CancelledError):
            await task


@pytest.mark.asyncio
async def test_proxy_call_tool_delegates(bridge_config):
    structured = {"memories": [{"id": "m1"}]}
    upstream_result = CallToolResult(
        content=[TextContent(type="text", text="ok")],
        structuredContent=structured,
        isError=False,
    )
    mock_session = AsyncMock()
    mock_session.list_tools.return_value = ListToolsResult(tools=[])
    mock_session.call_tool.return_value = upstream_result
    mock_session.initialize = AsyncMock()

    server_handlers: dict = {}

    class FakeServer:
        def __init__(self, name):
            self.name = name

        def list_tools(self):
            def deco(fn):
                server_handlers["list_tools"] = fn
                return fn

            return deco

        def call_tool(self, **kwargs):
            def deco(fn):
                server_handlers["call_tool"] = fn
                return fn

            return deco

        def create_initialization_options(self):
            return MagicMock()

        async def run(self, *args, **kwargs):
            return None

    with (
        patch("lumogis_mcp.proxy.Server", FakeServer),
        patch("lumogis_mcp.proxy.stdio_server") as mock_stdio,
        patch("lumogis_mcp.proxy.streamablehttp_client") as mock_http,
        patch("lumogis_mcp.proxy.ClientSession") as mock_cs,
    ):
        mock_stdio.return_value.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_stdio.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_http.return_value.__aenter__ = AsyncMock(return_value=(MagicMock(), MagicMock(), lambda: None))
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_cs.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cs.return_value.__aexit__ = AsyncMock(return_value=False)

        task = __import__("asyncio").create_task(run_stdio_proxy(bridge_config))
        for _ in range(50):
            if "call_tool" in server_handlers:
                break
            await __import__("asyncio").sleep(0.02)
        result = await server_handlers["call_tool"]("recall", {"query": "x"})
        assert result.structuredContent == structured
        mock_session.call_tool.assert_awaited_once_with("recall", {"query": "x"})
        task.cancel()
        with pytest.raises(__import__("asyncio").CancelledError):
            await task


def test_upstream_headers_omits_token_when_none():
    assert _upstream_headers(BridgeConfig(url="http://127.0.0.1:8000/mcp/", token=None)) == {}


def test_upstream_headers_includes_bearer():
    headers = _upstream_headers(BridgeConfig(url="http://127.0.0.1:8000/mcp/", token="lmcp_x"))
    assert headers == {"Authorization": "Bearer lmcp_x"}
    assert "Origin" not in headers


def test_main_connection_refused(monkeypatch):
    from lumogis_mcp import __main__ as entry

    monkeypatch.setenv("LUMOGIS_MCP_URL", "http://127.0.0.1:1/mcp/")
    monkeypatch.setenv("LUMOGIS_MCP_TOKEN", "t")

    with patch("lumogis_mcp.__main__.asyncio.run", side_effect=ConnectionRefusedError("nope")):
        with pytest.raises(SystemExit) as exc:
            entry.main()
        assert exc.value.code == 1
