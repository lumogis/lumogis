# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route-layer tests for the per-user MCP token surface.

Pins the HTTP contracts in plan ``mcp_token_user_map`` §"Route tests":

* ``GET / POST / DELETE /api/v1/me/mcp-tokens`` — caller-scoped CRUD
  (D12) including the 404-not-403 information-leak guard on the
  cross-user DELETE path.
* ``MintMcpTokenRequest.model_config = ConfigDict(extra='forbid')``
  rejects ``expires_at`` with HTTP 422 (D4 / D16).
* Plaintext bearer is returned exactly once at mint time; subsequent
  ``GET`` responses MUST NOT carry the plaintext or the SHA-256 hash
  or the lookup prefix (D15).
* Admin endpoints under ``/api/v1/admin/users/{user_id}/mcp-tokens``
  enforce ``require_admin`` and refuse ``user_id``s that don't exist.
* CSRF / Bearer interaction — Bearer-authenticated mutating calls
  bypass :func:`csrf.require_same_origin` by design (D11). This is a
  regression pin so the contract isn't accidentally tightened before
  the cookie-session work lands.

Service-level behaviour (mint format, hash, throttle, audit) lives in
:mod:`tests.test_mcp_tokens`. This module focuses strictly on the wire
boundary.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import jwt
import pytest
from fastapi.testclient import TestClient

from tests.test_auth_phase1 import FakeUsersStore  # noqa: E402


# ---------------------------------------------------------------------------
# Composite store: users CRUD (FakeUsersStore) + mcp_tokens CRUD +
# audit_log INSERTs. Kept in this module rather than promoted to a
# shared fixture because the route tests are the ONLY place we need
# both surfaces simultaneously.
# ---------------------------------------------------------------------------


class _RoutesFakeStore(FakeUsersStore):
    """``FakeUsersStore`` + the SQL surface ``services.mcp_tokens`` issues.

    Mirrors the audit + token shapes from ``tests.test_mcp_tokens._FakeStore``
    (we deliberately do NOT subclass it — the users surface is the
    primary base, mcp_tokens is a smaller add-on).
    """

    def __init__(self) -> None:
        super().__init__()
        self.tokens: dict[str, dict] = {}
        self.audit: list[dict] = []

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    def transaction(self):
        from contextlib import contextmanager

        @contextmanager
        def _noop():
            yield

        return _noop()

    # --- execute --------------------------------------------------------

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = self._norm(query)
        p = params or ()

        if q.startswith("insert into mcp_tokens"):
            token_id, user_id, token_prefix, token_hash, label, scopes = p
            for row in self.tokens.values():
                if (
                    row["revoked_at"] is None
                    and row["token_prefix"] == token_prefix
                ):
                    raise RuntimeError(
                        "duplicate key value violates unique constraint "
                        "mcp_tokens_active_prefix_uniq"
                    )
            self.tokens[token_id] = {
                "id": token_id,
                "user_id": user_id,
                "token_prefix": token_prefix,
                "token_hash": token_hash,
                "label": label,
                "scopes": scopes,
                "created_at": datetime.now(timezone.utc),
                "last_used_at": None,
                "expires_at": None,
                "revoked_at": None,
            }
            return

        if q.startswith(
            "update mcp_tokens set revoked_at = now() where id = %s "
            "and revoked_at is null"
        ):
            (tid,) = p
            row = self.tokens.get(tid)
            if row is not None and row["revoked_at"] is None:
                row["revoked_at"] = datetime.now(timezone.utc)
            return

        if q.startswith(
            "update mcp_tokens set last_used_at = now() where id = %s"
        ):
            (tid,) = p
            row = self.tokens.get(tid)
            if row is not None:
                row["last_used_at"] = datetime.now(timezone.utc)
            return

        return super().execute(query, params)

    # --- fetch_one ------------------------------------------------------

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()

        if q.startswith("select * from mcp_tokens where id = %s"):
            (tid,) = p
            row = self.tokens.get(tid)
            return dict(row) if row else None

        if q.startswith(
            "select * from mcp_tokens where token_prefix = %s "
            "and revoked_at is null"
        ):
            (prefix,) = p
            for row in self.tokens.values():
                if row["token_prefix"] == prefix and row["revoked_at"] is None:
                    return dict(row)
            return None

        if q.startswith("insert into audit_log"):
            row_id = len(self.audit) + 1
            self.audit.append({
                "id": row_id,
                "user_id": p[0],
                "action_name": p[1],
                "connector": p[2],
                "mode": p[3],
                "input_summary": p[4],
                "result_summary": p[5],
            })
            return {"id": row_id}

        return super().fetch_one(query, params)

    # --- fetch_all ------------------------------------------------------

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = self._norm(query)
        p = params or ()

        if q.startswith(
            "update mcp_tokens set revoked_at = now() where user_id = %s "
            "and revoked_at is null returning *"
        ):
            (uid,) = p
            now = datetime.now(timezone.utc)
            updated: list[dict] = []
            for row in self.tokens.values():
                if row["user_id"] == uid and row["revoked_at"] is None:
                    row["revoked_at"] = now
                    updated.append(dict(row))
            return updated

        if q.startswith(
            "select * from mcp_tokens where user_id = %s "
            "and revoked_at is null"
        ):
            (uid,) = p
            return sorted(
                (dict(r) for r in self.tokens.values()
                 if r["user_id"] == uid and r["revoked_at"] is None),
                key=lambda r: r["created_at"], reverse=True,
            )

        if q.startswith(
            "select * from mcp_tokens where user_id = %s order by created_at"
        ):
            (uid,) = p
            return sorted(
                (dict(r) for r in self.tokens.values() if r["user_id"] == uid),
                key=lambda r: r["created_at"], reverse=True,
            )

        return super().fetch_all(query, params)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(monkeypatch):
    """Install a composite store as the metadata-store singleton.

    Also wipes the per-process ``_LAST_STAMP_CACHE`` so test ordering
    doesn't leak through the throttle window (D5).
    """
    import config as _config
    from services import mcp_tokens as _mcp_tokens

    s = _RoutesFakeStore()
    _config._instances["metadata_store"] = s
    _mcp_tokens._LAST_STAMP_CACHE.clear()
    yield s
    _config._instances.pop("metadata_store", None)
    _mcp_tokens._LAST_STAMP_CACHE.clear()


