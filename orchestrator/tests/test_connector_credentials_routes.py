# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route-layer tests for the per-user connector credentials surface.

Pins the HTTP contracts described in the plan
``per_user_connector_credentials.plan.md`` §"API routes" + §"Route tests":

* User-facing CRUD at ``/api/v1/me/connector-credentials`` — list, get,
  put (UPSERT), delete — including the registry-strictness split: PUT
  is registry-strict (422 on unknown connector), GET / DELETE are
  format-strict only (admins/users can read + clean up stale-but-stored
  rows whose connector id has been removed from the registry).
* Admin CRUD at ``/api/v1/admin/users/{user_id}/connector-credentials``
  — same four verbs, ``actor`` is ``admin:<caller.user_id>`` so the
  audit row distinguishes operator interventions from self-service.
* Domain → HTTP mapping (D6a): ``connector_not_configured`` → 404,
  ``credential_unavailable`` → 503, ``connector_access_denied`` → 403,
  bad-format connector ids → 400, unknown registry membership → 422
  (PUT only), payload > 64 KiB → 422, oversized / non-dict payload → 422.
* Information-leak guard parity with mcp_tokens admin DELETE: an
  admin call against an unknown ``user_id`` returns 404 (not 403).
* Audit emission contract (lifecycle audits emitted by the service,
  NOT re-emitted by the routes): ``__connector_credential__.put`` and
  ``__connector_credential__.deleted`` carry the right ``actor``.

Service-level behaviour (encryption, key rotation, registry rules) lives
in :mod:`tests.test_connector_credentials_service`. This module focuses
strictly on the wire boundary.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import jwt
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tests.test_auth_phase1 import FakeUsersStore  # noqa: E402


# ---------------------------------------------------------------------------
# Composite store — FakeUsersStore (users CRUD) + the SQL surface
# `services.connector_credentials` issues + audit_log INSERTs. Kept in
# this module rather than promoted to a shared fixture because the
# route tests are the only place we need both surfaces simultaneously.
# ---------------------------------------------------------------------------


class _RoutesFakeStore(FakeUsersStore):
    """``FakeUsersStore`` + the SQL surface ``services.connector_credentials`` issues.

    Mirrors the fake-store pattern used by
    :mod:`tests.test_mcp_tokens_routes` and the service-level
    :mod:`tests.test_connector_credentials_service` fakes — we model
    the small set of statements the service actually issues
    (``SELECT``, ``INSERT … ON CONFLICT … DO UPDATE … RETURNING``,
    ``DELETE … RETURNING``, audit-log ``INSERT``) and trust the real
    Postgres for the rest of the surface in production.
    """

    def __init__(self) -> None:
        super().__init__()
        # Keyed by (user_id, connector). Each value is a dict mirroring
        # the `user_connector_credentials` row columns the service reads.
        self.creds: dict[tuple[str, str], dict] = {}
        self.audit: list[dict] = []

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    def transaction(self):
        from contextlib import contextmanager as _cm

        @_cm
        def _noop():
            yield

        return _noop()

    # --- fetch_one ------------------------------------------------------

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()

        # Metadata read (get_record).
        if q.startswith(
            "select user_id, connector, created_at, updated_at, "
            "created_by, updated_by, key_version "
            "from user_connector_credentials"
        ):
            uid, conn = p
            row = self.creds.get((uid, conn))
            return dict(row) if row else None

        # Plaintext read (get_payload — service layer; not exercised by
        # the route tests directly, but the stale `get_payload` path
        # may still be hit by a future test, so we model it).
        if q.startswith(
            "select ciphertext from user_connector_credentials"
        ):
            uid, conn = p
            row = self.creds.get((uid, conn))
            return {"ciphertext": row["ciphertext"]} if row else None

        # UPSERT (put_payload).
        if q.startswith("insert into user_connector_credentials"):
            uid, conn, ciphertext, key_version, created_by, updated_by = p
            now = datetime.now(timezone.utc)
            existing = self.creds.get((uid, conn))
            if existing is None:
                row = {
                    "user_id": uid,
                    "connector": conn,
                    "ciphertext": ciphertext,
                    "key_version": key_version,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": created_by,
                    "updated_by": updated_by,
                }
            else:
                row = dict(existing)
                row["ciphertext"] = ciphertext
                row["key_version"] = key_version
                row["updated_at"] = now
                row["updated_by"] = updated_by
            self.creds[(uid, conn)] = row
            return {
                "user_id": row["user_id"],
                "connector": row["connector"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "created_by": row["created_by"],
                "updated_by": row["updated_by"],
                "key_version": row["key_version"],
            }

        # DELETE … RETURNING key_version.
        if q.startswith("delete from user_connector_credentials"):
            uid, conn = p
            row = self.creds.pop((uid, conn), None)
            if row is None:
                return None
            return {"key_version": row["key_version"]}

        # Audit insert (services.actions / actions.audit.write_audit).
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

        # list_records — per-user enumeration ordered by connector ASC.
        if q.startswith(
            "select user_id, connector, created_at, updated_at, "
            "created_by, updated_by, key_version "
            "from user_connector_credentials where user_id"
        ):
            (uid,) = p
            return sorted(
                (dict(r) for (u, _c), r in self.creds.items() if u == uid),
                key=lambda r: r["connector"],
            )

        return super().fetch_all(query, params)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# Real Fernet key for the credential service. Test-only; rotated to a
# fresh value would also work — pinned so test failures can be
# reproduced without env drift.
_TEST_FERNET_KEY = "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A="


@pytest.fixture
def store(monkeypatch):
    """Install the composite store as the metadata-store singleton.

    Also resets the per-process MultiFernet cache in
    :mod:`services.connector_credentials` so credential-key env
    overrides take effect immediately in this test (and don't leak
    out of it).
    """
    import config as _config
    from services import connector_credentials as ccs

    s = _RoutesFakeStore()
    _config._instances["metadata_store"] = s
    ccs.reset_for_tests()
    yield s
    _config._instances.pop("metadata_store", None)
    ccs.reset_for_tests()


@pytest.fixture
def dev_env(monkeypatch):
    """``AUTH_ENABLED=false`` — admin/user gates are no-ops; caller is ``default``.

    Still sets a real Fernet key because the service layer's
    ``_load_keys()`` raises whenever no key is configured (regardless
    of ``auth_enabled()``); without a key, every PUT/GET would 503.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    yield


@pytest.fixture
def auth_env(monkeypatch):
    """``AUTH_ENABLED=true`` with deterministic JWT secrets + Fernet key."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-conn-cred-routes-access-secret")
    monkeypatch.setenv(
        "LUMOGIS_JWT_REFRESH_SECRET",
        "test-conn-cred-routes-refresh-secret",
    )
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
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
# User-facing routes — list / get / put / delete
# ---------------------------------------------------------------------------


