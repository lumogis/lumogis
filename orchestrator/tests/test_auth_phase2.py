# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 2 (admin gating + user management) tests for family-LAN multi-user.

Covers:

* :func:`authz.require_admin` and :func:`authz.require_user` behaviour
  in both ``AUTH_ENABLED=false`` (no-op) and ``AUTH_ENABLED=true`` (401
  vs 403 vs 200) modes.
* The 401/403/200 matrix for at least one representative endpoint per
  route module that received a ``Depends(require_admin)`` decoration in
  Phase 2 (``routes/admin.py``, ``routes/signals.py``).
* Coverage gate: every path the plan §7 admin-list nominated has the
  ``require_admin`` dependency in its FastAPI dependency chain. This is
  a route-walk smoke test that catches future regressions if a developer
  adds a route to the admin module without gating it.
* New ``/api/v1/admin/users`` CRUD: create, list, patch (role +
  disabled), delete, plus the safety invariants (cannot delete or
  demote the last active admin; cannot self-disable / self-delete;
  duplicate-email returns 409).
* The dashboard HTML smoke test: the inline auth widget is present
  and references ``/api/v1/auth/me`` and a Logout element.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

# Reuse the fakes + fixtures from Phase 1 — same in-memory MetadataStore.
from tests.test_auth_phase1 import FakeUsersStore  # noqa: F401
from tests.test_auth_phase1 import auth_env  # noqa: F401
from tests.test_auth_phase1 import dev_env  # noqa: F401
from tests.test_auth_phase1 import users_store  # noqa: F401

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mint(user_id: str, role: str) -> str:
    """Mint a valid access JWT against the current AUTH_SECRET."""
    return jwt.encode(
        {
            "sub": user_id,
            "role": role,
            "iat": int(time.time()),
            "exp": int(time.time()) + 600,
        },
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )


@contextmanager
def _client(users_store):
    """Boot the app with a single seeded admin so the consistency gate
    is satisfied even when the test re-enables it elsewhere."""
    import services.users as users_svc

    if users_svc.get_user_by_email("admin@home.lan") is None:
        users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")
    import main

    with TestClient(main.app) as client:
        yield client


def _admin_headers(users_store) -> dict:
    """Return Authorization headers for the seeded admin."""
    import services.users as users_svc

    admin = users_svc.get_user_by_email("admin@home.lan")
    assert admin is not None
    return {"Authorization": f"Bearer {_mint(admin.id, 'admin')}"}


def _user_headers(users_store) -> dict:
    """Create-or-reuse a non-admin user and return their auth headers."""
    import services.users as users_svc

    target = users_svc.get_user_by_email("bob@home.lan")
    if target is None:
        target = users_svc.create_user("bob@home.lan", "verylongpassword12", "user")
    return {"Authorization": f"Bearer {_mint(target.id, 'user')}"}


# ---------------------------------------------------------------------------
# Unit: authz dependencies
# ---------------------------------------------------------------------------


def test_require_admin_is_noop_in_dev_mode(dev_env):
    from auth import UserContext
    from authz import require_admin

    class _Req:
        state = type("S", (), {"user": UserContext()})()
        url = type("U", (), {"path": "/whatever"})()

    ctx = require_admin(_Req())
    assert ctx.role == "admin"


def test_require_admin_raises_401_when_unauth(auth_env):
    from auth import UserContext
    from authz import require_admin
    from fastapi import HTTPException

    class _Req:
        state = type("S", (), {"user": UserContext()})()
        url = type("U", (), {"path": "/dashboard"})()

    with pytest.raises(HTTPException) as ei:
        require_admin(_Req())
    assert ei.value.status_code == 401


def test_require_admin_raises_403_when_user_role(auth_env):
    from auth import UserContext
    from authz import require_admin
    from fastapi import HTTPException

    class _Req:
        state = type(
            "S", (), {"user": UserContext(user_id="u1", is_authenticated=True, role="user")}
        )()
        url = type("U", (), {"path": "/dashboard"})()

    with pytest.raises(HTTPException) as ei:
        require_admin(_Req())
    assert ei.value.status_code == 403


def test_require_user_passes_for_user_role(auth_env):
    from auth import UserContext
    from authz import require_user

    class _Req:
        state = type(
            "S", (), {"user": UserContext(user_id="u1", is_authenticated=True, role="user")}
        )()
        url = type("U", (), {"path": "/whatever"})()

    ctx = require_user(_Req())
    assert ctx.user_id == "u1"


# ---------------------------------------------------------------------------
# Coverage gate: every plan-nominated admin path has require_admin
# ---------------------------------------------------------------------------

