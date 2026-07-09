# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Shared JSON-RPC helpers for MCP integration tests (LUM-299)."""

from __future__ import annotations

import json
from typing import Any


def initialize_payload() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "lum299-integration", "version": "0.1"},
        },
    }


def mcp_post(client, payload: dict, headers: dict) -> Any:
    base = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": "localhost:8000",
    }
    base.update(headers)
    return client.post("/mcp/", content=json.dumps(payload), headers=base)


def list_tool_names(client, headers: dict) -> set[str]:
    resp = mcp_post(
        client,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return {t["name"] for t in body["result"]["tools"]}


def call_tool(client, headers: dict, name: str, arguments: dict) -> dict:
    resp = mcp_post(
        client,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()