@pytest.fixture
def dev_env(monkeypatch):
    """``AUTH_ENABLED=false`` — admin/user gates are no-ops; caller is ``default``."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    yield


@pytest.fixture
def auth_env(monkeypatch):
    """``AUTH_ENABLED=true`` with deterministic JWT secrets."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-mcp-token-routes-access-secret")
    monkeypatch.setenv(
        "LUMOGIS_JWT_REFRESH_SECRET",
        "test-mcp-token-routes-refresh-secret",
    )
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    yield
    from routes.auth import _reset_rate_limit_for_tests
    _reset_rate_limit_for_tests()


def _mint_jwt(user_id: str, role: str) -> str:
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
def _client():
    """Boot the live FastAPI app inside a TestClient (lifespan executes)."""
    import main
    with TestClient(main.app) as client:
        yield client


def _seed(store, *, email: str, role: str) -> str:
    """Create a user via the real service and return their id."""
    import services.users as users_svc
    if users_svc.get_user_by_email(email) is None:
        users_svc.create_user(email, "verylongpassword12", role)
    user = users_svc.get_user_by_email(email)
    assert user is not None
    return user.id


# ---------------------------------------------------------------------------
# Mint
# ---------------------------------------------------------------------------


def test_me_mint_returns_plaintext_exactly_once_in_dev_mode(store, dev_env):
    """201 + body carries plaintext + token public projection."""
    with _client() as client:
        resp = client.post(
            "/api/v1/me/mcp-tokens",
            json={"label": "Claude Desktop"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "token" in body and "plaintext" in body
    assert body["plaintext"].startswith("lmcp_")
    assert len(body["plaintext"]) == 50
    assert body["token"]["label"] == "Claude Desktop"
    assert body["token"]["revoked_at"] is None
    # D15: never leak hash / prefix on the wire.
    assert "token_hash" not in body["token"]
    assert "token_prefix" not in body["token"]


def test_me_mint_rejects_expires_at_with_422(store, dev_env):
    """D4 / D16: ``MintMcpTokenRequest`` is ``extra='forbid'``."""
    with _client() as client:
        resp = client.post(
            "/api/v1/me/mcp-tokens",
            json={
                "label": "lbl",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )
    assert resp.status_code == 422, resp.text


def test_me_mint_requires_label(store, dev_env):
    with _client() as client:
        resp = client.post("/api/v1/me/mcp-tokens", json={})
    assert resp.status_code == 422


def test_me_mint_label_length_capped_at_64(store, dev_env):
    with _client() as client:
        resp = client.post(
            "/api/v1/me/mcp-tokens",
            json={"label": "x" * 65},
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_me_list_returns_only_callers_tokens_and_redacts_secrets(
    store, auth_env,
):
    """Cross-user isolation: list never crosses the caller boundary.

    Also pins D15: the list response shape excludes ``token_hash`` and
    ``token_prefix`` and (obviously) the plaintext.
    """
    alice = _seed(store, email="alice@home.lan", role="user")
    bob = _seed(store, email="bob@home.lan", role="user")
    alice_hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}
    bob_hdr = {"Authorization": f"Bearer {_mint_jwt(bob, 'user')}"}

    with _client() as client:
        client.post("/api/v1/me/mcp-tokens",
                    headers=alice_hdr, json={"label": "alice-1"})
        client.post("/api/v1/me/mcp-tokens",
                    headers=bob_hdr, json={"label": "bob-1"})
        client.post("/api/v1/me/mcp-tokens",
                    headers=bob_hdr, json={"label": "bob-2"})

        a_resp = client.get("/api/v1/me/mcp-tokens", headers=alice_hdr)
        b_resp = client.get("/api/v1/me/mcp-tokens", headers=bob_hdr)

    assert a_resp.status_code == 200 and b_resp.status_code == 200
    a_rows = a_resp.json()
    b_rows = b_resp.json()
    assert {r["label"] for r in a_rows} == {"alice-1"}
    assert {r["label"] for r in b_rows} == {"bob-1", "bob-2"}
    for r in a_rows + b_rows:
        assert "token_hash" not in r
        assert "token_prefix" not in r
        assert "plaintext" not in r


def test_me_list_default_excludes_revoked(store, auth_env):
    alice = _seed(store, email="alice@home.lan", role="user")
    hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}
    with _client() as client:
        m = client.post("/api/v1/me/mcp-tokens",
                        headers=hdr, json={"label": "k1"})
        assert m.status_code == 201
        token_id = m.json()["token"]["id"]
        client.post("/api/v1/me/mcp-tokens",
                    headers=hdr, json={"label": "k2"})
        assert client.delete(
            f"/api/v1/me/mcp-tokens/{token_id}", headers=hdr,
        ).status_code == 200
        active = client.get("/api/v1/me/mcp-tokens", headers=hdr).json()
        all_rows = client.get(
            "/api/v1/me/mcp-tokens?include_revoked=true", headers=hdr,
        ).json()
    assert {r["label"] for r in active} == {"k2"}
    assert {r["label"] for r in all_rows} == {"k1", "k2"}


# ---------------------------------------------------------------------------
# Revoke (caller-owned)
# ---------------------------------------------------------------------------


def test_me_revoke_own_token_returns_revoked_at(store, dev_env):
    with _client() as client:
        m = client.post("/api/v1/me/mcp-tokens", json={"label": "k"})
        token_id = m.json()["token"]["id"]
        d = client.delete(f"/api/v1/me/mcp-tokens/{token_id}")
    assert d.status_code == 200, d.text
    assert d.json()["revoked_at"] is not None


def test_me_revoke_already_revoked_is_idempotent(store, dev_env):
    with _client() as client:
        m = client.post("/api/v1/me/mcp-tokens", json={"label": "k"})
        token_id = m.json()["token"]["id"]
        first = client.delete(f"/api/v1/me/mcp-tokens/{token_id}")
        second = client.delete(f"/api/v1/me/mcp-tokens/{token_id}")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["revoked_at"] == second.json()["revoked_at"], (
        "idempotent revoke must NOT bump revoked_at on the second call"
    )


def test_me_revoke_unknown_token_returns_404(store, dev_env):
    with _client() as client:
        resp = client.delete("/api/v1/me/mcp-tokens/does-not-exist")
    assert resp.status_code == 404


def test_me_revoke_other_users_token_returns_404_not_403(store, auth_env):
    """Information-leak guard: cross-user DELETE returns 404, not 403.

    A 403 would let a non-admin tell "this id exists but isn't yours"
    apart from "this id doesn't exist". 404 collapses both.
    """
    alice = _seed(store, email="alice@home.lan", role="user")
    bob = _seed(store, email="bob@home.lan", role="user")
    alice_hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}
    bob_hdr = {"Authorization": f"Bearer {_mint_jwt(bob, 'user')}"}

    with _client() as client:
        m = client.post("/api/v1/me/mcp-tokens",
                        headers=alice_hdr, json={"label": "k"})
        alice_token_id = m.json()["token"]["id"]
        cross = client.delete(
            f"/api/v1/me/mcp-tokens/{alice_token_id}",
            headers=bob_hdr,
        )
    assert cross.status_code == 404, (
        "leaking the existence of someone else's token via 403 would let "
        "non-admins probe id-space"
    )


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


def test_admin_list_user_tokens_includes_revoked_by_default(store, auth_env):
    admin = _seed(store, email="admin@home.lan", role="admin")
    alice = _seed(store, email="alice@home.lan", role="user")
    admin_hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    alice_hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}

    with _client() as client:
        m = client.post("/api/v1/me/mcp-tokens",
                        headers=alice_hdr, json={"label": "k1"})
        revoked_id = m.json()["token"]["id"]
        client.post("/api/v1/me/mcp-tokens",
                    headers=alice_hdr, json={"label": "k2"})
        client.delete(f"/api/v1/me/mcp-tokens/{revoked_id}",
                      headers=alice_hdr)

        resp = client.get(
            f"/api/v1/admin/users/{alice}/mcp-tokens",
            headers=admin_hdr,
        )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert {r["label"] for r in rows} == {"k1", "k2"}
    assert all("user_id" in r for r in rows)
    assert all("token_hash" not in r for r in rows)


