# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for `routes/capabilities.py`.

Contract under test:
  - GET /capabilities returns the static CapabilityManifest as JSON.
  - All required identity fields are populated.
  - `management_url` is plumbed through from `KG_MANAGEMENT_URL` and
    has a sane default referencing this service's `/mgm` page.
  - All six advertised tools are commercial.
  - Tool names are exactly the six the plan promises (no drift).
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient


_EXPECTED_TOOL_NAMES = {
    "graph.query_ego",
    "graph.query_path",
    "graph.query_mentions",
    "graph.get_context",
    "graph.backfill",
    "graph.health",
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
    assert body["name"] == "Lumogis Graph Pro"
    assert isinstance(body["version"], str) and body["version"]
    assert body["type"] == "service"
    assert body["transport"] == "http"
    assert body["license_mode"] == "commercial"
    assert body["maturity"] == "experimental"
    assert body["health_endpoint"] == "/health"
    assert body["capabilities_endpoint"] == "/capabilities"
    assert isinstance(body["management_url"], str) and body["management_url"]
    assert body["management_url"].endswith("/mgm")

    tool_names = {t["name"] for t in body["tools"]}
    assert tool_names == _EXPECTED_TOOL_NAMES, (
        f"unexpected tool drift: {tool_names ^ _EXPECTED_TOOL_NAMES}"
    )


def test_capabilities_tools_are_all_commercial():
    r = _client_with_capabilities().get("/capabilities")
    body = r.json()
    for tool in body["tools"]:
        assert tool["license_mode"] == "commercial", (
            f"tool {tool['name']!r} is not commercial — "
            "every graph.* tool must be commercial under Lumogis Graph Pro"
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
