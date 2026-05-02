# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase 1 (auth foundation) tests for family-LAN multi-user.

Covers:

* :class:`UserContext` role parsing (dev mode = admin; AUTH_ENABLED=true
  reads the JWT ``role`` claim; missing claim defaults to ``user``;
  invalid role -> 401).
* JWT round-trip (mint + verify) for both access and refresh tokens.
* :func:`services.users.bootstrap_if_empty` semantics.
* :func:`main._enforce_auth_consistency` refusal modes.
* ``/api/v1/auth/login`` end-to-end with rate limiting and timing-attack
  floor on the unknown-email path.
* ``/api/v1/auth/refresh`` rotation + jti eviction.
* ``/api/v1/auth/logout`` clears server-side jti.
* ``/api/v1/auth/me`` for both dev mode and authenticated mode.
"""

from __future__ import annotations

import datetime as _dt
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _csrf_origin_headers() -> dict[str, str]:
    """Match browser behaviour when LUMOGIS_PUBLIC_ORIGIN is set (Caddy/compose)."""
    o = os.environ.get("LUMOGIS_PUBLIC_ORIGIN", "").strip()
    return {"Origin": o} if o else {}


# ---------------------------------------------------------------------------
# Lightweight in-memory users-table mock
# ---------------------------------------------------------------------------


class FakeUsersStore:
    """Tiny in-memory MetadataStore that knows just enough about ``users``."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.exec_log: list[tuple[str, tuple]] = []

    def ping(self) -> bool:
        return True

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.exec_log.append((query, params or ()))
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
        if q.startswith("update users set role ="):
            self.rows[p[1]]["role"] = p[0]
            return
        if q.startswith("update users set disabled = true, refresh_token_jti = null"):
            self.rows[p[0]]["disabled"] = True
            self.rows[p[0]]["refresh_token_jti"] = None
            return
        if q.startswith("update users set disabled = false where id ="):
            self.rows[p[0]]["disabled"] = False
            return
        if q.startswith("update users set last_login_at = now()"):
            self.rows[p[0]]["last_login_at"] = datetime.now(timezone.utc)
            return
        if q.startswith("update users set refresh_token_jti ="):
            self.rows[p[1]]["refresh_token_jti"] = p[0]
            return
        if q.startswith("update users set password_hash ="):
            self.rows[p[1]]["password_hash"] = p[0]
            self.rows[p[1]]["refresh_token_jti"] = None
            return
        if q.startswith("delete from users where id ="):
            self.rows.pop(p[0], None)
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
        if q.startswith("select id from users where role = 'admin'"):
            admins = sorted(
                (r for r in self.rows.values() if r["role"] == "admin" and not r["disabled"]),
                key=lambda r: r["created_at"],
            )
            return {"id": admins[0]["id"]} if admins else None
        if q.startswith("select refresh_token_jti from users where id ="):
            row = self.rows.get(p[0])
            return {"refresh_token_jti": row["refresh_token_jti"]} if row else None
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = " ".join(query.split()).lower()
        if q.startswith("select * from users order by created_at"):
            return sorted((dict(r) for r in self.rows.values()), key=lambda r: r["created_at"])
        return []

    def close(self) -> None:
        pass

    def transaction(self):
        # Plan ``mcp_token_user_map`` D7 made ``services.users.set_disabled``
        # wrap the user UPDATE + ``mcp_tokens.cascade_revoke_for_user`` in a
        # ``ms.transaction()`` block so partial failures cannot leave a
        # disabled user with live MCP bearers. The fake doesn't model
        # rollback (every ``execute`` mutation lands in ``self.rows``
        # immediately), so the transaction is a no-op CM. The cascade
        # ``UPDATE mcp_tokens … RETURNING *`` falls through to the default
        # ``fetch_all → []`` branch, which is the correct shape (no MCP
        # tokens exist in this fake's universe).
        from contextlib import contextmanager

        @contextmanager
        def _noop():
            yield

        return _noop()


@pytest.fixture
def users_store(monkeypatch):
    import config as _config

    store = FakeUsersStore()
    _config._instances["metadata_store"] = store
    yield store
    _config._instances.pop("metadata_store", None)


