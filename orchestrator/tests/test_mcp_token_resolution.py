# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Resolution matrix for ``auth._check_mcp_bearer`` + ``mcp_server._resolve_user_id``.

Per plan ``mcp_token_user_map`` §"Unit / integration tests" §"Resolution
matrix", the canonical ``/mcp/*`` evaluation order is the load-bearing
contract of this slice. This file exercises EVERY branch in
:func:`auth._check_mcp_bearer` and pins the D8 single-verify cache that
makes :func:`mcp_server._resolve_user_id` reuse the middleware's verify
result instead of re-hitting the database.

Covered branches
----------------

* No bearer + ``AUTH_ENABLED=false``                — pass-through, resolver
                                                       falls back to ``MCP_DEFAULT_USER_ID``.
* No bearer + ``AUTH_ENABLED=true``                 — 401 ``missing mcp token``.
* ``lmcp_…`` valid + either mode                    — pass-through, resolver
                                                       returns the token's owner.
* ``lmcp_…`` invalid + either mode                  — 401, NEVER falls back to
                                                       ``MCP_AUTH_TOKEN`` or
                                                       ``MCP_DEFAULT_USER_ID``.
* ``MCP_AUTH_TOKEN`` legacy match + ``AUTH_ENABLED=false``
                                                    — pass-through, resolver →
                                                       ``MCP_DEFAULT_USER_ID``.
* ``MCP_AUTH_TOKEN`` legacy match + ``AUTH_ENABLED=true``
                                                    — 401 with the migration
                                                       hint message; NEVER
                                                       silently rescued (D6
                                                       regression pin).
* JWT bearer (multi-user) — JWT branch wins BEFORE the
  ``MCP_AUTH_TOKEN`` compare so the order isn't accidentally
  swapped (R3 critique).

The mini-app pattern mirrors ``test_phase3_1_mcp_bearer_wiring.py``: we
go through the real :func:`auth.auth_middleware` rather than calling
``_check_mcp_bearer`` directly, because the middleware is responsible
for populating the new ``_current_mcp_token_id`` / ``_current_mcp_user_id``
ContextVars (D8) and the resolver cache only works when that wiring is
exercised end-to-end.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient


_TEST_SECRET = "mcp-resolution-test-secret-please-32-bytes-minimum"


# ---------------------------------------------------------------------------
# In-memory store: just enough mcp_tokens surface for verify() + audit.
# ---------------------------------------------------------------------------


class _ResolutionStore:
    """Mocks the SQL surface that ``services.mcp_tokens.verify`` and
    ``services.mcp_tokens.mint`` issue.

    Deliberately minimal — ``test_mcp_tokens.py`` already covers the
    full surface. This store exists so the middleware-level matrix can
    issue real verifies without booting Postgres.
    """

    def __init__(self) -> None:
        self.tokens: dict[str, dict] = {}
        self.audit: list[dict] = []
        self.verify_calls: int = 0

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    def execute(self, query: str, params: tuple | None = None) -> None:
        q = self._norm(query)
        p = params or ()
        if q.startswith("insert into mcp_tokens"):
            tid, uid, prefix, h, label, scopes = p
            self.tokens[tid] = {
                "id": tid,
                "user_id": uid,
                "token_prefix": prefix,
                "token_hash": h,
                "label": label,
                "scopes": scopes,
                "created_at": datetime.now(timezone.utc),
                "last_used_at": None,
                "expires_at": None,
                "revoked_at": None,
            }
        elif q.startswith(
            "update mcp_tokens set last_used_at = now() where id = %s"
        ):
            (tid,) = p
            row = self.tokens.get(tid)
            if row is not None:
                row["last_used_at"] = datetime.now(timezone.utc)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()
        if q.startswith(
            "select * from mcp_tokens where token_prefix = %s "
            "and revoked_at is null"
        ):
            self.verify_calls += 1
            (prefix,) = p
            for row in self.tokens.values():
                if row["token_prefix"] == prefix and row["revoked_at"] is None:
                    return dict(row)
            return None
        if q.startswith("select * from mcp_tokens where id = %s"):
            (tid,) = p
            row = self.tokens.get(tid)
            return dict(row) if row else None
        if q.startswith("insert into audit_log"):
            row_id = len(self.audit) + 1
            self.audit.append({
                "id": row_id,
                "user_id": p[0],
                "action_name": p[1],
            })
            return {"id": row_id}
        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(monkeypatch):
    import config as _config
    from services import mcp_tokens

    s = _ResolutionStore()
    _config._instances["metadata_store"] = s
    mcp_tokens._LAST_STAMP_CACHE.clear()
    yield s
    _config._instances.pop("metadata_store", None)
    mcp_tokens._LAST_STAMP_CACHE.clear()


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", _TEST_SECRET)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MCP_DEFAULT_USER_ID", raising=False)
    yield


