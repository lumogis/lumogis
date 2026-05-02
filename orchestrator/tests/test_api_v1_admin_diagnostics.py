# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/admin/diagnostics`` — read-only admin diagnostics façade."""

from __future__ import annotations

import json
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from models.capability import CapabilityLicenseMode
from models.capability import CapabilityManifest
from models.capability import CapabilityMaturity
from models.capability import CapabilityTool
from models.capability import CapabilityTransport
from services.capability_registry import RegisteredService


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _auth_header(
    monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "admin"
) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-admin-diagnostics-access-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


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
        tools=[_ct(f"{mid}.tool")],
        health_endpoint="/health",
        capabilities_endpoint="/capabilities",
        permissions_required=[],
        config_schema={"type": "object"},
        min_core_version="0.1.0",
        maintainer="t",
    )


def test_admin_diagnostics_401_when_auth_enabled_without_token(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-admin-diagnostics-401-secret")
    r = client.get("/api/v1/admin/diagnostics")
    assert r.status_code == 401


def test_admin_diagnostics_403_non_admin(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "bob", "user")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 403


def test_admin_diagnostics_200_admin(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin-1", "admin")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert "generated_at" in body
    assert "core" in body
    assert body["core"]["auth_enabled"] is True
    assert "tool_catalog_enabled" in body["core"]
    assert body["core"]["core_version"]
    assert "mcp_enabled" in body["core"]
    assert "mcp_auth_required" in body["core"]
    names = [s["name"] for s in body["stores"]]
    assert names == ["postgres", "qdrant", "embedder", "graph"]
    cap = body["capabilities"]
    assert "total" in cap and "healthy" in cap and "unhealthy" in cap
    assert isinstance(cap["services"], list)
    tools = body["tools"]
    assert tools["total"] == tools["available"] + tools["unavailable"]
    assert isinstance(tools["by_source"], dict)
    assert isinstance(body["warnings"], list)
    st = body["speech_to_text"]
    assert "backend" in st and st["backend"] in ("none", "fake_stt", "whisper_sidecar")
    assert "transcribe_available" in st
    assert isinstance(st["max_audio_bytes"], int)
    assert isinstance(st["max_duration_sec"], int)
    assert st["endpoint"] == "/api/v1/voice/transcribe"


def test_admin_diagnostics_200_when_auth_disabled_default_user(client, monkeypatch) -> None:
    # VERIFY-PLAN: docker compose run loads repo .env — local smoke may set AUTH_ENABLED=true;
    # this test asserts the dev-mode façade; do not inherit host env.
    monkeypatch.setenv("AUTH_ENABLED", "false")
    r = client.get("/api/v1/admin/diagnostics")
    assert r.status_code == 200
    assert r.json()["core"]["auth_enabled"] is False


def test_admin_diagnostics_safe_json_no_secret_like_keys(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "admin-2", "admin")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    forbidden = frozenset(
        {
            "api_key",
            "password",
            "ciphertext",
            "access_token",
            "refresh_token",
            "authorization",
            "bearer",
            "payload",
            "private_key",
        }
    )

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                kl = str(k).lower()
                assert kl not in forbidden
                assert not kl.endswith("_secret")
                walk(v)
        elif isinstance(obj, list):
            for i in obj:
                walk(i)

    walk(body)
    raw = json.dumps(body)
    assert "BEGIN RSA" not in raw
    assert "sk-" not in raw  # common key prefix pattern


def test_admin_diagnostics_capability_summary_counts(client, monkeypatch) -> None:
    fixed = datetime(2026, 4, 26, 12, 0, 0, tzinfo=timezone.utc)

    class _Reg:
        def all_services(self):
            return [
                RegisteredService(
                    manifest=_manifest("svc-healthy"),
                    base_url="http://h:1",
                    registered_at=fixed,
                    healthy=True,
                    last_seen_healthy=fixed,
                ),
                RegisteredService(
                    manifest=_manifest("svc-sick"),
                    base_url="http://s:1",
                    registered_at=fixed,
                    healthy=False,
                ),
            ]

    monkeypatch.setattr(
        "services.admin_diagnostics.config.get_capability_registry",
        lambda: _Reg(),
    )

    hdr = _auth_header(monkeypatch, "admin-cap", "admin")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    cap = r.json()["capabilities"]
    assert cap["total"] == 2
    assert cap["healthy"] == 1
    assert cap["unhealthy"] == 1
    ids = [s["id"] for s in cap["services"]]
    assert ids == ["svc-healthy", "svc-sick"]
    assert cap["services"][0]["status"] == "healthy"
    assert cap["services"][0]["healthy"] is True
    assert cap["services"][1]["status"] == "unhealthy"
    assert cap["services"][1]["healthy"] is False


def test_admin_diagnostics_tools_summary_from_catalog_stub(client, monkeypatch) -> None:
    summary = SimpleNamespace(
        total=5,
        available=4,
        unavailable=1,
        by_source={"capability": 1, "core": 3, "mcp": 1},
    )

    def _fake_build(uid: str):
        assert uid == "admin-tools"
        return SimpleNamespace(summary=summary)

    monkeypatch.setattr(
        "services.admin_diagnostics.me_tools_catalog_svc.build_me_tools_response",
        _fake_build,
    )
    hdr = _auth_header(monkeypatch, "admin-tools", "admin")
    r = client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    t = r.json()["tools"]
    assert t["total"] == 5
    assert t["available"] == 4
    assert t["unavailable"] == 1
    assert t["by_source"] == {"capability": 1, "core": 3, "mcp": 1}
