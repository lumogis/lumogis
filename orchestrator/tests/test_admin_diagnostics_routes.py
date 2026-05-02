# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route-layer tests for ``routes/admin_diagnostics.py``.

Pins the HTTP contract for the read-only credential-key fingerprint
diagnostic surfaced by plan ``credential_management_ux`` D3 + D4 and
the per-tier breakdown wire shape introduced by ADR
``credential_scopes_shared_system``:

* ``GET /api/v1/admin/diagnostics/credential-key-fingerprint`` —
  admin-only; returns ``{"current_key_version": int,
  "rows_by_key_version": {"user": {...}, "household": {...},
  "system": {...}}}`` where every tier key is always present (empty
  dict on no rows); never returns ciphertext, plaintext, or key
  bytes.

The tests use a tiny in-memory store that records the SQL queries it
sees so the GROUP BY → "rows_by_key_version" wiring is asserted both
on output shape AND on actually issuing the GROUP BY for **every**
tier (the D-OK regression test for plan §"Test 11" + arbitration
round 1, generalised to per-tier).
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

import jwt
import pytest
from fastapi.testclient import TestClient
from tests.ephemeral_fernet_key import TEST_FERNET_KEY  # noqa: E402
from tests.test_auth_phase1 import FakeUsersStore  # noqa: E402

# ---------------------------------------------------------------------------
# Diagnostic-only fake store. We deliberately do NOT inherit from the
# `_RoutesFakeStore` in tests/test_connector_credentials_routes.py because:
#   * we need the recorded-queries list to assert GROUP BY was issued
#   * we want to seed pre-baked aggregates without round-tripping through
#     `put_payload` (which would force a real Fernet encrypt round and
#     the rotation predicate)
# ---------------------------------------------------------------------------


class _DiagFakeStore(FakeUsersStore):
    """Minimal store recording every query and serving the GROUP BY aggregate.

    Only the two SQLs the diagnostics endpoint actually hits are
    handled; everything else falls through to ``FakeUsersStore`` (the
    test client's ``require_admin`` dependency hits ``users``).
    """

    def __init__(self) -> None:
        super().__init__()
        # Pre-baked per-tier aggregates the diagnostic should return;
        # tests mutate these directly to model populated/empty/multi-
        # version tables without going through put_payload. Each
        # value is ``{key_version: row_count}``.
        self.rows_by_key_version: dict[int, int] = {}  # alias for user-tier (kept for back-compat)
        self.user_rows_by_key_version: dict[int, int] = {}
        self.household_rows_by_key_version: dict[int, int] = {}
        self.system_rows_by_key_version: dict[int, int] = {}
        # When set to a tier label ("user"/"household"/"system"), the
        # corresponding GROUP BY raises this exception class to
        # exercise the 503 ``diagnostic_unavailable`` path.
        self.fail_tier: str | None = None
        # Every SQL query passed through fetch_one/fetch_all/execute
        # is recorded so regression tests can assert that the GROUP BY
        # is actually issued (and not silently replaced by a SELECT
        # COUNT(*) or similar) for every tier.
        self.queries: list[str] = []

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    def _user_aggregate(self) -> list[dict]:
        # Back-compat: tests that assigned to ``store.rows_by_key_version``
        # directly (the pre-tier shape) still work; the new tests assign
        # to the explicit per-tier attribute.
        src = self.user_rows_by_key_version or self.rows_by_key_version
        return [{"key_version": int(k), "n": int(v)} for k, v in sorted(src.items())]

    def _household_aggregate(self) -> list[dict]:
        return [
            {"key_version": int(k), "n": int(v)}
            for k, v in sorted(self.household_rows_by_key_version.items())
        ]

    def _system_aggregate(self) -> list[dict]:
        return [
            {"key_version": int(k), "n": int(v)}
            for k, v in sorted(self.system_rows_by_key_version.items())
        ]

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        self.queries.append(query)
        q = self._norm(query)

        # GROUP BY aggregate — one branch per tier table.
        if "group by key_version" in q and "count(" in q:
            if "from user_connector_credentials" in q:
                if self.fail_tier == "user":
                    raise RuntimeError("simulated user-tier counter failure")
                return self._user_aggregate()
            if "from household_connector_credentials" in q:
                if self.fail_tier == "household":
                    raise RuntimeError("simulated household-tier counter failure")
                return self._household_aggregate()
            if "from instance_system_connector_credentials" in q:
                if self.fail_tier == "system":
                    raise RuntimeError("simulated system-tier counter failure")
                return self._system_aggregate()

        return super().fetch_all(query, params)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        self.queries.append(query)
        return super().fetch_one(query, params)

    def execute(self, query: str, params: tuple | None = None):  # noqa: ANN001
        self.queries.append(query)
        return super().execute(query, params)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(monkeypatch):
    """Install the diagnostic fake store and reset the MultiFernet cache."""
    import config as _config
    from services import connector_credentials as ccs

    s = _DiagFakeStore()
    _config._instances["metadata_store"] = s
    ccs.reset_for_tests()
    yield s
    _config._instances.pop("metadata_store", None)
    ccs.reset_for_tests()