# Per family-LAN multi-user plan §7. Add a path here when adding a new
# admin-only route; the test below will then ensure require_admin is
# wired up. Paths that do not exist yet are deliberately omitted (e.g.
# DELETE /sources/{id} — Phase 3).
_EXPECTED_ADMIN_PATHS: tuple[tuple[str, str], ...] = (
    ("PUT", "/permissions/{connector}"),
    ("POST", "/settings/restart"),
    ("GET", "/settings"),
    ("PUT", "/settings"),
    ("GET", "/settings/root-preview"),
    ("POST", "/settings/prune"),
    ("GET", "/settings/ollama-discovery"),
    ("POST", "/settings/ollama-pull"),
    ("POST", "/settings/ollama-delete"),
    ("POST", "/backup"),
    ("POST", "/restore"),
    ("POST", "/entities/merge"),
    ("POST", "/entities/deduplicate"),
    ("POST", "/sources"),
    # POST /review-queue/decide moved from require_admin → require_user
    # in audit B9 (review_queue_per_user_approval_scope). Per-item
    # authorization now enforces "originating user OR admin" inside the
    # handler. Coverage moved to test_review_queue.py
    # (TestReviewQueuePerUserApprovalScope).
    ("GET", "/dashboard"),
    ("GET", "/kg/settings"),
    ("POST", "/kg/settings"),
    ("DELETE", "/kg/settings/{key}"),
    ("GET", "/graph/mgm"),
    ("GET", "/kg/job-status"),
    ("POST", "/kg/trigger-weekly"),
    ("GET", "/kg/stop-entities"),
    ("POST", "/kg/stop-entities"),
    ("POST", "/browse/mkdir"),
    # /api/v1/admin/users — full CRUD via router-level dependency.
    ("POST", "/api/v1/admin/users"),
    ("GET", "/api/v1/admin/users"),
    ("POST", "/api/v1/admin/users/{user_id}/password"),
    ("PATCH", "/api/v1/admin/users/{user_id}"),
    ("DELETE", "/api/v1/admin/users/{user_id}"),
)


def _route_has_require_admin(route) -> bool:
    """True iff ``authz.require_admin`` is somewhere in the dep chain."""
    for dep in getattr(route, "dependant", None).dependencies if route.dependant else []:
        # Each Dependant has a `call` attribute holding the dep function.
        if getattr(dep, "call", None).__name__ == "require_admin":
            return True
        for sub in dep.dependencies:
            if getattr(sub, "call", None).__name__ == "require_admin":
                return True
    return False


def test_every_admin_path_has_require_admin_dependency(users_store, auth_env):
    """Walk the live FastAPI route table and assert each plan-nominated
    admin path actually carries ``require_admin``. Catches regressions
    where someone adds an admin route but forgets the dep."""
    import main

    by_key: dict[tuple[str, str], list] = {}
    for r in main.app.routes:
        for m in getattr(r, "methods", ()) or ():
            by_key.setdefault((m, r.path), []).append(r)

    missing = []
    ungated = []
    for method, path in _EXPECTED_ADMIN_PATHS:
        candidates = by_key.get((method, path), [])
        if not candidates:
            missing.append(f"{method} {path}")
            continue
        if not any(_route_has_require_admin(c) for c in candidates):
            ungated.append(f"{method} {path}")

    assert not missing, f"plan-nominated routes are not registered: {missing}"
    assert not ungated, f"plan-nominated routes lack require_admin: {ungated}"


# ---------------------------------------------------------------------------
# Integration: 401 / 403 / 200 matrix on representative endpoints
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/dashboard"),
        ("POST", "/settings/restart"),
        ("GET", "/settings"),
        ("POST", "/backup"),
        ("POST", "/sources"),  # signals.py
        ("POST", "/api/v1/admin/users"),  # admin_users.py
        ("GET", "/api/v1/admin/users"),  # admin_users.py
        ("POST", "/api/v1/admin/users/x/password"),
    ],
)
def test_admin_routes_return_401_when_unauthenticated(users_store, auth_env, method, path):
    """No bearer token → middleware returns 401 before the route runs."""
    with _client(users_store) as client:
        body: dict = {}
        if method == "POST" and path.endswith("/password"):
            body = {"new_password": "validpassword12x"}
        resp = client.request(method, path, json=body)
    assert resp.status_code == 401, (
        f"{method} {path} should be 401, got {resp.status_code}: {resp.text[:200]}"
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/dashboard"),
        ("POST", "/settings/restart"),
        ("GET", "/settings"),
        ("POST", "/api/v1/admin/users"),
        ("GET", "/api/v1/admin/users"),
        ("POST", "/api/v1/admin/users/x/password"),
    ],
)
def test_admin_routes_return_403_for_non_admin_user(users_store, auth_env, method, path):
    """Authenticated user without admin role → 403."""
    with _client(users_store) as client:
        body: dict = {}
        if method == "POST" and path.endswith("/password"):
            body = {"new_password": "validpassword12x"}
        resp = client.request(
            method,
            path,
            headers=_user_headers(users_store),
            json=body,
        )
    assert resp.status_code == 403, (
        f"{method} {path} should be 403, got {resp.status_code}: {resp.text[:200]}"
    )