def test_admin_list_unknown_user_returns_404(store, auth_env):
    admin = _seed(store, email="admin@home.lan", role="admin")
    admin_hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    with _client() as client:
        resp = client.get(
            "/api/v1/admin/users/no-such-user/mcp-tokens",
            headers=admin_hdr,
        )
    assert resp.status_code == 404


def test_admin_revoke_user_token_returns_admin_view(store, auth_env):
    admin = _seed(store, email="admin@home.lan", role="admin")
    alice = _seed(store, email="alice@home.lan", role="user")
    admin_hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    alice_hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}

    with _client() as client:
        m = client.post("/api/v1/me/mcp-tokens",
                        headers=alice_hdr, json={"label": "k"})
        token_id = m.json()["token"]["id"]
        d = client.delete(
            f"/api/v1/admin/users/{alice}/mcp-tokens/{token_id}",
            headers=admin_hdr,
        )
    assert d.status_code == 200, d.text
    assert d.json()["user_id"] == alice
    assert d.json()["revoked_at"] is not None


def test_admin_revoke_mismatched_user_id_in_path_returns_404(store, auth_env):
    """The path's ``user_id`` is identity, not routing convenience.

    Admin must not accidentally revoke a token via a wrong URL and get
    a misleading success — pin it explicitly.
    """
    admin = _seed(store, email="admin@home.lan", role="admin")
    alice = _seed(store, email="alice@home.lan", role="user")
    bob = _seed(store, email="bob@home.lan", role="user")
    admin_hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    alice_hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}

    with _client() as client:
        m = client.post("/api/v1/me/mcp-tokens",
                        headers=alice_hdr, json={"label": "k"})
        token_id = m.json()["token"]["id"]
        d = client.delete(
            f"/api/v1/admin/users/{bob}/mcp-tokens/{token_id}",
            headers=admin_hdr,
        )
    assert d.status_code == 404


