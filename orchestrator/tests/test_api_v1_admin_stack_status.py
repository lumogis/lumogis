# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``GET /api/v1/admin/diagnostics/stack-status`` — stack health dashboard API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from models.api_v1 import StackStatusResponse


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


def _auth_header(
    monkeypatch: pytest.MonkeyPatch, user_id: str, role: str = "admin"
) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-stack-status-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    tok = mint_access_token(user_id, role)
    return {"Authorization": f"Bearer {tok}"}


def test_stack_status_401_when_auth_enabled_without_token(client, monkeypatch) -> None:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-stack-status-401")
    r = client.get("/api/v1/admin/diagnostics/stack-status")
    assert r.status_code == 401


def test_stack_status_403_non_admin(client, monkeypatch) -> None:
    hdr = _auth_header(monkeypatch, "bob", "user")
    r = client.get("/api/v1/admin/diagnostics/stack-status", headers=hdr)
    assert r.status_code == 403


def test_stack_status_200_admin_no_secret_keys(client, monkeypatch) -> None:
    from services import stack_status as stack_status_svc

    sample = StackStatusResponse(
        meta={
            "generated_at": "2026-06-01T12:00:00Z",
            "cache_age_sec": 0,
            "stack_control_reachable": False,
            "overall_status": "degraded",
        },
        services=[
            {
                "id": "postgres",
                "display_name": "Postgres",
                "state": "healthy",
                "runtime_kind": "docker_compose",
                "runtime_detail": {"compose_state": "running"},
            }
        ],
        storage=[],
        ollama=[],
        warnings=[],
    )

    monkeypatch.setattr(
        stack_status_svc,
        "build_stack_status_response",
        lambda: sample,
    )
    hdr = _auth_header(monkeypatch, "admin-1", "admin")
    r = client.get("/api/v1/admin/diagnostics/stack-status", headers=hdr)
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["overall_status"] == "degraded"
    assert body["services"][0]["id"] == "postgres"
    dumped = r.text.lower()
    assert "restart_secret" not in dumped
    assert "lin_api_" not in dumped


def test_stack_status_postgres_down_returns_200(client, monkeypatch) -> None:
    from models.api_v1 import StackStatusMeta
    from models.api_v1 import StackStatusServiceItem

    from services import stack_status as stack_status_svc

    monkeypatch.setattr(
        stack_status_svc,
        "build_stack_status_response",
        lambda: StackStatusResponse(
            meta=StackStatusMeta(
                generated_at="2026-06-01T12:00:00+00:00",
                cache_age_sec=None,
                stack_control_reachable=True,
                overall_status="down",
            ),
            services=[
                StackStatusServiceItem(
                    id="postgres",
                    display_name="Postgres",
                    state="down",
                ),
            ],
            storage=[],
            ollama=[],
        ),
    )
    hdr = _auth_header(monkeypatch, "admin-1", "admin")
    r = client.get("/api/v1/admin/diagnostics/stack-status", headers=hdr)
    assert r.status_code == 200
    assert r.json()["meta"]["overall_status"] == "down"
    assert r.json()["services"][0]["state"] == "down"
