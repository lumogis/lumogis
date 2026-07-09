# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/health`` — non-admin cached per-service health (LUM-512)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from models.api_v1 import StackStatusServiceItem


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_health_cache():
    from services import user_health as user_health_svc

    user_health_svc.reset_cache()
    yield
    user_health_svc.reset_cache()


def _auth_header(
    monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "user"
) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-health-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


def _states(overall: str, services: dict[str, str]):
    """Build the (overall, [StackStatusServiceItem]) tuple build_service_states returns."""
    return overall, [
        StackStatusServiceItem(id=sid, display_name=sid.title(), state=state)
        for sid, state in services.items()
    ]


def test_health_401_when_auth_enabled_without_token(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-health-401")
    r = client.get("/api/v1/health")
    assert r.status_code == 401


def test_health_200_for_non_admin_user(client, monkeypatch) -> None:
    from services import stack_status as stack_status_svc

    monkeypatch.setattr(
        stack_status_svc,
        "build_service_states",
        lambda: _states("degraded", {"ollama": "down", "qdrant": "healthy"}),
    )
    hdr = _auth_header(monkeypatch, "bob", "user")
    r = client.get("/api/v1/health", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["overall"] == "degraded"
    assert body["services"] == {"ollama": "down", "qdrant": "healthy"}


def test_health_whitelists_services_for_non_admin(client, monkeypatch) -> None:
    """Only client-consumed ids are exposed; the rest of the topology is withheld."""
    from services import stack_status as stack_status_svc

    monkeypatch.setattr(
        stack_status_svc,
        "build_service_states",
        lambda: _states(
            "degraded",
            {
                "ollama": "healthy",
                "qdrant": "down",
                "graph": "healthy",
                # Internal services a non-admin must NOT learn about:
                "mongodb": "down",
                "librechat": "healthy",
                "stack_control": "unknown",
                "caddy": "healthy",
            },
        ),
    )
    hdr = _auth_header(monkeypatch, "bob", "user")
    r = client.get("/api/v1/health", headers=hdr)
    assert r.status_code == 200
    assert set(r.json()["services"].keys()) == {"ollama", "qdrant", "graph"}
    for hidden in ("mongodb", "librechat", "stack_control", "caddy"):
        assert hidden not in r.text


def test_health_omits_sensitive_admin_fields(client, monkeypatch) -> None:
    from services import stack_status as stack_status_svc

    monkeypatch.setattr(
        stack_status_svc,
        "build_service_states",
        lambda: _states("ok", {"qdrant": "healthy"}),
    )
    hdr = _auth_header(monkeypatch, "bob", "user")
    r = client.get("/api/v1/health", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    # Non-sensitive projection: no storage, ollama model list, runtime detail, or warnings.
    assert set(body.keys()) == {"overall", "services"}
    assert "runtime_detail" not in r.text
    assert "storage" not in r.text


def test_health_caches_within_ttl(client, monkeypatch) -> None:
    """Concurrent/repeat polls within the TTL must share a single probe."""
    from services import stack_status as stack_status_svc

    calls = {"n": 0}

    def _counting_build():
        calls["n"] += 1
        return _states("ok", {"qdrant": "healthy"})

    monkeypatch.setattr(stack_status_svc, "build_service_states", _counting_build)
    hdr = _auth_header(monkeypatch, "bob", "user")

    r1 = client.get("/api/v1/health", headers=hdr)
    r2 = client.get("/api/v1/health", headers=hdr)
    assert r1.status_code == r2.status_code == 200
    # Two requests, one underlying probe thanks to the TTL cache.
    assert calls["n"] == 1
