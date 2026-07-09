# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis

"""Stdio MCP server façade forwarding ``list_tools`` / ``call_tool`` to Core HTTP."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from lumogis_mcp.config import BridgeConfig


def _upstream_headers(config: BridgeConfig) -> dict[str, str]:
    if config.token is None:
        return {}
    return {"Authorization": f"Bearer {config.token}"}


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


async def run_stdio_proxy(config: BridgeConfig) -> None:
    """Run the stdio MCP bridge until stdin closes or the process is interrupted."""
    server = Server("lumogis-mcp")
    state: dict[str, Any] = {"session": None}

    @server.list_tools()
    async def handle_list_tools():
        session: ClientSession | None = state["session"]
        if session is None:
            raise RuntimeError("Lumogis Core is not connected — retry shortly")
        return await session.list_tools()

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict | None = None):
        session: ClientSession | None = state["session"]
        if session is None:
            raise RuntimeError("Lumogis Core is not connected — retry shortly")
        return await session.call_tool(name, arguments or {})

    init_options = server.create_initialization_options()

    async def maintain_upstream(shutdown: asyncio.Event) -> None:
        backoff = 0.1
        while not shutdown.is_set():
            headers = _upstream_headers(config)
            try:
                async with streamablehttp_client(config.url, headers=headers) as (
                    read,
                    write,
                    _get_session_id,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        state["session"] = session
                        backoff = 0.1
                        await shutdown.wait()
            except asyncio.CancelledError:
                raise
            except httpx.HTTPStatusError as exc:
                state["session"] = None
                status = exc.response.status_code
                if status == 401:
                    _stderr(
                        "MCP token rejected — mint a new lmcp_… token "
                        "(see docs/private/ops/connect-and-verify.md Step 9d)"
                    )
                elif status == 403:
                    _stderr(
                        "Unexpected Origin rejection — bridge must not send Origin; "
                        "check proxy/httpx defaults"
                    )
                else:
                    _stderr(f"Lumogis Core HTTP error {status}")
            except (httpx.ConnectError, httpx.ConnectTimeout, ConnectionRefusedError, OSError):
                state["session"] = None
                _stderr(
                    f"Lumogis Core is not reachable at {config.url}. "
                    "Start with: docker compose up -d"
                )
            except Exception as exc:
                state["session"] = None
                _stderr(f"Lumogis Core transport error: {exc.__class__.__name__}")
            if shutdown.is_set():
                break
            await asyncio.sleep(min(backoff, 2.0))
            backoff = min(backoff * 2, 2.0)

    shutdown = asyncio.Event()
    upstream_task = asyncio.create_task(maintain_upstream(shutdown))

    try:
        for _ in range(200):
            if state["session"] is not None:
                break
            await asyncio.sleep(0.05)
        else:
            raise ConnectionRefusedError(
                f"Lumogis Core is not reachable at {config.url}. "
                "Start with: docker compose up -d"
            )

        async with stdio_server() as (stdio_read, stdio_write):
            await server.run(stdio_read, stdio_write, init_options)
    finally:
        shutdown.set()
        upstream_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await upstream_task
