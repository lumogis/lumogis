# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/admin/knowledge-graph`` — admin knowledge-graph status (LUM-937)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests.test_graph_status import real_graph_service

import config
from services import graph_status

GIB = 1024**3


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _auth_header(
    monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "admin"
) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-knowledge-graph-access-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


class _FakeRegistry:
    def __init__(self, svc=None) -> None:
        self._svc = svc

    def get_service(self, service_id: str):
        return self._svc if service_id == "lumogis-graph" else None


def _arrange(
    monkeypatch: pytest.MonkeyPatch,
    *,
    env_mode: str | None,
    effective: str | None,
    registry=None,
    total: int | None = 16 * GIB,
) -> None:
    """Order matters: the client fixture's lifespan has already published a mode."""
    if env_mode is None:
        monkeypatch.delenv("GRAPH_MODE", raising=False)
    else:
        monkeypatch.setenv("GRAPH_MODE", env_mode)
    config.clear_graph_mode_env_cache()
    config.set_effective_graph_mode_for_process(effective)
    monkeypatch.setitem(config._instances, "capability_registry", registry or _FakeRegistry())
    monkeypatch.setattr(graph_status, "total_memory_bytes", lambda: total)


def test_knowledge_graph_401_when_auth_enabled_without_token(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-knowledge-graph-401-secret")
    assert client.get("/api/v1/admin/knowledge-graph").status_code == 401


def test_knowledge_graph_403_non_admin(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "bob", "user")
    assert client.get("/api/v1/admin/knowledge-graph", headers=hdr).status_code == 403


def test_knowledge_graph_200_admin_default_is_off(client, monkeypatch) -> None:
    _arrange(monkeypatch, env_mode=None, effective=None)
    hdr = _auth_header(monkeypatch, "admin-1", "admin")
    r = client.get("/api/v1/admin/knowledge-graph", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "off"
    assert body["mode"] == body["configured_mode"] == "disabled"
    assert body["memory"]["required_bytes"] == 10 * GIB
    assert body["memory"]["enough"] is True


def test_knowledge_graph_200_when_auth_disabled(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _arrange(monkeypatch, env_mode=None, effective=None)
    r = client.get("/api/v1/admin/knowledge-graph")
    assert r.status_code == 200
    assert r.json()["state"] == "off"


def test_knowledge_graph_reports_configured_and_effective_separately(client, monkeypatch) -> None:
    _arrange(monkeypatch, env_mode="service", effective="disabled")
    hdr = _auth_header(monkeypatch, "admin-2", "admin")
    body = client.get("/api/v1/admin/knowledge-graph", headers=hdr).json()
    assert body["configured_mode"] == "service"
    assert body["mode"] == "disabled"
    assert body["state"] == "service_wiring_failed"


def test_knowledge_graph_inprocess_is_unsupported(client, monkeypatch) -> None:
    _arrange(monkeypatch, env_mode="inprocess", effective="inprocess")
    hdr = _auth_header(monkeypatch, "admin-3", "admin")
    body = client.get("/api/v1/admin/knowledge-graph", headers=hdr).json()
    assert body["state"] == "unsupported_mode"


def test_knowledge_graph_leaks_no_urls_or_secrets(client, monkeypatch) -> None:
    svc = real_graph_service(healthy=True)
    _arrange(monkeypatch, env_mode="service", effective="service", registry=_FakeRegistry(svc))
    monkeypatch.setenv("KG_SERVICE_URL", "http://sentinel-kg-url.invalid:8001")
    monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", "sentinel-secret-9f3c")
    monkeypatch.setenv("CAPABILITY_SERVICE_URLS", "http://sentinel-cap.invalid:8001")
    hdr = _auth_header(monkeypatch, "admin-4", "admin")
    r = client.get("/api/v1/admin/knowledge-graph", headers=hdr)
    assert r.status_code == 200
    assert r.json()["state"] == "on"
    for needle in ("sentinel", "http"):
        assert needle not in r.text
    forbidden = frozenset(
        {
            "api_key",
            "password",
            "ciphertext",
            "access_token",
            "refresh_token",
            "authorization",
            "base_url",
            "url",
            "secret",
        }
    )

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                assert k.lower() not in forbidden
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(r.json())


def test_knowledge_graph_response_keys_are_exact(client, monkeypatch) -> None:
    svc = real_graph_service(healthy=False, reason="timeout")
    _arrange(monkeypatch, env_mode="service", effective="service", registry=_FakeRegistry(svc))
    hdr = _auth_header(monkeypatch, "admin-5", "admin")
    body = client.get("/api/v1/admin/knowledge-graph", headers=hdr).json()
    # LUM-989 added install_kind, turn_on, turn_off and failed_code.
    assert set(body) == {
        "state",
        "mode",
        "configured_mode",
        "service",
        "memory",
        "install_kind",
        "turn_on",
        "turn_off",
        "failed_code",
    }
    assert set(body["turn_on"]) == {"available", "block", "needs_confirmation"}
    assert set(body["turn_off"]) == {"available", "block"}
    assert set(body["service"]) == {
        "registered",
        "healthy",
        "last_seen_healthy",
        "last_unhealthy_reason",
    }
    assert set(body["memory"]) == {"total_bytes", "required_bytes", "enough"}
