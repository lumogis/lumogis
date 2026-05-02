# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""``/api/v1/audit`` + ``/api/v1/audit/{token}/reverse`` contract tests."""

from __future__ import annotations

from datetime import datetime, timezone
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
    }
    base.update(overrides)
    return base


def test_list_audit_returns_rows(client, monkeypatch):
    rows = [_row(), _row(token="tok-2", id=2)]

    def _get(connector, action_type, user_id, limit):
        assert user_id == "default"
        return rows

    import actions.audit as audit_module
    monkeypatch.setattr(audit_module, "get_audit", _get)

    resp = client.get("/api/v1/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["audit"]) == 2
    assert body["audit"][0]["reverse_token"] == "tok-1"


def test_list_audit_as_user_requires_admin(client, monkeypatch):
    """In dev mode the synthesised user is admin; check the 403 path
    by simulating a non-admin caller."""

    import auth
    monkeypatch.setattr(
        auth, "get_user",
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
        def fetch_one(self, q, p): return None
        def execute(self, *a, **k): pass
        def fetch_all(self, *a, **k): return []
        def close(self): pass
        def ping(self): return True

    import config as _config
    _config._instances["metadata_store"] = _MS()

    resp = client.post("/api/v1/audit/nonexistent-token/reverse")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_reverse_token"


def test_reverse_already_reversed_returns_400(client, monkeypatch):
    class _MS:
        def fetch_one(self, q, p):
            return {"id": 5, "reversed_at": datetime.now(timezone.utc)}
        def execute(self, *a, **k): pass
        def fetch_all(self, *a, **k): return []
        def close(self): pass
        def ping(self): return True

    import config as _config
    _config._instances["metadata_store"] = _MS()

    resp = client.post("/api/v1/audit/tok-1/reverse")
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "already_reversed"


def test_reverse_success(client, monkeypatch):
    class _MS:
        def fetch_one(self, q, p):
            return {"id": 5, "reversed_at": None}
        def execute(self, *a, **k): pass
        def fetch_all(self, *a, **k): return []
        def close(self): pass
        def ping(self): return True

    import config as _config
    _config._instances["metadata_store"] = _MS()

    import routes.api_v1.audit as v1_audit
    monkeypatch.setattr(
        v1_audit, "attempt_reverse",
        lambda token, *, user_id: SimpleNamespace(success=True, error=None),
    )
    resp = client.post("/api/v1/audit/tok-1/reverse")
    assert resp.status_code == 200
    assert resp.json()["status"] == "reversed"