def test_admin_endpoints_reject_non_admin_with_403(store, auth_env):
    alice = _seed(store, email="alice@home.lan", role="user")
    alice_hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}

    with _client() as client:
        resp = client.get(
            f"/api/v1/admin/users/{alice}/mcp-tokens",
            headers=alice_hdr,
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Audit emissions
# ---------------------------------------------------------------------------


def test_mint_emits_minted_audit_row(store, dev_env):
    with _client() as client:
        client.post("/api/v1/me/mcp-tokens", json={"label": "k"})
    actions = [a["action_name"] for a in store.audit]
    assert "__mcp_token__.minted" in actions


def test_user_revoke_emits_revoked_audit_row(store, dev_env):
    with _client() as client:
        m = client.post("/api/v1/me/mcp-tokens", json={"label": "k"})
        token_id = m.json()["token"]["id"]
        client.delete(f"/api/v1/me/mcp-tokens/{token_id}")
    actions = [a["action_name"] for a in store.audit]
    assert "__mcp_token__.revoked" in actions


def test_admin_revoke_emits_admin_revoked_audit_row(store, auth_env):
    admin = _seed(store, email="admin@home.lan", role="admin")
    alice = _seed(store, email="alice@home.lan", role="user")
    admin_hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    alice_hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}
    with _client() as client:
        m = client.post("/api/v1/me/mcp-tokens",
                        headers=alice_hdr, json={"label": "k"})
        token_id = m.json()["token"]["id"]
        client.delete(
            f"/api/v1/admin/users/{alice}/mcp-tokens/{token_id}",
            headers=admin_hdr,
        )
    actions = [a["action_name"] for a in store.audit]
    assert "__mcp_token__.admin_revoked" in actions


# ---------------------------------------------------------------------------
# CSRF / Bearer interaction (D11)
# ---------------------------------------------------------------------------


def test_bearer_authenticated_post_bypasses_origin_check(store, monkeypatch):
    """Bearer-authenticated POST works even when ``Origin`` mismatches.

    Pins the D11 contract: ``csrf.require_same_origin`` returns early
    on Bearer-authenticated calls so MCP / curl callers keep working.
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "csrf-bypass-test-secret")
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", "https://lumogis.example")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    alice = _seed(store, email="alice@home.lan", role="user")
    hdr = {
        "Authorization": f"Bearer {_mint_jwt(alice, 'user')}",
        "Origin": "https://attacker.example",
    }
    with _client() as client:
        resp = client.post(
            "/api/v1/me/mcp-tokens", headers=hdr, json={"label": "k"},
        )
    assert resp.status_code == 201, resp.text