@pytest.fixture
def dev_env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MCP_DEFAULT_USER_ID", raising=False)
    yield


@pytest.fixture
def mini_app():
    """A mini FastAPI with the real ``auth.auth_middleware`` and a
    ``/mcp/probe`` endpoint that records:

      * the resolved ``user_id`` (via ``mcp_server._resolve_user_id``);
      * whether the D8 single-verify cache populated
        ``_current_mcp_user_id``.

    Mirrors the helper in ``test_phase3_1_mcp_bearer_wiring.py`` so the
    two suites stay structurally consistent.
    """
    from auth import auth_middleware

    app = FastAPI()
    app.middleware("http")(auth_middleware)
    router = APIRouter()

    @router.get("/mcp/probe")
    def probe():
        from mcp_server import (
            _current_bearer_token,
            _current_mcp_token_id,
            _current_mcp_user_id,
            _resolve_user_id,
        )

        try:
            resolved = _resolve_user_id()
            error = None
        except RuntimeError as exc:
            resolved = None
            error = str(exc)
        return JSONResponse({
            "bearer": _current_bearer_token(),
            "cached_user_id": _current_mcp_user_id.get(),
            "cached_token_id": _current_mcp_token_id.get(),
            "resolved_user_id": resolved,
            "error": error,
        })

    app.include_router(router)
    return app


def _mint_jwt(user_id: str, role: str = "user") -> str:
    from auth import mint_access_token
    return mint_access_token(user_id=user_id, role=role)


def _mint_lmcp(user_id: str, label: str = "k") -> str:
    """Mint an ``lmcp_…`` token via the real service (uses ``store`` fixture)."""
    from services import mcp_tokens
    _, plaintext = mcp_tokens.mint(user_id, label)
    return plaintext


# ---------------------------------------------------------------------------
# 1. Empty / missing bearer
# ---------------------------------------------------------------------------


def test_no_bearer_in_dev_mode_falls_back_to_default_user(
    store, dev_env, mini_app, monkeypatch,
):
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "shared-default")
    client = TestClient(mini_app)
    resp = client.get("/mcp/probe")
    assert resp.status_code == 200
    assert resp.json()["resolved_user_id"] == "shared-default"


def test_no_bearer_in_multi_user_returns_401(store, auth_env, mini_app):
    client = TestClient(mini_app)
    resp = client.get("/mcp/probe")
    assert resp.status_code == 401
    assert resp.json()["error"] == "missing mcp token"


