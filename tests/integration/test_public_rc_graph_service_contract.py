# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""lumogis-graph health + Core ``query_graph`` catalog visibility (GRAPH_MODE=service)."""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.integration


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
def test_core_tool_catalog_includes_query_graph_proxy(api):
    r = api.get("/api/v1/me/tools")
    assert r.status_code == 200
    tools = r.json().get("tools") or []
    names = {t.get("name") for t in tools}
    assert "query_graph" in names
    qg = next((t for t in tools if t.get("name") == "query_graph"), None)
    assert qg is not None
    assert isinstance(qg.get("description"), str)