def test_user_list_empty(store, dev_env):
    """No rows → 200 + empty items list."""
    with _client() as client:
        resp = client.get("/api/v1/me/connector-credentials")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"items": []}


def test_user_get_404_when_missing(store, dev_env):
    """Missing row → 404 with ``connector_not_configured`` body shape."""
    with _client() as client:
        resp = client.get("/api/v1/me/connector-credentials/testconnector")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "connector_not_configured"
    assert body["detail"]["connector"] == "testconnector"


def test_user_put_creates_row_returns_200_and_record(store, dev_env):
    """Fresh PUT → 200 + ``ConnectorCredentialPublic`` projection.

    Pin the no-plaintext-on-the-wire invariant: the response must
    NOT contain a ``payload`` or any ciphertext field.
    """
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/testconnector",
            json={"payload": {"api_key": "sk-test-123"}},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connector"] == "testconnector"
    assert body["created_by"] == "self"
    assert body["updated_by"] == "self"
    assert "payload" not in body
    assert "ciphertext" not in body
    assert isinstance(body["key_version"], int)


def test_user_put_then_get_returns_metadata(store, dev_env):
    """PUT then GET on the same id round-trips metadata (still no plaintext)."""
    with _client() as client:
        client.put(
            "/api/v1/me/connector-credentials/testconnector",
            json={"payload": {"api_key": "sk-test-123"}},
        )
        resp = client.get("/api/v1/me/connector-credentials/testconnector")
    assert resp.status_code == 200
    assert resp.json()["connector"] == "testconnector"
    assert "payload" not in resp.json()


def test_user_put_unknown_connector_returns_422(store, dev_env):
    """Unknown registry membership → 422 with ``unknown_connector`` body."""
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/never_registered",
            json={"payload": {"api_key": "x"}},
        )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "unknown_connector"


def test_user_put_bad_format_connector_returns_400(store, dev_env):
    """Format failure (uppercase, hyphens, etc.) → 400 ``bad_connector_id``."""
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/Bad-Connector",
            json={"payload": {"api_key": "x"}},
        )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "bad_connector_id"