# ---------------------------------------------------------------------------
# 2. lmcp_… happy path (both modes)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_fixture", ["dev_env", "auth_env"])
def test_lmcp_token_resolves_to_owner_in_both_modes(
    store, mini_app, request, env_fixture,
):
    """``lmcp_…`` is the one path that gives real per-user MCP isolation.

    Pins that BOTH ``AUTH_ENABLED`` modes treat ``lmcp_…`` identically
    — the routing decision is shape-driven (``startswith('lmcp_')``),
    not env-driven.
    """
    request.getfixturevalue(env_fixture)
    plaintext = _mint_lmcp("alice")

    client = TestClient(mini_app)
    resp = client.get(
        "/mcp/probe",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolved_user_id"] == "alice"
    # D8 single-verify: the resolver MUST have read the cached value
    # populated by auth_middleware, NOT issued a second verify().
    assert body["cached_user_id"] == "alice"
    assert body["cached_token_id"] is not None


def test_lmcp_token_single_verify_cache_avoids_second_db_lookup(
    store, dev_env, mini_app,
):
    """D8: ``_resolve_user_id`` MUST NOT re-run ``verify()`` per request.

    The middleware verifies the token once on the way in and stashes
    the result via the new ContextVars. Re-verifying inside the resolver
    would double the DB hits on every MCP tool call.
    """
    plaintext = _mint_lmcp("alice")
    store.verify_calls = 0  # reset after mint setup
    client = TestClient(mini_app)

    resp = client.get(
        "/mcp/probe",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 200
    assert resp.json()["resolved_user_id"] == "alice"
    assert store.verify_calls == 1, (
        f"verify() must be called exactly once per /mcp/* request "
        f"(D8 cache); was called {store.verify_calls} times"
    )


# ---------------------------------------------------------------------------
# 3. lmcp_… miss MUST NOT fall back to MCP_AUTH_TOKEN / DEFAULT_USER_ID
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_fixture", ["dev_env", "auth_env"])
def test_invalid_lmcp_fails_closed_no_rescue(
    store, mini_app, request, monkeypatch, env_fixture,
):
    """An ``lmcp_…``-shaped bearer that doesn't verify is ALWAYS 401.

    Even when ``MCP_AUTH_TOKEN`` is set and would otherwise rescue the
    call, an invalid ``lmcp_…`` is a definitive reject. Anything else
    would let an attacker downgrade a per-user request to the shared
    bucket by guessing an ``lmcp_…``-shaped string.
    """
    request.getfixturevalue(env_fixture)
    monkeypatch.setenv("MCP_AUTH_TOKEN", "shared-secret")
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "would-be-fallback")

    client = TestClient(mini_app)
    resp = client.get(
        "/mcp/probe",
        headers={"Authorization": "Bearer lmcp_thiswillnotverifyeverandshouldnotrescue"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid mcp token"


# ---------------------------------------------------------------------------
# 4. Legacy MCP_AUTH_TOKEN — accept in dev, reject in multi-user (D6)
# ---------------------------------------------------------------------------


def test_legacy_mcp_auth_token_accepted_in_dev_mode(
    store, dev_env, mini_app, monkeypatch,
):
    """Single-user dev: legacy shared secret rescues, resolver → DEFAULT."""
    monkeypatch.setenv("MCP_AUTH_TOKEN", "shared-secret")
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "shared-default")

    client = TestClient(mini_app)
    resp = client.get(
        "/mcp/probe",
        headers={"Authorization": "Bearer shared-secret"},
    )
    assert resp.status_code == 200
    assert resp.json()["resolved_user_id"] == "shared-default"


def test_legacy_mcp_auth_token_rejected_in_multi_user(
    store, auth_env, mini_app, monkeypatch, caplog,
):
    """D6 regression pin: ``MCP_AUTH_TOKEN`` MUST NOT silently rescue
    a multi-user ``/mcp/*`` call.

    The fail-closed posture is the entire point of the per-user MCP
    token surface. We additionally pin the operator-facing CRITICAL
    log line (a one-shot signal, see
    :func:`auth._warn_legacy_fallback_in_multi_user_once`) so
    operators always have a reachable hint when this fires.
    """
    # Reset the one-shot latch so this test can observe the log line
    # regardless of which other resolution test happens to run first.
    import auth
    auth._warned_legacy_fallback_in_multi_user = False

    monkeypatch.setenv("MCP_AUTH_TOKEN", "shared-secret")
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "shared-default")

    caplog.set_level("CRITICAL")
    client = TestClient(mini_app)
    resp = client.get(
        "/mcp/probe",
        headers={"Authorization": "Bearer shared-secret"},
    )
    assert resp.status_code == 401
    assert "legacy MCP_AUTH_TOKEN" in resp.json()["error"]
    assert any(
        "legacy MCP_AUTH_TOKEN" in rec.message
        for rec in caplog.records
        if rec.levelname == "CRITICAL"
    ), "expected one-shot CRITICAL log emitted by D6 fail-closed branch"


# ---------------------------------------------------------------------------
# 5. JWT-before-legacy ordering pin (R3 critique)
# ---------------------------------------------------------------------------


def test_jwt_branch_wins_before_legacy_compare_in_multi_user(
    store, auth_env, mini_app, monkeypatch,
):
    """JWT detection in ``_check_mcp_bearer`` step 4 MUST run BEFORE
    the ``MCP_AUTH_TOKEN`` compare in step 5.

    Reordering reintroduces the D6 regression where a multi-user
    request that happens to present a JWT also matching the legacy
    shared secret would be silently downgraded.
    """
    monkeypatch.setenv("MCP_AUTH_TOKEN", "some-other-secret")
    monkeypatch.setenv("MCP_DEFAULT_USER_ID", "would-be-fallback")

    token = _mint_jwt("alice")
    client = TestClient(mini_app)
    resp = client.get(
        "/mcp/probe",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolved_user_id"] == "alice", (
        "JWT sub MUST win over MCP_DEFAULT_USER_ID in multi-user mode "
        "(JWT branch is step 4; legacy compare is step 5)"
    )
    # And the lmcp_… cache MUST be empty — JWT path doesn't populate it.
    assert body["cached_user_id"] is None
    assert body["cached_token_id"] is None