@pytest.fixture
def auth_env(monkeypatch):
    """Family-LAN mode with deterministic secrets and short TTLs."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-access-secret-do-not-use-in-prod")
    monkeypatch.setenv("LUMOGIS_JWT_REFRESH_SECRET", "test-refresh-secret-do-not-use-in-prod")
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    # Real (deterministic) Fernet key so `_enforce_auth_consistency`'s
    # post-AUTH_SECRET LUMOGIS_CREDENTIAL_KEY[S] gate passes for every
    # AUTH_ENABLED=true test in this file. The placeholder-refusal
    # tests below override this with monkeypatch on a per-test basis.
    # Generated once with `Fernet.generate_key().decode()` and pinned;
    # the value is not security-sensitive (test-only).
    monkeypatch.setenv(
        "LUMOGIS_CREDENTIAL_KEY",
        "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A=",
    )
    yield
    # Reset rate limit state between tests so the per-IP bucket from one
    # test doesn't bleed into the next.
    from routes.auth import _reset_rate_limit_for_tests
    _reset_rate_limit_for_tests()


@pytest.fixture
def dev_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    yield


# ---------------------------------------------------------------------------
# Unit: UserContext / role / JWT round-trip
# ---------------------------------------------------------------------------


def test_user_context_default_is_admin_in_dev_mode(dev_env):
    import auth
    ctx = auth.UserContext()
    assert ctx.user_id == "default"
    assert ctx.role == "admin"
    assert ctx.is_authenticated is False


def test_mint_and_verify_access_token_round_trip(auth_env):
    import auth
    token = auth.mint_access_token("user-abc", "admin")
    payload = auth.verify_token(token)
    assert payload is not None
    assert payload["sub"] == "user-abc"
    assert payload["role"] == "admin"
    assert payload["exp"] > payload["iat"]


def test_mint_and_verify_refresh_token_round_trip(auth_env):
    import auth
    jti = uuid.uuid4().hex
    token = auth.mint_refresh_token("user-abc", jti)
    payload = auth.verify_refresh_token(token)
    assert payload is not None
    assert payload["sub"] == "user-abc"
    assert payload["jti"] == jti


def test_verify_token_returns_none_on_bad_signature(auth_env, monkeypatch):
    import auth
    token = auth.mint_access_token("user-abc", "admin")
    monkeypatch.setenv("AUTH_SECRET", "different-secret")
    assert auth.verify_token(token) is None


def test_invalid_role_in_jwt_returns_401(auth_env, users_store):
    import jwt
    bad = jwt.encode(
        {"sub": "alice", "role": "superuser", "iat": 0, "exp": int(time.time()) + 60},
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )
    with _client_with_admin(users_store) as client:
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad}"})
    assert resp.status_code == 401


def test_missing_role_claim_defaults_to_user(auth_env, users_store):
    """Legacy access tokens without a ``role`` claim must be honoured as ``user``."""
    import jwt
    import services.users as users_svc
    user = users_svc.create_user("alice@home.lan", "verylongpassword12", "user")
    legacy = jwt.encode(
        {"sub": user.id, "iat": 0, "exp": int(time.time()) + 60},
        os.environ["AUTH_SECRET"],
        algorithm="HS256",
    )
    import main
    with TestClient(main.app) as client:
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {legacy}"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "user"


# ---------------------------------------------------------------------------
# Unit: services.users
# ---------------------------------------------------------------------------


def test_create_user_password_hash_round_trip(users_store):
    import services.users as users_svc
    u = users_svc.create_user("alice@home.lan", "verylongpassword12", "user")
    assert u.email == "alice@home.lan"
    assert u.role == "user"
    assert u.password_hash.startswith("$argon2")

    found = users_svc.verify_credentials("alice@home.lan", "verylongpassword12")
    assert found is not None and found.id == u.id

    assert users_svc.verify_credentials("alice@home.lan", "wrongpassword") is None


def test_create_user_duplicate_email_raises(users_store):
    import services.users as users_svc
    users_svc.create_user("bob@home.lan", "verylongpassword12", "user")
    with pytest.raises(ValueError):
        users_svc.create_user("bob@home.lan", "anotherlongpassword", "user")


def test_count_users_and_count_admins(users_store):
    import services.users as users_svc
    assert users_svc.count_users() == 0
    users_svc.create_user("a@home.lan", "verylongpassword12", "admin")
    users_svc.create_user("u@home.lan", "verylongpassword12", "user")
    assert users_svc.count_users() == 2
    assert users_svc.count_admins() == 1


def test_set_disabled_clears_refresh_jti(users_store):
    import services.users as users_svc
    u = users_svc.create_user("alice@home.lan", "verylongpassword12", "admin")
    users_svc.set_refresh_jti(u.id, "abc123")
    assert users_svc.get_refresh_jti(u.id) == "abc123"
    users_svc.set_disabled(u.id, True)
    assert users_svc.get_refresh_jti(u.id) is None
    refreshed = users_svc.get_user_by_id(u.id)
    assert refreshed is not None and refreshed.disabled is True


def test_verify_credentials_disabled_user_returns_none(users_store):
    import services.users as users_svc
    u = users_svc.create_user("alice@home.lan", "verylongpassword12", "user")
    users_svc.set_disabled(u.id, True)
    assert users_svc.verify_credentials("alice@home.lan", "verylongpassword12") is None


def test_bootstrap_if_empty_creates_admin_when_env_set(users_store, monkeypatch):
    import services.users as users_svc
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "admin@home.lan")
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD", "verylongpassword12")
    admin = users_svc.bootstrap_if_empty()
    assert admin is not None and admin.role == "admin"
    assert users_svc.count_users() == 1


def test_bootstrap_if_empty_noop_when_users_exist(users_store, monkeypatch):
    import services.users as users_svc
    users_svc.create_user("existing@home.lan", "verylongpassword12", "admin")
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "admin@home.lan")
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD", "verylongpassword12")
    assert users_svc.bootstrap_if_empty() is None
    assert users_svc.count_users() == 1


def test_bootstrap_if_empty_refuses_short_password(users_store, monkeypatch):
    import services.users as users_svc
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "admin@home.lan")
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD", "short")
    assert users_svc.bootstrap_if_empty() is None
    assert users_svc.count_users() == 0


# ---------------------------------------------------------------------------
# Unit: lifespan refusal gates
# ---------------------------------------------------------------------------


@pytest.fixture
def no_skip_consistency(monkeypatch):
    """Disable the conftest-wide test escape hatch so we can assert the gate."""
    monkeypatch.setenv(
        "_LUMOGIS_TEST_SKIP_AUTH_CONSISTENCY_DO_NOT_SET_IN_PRODUCTION",
        "false",
    )


def test_enforce_auth_consistency_refuses_dev_mode_with_multiple_users(
    users_store, dev_env, no_skip_consistency
):
    import services.users as users_svc
    import main
    users_svc.create_user("a@home.lan", "verylongpassword12", "admin")
    users_svc.create_user("b@home.lan", "verylongpassword12", "user")
    with pytest.raises(RuntimeError, match="AUTH_ENABLED=false"):
        main._enforce_auth_consistency()


def test_enforce_auth_consistency_refuses_true_mode_with_no_users(
    users_store, auth_env, no_skip_consistency
):
    import main
    with pytest.raises(RuntimeError, match="AUTH_ENABLED=true"):
        main._enforce_auth_consistency()


def test_enforce_auth_consistency_passes_dev_mode_one_user(
    users_store, dev_env, no_skip_consistency
):
    import services.users as users_svc
    import main
    users_svc.create_user("a@home.lan", "verylongpassword12", "admin")
    main._enforce_auth_consistency()


def test_enforce_auth_consistency_passes_true_mode_with_admin(
    users_store, auth_env, no_skip_consistency
):
    import services.users as users_svc
    import main
    users_svc.create_user("a@home.lan", "verylongpassword12", "admin")
    main._enforce_auth_consistency()


@pytest.mark.parametrize(
    "secret_value",
    ["", "   ", "change-me-in-production", "__GENERATE_ME__"],
)
def test_enforce_auth_consistency_refuses_placeholder_auth_secret(
    users_store, auth_env, no_skip_consistency, monkeypatch, secret_value
):
    """AUTH_ENABLED=true + placeholder/empty AUTH_SECRET → refuse to boot.

    Post-/verify-plan hardening: tokens minted with `change-me-in-production`
    would be trivially forgeable across every Lumogis install on the planet.
    The lifespan gate must catch this *before* uvicorn starts serving traffic
    even if the operator has otherwise seeded a real admin user.
    """
    import services.users as users_svc
    import main
    users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")
    monkeypatch.setenv("AUTH_SECRET", secret_value)
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        main._enforce_auth_consistency()


def test_enforce_auth_consistency_accepts_real_auth_secret(
    users_store, auth_env, no_skip_consistency
):
    """Same setup as above but with a real-looking secret → boots cleanly.

    `auth_env` already sets AUTH_SECRET to a non-placeholder value; this test
    pins that contract so a future change to `auth_env` cannot silently make
    the placeholder-refusal test above pass for the wrong reason.
    """
    import services.users as users_svc
    import main
    users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")
    main._enforce_auth_consistency()


def test_enforce_auth_consistency_ignores_auth_secret_in_dev_mode(
    users_store, dev_env, no_skip_consistency, monkeypatch
):
    """AUTH_ENABLED=false → AUTH_SECRET is not consulted (no JWTs are minted)."""
    import services.users as users_svc
    import main
    users_svc.create_user("solo@home.lan", "verylongpassword12", "admin")
    monkeypatch.setenv("AUTH_SECRET", "change-me-in-production")
    main._enforce_auth_consistency()


# ---------------------------------------------------------------------------
# Unit: lifespan refusal gate — LUMOGIS_CREDENTIAL_KEY[S]
#
# Mirrors the AUTH_SECRET block above. Validates that
# `_enforce_auth_consistency()` refuses to boot when the per-user connector
# credential subsystem has no usable Fernet key under AUTH_ENABLED=true,
# and is silent in dev mode. See per_user_connector_credentials.plan.md
# §Modified files → main.py.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key_value",
    ["", "   ", "change-me-in-production", "__GENERATE_ME__"],
)
def test_enforce_auth_consistency_refuses_placeholder_credential_key(
    users_store, auth_env, no_skip_consistency, monkeypatch, key_value
):
    """AUTH_ENABLED=true + placeholder/empty LUMOGIS_CREDENTIAL_KEY[S] → refuse to boot.

    Per-user connector credentials sealed with a placeholder key would either
    be unrecoverable (decrypt fails on first GET because the placeholder
    isn't a valid Fernet key) or — worse — would all be encrypted with the
    SAME widely-known string across every Lumogis install. The lifespan gate
    must catch this before uvicorn starts serving traffic.
    """
    import services.users as users_svc
    import main
    users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", key_value)
    with pytest.raises(RuntimeError, match="LUMOGIS_CREDENTIAL_KEY"):
        main._enforce_auth_consistency()


def test_enforce_auth_consistency_refuses_when_both_credential_envs_unset(
    users_store, auth_env, no_skip_consistency, monkeypatch
):
    """Neither LUMOGIS_CREDENTIAL_KEY nor LUMOGIS_CREDENTIAL_KEYS set → refuse.

    Distinct from the placeholder-string case: this asserts the *unset* path
    (both env vars deleted) also refuses, mirroring how the entrypoint's
    `${LUMOGIS_CREDENTIAL_KEYS:-${LUMOGIS_CREDENTIAL_KEY:-}}` substitution
    falls back to empty when both are missing.
    """
    import services.users as users_svc
    import main
    users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LUMOGIS_CREDENTIAL_KEY"):
        main._enforce_auth_consistency()


def test_enforce_auth_consistency_accepts_keys_csv_when_single_key_unset(
    users_store, auth_env, no_skip_consistency, monkeypatch
):
    """LUMOGIS_CREDENTIAL_KEYS (CSV) honoured even when LUMOGIS_CREDENTIAL_KEY unset.

    Mirrors `_load_keys()`'s precedence rule: KEYS overrides KEY when set.
    A common rotation midpoint will have KEY unset (or stale) and KEYS
    holding the new+old pair — that must boot cleanly.
    """
    import services.users as users_svc
    import main
    users_svc.create_user("admin@home.lan", "verylongpassword12", "admin")
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEY", raising=False)
    monkeypatch.setenv(
        "LUMOGIS_CREDENTIAL_KEYS",
        "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A=,"
        "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A=",
    )
    main._enforce_auth_consistency()


def test_enforce_auth_consistency_ignores_credential_key_in_dev_mode(
    users_store, dev_env, no_skip_consistency, monkeypatch
):
    """AUTH_ENABLED=false → LUMOGIS_CREDENTIAL_KEY[S] not consulted.

    Single-user dev installs are allowed to ship without per-user crypto
    configured at all; the credential subsystem will raise on first request
    if the operator actually tries to use it (route returns 503), but boot
    must not refuse.
    """
    import services.users as users_svc
    import main
    users_svc.create_user("solo@home.lan", "verylongpassword12", "admin")
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", "change-me-in-production")
    main._enforce_auth_consistency()


def test_skip_consistency_env_var_bypasses_gate(users_store, auth_env, monkeypatch):
    """Test escape hatch must work — empty users table, AUTH_ENABLED=true,
    escape-hatch env var set → the gate is a no-op."""
    monkeypatch.setenv(
        "_LUMOGIS_TEST_SKIP_AUTH_CONSISTENCY_DO_NOT_SET_IN_PRODUCTION",
        "true",
    )
    import main
    main._enforce_auth_consistency()


# ---------------------------------------------------------------------------
# Integration: /api/v1/auth/* endpoints
# ---------------------------------------------------------------------------


@contextmanager
def _client_with_admin(users_store):
    import services.users as users_svc
    import main
    if users_svc.get_user_by_email("alice@home.lan") is None:
        users_svc.create_user("alice@home.lan", "verylongpassword12", "admin")
    with TestClient(main.app) as client:
        yield client


def test_login_returns_access_token_and_sets_refresh_cookie(users_store, auth_env):
    with _client_with_admin(users_store) as client:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "verylongpassword12"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] == 900
    assert body["user"]["role"] == "admin"
    cookie_jar = resp.cookies
    assert "lumogis_refresh" in cookie_jar


def test_login_bad_password_returns_401(users_store, auth_env):
    with _client_with_admin(users_store) as client:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "wronglongpassword"},
        )
    assert resp.status_code == 401


def test_login_unknown_email_returns_401_same_shape(users_store, auth_env):
    with _client_with_admin(users_store) as client:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "ghost@home.lan", "password": "verylongpassword12"},
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid credentials"


def test_login_disabled_user_returns_401_same_as_unknown(users_store, auth_env):
    import services.users as users_svc
    user = users_svc.create_user("alice@home.lan", "verylongpassword12", "admin")
    users_svc.set_disabled(user.id, True)
    import main
    with TestClient(main.app) as client:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "verylongpassword12"},
        )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid credentials"


def test_login_unknown_email_takes_at_least_argon2_floor(users_store, auth_env):
    """Timing-attack floor: unknown-email path takes within an order of
    magnitude of the known-email path. Direct service call avoids per-test
    HTTP overhead (which dominates argon2 cost on fast hosts).
    """
    import services.users as users_svc
    users_svc.create_user("alice@home.lan", "verylongpassword12", "admin")

    samples = 5

    def _time_calls(email: str, password: str) -> float:
        total = 0.0
        for _ in range(samples):
            t0 = time.monotonic()
            users_svc.verify_credentials(email, password)
            total += time.monotonic() - t0
        return total / samples

    known_avg = _time_calls("alice@home.lan", "wronglongpassword")
    unknown_avg = _time_calls("ghost@home.lan", "wronglongpassword")

    # Both paths must hit argon2; ratios within 5x are acceptable. A bug
    # that returns instantly on unknown-email shows up as ratio >> 100.
    assert unknown_avg >= 0.001, (
        f"unknown-email path is suspiciously fast ({unknown_avg*1000:.2f} ms)"
    )
    ratio = max(known_avg, unknown_avg) / max(min(known_avg, unknown_avg), 1e-6)
    assert ratio < 5.0, (
        f"timing diverges between known/unknown email paths "
        f"(known={known_avg*1000:.2f} ms, unknown={unknown_avg*1000:.2f} ms, ratio={ratio:.1f})"
    )


def test_login_short_password_returns_422(users_store, auth_env):
    with _client_with_admin(users_store) as client:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "short"},
        )
    assert resp.status_code == 422


def test_login_rate_limit_per_ip(users_store, auth_env):
    with _client_with_admin(users_store) as client:
        for _ in range(5):
            r = client.post(
                "/api/v1/auth/login",
                json={"email": "alice@home.lan", "password": "wronglongpassword"},
            )
            assert r.status_code == 401
        # Sixth attempt — bucket full.
        r = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "wronglongpassword"},
        )
        assert r.status_code == 429
        assert r.headers.get("Retry-After") == "60"


def test_login_in_dev_mode_returns_503(users_store, dev_env):
    import main
    with TestClient(main.app) as client:
        resp = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "verylongpassword12"},
        )
    assert resp.status_code == 503


def test_me_in_dev_mode_returns_synthesised_admin(users_store, dev_env):
    import main
    with TestClient(main.app) as client:
        resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "admin"
    assert body["id"] == "default"


def test_me_with_bearer_returns_user(users_store, auth_env):
    with _client_with_admin(users_store) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "verylongpassword12"},
        )
        token = login.json()["access_token"]
        resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "alice@home.lan"
    assert body["role"] == "admin"


def test_me_without_bearer_in_true_mode_returns_401(users_store, auth_env):
    with _client_with_admin(users_store) as client:
        resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_refresh_rotates_jti_and_evicts_old_cookie(users_store, auth_env):
    with _client_with_admin(users_store) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "verylongpassword12"},
        )
        first_cookie = login.cookies.get("lumogis_refresh")
        assert first_cookie

        client.cookies.clear()
        r1 = client.post(
            "/api/v1/auth/refresh",
            cookies={"lumogis_refresh": first_cookie},
            headers=_csrf_origin_headers(),
        )
        assert r1.status_code == 200, r1.text
        rotated = r1.cookies.get("lumogis_refresh")
        assert rotated and rotated != first_cookie

        # Replaying the FIRST cookie now must fail (single-active-jti).
        client.cookies.clear()
        r2 = client.post(
            "/api/v1/auth/refresh",
            cookies={"lumogis_refresh": first_cookie},
            headers=_csrf_origin_headers(),
        )
        assert r2.status_code == 401


def test_refresh_with_disabled_user_returns_401(users_store, auth_env):
    import services.users as users_svc
    with _client_with_admin(users_store) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "verylongpassword12"},
        )
        cookie = login.cookies.get("lumogis_refresh")

        user = users_svc.get_user_by_email("alice@home.lan")
        assert user is not None
        users_svc.set_disabled(user.id, True)

        client.cookies.clear()
        resp = client.post(
            "/api/v1/auth/refresh",
            cookies={"lumogis_refresh": cookie},
            headers=_csrf_origin_headers(),
        )
    assert resp.status_code == 401


def test_refresh_without_cookie_returns_401(users_store, auth_env):
    with _client_with_admin(users_store) as client:
        resp = client.post("/api/v1/auth/refresh", headers=_csrf_origin_headers())
    assert resp.status_code == 401


def test_logout_clears_users_refresh_token_jti(users_store, auth_env):
    import services.users as users_svc
    with _client_with_admin(users_store) as client:
        client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "verylongpassword12"},
        )
        user = users_svc.get_user_by_email("alice@home.lan")
        assert user is not None
        assert users_svc.get_refresh_jti(user.id) is not None
        resp = client.post("/api/v1/auth/logout")
        assert resp.status_code == 200
        assert users_svc.get_refresh_jti(user.id) is None


def test_login_evicts_previous_refresh_jti(users_store, auth_env):
    """Single-active-session: re-login on a different device evicts the first.

    Stops at the eviction assertion. Whether the second cookie still works
    is covered by :func:`test_refresh_rotates_jti_and_evicts_old_cookie`.
    """
    with _client_with_admin(users_store) as client:
        first = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "verylongpassword12"},
        )
        first_cookie = first.cookies.get("lumogis_refresh")
        assert first_cookie

        second = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@home.lan", "password": "verylongpassword12"},
        )
        assert second.status_code == 200
        second_cookie = second.cookies.get("lumogis_refresh")
        assert second_cookie and second_cookie != first_cookie

        client.cookies.clear()
        evict = client.post(
            "/api/v1/auth/refresh",
            cookies={"lumogis_refresh": first_cookie},
            headers=_csrf_origin_headers(),
        )
        assert evict.status_code == 401