def test_user_put_payload_must_be_non_empty_dict(store, dev_env):
    """Empty dict / non-dict / oversized payload → Pydantic 422."""
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/testconnector",
            json={"payload": {}},
        )
    assert resp.status_code == 422, resp.text


def test_user_put_payload_size_cap_returns_422(store, dev_env):
    """Payload > 64 KiB JSON-encoded → 422 (Pydantic validator)."""
    big_value = "A" * (70 * 1024)
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/testconnector",
            json={"payload": {"k": big_value}},
        )
    assert resp.status_code == 422, resp.text


def test_user_delete_204_on_hit(store, dev_env):
    """DELETE on existing row → 204; subsequent GET → 404."""
    with _client() as client:
        client.put(
            "/api/v1/me/connector-credentials/testconnector",
            json={"payload": {"x": 1}},
        )
        d = client.delete("/api/v1/me/connector-credentials/testconnector")
        g = client.get("/api/v1/me/connector-credentials/testconnector")
    assert d.status_code == 204, d.text
    assert g.status_code == 404


def test_user_delete_404_when_missing(store, dev_env):
    """DELETE on missing row → 404 ``connector_not_configured`` (idempotency boundary)."""
    with _client() as client:
        resp = client.delete("/api/v1/me/connector-credentials/testconnector")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "connector_not_configured"


def test_user_delete_stale_unregistered_connector(store, dev_env):
    """DELETE on a stale-but-stored row whose connector is NOT registered.

    Pin the registry-strictness split: ``delete_payload`` is
    format-strict only (R2 blocker 2), so admins/users can clean up
    rows for connectors that were previously registered and have since
    been removed. Format check still fires (and would 400 on bad ids).
    """
    # Pre-seed a row directly through the store — bypassing put_payload's
    # registry-strict check, which is the only way to land a row whose
    # connector id is not in the canonical CONNECTORS mapping.
    store.creds[("default", "historic_connector")] = {
        "user_id": "default",
        "connector": "historic_connector",
        "ciphertext": b"\x00",
        "key_version": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "created_by": "self",
        "updated_by": "self",
    }
    with _client() as client:
        d = client.delete("/api/v1/me/connector-credentials/historic_connector")
    assert d.status_code == 204, d.text
    assert ("default", "historic_connector") not in store.creds


def test_user_get_stale_unregistered_connector_returns_metadata(store, dev_env):
    """GET-single on a stale-but-stored row → 200 (not 422)."""
    store.creds[("default", "historic_connector")] = {
        "user_id": "default",
        "connector": "historic_connector",
        "ciphertext": b"\x00",
        "key_version": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "created_by": "self",
        "updated_by": "self",
    }
    with _client() as client:
        resp = client.get("/api/v1/me/connector-credentials/historic_connector")
    assert resp.status_code == 200, resp.text
    assert resp.json()["connector"] == "historic_connector"


def test_user_cannot_target_other_user_id(store, auth_env):
    """User-facing GET-single is implicitly scoped to caller; another user's row is invisible."""
    alice = _seed(store, email="alice@home.lan", role="user")
    bob = _seed(store, email="bob@home.lan", role="user")
    bob_hdr = {"Authorization": f"Bearer {_mint_jwt(bob, 'user')}"}
    alice_hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}

    with _client() as client:
        client.put(
            "/api/v1/me/connector-credentials/testconnector",
            headers=bob_hdr,
            json={"payload": {"x": 1}},
        )
        resp = client.get(
            "/api/v1/me/connector-credentials/testconnector",
            headers=alice_hdr,
        )
    assert resp.status_code == 404, (
        "Alice's row is missing — Bob's must be invisible by route construction"
    )


# ---------------------------------------------------------------------------
# Plaintext-not-on-the-wire invariant (cross-cut over every read path)
# ---------------------------------------------------------------------------


def test_get_response_never_carries_payload_or_ciphertext(store, dev_env):
    """Belt-and-braces: GET responses must never include ``payload`` / ``ciphertext``.

    A grep-once-and-pin assertion: if a future refactor accidentally
    plumbs the decrypted payload onto the wire model, this test fails
    loudly rather than silently shipping plaintext credentials.
    """
    with _client() as client:
        client.put(
            "/api/v1/me/connector-credentials/testconnector",
            json={"payload": {"api_key": "sk-secret-must-not-leak"}},
        )
        single = client.get("/api/v1/me/connector-credentials/testconnector").json()
        listed = client.get("/api/v1/me/connector-credentials").json()

    forbidden = {"payload", "ciphertext"}
    assert forbidden.isdisjoint(single.keys())
    for item in listed["items"]:
        assert forbidden.isdisjoint(item.keys())
    raw_text = (
        client_get_raw := str(single) + str(listed)
    )
    assert "sk-secret-must-not-leak" not in raw_text


