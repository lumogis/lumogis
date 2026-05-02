# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for the first Lumogis Web slice (`routes/web.py`).

Success criteria for this slice (per family-LAN plan §24):

1. user can log in
2. sees current email + role
3. can call one authenticated endpoint successfully
4. logout works cleanly

These tests prove the **server-side foundations** required to satisfy
those criteria from a real browser:

* The static SPA at ``/web/`` is reachable in dev mode AND family-LAN
  mode (it is in ``_AUTH_BYPASS_PREFIXES``).
* The HTML body contains the wiring for each of the four success
  criteria (login form → ``/api/v1/auth/login``; user-info display →
  ``/api/v1/auth/me``; demo authenticated call → ``/signals``;
  logout → ``/api/v1/auth/logout``).
* End-to-end: a real ``TestClient`` flow performs login → ``/me`` →
  ``/signals`` → ``/logout`` against the fully-wired FastAPI app.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from datetime import timezone

import pytest
from fastapi.testclient import TestClient
from tests.test_auth_phase1 import _csrf_origin_headers

# ---------------------------------------------------------------------------
# Shared fixtures (mirror the patterns in test_auth_phase1.py so this slice
# stays consistent with how the rest of the auth suite stages its world).
# ---------------------------------------------------------------------------


class _FakeUsersStore:
    """Minimal in-memory ``users`` table — same surface the Phase 1 suite uses."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = " ".join(query.split()).lower()
        p = params or ()
        if q.startswith("insert into users"):
            self.rows[p[0]] = {
                "id": p[0],
                "email": p[1],
                "password_hash": p[2],
                "role": p[3],
                "disabled": False,
                "created_at": datetime.now(timezone.utc),
                "last_login_at": None,
                "refresh_token_jti": None,
            }
            return
        if q.startswith("update users set last_login_at = now()"):
            self.rows[p[0]]["last_login_at"] = datetime.now(timezone.utc)
            return
        if q.startswith("update users set refresh_token_jti ="):
            self.rows[p[1]]["refresh_token_jti"] = p[0]
            return

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = " ".join(query.split()).lower()
        p = params or ()
        if q.startswith("select id from users where lower(email) ="):
            target = p[0].lower()
            for row in self.rows.values():
                if row["email"].lower() == target:
                    return {"id": row["id"]}
            return None
        if q.startswith("select * from users where id ="):
            return dict(self.rows.get(p[0])) if p[0] in self.rows else None
        if q.startswith("select * from users where lower(email) ="):
            target = p[0].lower()
            for row in self.rows.values():
                if row["email"].lower() == target:
                    return dict(row)
            return None
        if q.startswith("select count(*) as n from users where role = 'admin'"):
            n = sum(1 for r in self.rows.values() if r["role"] == "admin" and not r["disabled"])
            return {"n": n}
        if q.startswith("select count(*) as n from users"):
            return {"n": len(self.rows)}
        if q.startswith("select refresh_token_jti from users where id ="):
            row = self.rows.get(p[0])
            return {"refresh_token_jti": row["refresh_token_jti"]} if row else None
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        return []

    def close(self) -> None:
        pass


@pytest.fixture
def users_store():
    """Install a fake users store onto the shared config registry."""
    import config as _config

    store = _FakeUsersStore()
    _config._instances["metadata_store"] = store
    yield store
    _config._instances.pop("metadata_store", None)


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-access-secret-do-not-use-in-prod")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "test-refresh-secret-do-not-use-in-prod")
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    yield
    from routes.auth import _reset_rate_limit_for_tests

    _reset_rate_limit_for_tests()


@pytest.fixture
def dev_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    yield


@contextmanager
def _client_with_admin(users_store):
    import main
    import services.users as users_svc

    if users_svc.get_user_by_email("alice@home.lan") is None:
        users_svc.create_user("alice@home.lan", "verylongpassword12", "admin")
    with TestClient(main.app) as client:
        yield client


# ---------------------------------------------------------------------------
# Static-shell route behaviour
# ---------------------------------------------------------------------------


def test_healthz_unauthenticated(dev_env):
    """``GET /healthz`` must stay un-gated — Docker probes cannot send JWT."""
    import main

    with TestClient(main.app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_web_root_redirects_to_trailing_slash(dev_env):
    """``GET /web`` must 307 to ``/web/`` so relative URLs resolve.

    Same convention Starlette uses for directory mounts; the test guards
    against accidentally swapping the order of route declarations in
    ``routes/web.py``.
    """
    import main

    with TestClient(main.app) as client:
        resp = client.get("/web", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/web/"


def test_web_index_served_in_dev_mode(dev_env):
    """In ``AUTH_ENABLED=false`` the SPA is reachable without any token."""
    import main

    with TestClient(main.app) as client:
        resp = client.get("/web/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "Lumogis Web" in body
    assert '<form id="login-form"' in body


def test_web_index_served_in_family_lan_mode_without_bearer(auth_env, users_store):
    """In ``AUTH_ENABLED=true`` the SPA must STILL be reachable without a bearer.

    This is the whole point of adding ``/web`` to ``_AUTH_BYPASS_PREFIXES``:
    a user who hasn't signed in yet has to be able to load the login
    page. If this test ever turns 401, the bypass list regressed.
    """
    import main

    with TestClient(main.app) as client:
        resp = client.get("/web/")
    assert resp.status_code == 200
    assert "Sign in" in resp.text


def test_web_index_contains_all_four_success_criterion_wirings(dev_env):
    """The static HTML must wire every endpoint the four success criteria need."""
    import main

    with TestClient(main.app) as client:
        body = client.get("/web/").text

    # 1. user can log in → POST /api/v1/auth/login
    assert "/api/v1/auth/login" in body
    # 2. sees current email + role → GET /api/v1/auth/me
    assert "/api/v1/auth/me" in body
    # 3. one authenticated endpoint → GET /signals
    assert "/signals" in body
    # 4. logout works cleanly → POST /api/v1/auth/logout
    assert "/api/v1/auth/logout" in body
    # And there must be a logout button to drive (4) from the UI.
    assert 'id="logout-btn"' in body


def test_auth_bypass_includes_web_prefix():
    """Direct unit assertion on the bypass tuple — guards against silent removal."""
    import auth

    assert "/web" in auth._AUTH_BYPASS_PREFIXES


def test_auth_bypass_matching_is_path_segment_safe():
    """``/web`` must NOT also bypass auth on ``/web``-prefixed siblings.

    Naive ``str.startswith("/web")`` would silently exempt ``/webhook``,
    ``/webfoo``, ``/website-data`` from JWT enforcement — a latent
    auth-hole class that would land any time a future route happens to
    share a prefix string with a bypass entry. ``_path_is_bypassed`` is
    the gatekeeper; this test pins down its segment boundary semantics
    so the matching can never silently regress.
    """
    import auth

    # Should bypass — exact match and proper subtree.
    assert auth._path_is_bypassed("/web") is True
    assert auth._path_is_bypassed("/web/") is True
    assert auth._path_is_bypassed("/web/index.html") is True
    assert auth._path_is_bypassed("/web/static/app.js") is True
    assert auth._path_is_bypassed("/healthz") is True
    assert auth._path_is_bypassed("/health") is True
    assert auth._path_is_bypassed("/api/v1/auth/login") is True

    # Must NOT bypass — sibling paths that merely start with the same
    # characters but diverge mid-segment.
    assert auth._path_is_bypassed("/webhook") is False
    assert auth._path_is_bypassed("/webhooks/lumogis") is False
    assert auth._path_is_bypassed("/webfoo") is False
    assert auth._path_is_bypassed("/healthzfoo") is False
    assert auth._path_is_bypassed("/healthy") is False
    assert auth._path_is_bypassed("/api/v1/auth/loginX") is False
    assert auth._path_is_bypassed("/api/v1/auth/me") is False  # /me is gated
    assert auth._path_is_bypassed("/signals") is False


def test_webhook_lookalike_path_returns_401_in_family_lan_mode(auth_env, users_store):
    """End-to-end proof that the segment-safe matcher is wired into the middleware.

    A ``/webhook``-style path (no such route exists today, but might
    tomorrow) must still go through the JWT gate when ``AUTH_ENABLED=true``.
    Without the segment-safe matcher, the middleware would bypass auth
    and let the request reach the handler (or a 404) without ever
    checking the bearer.
    """
    import main

    with TestClient(main.app) as client:
        # No such route exists, so the truthful proof is: the middleware
        # rejects with 401 BEFORE the router gets a chance to 404.
        # If the bypass matcher were broken the reply would be 404 instead.
        resp = client.get("/webhook-does-not-exist")
        assert resp.status_code == 401, (
            f"/webhook-* must NOT be in the auth bypass — got {resp.status_code} "
            f"({resp.text!r}). If this is 404, the segment-safe matcher regressed."
        )


# ---------------------------------------------------------------------------
# End-to-end flow (the whole point — proves the slice works against the real app)
# ---------------------------------------------------------------------------


def test_full_signin_flow_login_me_signals_logout(users_store, auth_env):
    """Walk the four success criteria back-to-back through ``TestClient``.

    This is the integration test that proves an actual browser using the
    SPA's flow can: (1) log in, (2) read its own ``/me``, (3) call an
    authenticated data-plane endpoint, and (4) log out cleanly with the
    server-side refresh jti cleared.
    """
    with _client_with_admin(users_store) as client:
        # Sanity: SPA reachable without auth even in family-LAN mode.
        spa = client.get("/web/")
        assert spa.status_code == 200

        # (1) Login.
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "verylongpassword12"},
        )
        assert login.status_code == 200, login.text
        body = login.json()
        access_token = body["access_token"]
        assert access_token
        assert body["user"]["email"] == "alice@home.lan"
        assert body["user"]["role"] == "admin"
        # Refresh cookie set on the response so the browser holds it.
        assert "lumogis_refresh" in login.cookies

        bearer = {"Authorization": f"Bearer {access_token}"}

        # (2) `/me` returns the same identity the login response advertised.
        me = client.get("/api/v1/auth/me", headers=bearer)
        assert me.status_code == 200
        me_body = me.json()
        assert me_body["email"] == "alice@home.lan"
        assert me_body["role"] == "admin"
        assert me_body["id"] == body["user"]["id"]

        # (3) An authenticated data-plane endpoint accepts the bearer.
        # `/signals` is `require_user`-gated — the demo target the SPA hits.
        # The DB-less FakeUsersStore means the actual signals query falls
        # through to the route's "DB query failed → empty list" branch,
        # which returns 200 with a deterministic shape. We assert auth
        # acceptance, not query semantics.
        signals = client.get("/signals?limit=5", headers=bearer)
        assert signals.status_code == 200, signals.text
        sig_body = signals.json()
        assert "signals" in sig_body
        assert "total" in sig_body

        # (3b) Without the bearer, `/signals` is rejected — proves the
        # gate is real, not just incidental.
        signals_no_bearer = client.get("/signals?limit=5")
        assert signals_no_bearer.status_code == 401

        # Confirm the refresh jti is currently set server-side (login wrote it).
        import services.users as users_svc

        user_id = body["user"]["id"]
        assert users_svc.get_refresh_jti(user_id) is not None

        # (4) Logout — must clear server-side jti AND expire the cookie.
        logout = client.post("/api/v1/auth/logout")
        assert logout.status_code == 200
        assert logout.json() == {"ok": True}
        assert users_svc.get_refresh_jti(user_id) is None
        # Set-Cookie should expire the refresh cookie (max-age=0).
        set_cookie = logout.headers.get("set-cookie", "")
        assert "lumogis_refresh=" in set_cookie
        assert "Max-Age=0" in set_cookie or "max-age=0" in set_cookie.lower()

        # (4b) After logout, the previous refresh cookie no longer rotates.
        # (Login also rewrote the jti to a new value; logout cleared it to
        # NULL, so any refresh attempt now correctly fails.)
        refresh_after_logout = client.post(
            "/api/v1/auth/refresh",
            headers=_csrf_origin_headers(),
        )
        assert refresh_after_logout.status_code == 401


def test_full_signin_flow_in_dev_mode_skips_login(dev_env):
    """In `AUTH_ENABLED=false`, `/me` returns the synth dev admin without a bearer.

    The SPA's boot flow detects this case (it gets a 200 from `/me` even
    though `sessionStorage` is empty) and renders the user view directly
    — proving the dev-mode UX requires no special path.
    """
    import main

    with TestClient(main.app) as client:
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["role"] == "admin"
        assert me.json()["email"] == "dev@local.lan"

        # Login is intentionally 503 in dev mode — the SPA falls back to
        # the dev-mode view rather than treating this as an error.
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "x@x.lan", "password": "verylongpassword12"},
        )
        assert login.status_code == 503