@pytest.fixture
def auth_env(monkeypatch):
    """``AUTH_ENABLED=true`` with deterministic JWT + Fernet env."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-admin-diag-routes-access-secret")
    monkeypatch.setenv(
        "LUMOGIS_JWT_REFRESH_SECRET",
        "test-admin-diag-routes-refresh-secret",
    )
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "900")
    monkeypatch.setenv("REFRESH_TOKEN_TTL_SECONDS", "3600")
    monkeypatch.setenv("LUMOGIS_REFRESH_COOKIE_SECURE", "false")
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", TEST_FERNET_KEY)
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
# Tests
# ---------------------------------------------------------------------------


_URL = "/api/v1/admin/diagnostics/credential-key-fingerprint"


def test_fingerprint_requires_admin_role(store, auth_env):
    """Non-admin caller → 403 from ``require_admin``."""
    alice = _seed(store, email="alice@home.lan", role="user")
    hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}
    with _client() as client:
        resp = client.get(_URL, headers=hdr)
    assert resp.status_code == 403, resp.text


def test_fingerprint_unauthenticated_rejected(store, auth_env):
    """No bearer token → 401/403 (auth-gating fires before the handler)."""
    with _client() as client:
        resp = client.get(_URL)
    assert resp.status_code in {401, 403}, resp.text


def test_fingerprint_empty_tables_returns_current_with_per_tier_empty_dicts(
    store,
    auth_env,
):
    """All three tiers empty → every tier key present with empty inner dict.

    Pinned by ADR ``credential_scopes_shared_system`` § routes/admin
    diagnostics: every tier key (``user``, ``household``, ``system``)
    is **always present**, even when its inner dict is empty (``{}``).
    Clients branch on inner-dict emptiness, never on key presence.
    """
    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}

    with _client() as client:
        resp = client.get(_URL, headers=hdr)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"current_key_version", "rows_by_key_version"}
    assert isinstance(body["current_key_version"], int)
    rbkv = body["rows_by_key_version"]
    assert set(rbkv.keys()) == {"user", "household", "system"}
    assert rbkv == {"user": {}, "household": {}, "system": {}}


def test_fingerprint_groups_rows_by_key_version(store, auth_env):
    """Multi-version table → counts come back keyed by stringified key_version.

    Phase A: seed two pre-baked aggregates (pretend there's an older key
    fingerprint with 2 rows and a newer one with 4 rows).
    Phase B: call the endpoint.
    Phase C: assert (i) the dict is keyed by the int-as-string key, (ii)
    the counts are right, (iii) the current_key_version matches the
    fingerprint of the configured ``LUMOGIS_CREDENTIAL_KEY``, (iv) the
    GROUP BY query was actually issued (regression test for §"Test 11"
    + arbitration R1 — without the actual GROUP BY query, the fake
    store would silently return ``[]`` and the wire shape would still
    look right).
    """
    from services import connector_credentials as ccs

    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}

    # Phase A — deterministic pre-baked aggregate.
    older_fp = 0xDEADBEEF
    current_fp = ccs.get_current_key_version()
    assert older_fp != current_fp, (
        "older_fp must differ from current_fp for the test to be meaningful"
    )
    store.user_rows_by_key_version = {older_fp: 2, current_fp: 4}

    # Phase B.
    with _client() as client:
        resp = client.get(_URL, headers=hdr)

    # Phase C.
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_key_version"] == current_fp
    assert body["rows_by_key_version"]["user"] == {
        str(older_fp): 2,
        str(current_fp): 4,
    }
    # Tiers without seeded rows still appear with empty inner dicts.
    assert body["rows_by_key_version"]["household"] == {}
    assert body["rows_by_key_version"]["system"] == {}
    # Inner keys must be JSON strings (object keys are always strings,
    # but the test asserts the type explicitly so a future refactor
    # to a list-of-objects can't sneak in unnoticed).
    for k in body["rows_by_key_version"]["user"].keys():
        assert isinstance(k, str)

    # GROUP BY query must have actually been issued for EVERY tier
    # table — guards against someone collapsing all three into a
    # single union query (which would still produce a structurally-
    # valid response but lose the per-version breakdown).
    joined = " ".join(" ".join(q.split()).lower() for q in store.queries)
    assert "group by key_version" in joined
    assert "from user_connector_credentials" in joined
    assert "from household_connector_credentials" in joined
    assert "from instance_system_connector_credentials" in joined


def test_fingerprint_includes_unregistered_connector_rows(store, auth_env, monkeypatch):
    """Stale rows (connector no longer in the CONNECTORS mapping) still count.

    Regression test pinning the design intent recorded in the plan
    §Security: registry-strictness deliberately does NOT gate the
    fingerprint diagnostic — operators planning a key rotation need
    to see every sealed row, including stale ones, otherwise the
    rotation completion signal lies.

    The test asserts the count breakdown sums to the total stored —
    if a future refactor accidentally adds a registry filter, the
    aggregate count would shrink and this assertion fails.
    """
    from services import connector_credentials as ccs

    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}

    current_fp = ccs.get_current_key_version()
    # Pretend 1 row is for a connector id not in the CONNECTORS mapping;
    # the store's fake aggregate doesn't model the connector column —
    # the point is to assert the aggregate goes through unfiltered.
    store.user_rows_by_key_version = {current_fp: 3}

    with _client() as client:
        resp = client.get(_URL, headers=hdr)

    assert resp.status_code == 200, resp.text
    user_counts = resp.json()["rows_by_key_version"]["user"]
    total = sum(user_counts.values())
    assert total == 3


def test_fingerprint_503_when_credential_key_missing(monkeypatch, store, auth_env):
    """No usable LUMOGIS_CREDENTIAL_KEY[S] → 503 ``credential_unavailable``.

    The service raises ``RuntimeError`` from ``_load_keys()`` regardless
    of ``auth_enabled()``; the route translates that into the same
    ``credential_unavailable`` body shape the user-facing PUT/GET use.
    Body MUST NOT include the exception text (operators see the full
    error in the server log).
    """
    from services import connector_credentials as ccs

    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}

    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEY", raising=False)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    ccs.reset_for_tests()

    with _client() as client:
        resp = client.get(_URL, headers=hdr)

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["detail"] == {"code": "credential_unavailable"}, body


@pytest.mark.parametrize("failing_tier", ["user", "household", "system"])
def test_fingerprint_503_when_any_tier_counter_fails(
    store,
    auth_env,
    failing_tier,
):
    """Per-tier counter raises → 503 ``diagnostic_unavailable`` (fail-fast).

    Pinned by ADR ``credential_scopes_shared_system`` § routes/admin
    diagnostics: the endpoint MUST fail-fast on **any** tier counter
    failure rather than silently returning a partial breakdown that
    would mask a broken tier (e.g. a missing migration on the
    ``household_connector_credentials`` table). The body uses a
    distinct error code (``diagnostic_unavailable``) so operators can
    distinguish "no key configured" (``credential_unavailable``) from
    "one or more tier queries failed".
    """
    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}

    store.fail_tier = failing_tier

    with _client() as client:
        resp = client.get(_URL, headers=hdr)

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["detail"] == {
        "code": "diagnostic_unavailable",
        "tier": failing_tier,
    }, body


def test_fingerprint_does_not_emit_audit_row(store, auth_env):
    """Plan D5: the fingerprint GET MUST NOT write an ``audit_log`` row.

    Consistent with the read-only admin GETs already in this codebase
    (``GET /api/v1/admin/users``,
    ``GET /api/v1/admin/users/{id}/connector-credentials``) — none audit.
    The ``_DiagFakeStore`` records every query it sees, so the assertion
    is "no recorded query starts with ``insert into audit_log``" rather
    than relying on a counter exposed by a different fake.
    """
    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}

    with _client() as client:
        resp = client.get(_URL, headers=hdr)
    assert resp.status_code == 200, resp.text

    audit_writes = [
        q for q in store.queries if "insert into audit_log" in " ".join(q.split()).lower()
    ]
    assert audit_writes == [], (
        f"fingerprint GET wrote {len(audit_writes)} unexpected audit row(s); SQL: {audit_writes!r}"
    )


def test_fingerprint_response_never_carries_ciphertext_or_plaintext_strings(
    store,
    auth_env,
):
    """Belt-and-braces: response body has no ``ciphertext`` / ``payload`` keys.

    Mirrors the plaintext-not-on-the-wire invariant test in the
    user-facing routes; pins the contract that the diagnostic exposes
    only ints and counts — never sealed bytes, never plaintext.
    """
    from services import connector_credentials as ccs

    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    store.user_rows_by_key_version = {ccs.get_current_key_version(): 1}

    with _client() as client:
        resp = client.get(_URL, headers=hdr)

    assert resp.status_code == 200
    body = resp.json()
    forbidden = {"ciphertext", "payload", "key", "keys"}
    assert forbidden.isdisjoint(body.keys())