def test_dashboard_get_returns_200_for_admin(users_store, auth_env):
    with _client(users_store) as client:
        resp = client.get("/dashboard", headers=_admin_headers(users_store))
    assert resp.status_code == 200


def test_settings_get_returns_200_for_admin(users_store, auth_env):
    with _client(users_store) as client:
        resp = client.get("/settings", headers=_admin_headers(users_store))
    assert resp.status_code == 200


def test_admin_routes_open_in_dev_mode(users_store, dev_env):
    """``AUTH_ENABLED=false`` → admin gating is a no-op; admin endpoints
    are reachable without any token (preserves current dev experience)."""
    import main

    with TestClient(main.app) as client:
        # GET /dashboard is HTML; we just need a non-401/403.
        resp = client.get("/dashboard")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Integration: /api/v1/admin/users CRUD
# ---------------------------------------------------------------------------


def test_admin_users_create_returns_201_and_admin_view(users_store, auth_env):
    with _client(users_store) as client:
        resp = client.post(
            "/api/v1/admin/users",
            headers=_admin_headers(users_store),
            json={
                "email": "carol@home.lan",
                "password": "verylongpassword12",
                "role": "user",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "carol@home.lan"
    assert body["role"] == "user"
    assert body["disabled"] is False
    assert "password_hash" not in body
    assert "refresh_token_jti" not in body


def test_admin_users_create_duplicate_email_returns_409(users_store, auth_env):
    with _client(users_store) as client:
        h = _admin_headers(users_store)
        first = client.post(
            "/api/v1/admin/users",
            headers=h,
            json={
                "email": "dup@home.lan",
                "password": "verylongpassword12",
                "role": "user",
            },
        )
        assert first.status_code == 201
        second = client.post(
            "/api/v1/admin/users",
            headers=h,
            json={
                "email": "dup@home.lan",
                "password": "verylongpassword12",
                "role": "user",
            },
        )
    assert second.status_code == 409


def test_admin_users_list_returns_admin_view_array(users_store, auth_env):
    with _client(users_store) as client:
        client.post(
            "/api/v1/admin/users",
            headers=_admin_headers(users_store),
            json={
                "email": "alice@home.lan",
                "password": "verylongpassword12",
                "role": "user",
            },
        )
        resp = client.get("/api/v1/admin/users", headers=_admin_headers(users_store))
    assert resp.status_code == 200
    rows = resp.json()
    emails = {r["email"] for r in rows}
    assert {"admin@home.lan", "alice@home.lan"}.issubset(emails)


def test_admin_users_patch_role_promotes_user_to_admin(users_store, auth_env):
    import services.users as users_svc

    with _client(users_store) as client:
        target = users_svc.create_user("eve@home.lan", "verylongpassword12", "user")
        resp = client.patch(
            f"/api/v1/admin/users/{target.id}",
            headers=_admin_headers(users_store),
            json={"role": "admin"},
        )
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_admin_users_patch_disables_user(users_store, auth_env):
    import services.users as users_svc

    with _client(users_store) as client:
        target = users_svc.create_user("eve@home.lan", "verylongpassword12", "user")
        resp = client.patch(
            f"/api/v1/admin/users/{target.id}",
            headers=_admin_headers(users_store),
            json={"disabled": True},
        )
    assert resp.status_code == 200
    assert resp.json()["disabled"] is True


def test_admin_users_patch_refuses_demoting_last_admin(users_store, auth_env):
    import services.users as users_svc

    with _client(users_store) as client:
        admin = users_svc.get_user_by_email("admin@home.lan")
        assert admin is not None
        resp = client.patch(
            f"/api/v1/admin/users/{admin.id}",
            headers=_admin_headers(users_store),
            json={"role": "user"},
        )
    assert resp.status_code == 400
    assert "last active admin" in resp.json()["detail"]


def test_admin_users_patch_refuses_disabling_last_admin(users_store, auth_env):
    import services.users as users_svc

    with _client(users_store) as client:
        admin = users_svc.get_user_by_email("admin@home.lan")
        # Caller != target so the self-disable guard does not trigger first.
        # Make a second admin so the seed admin isn't the caller…
        second = users_svc.create_user("admin2@home.lan", "verylongpassword12", "admin")
        # …then disable the second admin (would still leave one).
        ok = client.patch(
            f"/api/v1/admin/users/{second.id}",
            headers=_admin_headers(users_store),
            json={"disabled": True},
        )
        assert ok.status_code == 200
        # Now seed admin is the last active admin. Try to disable them as
        # admin2 (re-enable admin2 first to act as caller).
        users_svc.set_disabled(second.id, False)
        admin2_headers = {"Authorization": f"Bearer {_mint(second.id, 'admin')}"}
        users_svc.set_disabled(second.id, True)
        resp = client.patch(
            f"/api/v1/admin/users/{admin.id}",
            headers=admin2_headers,
            json={"disabled": True},
        )
    assert resp.status_code == 400


def test_admin_users_patch_refuses_self_disable(users_store, auth_env):
    import services.users as users_svc

    with _client(users_store) as client:
        # Make a second admin so the last-admin guard isn't tripped first.
        second = users_svc.create_user("admin2@home.lan", "verylongpassword12", "admin")
        admin2_headers = {"Authorization": f"Bearer {_mint(second.id, 'admin')}"}
        resp = client.patch(
            f"/api/v1/admin/users/{second.id}",
            headers=admin2_headers,
            json={"disabled": True},
        )
    assert resp.status_code == 400
    assert "self-disable" in resp.json()["detail"]


def test_admin_users_patch_requires_at_least_one_field(users_store, auth_env):
    import services.users as users_svc

    with _client(users_store) as client:
        target = users_svc.create_user("eve@home.lan", "verylongpassword12", "user")
        resp = client.patch(
            f"/api/v1/admin/users/{target.id}",
            headers=_admin_headers(users_store),
            json={},
        )
    assert resp.status_code == 400


def test_admin_users_delete_removes_target(users_store, auth_env):
    import services.users as users_svc

    with _client(users_store) as client:
        target = users_svc.create_user("eve@home.lan", "verylongpassword12", "user")
        resp = client.delete(
            f"/api/v1/admin/users/{target.id}",
            headers=_admin_headers(users_store),
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # Stay inside the context — TestClient.__exit__ runs the FastAPI
        # lifespan teardown which calls config.shutdown() and clears the
        # in-memory metadata-store override.
        assert users_svc.get_user_by_id(target.id) is None


def test_admin_users_delete_refuses_last_active_admin(users_store, auth_env):
    """Sole active admin tries to self-delete — last-admin guard fires
    before the self-delete guard (more informative message)."""
    import services.users as users_svc

    with _client(users_store) as client:
        admin = users_svc.get_user_by_email("admin@home.lan")
        assert admin is not None
        resp = client.delete(
            f"/api/v1/admin/users/{admin.id}",
            headers=_admin_headers(users_store),
        )
    assert resp.status_code == 400
    assert "last active admin" in resp.json()["detail"]


def test_admin_users_delete_refuses_self_delete(users_store, auth_env):
    import services.users as users_svc

    with _client(users_store) as client:
        # Need a second admin so the last-admin guard isn't tripped first.
        second = users_svc.create_user("admin2@home.lan", "verylongpassword12", "admin")
        admin2_headers = {"Authorization": f"Bearer {_mint(second.id, 'admin')}"}
        resp = client.delete(
            f"/api/v1/admin/users/{second.id}",
            headers=admin2_headers,
        )
    assert resp.status_code == 400
    assert "self-delete" in resp.json()["detail"]


def test_admin_users_delete_returns_404_for_missing(users_store, auth_env):
    with _client(users_store) as client:
        resp = client.delete(
            "/api/v1/admin/users/does-not-exist",
            headers=_admin_headers(users_store),
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Smoke: dashboard widget HTML
# ---------------------------------------------------------------------------


def test_dashboard_html_includes_auth_widget():
    """Smoke test: the inline widget that fetches /api/v1/auth/me and
    offers a Logout link must be present. Pure file-content check; does
    not boot FastAPI.
    """
    html = (Path(__file__).resolve().parent.parent / "dashboard" / "index.html").read_text(
        encoding="utf-8"
    )
    assert "/api/v1/auth/me" in html, "dashboard widget should fetch /api/v1/auth/me"
    assert "authWidget" in html, "dashboard widget DOM anchor missing"
    assert "lumogisLogout" in html or "/api/v1/auth/logout" in html, (
        "dashboard widget should expose a Logout action"
    )
    assert "Single-user mode" in html, (
        "dashboard widget should render a dev-mode label when AUTH_ENABLED=false"
    )
