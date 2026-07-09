# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/audit`` + ``/api/v1/audit/{token}/reverse`` contract tests."""

from __future__ import annotations

import asyncio
from datetime import datetime
from datetime import timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import main

    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_reverse_bucket():
    from routes.api_v1.audit import _reverse_calls

    _reverse_calls.clear()
    yield
    _reverse_calls.clear()


def _row(token="tok-1", reversed_at=None, **overrides):
    base = {
        "id": 1,
        "action_name": "draft_email",
        "connector": "smtp",
        "mode": "ASK",
        "input_summary": "to=alice",
        "result_summary": "ok",
        "reverse_token": token,
        "reverse_action": None,
        "executed_at": datetime(2026, 4, 20, 10, 0, tzinfo=timezone.utc),
        "reversed_at": reversed_at,
        "scope": "personal",
    }
    base.update(overrides)
    return base


def test_list_audit_returns_rows(client, monkeypatch):
    rows = [_row(), _row(token="tok-2", id=2)]

    def _get(**kwargs):
        assert kwargs["user_id"] == "default"
        return rows

    def _count(**kwargs):
        assert kwargs["user_id"] == "default"
        return len(rows)

    import actions.audit as audit_module

    monkeypatch.setattr(audit_module, "get_audit", _get)
    monkeypatch.setattr(audit_module, "count_audit", _count)

    resp = client.get("/api/v1/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["audit"]) == 2
    assert body["audit"][0]["reverse_token"] == "tok-1"
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 0


def test_list_audit_invalid_date_range_422(client):
    resp = client.get(
        "/api/v1/audit",
        params={
            "after": "2026-06-01T00:00:00Z",
            "before": "2026-05-01T00:00:00Z",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_date_range"


def test_list_audit_pagination_offset(client, monkeypatch):
    rows = [_row(id=3)]

    def _get(**kwargs):
        assert kwargs["offset"] == 10
        assert kwargs["limit"] == 5
        return rows

    def _count(**kwargs):
        return 25

    import actions.audit as audit_module

    monkeypatch.setattr(audit_module, "get_audit", _get)
    monkeypatch.setattr(audit_module, "count_audit", _count)

    resp = client.get("/api/v1/audit", params={"offset": 10, "limit": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 25
    assert body["offset"] == 10
    assert body["limit"] == 5


def test_list_audit_event_type_filter(client, monkeypatch):
    captured = {}

    def _get(**kwargs):
        captured.update(kwargs)
        return []

    def _count(**kwargs):
        return 0

    import actions.audit as audit_module

    monkeypatch.setattr(audit_module, "get_audit", _get)
    monkeypatch.setattr(audit_module, "count_audit", _count)

    resp = client.get("/api/v1/audit", params={"event_type": "auth.invite"})
    assert resp.status_code == 200
    assert captured["event_type"] == "auth.invite"


def test_list_audit_enriches_event_type_privacy_block(client, monkeypatch):
    summary = (
        '{"decline_type": "external_call_denied", "requested_model": "gpt-4", '
        '"reason": "privacy_mode_block"}'
    )
    rows = [
        _row(
            action_name="privacy_mode_block",
            connector="llm",
            mode="privacy_gate",
            input_summary=summary,
            reverse_token=None,
        )
    ]

    import actions.audit as audit_module

    monkeypatch.setattr(audit_module, "get_audit", lambda **k: rows)
    monkeypatch.setattr(audit_module, "count_audit", lambda **k: 1)

    resp = client.get("/api/v1/audit")
    assert resp.status_code == 200
    entry = resp.json()["audit"][0]
    assert entry["event_type"] == "privacy.external_call.denied"
    assert entry["scope"] == "personal"
    assert entry["source"] == "llm/privacy_gate"
    assert "requested_model=gpt-4" in (entry["description"] or "")


def test_list_audit_event_type_invite_excludes_wildcard_collision(client, monkeypatch):
    """auth.invite filter must not match fabricated near-miss action names."""

    def _get(**kwargs):
        if kwargs.get("event_type") == "auth.invite":
            return [_row(action_name="__user_invite__.minted", id=1)]
        return []

    def _count(**kwargs):
        return 1 if kwargs.get("event_type") == "auth.invite" else 0

    import actions.audit as audit_module

    monkeypatch.setattr(audit_module, "get_audit", _get)
    monkeypatch.setattr(audit_module, "count_audit", _count)

    resp = client.get("/api/v1/audit", params={"event_type": "auth.invite"})
    assert resp.status_code == 200
    assert resp.json()["audit"][0]["action_name"] == "__user_invite__.minted"

    from services.audit_taxonomy import action_names_for_event_type

    pred = action_names_for_event_type("auth.invite")
    assert pred is not None
    assert "xxuser_invitexx.foo" not in pred.action_names


def test_list_audit_event_type_action_executed_exclusion(client, monkeypatch):
    rows = [
        _row(action_name="send_email", id=1),
        _row(action_name="__user_invite__.minted", id=2),
    ]

    def _get(**kwargs):
        if kwargs.get("event_type") == "action.executed":
            return [rows[0]]
        return rows

    def _count(**kwargs):
        return 1 if kwargs.get("event_type") == "action.executed" else 2

    import actions.audit as audit_module

    monkeypatch.setattr(audit_module, "get_audit", _get)
    monkeypatch.setattr(audit_module, "count_audit", _count)

    resp = client.get("/api/v1/audit", params={"event_type": "action.executed"})
    assert resp.status_code == 200
    names = [r["action_name"] for r in resp.json()["audit"]]
    assert names == ["send_email"]


def test_list_audit_description_redacts_secrets(client, monkeypatch):
    rows = [
        _row(
            action_name="test",
            input_summary='{"reverse_token": "secret-tok", "connector": "smtp"}',
            result_summary="password=hunter2",
        )
    ]

    import actions.audit as audit_module

    monkeypatch.setattr(audit_module, "get_audit", lambda **k: rows)
    monkeypatch.setattr(audit_module, "count_audit", lambda **k: 1)

    resp = client.get("/api/v1/audit")
    desc = resp.json()["audit"][0]["description"] or ""
    assert "secret-tok" not in desc
    assert "hunter2" not in desc
    assert "reverse_token" not in desc.lower()


def test_list_audit_as_user_requires_admin(client, monkeypatch):
    """In dev mode the synthesised user is admin; check the 403 path
    by simulating a non-admin caller."""

    import auth

    monkeypatch.setattr(
        auth,
        "get_user",
        lambda req: auth.UserContext(user_id="bob", role="user", is_authenticated=True),
    )
    # routes/api_v1/audit imports get_user directly into its module namespace
    import routes.api_v1.audit as v1_audit

    monkeypatch.setattr(v1_audit, "get_user", auth.get_user)

    resp = client.get("/api/v1/audit", params={"as_user": "alice"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "admin_required"


def test_reverse_unknown_token_returns_404(client, monkeypatch):
    """When the audit_log row doesn't exist for this user, return 404
    (not 403) so a malicious caller can't enumerate other users' tokens."""

    class _MS:
        def fetch_one(self, q, p):
            return None

        def execute(self, *a, **k):
            pass

        def fetch_all(self, *a, **k):
            return []

        def close(self):
            pass

        def ping(self):
            return True

    import config as _config

    _config._instances["metadata_store"] = _MS()

    resp = client.post("/api/v1/audit/nonexistent-token/reverse")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_reverse_token"


def test_reverse_already_reversed_returns_400(client, monkeypatch):
    class _MS:
        def fetch_one(self, q, p):
            return {"id": 5, "reversed_at": datetime.now(timezone.utc)}

        def execute(self, *a, **k):
            pass

        def fetch_all(self, *a, **k):
            return []

        def close(self):
            pass

        def ping(self):
            return True

    import config as _config

    _config._instances["metadata_store"] = _MS()

    resp = client.post("/api/v1/audit/tok-1/reverse")
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "already_reversed"


def test_reverse_success(client, monkeypatch):
    class _MS:
        def fetch_one(self, q, p):
            return {"id": 5, "reversed_at": None}

        def execute(self, *a, **k):
            pass

        def fetch_all(self, *a, **k):
            return []

        def close(self):
            pass

        def ping(self):
            return True

    import config as _config

    _config._instances["metadata_store"] = _MS()

    import routes.api_v1.audit as v1_audit

    monkeypatch.setattr(
        v1_audit,
        "attempt_reverse",
        lambda token, *, user_id: SimpleNamespace(success=True, error=None),
    )
    resp = client.post("/api/v1/audit/tok-1/reverse")
    assert resp.status_code == 200
    assert resp.json()["status"] == "reversed"


def test_stream_audit_emits_entry(client, monkeypatch):
    row = _row(id=99, action_name="test_action")

    calls: list[dict] = []

    def _after(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return [row]
        return []

    import actions.audit as audit_module

    monkeypatch.setattr(audit_module, "get_audit_after_id", _after)

    import routes.api_v1.audit as v1_audit

    async def _stop_after_first_poll(_sec: float) -> None:
        raise asyncio.CancelledError()

    monkeypatch.setattr(v1_audit.asyncio, "sleep", _stop_after_first_poll)

    with client.stream("GET", "/api/v1/audit/stream", params={"since_id": 0}) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        chunk = next(resp.iter_text(chunk_size=2048))
        assert "event: audit_entry" in chunk
        assert "test_action" in chunk
        assert calls[0]["user_id"] == "default"


def test_stream_audit_as_user_forbidden_for_member(client, monkeypatch):
    import auth

    monkeypatch.setattr(
        auth,
        "get_user",
        lambda req: auth.UserContext(user_id="bob", role="user", is_authenticated=True),
    )
    import routes.api_v1.audit as v1_audit

    monkeypatch.setattr(v1_audit, "get_user", auth.get_user)

    resp = client.get("/api/v1/audit/stream", params={"as_user": "other-user"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "admin_required"
