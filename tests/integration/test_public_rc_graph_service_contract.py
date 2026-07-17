# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""lumogis-graph health + capability invoke contract v1 (GRAPH_MODE=service)."""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def _graph_base_url() -> str:
    health = os.environ.get("LUMOGIS_GRAPH_HEALTH_URL", "http://127.0.0.1:18001/health").strip()
    if health.endswith("/health"):
        return health[: -len("/health")]
    return os.environ.get("LUMOGIS_GRAPH_BASE_URL", "http://127.0.0.1:18001").strip().rstrip("/")


def _graph_query_envelope(arguments: dict) -> dict:
    return {
        "contract_version": "1.0",
        "tool": "graph.query",
        "arguments": arguments,
        "meta": {"user": "default", "request_id": f"rc-{uuid.uuid4().hex[:8]}"},
    }


@pytest.mark.public_rc
def test_graph_service_health_contract():
    url = os.environ.get("LUMOGIS_GRAPH_HEALTH_URL", "http://127.0.0.1:18001/health").strip()
    with httpx.Client(timeout=30.0) as c:
        r = c.get(url)
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    assert "falkordb" in body
    assert "postgres" in body
    assert isinstance(body.get("version"), str) and body["version"]
    assert isinstance(body.get("pending_webhook_tasks"), int)


@pytest.mark.public_rc
def test_graph_capabilities_v1_manifest():
    base = _graph_base_url()
    with httpx.Client(timeout=30.0) as c:
        r = c.get(f"{base}/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body.get("contract_version") == "1.0"
    assert body.get("id") == "lumogis-graph"
    auth = body.get("auth") or {}
    assert auth.get("mode") == "bearer"
    assert auth.get("credential_ref") == "KG_WEBHOOK_SECRET"
    tools = body.get("tools") or []
    assert len(tools) == 1
    tool = tools[0]
    assert tool.get("name") == "graph.query"
    invoke = tool.get("invoke") or {}
    assert invoke.get("path") == "/tools/query_graph"


def _graph_auth_headers() -> dict[str, str]:
    """Bearer when GRAPH_WEBHOOK_SECRET is set; else rely on KG_ALLOW_INSECURE_WEBHOOKS."""
    secret = os.environ.get("GRAPH_WEBHOOK_SECRET", "").strip()
    if secret:
        return {"Authorization": f"Bearer {secret}"}
    return {}


@pytest.mark.public_rc
def test_graph_query_graph_v1_invoke_envelope():
    """Live KG invoke returns the v1 response envelope (ok+output or ok+error)."""
    base = _graph_base_url()
    with httpx.Client(timeout=30.0) as c:
        r = c.post(
            f"{base}/tools/query_graph",
            json=_graph_query_envelope(
                {"mode": "ego", "entity": "Alice", "user_id": "default"}
            ),
            headers=_graph_auth_headers(),
        )
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("ok"), bool)
    if body["ok"]:
        assert "output" in body
        assert body.get("error") is None
    else:
        err = body.get("error") or {}
        assert isinstance(err.get("code"), str) and err["code"]
        assert isinstance(err.get("message"), str)
        assert isinstance(err.get("retryable"), bool)


@pytest.mark.public_rc
def test_core_tool_catalog_includes_query_graph_proxy(api):
    r = api.get("/api/v1/me/tools")
    assert r.status_code == 200
    tools = r.json().get("tools") or []
    names = {t.get("name") for t in tools}
    assert "query_graph" in names
    qg = next((t for t in tools if t.get("name") == "query_graph"), None)
    assert qg is not None
    assert isinstance(qg.get("description"), str)
