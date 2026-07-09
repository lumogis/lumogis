# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-583 — GET /api/v1/me/shared-items route: auth, shape, owner-scoping."""
from __future__ import annotations

import routes.me as me_mod
from auth import UserContext
from authz import require_user
from fastapi import FastAPI
from fastapi.testclient import TestClient

CALLER = UserContext(user_id="alice", role="user", is_authenticated=True)


def _app(monkeypatch):
    application = FastAPI()
    application.include_router(me_mod.router)
    application.dependency_overrides[require_user] = lambda: CALLER
    monkeypatch.setattr(me_mod, "get_user", lambda request: CALLER)
    return application


def test_shared_items_returns_owner_scoped_list(monkeypatch):
    captured = {}

    def fake_list(user_id, **k):
        captured["user_id"] = user_id
        return [
            {
                "resource_type": "files",
                "resource_id": "7",
                "label": "tax.pdf",
                "shared_at": None,
            }
        ]

    monkeypatch.setattr(me_mod.shared_items_svc, "list_my_shared_items", fake_list)
    resp = TestClient(_app(monkeypatch)).get("/api/v1/me/shared-items")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"][0]["resource_id"] == "7"
    assert body["items"][0]["resource_type"] == "files"
    # The service was scoped to the authenticated caller.
    assert captured["user_id"] == "alice"


def test_shared_items_empty_is_200_not_404(monkeypatch):
    monkeypatch.setattr(me_mod.shared_items_svc, "list_my_shared_items", lambda uid, **k: [])
    resp = TestClient(_app(monkeypatch)).get("/api/v1/me/shared-items")
    assert resp.status_code == 200
    assert resp.json() == {"items": []}
