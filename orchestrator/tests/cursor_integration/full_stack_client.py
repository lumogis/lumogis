# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live-stack httpx MCP client for cursor integration full gate (LUM-540)."""

from __future__ import annotations

import json

import httpx

from tests.cursor_integration.mcp_jsonrpc import initialize_payload


def _mcp_headers(token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": "localhost:8000",
        "Authorization": f"Bearer {token}",
    }


def build_mcp_http_client(base_url: str, token: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url.rstrip("/"),
        timeout=30.0,
    )


def mcp_initialize(client: httpx.Client, token: str) -> None:
    resp = client.post(
        "/mcp/",
        content=json.dumps(initialize_payload()),
        headers=_mcp_headers(token),
    )
    if resp.status_code != 200:
        raise AssertionError(f"MCP initialize failed status={resp.status_code} body={resp.text}")


def mcp_call_tool(
    client: httpx.Client,
    token: str,
    name: str,
    arguments: dict,
) -> dict:
    resp = client.post(
        "/mcp/",
        content=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        ),
        headers=_mcp_headers(token),
    )
    if resp.status_code != 200:
        raise AssertionError(f"MCP tools/call failed status={resp.status_code} body={resp.text}")
    return resp.json()
