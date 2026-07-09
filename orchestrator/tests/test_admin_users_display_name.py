# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-585 — admin PATCH of users.display_name: persist, normalize, gate, audit,
and the _to_admin_view projection (so the admin UI reads back the written value).
"""
from __future__ import annotations

from datetime import datetime
from datetime import timezone

import authz
import pytest
import routes.admin_users as au
from auth import UserContext
from authz import require_admin
from csrf import require_same_origin
from fastapi import FastAPI
from fastapi.testclient import TestClient
from models.auth import InternalUser

ADMIN = UserContext(user_id="admin", role="admin", is_authenticated=True)
MEMBER = UserContext(user_id="bob", role="user", is_authenticated=True)


def _internal(uid="u1", display_name=None, role="user"):
    return InternalUser(
        id=uid,
        email=f"{uid}@home.lan",
        password_hash="x",
        role=role,
        disabled=False,
        created_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        display_name=display_name,
    )


class _FakeUsers:
    def __init__(self):
        self.store = {"u1": _internal("u1")}
        self.set_calls: list[tuple] = []

    def get_user_by_id(self, uid):
        return self.store.get(uid)

    def set_display_name(self, uid, name):
        norm = (name or "").strip() or None
        self.store[uid] = self.store[uid].model_copy(update={"display_name": norm})
        self.set_calls.append((uid, norm))
        return self.store[uid]

    def count_admins(self):
        return 2  # never the last admin, so role/disabled guards don't fire

    def update_role(self, uid, role):  # pragma: no cover - not exercised here
        raise AssertionError("update_role must not be called for a display_name patch")

    def set_disabled(self, uid, disabled, **k):  # pragma: no cover
        raise AssertionError("set_disabled must not be called for a display_name patch")


@pytest.fixture
def fake_users(monkeypatch):
    fake = _FakeUsers()
    monkeypatch.setattr(au, "users_service", fake)
    return fake


def _admin_app():
    app = FastAPI()
    app.include_router(au.router)
    app.dependency_overrides[require_admin] = lambda: ADMIN
    app.dependency_overrides[require_same_origin] = lambda: None
    return app


def test_patch_sets_display_name_and_reads_it_back(fake_users, monkeypatch):
    monkeypatch.setattr(au, "get_user", lambda request: ADMIN)
    resp = TestClient(_admin_app()).patch("/api/v1/admin/users/u1", json={"display_name": "Alex"})
    assert resp.status_code == 200, resp.text
    # _to_admin_view must surface the written value (else the UI shows null).
    assert resp.json()["display_name"] == "Alex"
    assert fake_users.set_calls == [("u1", "Alex")]


def test_patch_empty_display_name_clears_to_null(fake_users, monkeypatch):
    fake_users.store["u1"] = _internal("u1", display_name="Alex")
    monkeypatch.setattr(au, "get_user", lambda request: ADMIN)
    resp = TestClient(_admin_app()).patch("/api/v1/admin/users/u1", json={"display_name": "   "})
    assert resp.status_code == 200, resp.text
    assert resp.json()["display_name"] is None
    assert fake_users.set_calls == [("u1", None)]  # whitespace normalized to NULL


def test_patch_display_name_is_audited(fake_users, monkeypatch, caplog):
    monkeypatch.setattr(au, "get_user", lambda request: ADMIN)
    with caplog.at_level("INFO"):
        TestClient(_admin_app()).patch("/api/v1/admin/users/u1", json={"display_name": "Alex"})
    msgs = [r.getMessage() for r in caplog.records]
    assert any("set display_name" in m and "by=admin" in m for m in msgs)


def test_patch_empty_body_still_400(fake_users, monkeypatch):
    monkeypatch.setattr(au, "get_user", lambda request: ADMIN)
    resp = TestClient(_admin_app()).patch("/api/v1/admin/users/u1", json={})
    assert resp.status_code == 400
    assert fake_users.set_calls == []


def test_patch_display_name_requires_admin(fake_users, monkeypatch):
    # Drive the real require_admin: a member is rejected before any write.
    monkeypatch.setattr(authz, "auth_enabled", lambda: True)
    monkeypatch.setattr(authz, "get_user", lambda request: MEMBER)
    app = FastAPI()
    app.include_router(au.router)
    app.dependency_overrides[require_same_origin] = lambda: None
    resp = TestClient(app).patch("/api/v1/admin/users/u1", json={"display_name": "Alex"})
    assert resp.status_code == 403
    assert fake_users.set_calls == []


def test_to_admin_view_includes_display_name():
    view = au._to_admin_view(_internal("u1", display_name="Alex"))
    assert view.display_name == "Alex"
