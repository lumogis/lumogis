# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route-layer tests for the household + instance/system credential
tier admin surfaces.

Pinned by ADR ``credential_scopes_shared_system`` and the
implementation plan §"Negative test cases" + §"OpenAPI presence smoke".

Two new admin routers (per ADR §API routes):

* ``/api/v1/admin/connector-credentials/household`` — admin-only CRUD
  on the ``household_connector_credentials`` table.
* ``/api/v1/admin/connector-credentials/system``   — admin-only CRUD
  on the ``instance_system_connector_credentials`` table.

Both routers are admin-only **and** admin-only-visible — no per-user
route reveals that these credentials exist (privacy posture from the
ADR § "Privacy posture").

The fake store reuses the per-tier SQL pattern catalogue from
:mod:`tests.test_credential_tiers_service` so the route layer
exercises the full request → service → fake-store → response loop
without hitting Postgres.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager

import jwt
import pytest
from fastapi.testclient import TestClient

from tests.test_auth_phase1 import FakeUsersStore  # noqa: E402
from tests.test_credential_tiers_service import _TiersFakeStore as _BaseTiersStore


# ---------------------------------------------------------------------------
# Composite store: tiers SQL surface + users (admin-auth dependency).
# Mirrors `_RoutesFakeStore` in tests/test_connector_credentials_routes.py.
# Honours the BLOCKING dispatch fallthrough rule from
# `## Test infrastructure / _TiersFakeStore` — unknown SQL prefix
# raises AssertionError so a future schema change can't silently no-op.
# ---------------------------------------------------------------------------


_TIER_TABLE_TOKENS = (
    "household_connector_credentials",
    "instance_system_connector_credentials",
    "user_connector_credentials",
    "audit_log",
)


def _looks_like_tier_sql(query: str) -> bool:
    """Cheap dispatch predicate: SQL touches a credential / audit table.

    Used to decide whether to route a query through the tier dispatch
    (which raises AssertionError on truly-unknown SQL — preserving the
    BLOCKING fallthrough rule) or through ``FakeUsersStore`` (which
    silently no-ops on unknown SQL — necessary for users/auth queries
    we don't model here).
    """
    q = query.lower()
    return any(token in q for token in _TIER_TABLE_TOKENS)


class _RoutesFakeStore(FakeUsersStore, _BaseTiersStore):
    """``FakeUsersStore`` (users CRUD) + the tier SQL surface from
    ``_TiersFakeStore``.

    Dispatch:

    * SQL touching a credential / audit table is routed through the
      tier dispatch — which raises AssertionError on unknown SQL,
      preserving the BLOCKING fallthrough rule.
    * Everything else falls through to ``FakeUsersStore``.
    """

    def __init__(self) -> None:
        FakeUsersStore.__init__(self)
        self.household = {}
        self.system = {}
        self.user = {}
        self.audit_log_rows = []

    def fetch_one(self, query: str, params=None):
        if _looks_like_tier_sql(query):
            return _BaseTiersStore.fetch_one(self, query, params)
        return FakeUsersStore.fetch_one(self, query, params)

    def fetch_all(self, query: str, params=None):
        if _looks_like_tier_sql(query):
            return _BaseTiersStore.fetch_all(self, query, params)
        return FakeUsersStore.fetch_all(self, query, params)

    def execute(self, query: str, params=None):
        if _looks_like_tier_sql(query):
            return _BaseTiersStore.execute(self, query, params)
        return FakeUsersStore.execute(self, query, params)


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


_TEST_FERNET_KEY = "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A="


@pytest.fixture
def store(monkeypatch):
    import config as _config
    from services import _credential_internals as ci

    s = _RoutesFakeStore()
    _config._instances["metadata_store"] = s
    ci.reset_for_tests()
    yield s
    _config._instances.pop("metadata_store", None)
    ci.reset_for_tests()


