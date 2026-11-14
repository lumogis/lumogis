# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for `routes/capabilities.py`.

Contract under test:
  - GET /capabilities returns the static CapabilityManifest as JSON.
  - All required identity fields are populated.
  - `management_url` is plumbed through from `KG_MANAGEMENT_URL` and
    has a sane default referencing this service's `/mgm` page.
  - All advertised tools are community (AGPL-3.0-only, ADR 229).
  - Tool names are exactly the canonical set (no drift) — every advertised
    tool MUST resolve to a real invoke endpoint (LUM-41). The five aspirational
    graph.* tools with no endpoint are deferred (child ticket under LUM-78).
  - The manifest declares the v1 contract fields (`contract_version`, `auth`)
    and each tool declares its `invoke` route.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Only tools that resolve to a real endpoint may be advertised. Today that is
# the single canonical graph.query tool (→ POST /tools/query_graph).
_EXPECTED_TOOL_NAMES = {
    "graph.query",
}


def _client_with_capabilities() -> TestClient:
    from routes.capabilities import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_capabilities_returns_complete_manifest():
    r = _client_with_capabilities().get("/capabilities")
    assert r.status_code == 200
    body = r.json()

    assert body["id"] == "lumogis-graph"
    assert body["name"] == "Lumogis Graph"
    assert isinstance(body["version"], str) and body["version"]
    assert body["type"] == "service"
    assert body["transport"] == "http"
    assert body["license_mode"] == "community"
    assert body["maturity"] == "experimental"
    assert body["health_endpoint"] == "/health"
    assert body["capabilities_endpoint"] == "/capabilities"
    assert isinstance(body["management_url"], str) and body["management_url"]
    assert body["management_url"].endswith("/mgm")

    tool_names = {t["name"] for t in body["tools"]}
    assert tool_names == _EXPECTED_TOOL_NAMES, (
        f"unexpected tool drift: {tool_names ^ _EXPECTED_TOOL_NAMES}"
    )


def test_capabilities_declares_v1_contract_fields():
    """The manifest declares the invoke-contract v1 fields (LUM-41)."""
    r = _client_with_capabilities().get("/capabilities")
    body = r.json()

    assert body["contract_version"] == "1.0"
    assert body["auth"]["mode"] == "bearer"
    assert body["auth"]["credential_ref"] == "KG_WEBHOOK_SECRET"


def test_capabilities_graph_query_declares_real_invoke_path():
    """graph.query must point at the one real endpoint — no manifest↔reality drift."""
    r = _client_with_capabilities().get("/capabilities")
    body = r.json()

    tool = next(t for t in body["tools"] if t["name"] == "graph.query")
    assert tool["invoke"]["path"] == "/tools/query_graph"
    assert tool["invoke"]["method"] == "POST"
    assert tool["is_write"] is False


def test_capabilities_tools_are_all_community():
    """The knowledge graph is AGPL-3.0-only (ADR 229): every graph.* tool is community."""
    r = _client_with_capabilities().get("/capabilities")
    body = r.json()
    for tool in body["tools"]:
        assert tool["license_mode"] == "community", (
            f"tool {tool['name']!r} is not community — "
            "every graph.* tool must be community under ADR 229"
        )


def test_capabilities_management_url_honours_env(monkeypatch):
    """KG_MANAGEMENT_URL override flows into the manifest at build time."""
    monkeypatch.setenv("KG_MANAGEMENT_URL", "https://kg.example.com/operator")
    # Force a re-build of the cached manifest by importing fresh.
    import importlib

    import routes.capabilities as cap

    importlib.reload(cap)
    try:
        app = FastAPI()
        app.include_router(cap.router)
        body = TestClient(app).get("/capabilities").json()
        assert body["management_url"] == "https://kg.example.com/operator"
    finally:
        monkeypatch.delenv("KG_MANAGEMENT_URL", raising=False)
        importlib.reload(cap)
