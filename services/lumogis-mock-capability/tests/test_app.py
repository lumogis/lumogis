# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from main import SERVICE_ID
from main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MOCK_CAPABILITY_SHARED_SECRET", "test-secret-xyz")
    return TestClient(app)


def test_health(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_capabilities_shape(client: TestClient) -> None:
    r = client.get("/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == SERVICE_ID
    assert any(t["name"] == "mock.echo_ping" for t in body["tools"])


def test_echo_requires_bearer(client: TestClient) -> None:
    r = client.post("/tools/mock.echo_ping", json={"msg": "hi"})
    assert r.status_code == 401


def test_echo_with_bearer(client: TestClient) -> None:
    r = client.post(
        "/tools/mock.echo_ping",
        json={"msg": "hi"},
        headers={"Authorization": "Bearer test-secret-xyz"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["echo"]["msg"] == "hi"


def test_echo_wrong_secret(client: TestClient) -> None:
    r = client.post(
        "/tools/mock.echo_ping",
        json={},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 403