@pytest.fixture
def auth_env(monkeypatch):
    """``AUTH_ENABLED=true`` with deterministic JWT + Fernet env."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-tier-routes-access-secret")
    monkeypatch.setenv(
        "LUMOGIS_JWT_REFRESH_SECRET",
        "test-tier-routes-refresh-secret",
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
    import main
    with TestClient(main.app) as client:
        yield client


def _seed(store, *, email: str, role: str) -> str:
    import services.users as users_svc
    if users_svc.get_user_by_email(email) is None:
        users_svc.create_user(email, "verylongpassword12", role)
    user = users_svc.get_user_by_email(email)
    assert user is not None
    return user.id


# ---------------------------------------------------------------------------
# Route URL constants — both tiers exercised by parametrised tests.
# ---------------------------------------------------------------------------


_TIER_PREFIX = {
    "household": "/api/v1/admin/connector-credentials/household",
    "system": "/api/v1/admin/connector-credentials/system",
}


# ---------------------------------------------------------------------------
# Auth gating — non-admin / unauthenticated.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["household", "system"])
def test_tier_get_list_requires_admin(store, auth_env, tier):
    alice = _seed(store, email="alice@home.lan", role="user")
    hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}
    with _client() as client:
        resp = client.get(_TIER_PREFIX[tier], headers=hdr)
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("tier", ["household", "system"])
def test_tier_get_list_unauthenticated(store, auth_env, tier):
    with _client() as client:
        resp = client.get(_TIER_PREFIX[tier])
    assert resp.status_code in {401, 403}, resp.text


@pytest.mark.parametrize("tier", ["household", "system"])
def test_tier_put_requires_admin(store, auth_env, tier):
    alice = _seed(store, email="alice@home.lan", role="user")
    hdr = {"Authorization": f"Bearer {_mint_jwt(alice, 'user')}"}
    with _client() as client:
        resp = client.put(
            f"{_TIER_PREFIX[tier]}/testconnector",
            json={"payload": {"k": "v"}},
            headers=hdr,
        )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Happy-path round-trips under admin auth.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["household", "system"])
def test_tier_put_and_get_roundtrip(store, auth_env, tier):
    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}

    with _client() as client:
        put = client.put(
            f"{_TIER_PREFIX[tier]}/testconnector",
            json={"payload": {"api_key": "sk-test-123"}},
            headers=hdr,
        )
        assert put.status_code == 200, put.text
        body = put.json()
        assert body["connector"] == "testconnector"
        assert body["created_by"] == f"admin:{admin}"
        assert body["updated_by"] == f"admin:{admin}"
        # Wire-shape invariants: per-user `user_id` field MUST NOT
        # appear on the household/system public model.
        assert "user_id" not in body
        assert "payload" not in body
        assert "ciphertext" not in body

        get_one = client.get(
            f"{_TIER_PREFIX[tier]}/testconnector",
            headers=hdr,
        )
        assert get_one.status_code == 200, get_one.text
        assert get_one.json()["connector"] == "testconnector"

        listing = client.get(_TIER_PREFIX[tier], headers=hdr)
        assert listing.status_code == 200
        items = listing.json()["items"]
        assert len(items) == 1
        assert items[0]["connector"] == "testconnector"
        assert "user_id" not in items[0]


@pytest.mark.parametrize("tier", ["household", "system"])
def test_tier_get_unknown_connector_returns_404(store, auth_env, tier):
    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    with _client() as client:
        resp = client.get(
            f"{_TIER_PREFIX[tier]}/unknown_connector_id_zzz",
            headers=hdr,
        )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["code"] == "connector_not_configured"


@pytest.mark.parametrize("tier", ["household", "system"])
def test_tier_put_unknown_connector_returns_422(store, auth_env, tier):
    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    with _client() as client:
        resp = client.put(
            f"{_TIER_PREFIX[tier]}/never_registered",
            json={"payload": {"api_key": "x"}},
            headers=hdr,
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["code"] == "unknown_connector"


@pytest.mark.parametrize("tier", ["household", "system"])
def test_tier_put_bad_format_returns_400(store, auth_env, tier):
    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    with _client() as client:
        resp = client.put(
            f"{_TIER_PREFIX[tier]}/Bad-Connector",
            json={"payload": {"api_key": "x"}},
            headers=hdr,
        )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "bad_connector_id"


@pytest.mark.parametrize("tier", ["household", "system"])
def test_tier_put_payload_too_large_returns_422(store, auth_env, tier):
    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    big_value = "A" * (70 * 1024)
    with _client() as client:
        resp = client.put(
            f"{_TIER_PREFIX[tier]}/testconnector",
            json={"payload": {"k": big_value}},
            headers=hdr,
        )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("tier", ["household", "system"])
def test_tier_put_extra_field_in_body_returns_422(store, auth_env, tier):
    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    with _client() as client:
        resp = client.put(
            f"{_TIER_PREFIX[tier]}/testconnector",
            json={"payload": {"k": "v"}, "extra": "x"},
            headers=hdr,
        )
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("tier", ["household", "system"])
def test_tier_delete_missing_returns_404(store, auth_env, tier):
    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    with _client() as client:
        resp = client.delete(
            f"{_TIER_PREFIX[tier]}/testconnector",
            headers=hdr,
        )
    assert resp.status_code == 404, resp.text


@pytest.mark.parametrize("tier", ["household", "system"])
def test_tier_delete_existing_returns_204(store, auth_env, tier):
    admin = _seed(store, email="admin@home.lan", role="admin")
    hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    with _client() as client:
        client.put(
            f"{_TIER_PREFIX[tier]}/testconnector",
            json={"payload": {"k": "v"}},
            headers=hdr,
        )
        resp = client.delete(
            f"{_TIER_PREFIX[tier]}/testconnector",
            headers=hdr,
        )
        assert resp.status_code == 204, resp.text
        assert resp.content == b""


# ---------------------------------------------------------------------------
# OpenAPI presence smoke (D6.10) — pinned in plan
# §`## Test infrastructure / OpenAPI presence smoke`.
# Catches the FastAPI-silent-route-drop case where the orchestrator
# would still boot cleanly but the dashboard would never see the route.
# ---------------------------------------------------------------------------


def test_household_and_system_admin_routes_are_in_openapi_spec():
    from main import app
    paths = set(app.openapi()["paths"].keys())
    assert any(
        p.endswith("/admin/connector-credentials/household") for p in paths
    )
    assert any(
        p.endswith("/admin/connector-credentials/system") for p in paths
    )
    assert any(
        "/admin/connector-credentials/household/{connector}" in p
        for p in paths
    )
    assert any(
        "/admin/connector-credentials/system/{connector}" in p
        for p in paths
    )


# ---------------------------------------------------------------------------
# Privacy posture: per-user listing route MUST NOT reveal household /
# system tier rows. Pinned by ADR § Privacy posture.
# ---------------------------------------------------------------------------


def test_per_user_listing_does_not_expose_tier_rows(store, auth_env):
    """Seed a household + system row; bob's per-user GET still returns
    bob's own (zero) rows — no household/system metadata leaks."""
    admin = _seed(store, email="admin@home.lan", role="admin")
    bob = _seed(store, email="bob@home.lan", role="user")
    admin_hdr = {"Authorization": f"Bearer {_mint_jwt(admin, 'admin')}"}
    bob_hdr = {"Authorization": f"Bearer {_mint_jwt(bob, 'user')}"}

    with _client() as client:
        client.put(
            f"{_TIER_PREFIX['household']}/testconnector",
            json={"payload": {"k": "household"}},
            headers=admin_hdr,
        )
        client.put(
            f"{_TIER_PREFIX['system']}/testconnector",
            json={"payload": {"k": "system"}},
            headers=admin_hdr,
        )
        resp = client.get(
            "/api/v1/me/connector-credentials",
            headers=bob_hdr,
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        # bob has no per-user rows of his own; tier rows MUST NOT appear.
        assert items == []