# ---------------------------------------------------------------------------
# Admin routes — list / get / put / delete
# ---------------------------------------------------------------------------


def test_admin_list_for_user(store, auth_env):
    """Admin GET-list returns the target user's rows."""
    admin = _seed(store, email="admin@home.lan", role="admin")
    bob = _seed(store, email="bob@home.lan", role="user")
    admin_hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    bob_hdr = {"Authorization": f"Bearer {_mint_jwt(bob, 'user')}"}

    with _client() as client:
        client.put(
            "/api/v1/me/connector-credentials/testconnector",
            headers=bob_hdr, json={"payload": {"x": 1}},
        )
        resp = client.get(
            f"/api/v1/admin/users/{bob}/connector-credentials",
            headers=admin_hdr,
        )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert any(r["connector"] == "testconnector" for r in items)


def test_admin_list_unknown_user_returns_404(store, auth_env):
    """Admin GET-list with non-existent user_id → 404 (not 403)."""
    admin = _seed(store, email="admin@home.lan", role="admin")
    admin_hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    with _client() as client:
        resp = client.get(
            "/api/v1/admin/users/no-such-user/connector-credentials",
            headers=admin_hdr,
        )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "user_not_found"


def test_admin_put_uses_admin_actor_string(store, auth_env):
    """Admin PUT writes ``actor=admin:<caller.user_id>`` to the audit row."""
    admin = _seed(store, email="admin@home.lan", role="admin")
    bob = _seed(store, email="bob@home.lan", role="user")
    admin_hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}

    with _client() as client:
        resp = client.put(
            f"/api/v1/admin/users/{bob}/connector-credentials/testconnector",
            headers=admin_hdr,
            json={"payload": {"api_key": "sk-admin-set"}},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created_by"] == f"admin:{admin}"

    put_audits = [
        a for a in store.audit
        if a["action_name"] == "__connector_credential__.put"
    ]
    assert put_audits, "expected at least one put audit row"
    assert f"admin:{admin}" in put_audits[-1]["input_summary"]


def test_admin_get_for_unregistered_stale_row(store, auth_env):
    """Admin GET-single on a stale-but-stored row → 200 (not 422).

    Mirrors the user-facing parity test but on the admin surface;
    operators can inspect connector ids that have been removed from
    the registry.
    """
    admin = _seed(store, email="admin@home.lan", role="admin")
    bob = _seed(store, email="bob@home.lan", role="user")
    admin_hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    store.creds[(bob, "historic_connector")] = {
        "user_id": bob,
        "connector": "historic_connector",
        "ciphertext": b"\x00",
        "key_version": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "created_by": "self",
        "updated_by": "self",
    }
    with _client() as client:
        resp = client.get(
            f"/api/v1/admin/users/{bob}/connector-credentials/historic_connector",
            headers=admin_hdr,
        )
    assert resp.status_code == 200, resp.text


def test_admin_delete_for_unregistered_stale_row(store, auth_env):
    """Admin DELETE on a stale-but-stored row → 204 + audit records the historical id + admin actor."""
    admin = _seed(store, email="admin@home.lan", role="admin")
    bob = _seed(store, email="bob@home.lan", role="user")
    admin_hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    store.creds[(bob, "historic_connector")] = {
        "user_id": bob,
        "connector": "historic_connector",
        "ciphertext": b"\x00",
        "key_version": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "created_by": "self",
        "updated_by": "self",
    }
    with _client() as client:
        resp = client.delete(
            f"/api/v1/admin/users/{bob}/connector-credentials/historic_connector",
            headers=admin_hdr,
        )
    assert resp.status_code == 204, resp.text
    assert (bob, "historic_connector") not in store.creds

    del_audits = [
        a for a in store.audit
        if a["action_name"] == "__connector_credential__.deleted"
    ]
    assert del_audits, "expected at least one delete audit row"
    last = del_audits[-1]
    assert last["connector"] == "historic_connector"
    assert f"admin:{admin}" in last["input_summary"]


def test_admin_endpoints_reject_non_admin_with_403(store, auth_env):
    """Non-admin caller hitting the admin router → 403 from ``require_admin``."""
    alice = _seed(store, email="alice@home.lan", role="user")
    alice_hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}
    with _client() as client:
        resp = client.get(
            f"/api/v1/admin/users/{alice}/connector-credentials",
            headers=alice_hdr,
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Audit emissions
# ---------------------------------------------------------------------------


def test_user_put_emits_put_audit_row(store, dev_env):
    """PUT lifecycle audit is emitted with ``actor="self"``."""
    with _client() as client:
        client.put(
            "/api/v1/me/connector-credentials/testconnector",
            json={"payload": {"x": 1}},
        )
    actions = [a["action_name"] for a in store.audit]
    assert "__connector_credential__.put" in actions
    last = store.audit[-1]
    assert "self" in last["input_summary"]


def test_user_delete_emits_deleted_audit_row(store, dev_env):
    """DELETE lifecycle audit is emitted with ``actor="self"``."""
    with _client() as client:
        client.put(
            "/api/v1/me/connector-credentials/testconnector",
            json={"payload": {"x": 1}},
        )
        client.delete("/api/v1/me/connector-credentials/testconnector")
    actions = [a["action_name"] for a in store.audit]
    assert "__connector_credential__.deleted" in actions


# ---------------------------------------------------------------------------
# CSRF / Bearer interaction (mirror the mcp_tokens-routes precedent)
# ---------------------------------------------------------------------------


def test_bearer_authenticated_put_bypasses_origin_check(store, monkeypatch):
    """Bearer-authenticated PUT works even when ``Origin`` mismatches.

    Pins the contract: ``csrf.require_same_origin`` returns early on
    Bearer-authenticated calls so curl + MCP clients keep working. A
    cookie-session caller would still be checked, but that flow lives
    in the dashboard tests.
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "csrf-bypass-test-secret")
    monkeypatch.setenv("LUMOGIS_PUBLIC_ORIGIN", "https://lumogis.example")
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    alice = _seed(store, email="alice@home.lan", role="user")
    hdr = {
        "Authorization": f"Bearer {_mint_jwt(alice, 'user')}",
        "Origin": "https://attacker.example",
    }
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/testconnector",
            headers=hdr,
            json={"payload": {"k": "v"}},
        )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Registry endpoint — GET /api/v1/me/connector-credentials/registry
# (plan credential_management_ux D2 + D9 + D20)
# ---------------------------------------------------------------------------


def test_user_registry_returns_items_with_id_and_description(store, dev_env):
    """User-facing registry GET → 200 + ``{"items": [{"id", "description"}, ...]}``.

    Pins the wire shape the dashboard's connector dropdown relies on
    (D2). The server-side helper raises if any registered connector
    lacks a description, so a successful request is also a runtime
    invariant check.
    """
    from connectors import registry as connectors_registry

    with _client() as client:
        resp = client.get("/api/v1/me/connector-credentials/registry")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    items = body["items"]

    # One item per registered connector, each with both keys.
    assert len(items) == len(connectors_registry.REGISTERED_CONNECTORS)
    for item in items:
        assert set(item.keys()) == {"id", "description"}
        assert isinstance(item["id"], str) and item["id"]
        assert isinstance(item["description"], str) and item["description"]

    # Sorted by id ASC — useful for snapshot diffs and the UI dropdown order.
    ids = [item["id"] for item in items]
    assert ids == sorted(ids)


def test_user_registry_route_order_does_not_shadow_connector_route(store, dev_env):
    """``/registry`` MUST NOT be parsed as a connector id.

    FastAPI matches static routes before parameterised ones, so the
    ``/registry`` GET wins. If a future refactor accidentally puts
    the parameterised ``/{connector}`` route first, the request
    would fall through to ``get_my_credential('registry')`` and
    return either a 400 (format check) or a 404 (no row), depending
    on whether ``registry`` happens to be a valid connector format.

    The test seeds a row for the already-registered ``testconnector``
    id (so :func:`iter_registered_with_descriptions` does not raise
    for any registered id without a description) and asserts the
    registry endpoint still returns its dictionary shape.
    """
    with _client() as client:
        client.put(
            "/api/v1/me/connector-credentials/testconnector",
            json={"payload": {"k": "v"}},
        )
        resp = client.get("/api/v1/me/connector-credentials/registry")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    # Confirm the `testconnector` ID is still listed in the registry
    # (i.e. we got the static-route handler and not the parameterised one).
    ids = [i["id"] for i in body["items"]]
    assert "testconnector" in ids


def test_user_registry_admin_caller_also_sees_items(store, auth_env):
    """The registry endpoint is available to admins (admins are also users).

    No separate admin copy of the endpoint by design (D1) — the
    underlying registry is global, so a second endpoint would add
    surface area without adding meaning.
    """
    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    with _client() as client:
        resp = client.get(
            "/api/v1/me/connector-credentials/registry",
            headers=hdr,
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"]


def test_user_registry_unauthenticated_rejected(store, auth_env):
    """No bearer token → 401/403 (require_user fires before the handler)."""
    with _client() as client:
        resp = client.get("/api/v1/me/connector-credentials/registry")
    assert resp.status_code in {401, 403}, resp.text


def test_user_registry_does_not_emit_audit_row(store, dev_env):
    """Plan D5: the registry GET MUST NOT write an ``audit_log`` row.

    Read-only listings of operator-authored static metadata are not
    audit-worthy in this codebase (matches ``GET /api/v1/admin/users``
    and the existing ``GET /api/v1/me/connector-credentials`` posture).
    Snapshots ``len(store.audit)`` before and after to catch any
    future refactor that accidentally wires audit emission into the
    registry route.
    """
    before = len(store.audit)
    with _client() as client:
        resp = client.get("/api/v1/me/connector-credentials/registry")
    assert resp.status_code == 200, resp.text
    assert len(store.audit) == before, (
        f"registry GET wrote {len(store.audit) - before} unexpected audit row(s)"
    )


# ---------------------------------------------------------------------------
# LLM payload validation (plan llm_provider_keys_per_user_migration Pass 1.4)
#
# v1 LLM payload is fixed to ``{"api_key": "<non-empty string>"}``. Both
# the user-facing and admin PUT routes must reject non-conforming payloads
# with 422 ``invalid_llm_payload`` BEFORE the credential row is written
# (so encrypted-but-unusable rows cannot accumulate).
# ---------------------------------------------------------------------------


def test_user_put_llm_connector_happy_path_returns_200(store, dev_env):
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/llm_anthropic",
            json={"payload": {"api_key": "sk-anth-real"}},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["connector"] == "llm_anthropic"


def test_user_put_llm_connector_missing_api_key_returns_invalid_llm_payload(store, dev_env):
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/llm_anthropic",
            json={"payload": {"wrong_field": "x"}},
        )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "invalid_llm_payload"
    assert body["detail"]["connector"] == "llm_anthropic"


def test_user_put_llm_connector_extra_keys_returns_invalid_llm_payload(store, dev_env):
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/llm_openai",
            json={"payload": {"api_key": "sk-x", "extra": "no"}},
        )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["detail"]["code"] == "invalid_llm_payload"
    assert "extra" in body["detail"]["message"]


def test_user_put_llm_connector_non_string_api_key_returns_invalid_llm_payload(store, dev_env):
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/llm_xai",
            json={"payload": {"api_key": 12345}},
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_llm_payload"


def test_user_put_llm_connector_blank_api_key_returns_invalid_llm_payload(store, dev_env):
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/llm_perplexity",
            json={"payload": {"api_key": "   "}},
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_llm_payload"


def test_user_put_llm_invalid_payload_does_not_persist_row(store, dev_env):
    """Validation is pre-flight: no row written, no audit emitted."""
    audit_before = len(store.audit)
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/llm_gemini",
            json={"payload": {"api_key": ""}},
        )
    assert resp.status_code == 422
    assert ("default", "llm_gemini") not in store.creds
    assert len(store.audit) == audit_before


def test_user_put_non_llm_connector_unchanged_by_validator(store, dev_env):
    """A non-llm payload like ``{"api_key": "x", "extra": "y"}`` is fine
    on a non-llm connector — validator only fires on llm_* ids."""
    with _client() as client:
        resp = client.put(
            "/api/v1/me/connector-credentials/testconnector",
            json={"payload": {"api_key": "x", "extra": "y"}},
        )
    assert resp.status_code == 200, resp.text


def test_admin_put_llm_invalid_payload_returns_invalid_llm_payload(store, auth_env):
    admin = _seed(store, email="admin@home.lan", role="admin")
    target = _seed(store, email="alice@home.lan", role="user")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    with _client() as client:
        resp = client.put(
            f"/api/v1/admin/users/{target}/connector-credentials/llm_mistral",
            json={"payload": {"oops": "not api_key"}},
            headers=hdr,
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "invalid_llm_payload"
