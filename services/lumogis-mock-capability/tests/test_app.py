# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from main import CONTRACT_VERSION
from main import SERVICE_ID
from main import TOOL_NAME
from main import app


def _envelope(arguments: dict) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "tool": TOOL_NAME,
        "arguments": arguments,
        "meta": {"user": "tester", "request_id": "unit-1"},
    }


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
    assert body["contract_version"] == CONTRACT_VERSION
    tool = next(t for t in body["tools"] if t["name"] == TOOL_NAME)
    assert tool["invoke"]["path"] == f"/tools/{TOOL_NAME}"


def test_echo_requires_bearer(client: TestClient) -> None:
    r = client.post(f"/tools/{TOOL_NAME}", json=_envelope({"msg": "hi"}))
    assert r.status_code == 401


def test_echo_with_bearer(client: TestClient) -> None:
    r = client.post(
        f"/tools/{TOOL_NAME}",
        json=_envelope({"msg": "hi"}),
        headers={"Authorization": "Bearer test-secret-xyz"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "output": {"echo": {"msg": "hi"}}}


def test_echo_wrong_secret(client: TestClient) -> None:
    r = client.post(
        f"/tools/{TOOL_NAME}",
        json=_envelope({}),
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 403


def test_echo_rejects_wrong_tool_name(client: TestClient) -> None:
    body = _envelope({"msg": "hi"})
    body["tool"] = "mock.other"
    r = client.post(
        f"/tools/{TOOL_NAME}",
        json=body,
        headers={"Authorization": "Bearer test-secret-xyz"},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "not_found"
