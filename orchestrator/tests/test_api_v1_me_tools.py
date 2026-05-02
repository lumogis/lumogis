# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/me/tools`` — read-only catalog façade (Phase 4)."""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone

import pytest
from fastapi.testclient import TestClient
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport
from services.capability_registry import RegisteredService
from services.unified_tools import build_tool_catalog


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _auth_header(
    monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "user"
) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-me-tools-access-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


def test_me_tools_401_when_auth_enabled_without_token(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-me-tools-401-secret")
    r = client.get("/api/v1/me/tools")
    assert r.status_code == 401


def test_me_tools_200_authenticated_when_auth_enabled(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "alice-1", "user")
    r = client.get("/api/v1/me/tools", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert "tools" in body and "summary" in body
    assert body["summary"]["total"] == len(body["tools"])


def test_me_tools_200_default_user_when_auth_disabled(client) -> None:
    r = client.get("/api/v1/me/tools")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["tools"], list)
    assert body["summary"]["total"] >= 3


def test_me_tools_response_shape_and_no_forbidden_keys(client) -> None:
    r = client.get("/api/v1/me/tools")
    assert r.status_code == 200
    body = r.json()
    required_tool_keys = {
        "name",
        "label",
        "description",
        "source",
        "transport",
        "origin_tier",
        "available",
        "why_not_available",
        "capability_id",
        "connector",
        "action_type",
        "permission_mode",
        "requires_credentials",
    }
    required_summary_keys = {"total", "available", "unavailable", "by_source"}
    assert set(body["summary"].keys()) == required_summary_keys
    for t in body["tools"]:
        assert set(t.keys()) == required_tool_keys
        assert "parameters" not in t
        assert "tool_schema" not in t
        assert "source_id" not in t
    raw = json.dumps(body)
    assert "Bearer " not in raw
    assert "handler" not in raw.lower()


def test_me_tools_core_and_mcp_sources_present(client) -> None:
    r = client.get("/api/v1/me/tools")
    assert r.status_code == 200
    by_name = {t["name"]: t for t in r.json()["tools"]}
    assert "search_files" in by_name
    assert by_name["search_files"]["source"] == "core"
    assert by_name["search_files"]["transport"] == "llm_loop"
    mcp = [t for t in r.json()["tools"] if t["source"] == "mcp"]
    assert mcp
    assert all(x["transport"] == "mcp_surface" for x in mcp)
    assert all(x["origin_tier"] == "mcp_only" for x in mcp)


def test_me_tools_deterministic_ordering(client) -> None:
    r1 = client.get("/api/v1/me/tools").json()["tools"]
    r2 = client.get("/api/v1/me/tools").json()["tools"]
    assert [t["name"] for t in r1] == [t["name"] for t in r2]


def test_me_tools_capability_row_safe_metadata(client, monkeypatch) -> None:
    def _ct(name: str) -> CapabilityTool:
        return CapabilityTool(
            name=name,
            description="Discovered tool",
            license_mode=CapabilityLicenseMode.COMMUNITY,
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            output_schema={"type": "object"},
        )

    def _manifest(mid: str) -> CapabilityManifest:
        return CapabilityManifest(
            name=mid,
            id=mid,
            version="0.1.0",
            type="service",
            transport=CapabilityTransport.HTTP,
            license_mode=CapabilityLicenseMode.COMMUNITY,
            maturity=CapabilityMaturity.PREVIEW,
            description="Test cap",
            tools=[_ct("cap.discovered.tool")],
            health_endpoint="/health",
            capabilities_endpoint="/capabilities",
            permissions_required=[],
            config_schema={"type": "object"},
            min_core_version="0.1.0",
            maintainer="t",
        )

    class _FakeReg:
        def all_services(self):
            return [
                RegisteredService(
                    manifest=_manifest("com.example.facade"),
                    base_url="http://example:1",
                    registered_at=datetime.now(timezone.utc),
                    healthy=False,
                )
            ]

    fake_reg = _FakeReg()

    monkeypatch.setattr(
        "services.me_tools_catalog.build_tool_catalog_for_user",
        lambda **_kw: build_tool_catalog(capability_registry=fake_reg),
    )
    r = client.get("/api/v1/me/tools")
    assert r.status_code == 200
    cap_rows = [t for t in r.json()["tools"] if t["name"] == "cap.discovered.tool"]
    assert len(cap_rows) == 1
    row = cap_rows[0]
    assert row["source"] == "capability"
    assert row["capability_id"] == "com.example.facade"
    assert row["connector"] == "capability.com.example.facade"
    assert row["action_type"] == "cap.discovered.tool"
    assert row["available"] is False
    assert row["why_not_available"]
    raw = json.dumps(row)
    assert "properties" not in raw  # no JSON Schema leakage


def test_me_tools_does_not_invoke_capability_http(client, monkeypatch) -> None:
    called: list[str] = []

    def _no_http(*_a, **_k):
        called.append("post_capability_tool_invocation")
        raise AssertionError("tool HTTP must not run for /me/tools")

    import services.capability_http as ch

    monkeypatch.setattr(ch, "post_capability_tool_invocation", _no_http)

    import services.tools as tools_mod

    def _no_run(*_a, **_k):
        called.append("run_tool")
        raise AssertionError("run_tool must not run for /me/tools")

    monkeypatch.setattr(tools_mod, "run_tool", _no_run)

    r = client.get("/api/v1/me/tools")
    assert r.status_code == 200
    assert called == []
