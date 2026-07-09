# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-584 — the authz core for the admin unshare surface (merge blockers).

`require_admin` is the enforcement boundary; the UI affordance visibility is
convenience only. These tests drive the real `require_admin` (via a patched
auth context) so a member's forged call is refused at the gate — no teardown,
no audit. The owner-only path in `routes/scope.py` is asserted untouched.
"""

from __future__ import annotations

import authz
import pytest
import routes.admin_sharing as admin_sharing
from auth import UserContext
from fastapi import FastAPI
from fastapi.testclient import TestClient

ADMIN = UserContext(user_id="carol-admin", role="admin", is_authenticated=True)
MEMBER = UserContext(user_id="bob", role="user", is_authenticated=True)
ANON = UserContext(user_id="anon", role="user", is_authenticated=False)


@pytest.fixture
def app():
    from csrf import require_same_origin

    application = FastAPI()
    application.include_router(admin_sharing.router)
    # CSRF is tested separately; isolate these tests to the admin gate.
    application.dependency_overrides[require_same_origin] = lambda: None
    return application


def _auth_as(monkeypatch, ctx):
    """Make the real require_admin see ``ctx`` (AUTH_ENABLED=true path)."""
    monkeypatch.setattr(authz, "auth_enabled", lambda: True)
    monkeypatch.setattr(authz, "get_user", lambda request: ctx)


def _guard_service(monkeypatch):
    """Fail if the service runs — proves the gate rejects before any work."""

    def boom(*a, **k):
        pytest.fail("service reached despite authz rejection")

    monkeypatch.setattr(admin_sharing.svc, "admin_unshare", boom)
    monkeypatch.setattr(admin_sharing.svc, "admin_list_shared_items", boom)


def test_member_forbidden_403_on_delete(app, monkeypatch):
    _auth_as(monkeypatch, MEMBER)
    _guard_service(monkeypatch)
    resp = TestClient(app).delete("/api/v1/admin/shared-items/notes/note-1")
    assert resp.status_code == 403


def test_member_forbidden_403_on_list(app, monkeypatch):
    _auth_as(monkeypatch, MEMBER)
    _guard_service(monkeypatch)
    resp = TestClient(app).get("/api/v1/admin/shared-items")
    assert resp.status_code == 403


def test_unauthenticated_401(app, monkeypatch):
    _auth_as(monkeypatch, ANON)
    _guard_service(monkeypatch)
    resp = TestClient(app).delete("/api/v1/admin/shared-items/notes/note-1")
    assert resp.status_code == 401


def test_admin_allowed_delete_calls_service(app, monkeypatch):
    _auth_as(monkeypatch, ADMIN)
    captured: dict = {}

    def fake_unshare(*, actor, resource, pk):
        captured["actor"] = actor.user_id
        captured["resource"] = resource
        captured["pk"] = pk
        return {
            "resource_type": resource,
            "resource_id": pk,
            "source_owner_id": "bob",
            "unshared": True,
        }

    monkeypatch.setattr(admin_sharing.svc, "admin_unshare", fake_unshare)
    resp = TestClient(app).delete("/api/v1/admin/shared-items/files/42")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "resource_type": "files",
        "resource_id": "42",
        "source_owner_id": "bob",
        "unshared": True,
    }
    assert captured == {"actor": "carol-admin", "resource": "files", "pk": "42"}


def test_admin_allowed_list_returns_items(app, monkeypatch):
    _auth_as(monkeypatch, ADMIN)
    monkeypatch.setattr(
        admin_sharing.svc,
        "admin_list_shared_items",
        lambda: [
            {
                "resource_type": "files",
                "resource_id": "42",
                "source_owner_id": "bob",
                "label": "insurance.pdf",
            }
        ],
    )
    resp = TestClient(app).get("/api/v1/admin/shared-items")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert items[0]["resource_id"] == "42"
    assert items[0]["source_owner_id"] == "bob"


def test_delete_unknown_resource_type_400(app, monkeypatch):
    _auth_as(monkeypatch, ADMIN)

    def raise_unknown(*, actor, resource, pk):
        raise admin_sharing.svc.UnknownResource(resource)

    monkeypatch.setattr(admin_sharing.svc, "admin_unshare", raise_unknown)
    resp = TestClient(app).delete("/api/v1/admin/shared-items/widgets/1")
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "unknown_resource_type"


def test_delete_not_shared_opaque_404(app, monkeypatch):
    _auth_as(monkeypatch, ADMIN)

    def raise_missing(*, actor, resource, pk):
        raise admin_sharing.svc.SharedItemNotFound(resource, pk)

    monkeypatch.setattr(admin_sharing.svc, "admin_unshare", raise_missing)
    resp = TestClient(app).delete("/api/v1/admin/shared-items/notes/private")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "not_found"


def test_delete_incomplete_teardown_500(app, monkeypatch):
    _auth_as(monkeypatch, ADMIN)

    def raise_incomplete(*, actor, resource, pk):
        raise admin_sharing.svc.TeardownIncomplete(resource, pk, 1)

    monkeypatch.setattr(admin_sharing.svc, "admin_unshare", raise_incomplete)
    resp = TestClient(app).delete("/api/v1/admin/shared-items/notes/n1")
    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "unshare_incomplete"


def test_delete_audit_failure_500(app, monkeypatch):
    _auth_as(monkeypatch, ADMIN)

    def raise_audit(*, actor, resource, pk):
        raise admin_sharing.svc.AuditWriteFailed(resource, pk)

    monkeypatch.setattr(admin_sharing.svc, "admin_unshare", raise_audit)
    resp = TestClient(app).delete("/api/v1/admin/shared-items/notes/n1")
    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "audit_write_failed"


# ---------------------------------------------------------------------------
# Owner-only path is untouched by this plan (merge gate): a member still
# cannot unshare a foreign item via the normal owner route — 404.
# ---------------------------------------------------------------------------


class _OwnerScopedStore:
    """Returns a personal row only for the OWNING caller — faithfully models
    scope.py's ``AND user_id = %s AND scope = 'personal'`` predicate, so a 404
    for a foreign caller proves the ownership predicate (not merely an empty
    table).
    """

    def __init__(self, owner: str):
        self.owner = owner
        self.queried: list[tuple] = []

    def fetch_one(self, query, params=None):
        q = " ".join(query.split()).lower()
        self.queried.append(tuple(params or ()))
        if "from notes" in q and "scope = 'personal'" in q:
            note_id, user_id = params[0], params[1]
            if user_id == self.owner:
                return {"note_id": note_id, "user_id": user_id, "scope": "personal", "text": "t"}
        return None


def test_owner_route_still_owner_only(monkeypatch):
    from authz import require_user
    from routes.scope import router as scope_router

    import config

    # The note is owned by alice; the row genuinely EXISTS in the store.
    store = _OwnerScopedStore(owner="alice")
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    application = FastAPI()
    application.include_router(scope_router)
    # Foreign member (bob) tries to unshare alice's note via the normal owner
    # route → the owner-scoping predicate excludes him even though the row
    # exists → 404. LUM-584 does not touch this path.
    application.dependency_overrides[require_user] = lambda: MEMBER  # bob
    resp = TestClient(application).delete("/api/v1/notes/alice-note/publish")
    assert resp.status_code == 404
    # The fetch was scoped to the caller (bob), which is why it missed.
    assert store.queried and store.queried[0][1] == MEMBER.user_id
