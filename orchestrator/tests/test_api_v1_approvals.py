# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/approvals/*`` — pending list, set-mode, elevate."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_approvals_bucket():
    from routes.api_v1.approvals import _approval_calls

    _approval_calls.clear()
    yield
    _approval_calls.clear()


def test_pending_returns_empty_when_no_data(client):
    resp = client.get("/api/v1/approvals/pending")
    assert resp.status_code == 200
    assert resp.json() == {"pending": []}


def test_set_mode_unknown_connector_returns_404(client, monkeypatch):
    import actions.registry as reg

    monkeypatch.setattr(reg, "list_actions", lambda: [])
    resp = client.post("/api/v1/approvals/connector/nope/mode", json={"mode": "DO"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_connector"


def test_set_mode_hard_limited_connector_403(client, monkeypatch):
    import actions.registry as reg

    monkeypatch.setattr(
        reg,
        "list_actions",
        lambda: [{"connector": "smtp", "action_type": "send_email"}],
    )
    import routes.api_v1.approvals as v1

    monkeypatch.setattr(v1, "is_hard_limited", lambda at: True)

    resp = client.post("/api/v1/approvals/connector/smtp/mode", json={"mode": "DO"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "hard_limited_connector"


def test_set_mode_happy_path(client, monkeypatch):
    import actions.registry as reg

    monkeypatch.setattr(
        reg,
        "list_actions",
        lambda: [{"connector": "smtp", "action_type": "draft_email"}],
    )
    import routes.api_v1.approvals as v1

    monkeypatch.setattr(v1, "is_hard_limited", lambda at: False)

    calls = {}

    def _set(*, user_id, connector, mode):
        calls["set"] = (user_id, connector, mode)

    monkeypatch.setattr(v1, "set_connector_mode", _set)

    written = []
    monkeypatch.setattr(v1.audit_module, "write_audit", lambda entry: written.append(entry))

    resp = client.post("/api/v1/approvals/connector/smtp/mode", json={"mode": "DO"})
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"connector": "smtp", "mode": "DO"}
    assert calls["set"][1] == "smtp"
    assert len(written) == 1
    assert written[0].connector == "__permissions_change__"


def test_elevate_unknown_action_returns_404(client, monkeypatch):
    import actions.registry as reg

    monkeypatch.setattr(reg, "list_actions", lambda: [])
    import routes.api_v1.approvals as v1

    monkeypatch.setattr(v1, "is_hard_limited", lambda at: False)

    resp = client.post(
        "/api/v1/approvals/elevate",
        json={"connector": "smtp", "action_type": "ghost"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_action"


def test_elevate_hard_limited_returns_403(client, monkeypatch):
    import routes.api_v1.approvals as v1

    monkeypatch.setattr(v1, "is_hard_limited", lambda at: True)

    resp = client.post(
        "/api/v1/approvals/elevate",
        json={"connector": "shell", "action_type": "rm_rf"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "hard_limited_action"


def test_elevate_happy_path(client, monkeypatch):
    import actions.registry as reg

    monkeypatch.setattr(
        reg,
        "list_actions",
        lambda: [{"connector": "smtp", "action_type": "draft_email"}],
    )
    import routes.api_v1.approvals as v1

    monkeypatch.setattr(v1, "is_hard_limited", lambda at: False)

    calls = {}

    def _elev(*, user_id, connector, action_type):
        calls["elev"] = (user_id, connector, action_type)

    monkeypatch.setattr(v1, "elevate_to_routine", _elev)

    resp = client.post(
        "/api/v1/approvals/elevate",
        json={"connector": "smtp", "action_type": "draft_email"},
    )
    assert resp.status_code == 200
    assert resp.json()["elevated"] is True
    assert calls["elev"] == ("default", "smtp", "draft_email")
