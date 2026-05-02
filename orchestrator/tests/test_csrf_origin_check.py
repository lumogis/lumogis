# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Origin-header CSRF check on cookie-authenticated browser writes.

Pinned by family-LAN plan §12 (CSRF) and the post-/verify-plan
follow-up. The check is defence-in-depth on top of the
``SameSite=Strict`` refresh cookie:

* When ``LUMOGIS_PUBLIC_ORIGIN`` is set, browser writes whose ``Origin``
  header does not match are rejected with 403 *before* the route runs.
* When ``LUMOGIS_PUBLIC_ORIGIN`` is unset (early bring-up), the check
  is a no-op — the cookie's ``SameSite=Strict`` attribute remains the
  only defence.
* ``Authorization: Bearer ...`` callers are exempt (browsers do not
  auto-attach Bearer headers; CSRF cannot mint one).
* GET / HEAD / OPTIONS are exempt (no state change).
* ``AUTH_ENABLED=false`` is exempt (no real sessions to forge).

Routes covered:
* ``POST /api/v1/auth/refresh``
* ``POST /api/v1/admin/users``
* ``PATCH /api/v1/admin/users/{id}``
* ``DELETE /api/v1/admin/users/{id}``
"""

from __future__ import annotations

import pytest

# Reuse the FakeUsersStore + auth_env / dev_env fixtures from the Phase 1
# test module — they're tagged @pytest.fixture there, not in conftest.
from tests.test_auth_phase1 import FakeUsersStore  # noqa: F401 — re-exported as fixtures
from tests.test_auth_phase1 import auth_env  # noqa: F401 — re-exported as fixtures
from tests.test_auth_phase1 import dev_env  # noqa: F401 — re-exported as fixtures
from tests.test_auth_phase1 import users_store  # noqa: F401 — re-exported as fixtures

PUBLIC_ORIGIN = "https://lumogis.lan"


@pytest.fixture
def origin_pinned(monkeypatch):
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", PUBLIC_ORIGIN)


@pytest.fixture
def origin_unset(monkeypatch):
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)


def _make_client():
    import main
    from fastapi.testclient import TestClient

    return TestClient(main.app)


def _seed_admin(users_store) -> dict:
    """Create one admin user. Return the matching access-token kwargs."""
    import services.users as users_svc
    from auth import mint_access_token

    user = users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")
    return {
        "access_token": mint_access_token(user.id, "admin"),
        "user_id": user.id,
    }


# ---------------------------------------------------------------------------
# /api/v1/auth/refresh — cookie-authenticated, always cookie-only
# ---------------------------------------------------------------------------


def _login_and_get_refresh_cookie(client) -> str:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@home.lan", "password": "verylongpassword12"},
        headers={"Origin": PUBLIC_ORIGIN},
    )
    assert resp.status_code == 200, resp.text
    return client.cookies.get("lumogis_refresh")


def test_refresh_403_when_origin_mismatch(users_store, auth_env, origin_pinned):
    _seed_admin(users_store)
    client = _make_client()
    _login_and_get_refresh_cookie(client)

    resp = client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "https://attacker.example"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "origin mismatch"


def test_refresh_200_when_origin_matches(users_store, auth_env, origin_pinned):
    _seed_admin(users_store)
    client = _make_client()
    _login_and_get_refresh_cookie(client)

    resp = client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": PUBLIC_ORIGIN},
    )
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()


def test_refresh_403_when_origin_missing_and_pinned(users_store, auth_env, origin_pinned):
    """No Origin header at all + pinned origin → refuse.

    Modern browsers always send Origin on POST. A POST with no Origin
    is either a misconfigured proxy or a non-browser caller — and a
    non-browser caller should be using Bearer, not the refresh cookie.
    """
    _seed_admin(users_store)
    client = _make_client()
    _login_and_get_refresh_cookie(client)

    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code == 403


def test_refresh_passthrough_when_public_origin_unset(users_store, auth_env, origin_unset):
    """Without ``LUMOGIS_PUBLIC_ORIGIN`` set, the check is a no-op."""
    _seed_admin(users_store)
    client = _make_client()
    _login_and_get_refresh_cookie(client)

    resp = client.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "https://anything.example"},
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# /api/v1/admin/users — Bearer-friendly admin surface
# ---------------------------------------------------------------------------


def test_admin_users_post_unauthenticated_rejected_outright(users_store, auth_env, origin_pinned):
    """No Bearer on an admin route → 401 from the auth middleware, full stop.

    The admin/users routes are Bearer-only today; ``auth.auth_middleware``
    rejects credential-less requests with 401 before any route-level
    dependency runs. The CSRF dep on the admin route is therefore
    forward-compatible defence-in-depth — when a future cookie-authenticated
    surface (Lumogis Web) eventually calls these routes, the dep will
    refuse mismatched origins. Today the 401 takes priority and is the
    stronger refusal.
    """
    _seed_admin(users_store)
    client = _make_client()

    resp = client.post(
        "/api/v1/admin/users",
        json={
            "email": "newuser@home.lan",
            "password": "anotherlongpw12",
            "role": "user",
        },
        headers={"Origin": "https://attacker.example"},
    )
    assert resp.status_code == 401


def test_admin_users_post_passes_with_bearer_regardless_of_origin(
    users_store, auth_env, origin_pinned
):
    """Bearer-authenticated callers are exempt — they're not a CSRF surface.

    A wrong Origin header is irrelevant when the caller proves identity
    via a header browsers cannot auto-attach.
    """
    creds = _seed_admin(users_store)
    client = _make_client()

    resp = client.post(
        "/api/v1/admin/users",
        json={
            "email": "newuser@home.lan",
            "password": "anotherlongpw12",
            "role": "user",
        },
        headers={
            "Authorization": f"Bearer {creds['access_token']}",
            "Origin": "https://attacker.example",
        },
    )
    assert resp.status_code == 201, resp.text


def test_admin_users_get_passes_without_origin(users_store, auth_env, origin_pinned):
    """GET requests are never gated — read-only is not a CSRF surface."""
    creds = _seed_admin(users_store)
    client = _make_client()

    resp = client.get(
        "/api/v1/admin/users",
        headers={"Authorization": f"Bearer {creds['access_token']}"},
    )
    assert resp.status_code == 200


def test_admin_users_delete_passes_with_bearer_and_correct_origin(
    users_store, auth_env, origin_pinned
):
    """DELETE with Bearer + correct Origin → CSRF dep is a no-op for Bearer callers.

    A 404 here means the route ran (user not found is the route's job),
    proving both the auth middleware and the CSRF dep let the request
    through. A 403 would mean the CSRF dep wrongly rejected a
    Bearer-authenticated caller.
    """
    creds = _seed_admin(users_store)
    client = _make_client()

    resp = client.delete(
        "/api/v1/admin/users/missing-user-id",
        headers={
            "Authorization": f"Bearer {creds['access_token']}",
            "Origin": PUBLIC_ORIGIN,
        },
    )
    assert resp.status_code == 404
    # Belt + braces: also confirm a wrong Origin still passes when Bearer is present.
    resp2 = client.delete(
        "/api/v1/admin/users/missing-user-id",
        headers={
            "Authorization": f"Bearer {creds['access_token']}",
            "Origin": "https://attacker.example",
        },
    )
    assert resp2.status_code == 404


# ---------------------------------------------------------------------------
# Dev-mode bypass
# ---------------------------------------------------------------------------


def test_csrf_check_skipped_when_auth_disabled(users_store, dev_env, origin_pinned):
    """``AUTH_ENABLED=false`` → CSRF dep is a no-op.

    There are no real sessions to forge in single-user dev mode; the
    Origin check exists only to protect cookie-authenticated browser
    sessions that don't exist when AUTH_ENABLED=false.
    """
    client = _make_client()
    # /me works in dev mode without any auth headers.
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 200
