# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Opt-in full-stack cursor integration (LUM-299 / LUM-540) — real Postgres+Qdrant."""

from __future__ import annotations

import json
import os
import socket

import pytest

from tests.cursor_integration.full_stack_client import build_mcp_http_client
from tests.cursor_integration.full_stack_client import mcp_call_tool
from tests.cursor_integration.full_stack_client import mcp_initialize
from tests.cursor_integration.fixture_loader import load_coding_bank
from tests.cursor_integration.p95 import measure_recall_p95_ms

pytestmark = pytest.mark.integration

FIXTURE = load_coding_bank()
DEFAULT_USER = os.environ.get("CURSOR_INTEGRATION_FULL_USER_ID", "cursor-integration-full")
RECALL_STRATEGIES = ["semantic", "bm25", "temporal"]


def _stack_reachable() -> bool:
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = int(os.environ.get("POSTGRES_PORT", "5433"))
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def _pg_conn_kwargs() -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        "port": int(os.environ.get("POSTGRES_PORT", "5433")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": os.environ.get("POSTGRES_DB", "lumogis"),
        "connect_timeout": 3,
    }


def _coding_memory_count(user_id: str) -> int | None:
    psycopg2 = pytest.importorskip("psycopg2")
    try:
        conn = psycopg2.connect(**_pg_conn_kwargs())
    except Exception:  # noqa: BLE001
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id = %s AND bank = 'coding'",
                (user_id,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
    finally:
        conn.close()


def _require_full_gate_env() -> tuple[str, str]:
    if not os.environ.get("LUMOGIS_CURSOR_INTEGRATION_FULL"):
        pytest.skip("set LUMOGIS_CURSOR_INTEGRATION_FULL=1 for full Compose gate")
    if not _stack_reachable():
        pytest.skip("lumogis-test compose stack not reachable")
    token = os.environ.get("LUMOGIS_CURSOR_INTEGRATION_MCP_TOKEN")
    if not token:
        pytest.skip("run make seed-cursor-integration-fixture first (or source ai-workspace/mcp/cursor-integration-full.env)")
    count = _coding_memory_count(DEFAULT_USER)
    if count is None:
        pytest.skip("Postgres not reachable for memory count probe")
    if count < 50:
        pytest.skip("fixture not seeded — coding bank count < 50")
    base_url = os.environ.get("LUMOGIS_API_URL", "http://127.0.0.1:8000")
    return token, base_url


@pytest.fixture(scope="module")
def full_mcp_client():
    token, base_url = _require_full_gate_env()
    client = build_mcp_http_client(base_url, token)
    mcp_initialize(client, token)
    yield client, token
    client.close()


def _result_text(resp: dict) -> str:
    return json.dumps(resp)


def _recall(client, token: str, *, query: str, bank: str = "coding") -> dict:
    return mcp_call_tool(
        client,
        token,
        "recall",
        {
            "query": query,
            "bank": bank,
            "retrieval_strategies": RECALL_STRATEGIES,
        },
    )


@pytest.mark.skipif(
    not os.environ.get("LUMOGIS_CURSOR_INTEGRATION_FULL"),
    reason="set LUMOGIS_CURSOR_INTEGRATION_FULL=1 for full Compose gate",
)
@pytest.mark.skipif(
    not _stack_reachable(),
    reason="lumogis-test compose stack not reachable",
)
def test_full_stack_recall_smoke_mappings(full_mcp_client):
    client, token = full_mcp_client
    cases = [
        ("why FalkorDB", "cd-001"),
        ("TEMPR fusion RRF", "cd-002"),
        ("readOnlyHint true for Cursor", "cc-001"),
    ]
    for query, memory_id in cases:
        resp = _recall(client, token, query=query)
        assert memory_id in _result_text(resp), f"query={query!r} missing {memory_id}"


@pytest.mark.skipif(
    not os.environ.get("LUMOGIS_CURSOR_INTEGRATION_FULL"),
    reason="set LUMOGIS_CURSOR_INTEGRATION_FULL=1 for full Compose gate",
)
@pytest.mark.skipif(
    not _stack_reachable(),
    reason="lumogis-test compose stack not reachable",
)
def test_recall_p95_under_200ms_full_stack(full_mcp_client):
    client, token = full_mcp_client

    def _call():
        _recall(client, token, query="FalkorDB")

    p95 = measure_recall_p95_ms(_call, iterations=200, warmup=20)
    assert p95 < 200.0, f"p95 {p95:.1f}ms exceeds 200ms ceiling"
