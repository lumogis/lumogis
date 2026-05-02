# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Headline integration test for the credential tier resolver.

Pinned by ADR ``credential_scopes_shared_system`` and the
implementation plan §"Test cases / Integration tests
(``tests/integration/test_credential_tier_precedence.py``)".

End-to-end exercise of the ``user → household → system → env``
precedence walk for the synthetic ``testconnector`` registered in
:mod:`connectors.registry`, plus a ``caldav`` smoke that asserts the
resolver can deliver a household-tier ``caldav`` payload **directly**
(NOT through ``services/caldav_credentials.load_connection`` — that
adapter is per-user-only by design and migrating it to the resolver
is a separate follow-up chunk; see plan §Interoperability notes).

Walks executed:

    1. PUT user-tier   → resolve(alice, testconnector).tier == "user"
    2. DELETE user     → resolve(alice, testconnector).tier == "household"
    3. DELETE household → resolve(alice, testconnector).tier == "system"
    4. DELETE system   → ConnectorNotConfigured

Plus the household ``caldav`` smoke (step 8 in the plan):
    PUT household-tier caldav payload
    resolve(alice, caldav) ⇒ tier == "household" + payload keys
    sufficient for ``caldav_credentials`` consumers.

The test reuses the in-memory ``_RoutesFakeStore`` from
``tests/test_credential_tiers_routes.py`` so the seeding path goes
through the real route layer (admin-only PUTs to the household /
system tiers; per-user PUT to ``/api/v1/me/...``) — same dispatch
contract, same SQL surface, same audit emission. The resolver is
called directly in-process between HTTP rounds — the lifespan's
``config.shutdown()`` clears every singleton on each TestClient
exit, so we re-install the fake store + autouse mocks via
:func:`_reinstall_singletons` (mirrors the precedent in
``tests/integration/test_testconnector_roundtrip.py``).
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager

import jwt
import pytest
from fastapi.testclient import TestClient
from tests.test_credential_tiers_routes import _TEST_FERNET_KEY  # noqa: E402
from tests.test_credential_tiers_routes import _TIER_PREFIX  # noqa: E402
from tests.test_credential_tiers_routes import _RoutesFakeStore  # noqa: E402

_TEST_CONNECTOR = "testconnector"
_CALDAV_CONNECTOR = "caldav"


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def store(monkeypatch, mock_vector_store, mock_embedder, mock_scheduler):
    """Install the composite store and reset the MultiFernet cache.

    Depends on the autouse mock fixtures so :func:`_reinstall_singletons`
    can re-bind them after each ``with _client():`` lifespan exit (the
    ``main.lifespan`` shutdown hook calls ``config.shutdown()`` which
    clears every singleton).
    """
    import config as _config
    from services import _credential_internals as ci

    s = _RoutesFakeStore()
    s._vector_store = mock_vector_store
    s._embedder = mock_embedder
    s._scheduler = mock_scheduler
    _config._instances["metadata_store"] = s
    ci.reset_for_tests()
    yield s
    _config._instances.pop("metadata_store", None)
    ci.reset_for_tests()


def _reinstall_singletons(store) -> None:
    import config as _config
    from services import _credential_internals as ci

    _config._instances["metadata_store"] = store
    _config._instances["vector_store"] = store._vector_store
    _config._instances["embedder"] = store._embedder
    _config._instances["scheduler"] = store._scheduler
    _config._instances["reranker"] = None
    ci.reset_for_tests()


@pytest.fixture
def auth_env(monkeypatch):
    """``AUTH_ENABLED=true`` with deterministic JWT + Fernet env."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-tier-precedence-access-secret")
    monkeypatch.setenv(
        "LUMOGIS_JWT_REFRESH_SECRET",
        "test-tier-precedence-refresh-secret",
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


def _seed_user_account(store, *, email: str, role: str) -> str:
    import services.users as users_svc

    if users_svc.get_user_by_email(email) is None:
        users_svc.create_user(email, "verylongpassword12", role)
    user = users_svc.get_user_by_email(email)
    assert user is not None
    return user.id


# ---------------------------------------------------------------------------
# Headline precedence walk.
# ---------------------------------------------------------------------------


def test_resolver_walks_user_household_system_then_unconfigured(
    store,
    auth_env,
):
    """The headline integration test for this chunk.

    Seeds testconnector at all three tiers via the live route surface,
    then deletes each tier in precedence order and asserts the
    resolver's tier label reflects the new top of the stack at every
    step.
    """
    from services._credential_internals import ConnectorNotConfigured
    from services.credential_tiers import resolve_runtime_credential

    alice_id = _seed_user_account(store, email="alice@home.lan", role="admin")
    alice_hdr = {"Authorization": f"Bearer {_mint_jwt(alice_id, 'admin')}"}

    user_secret = {"value": "user-tier-secret"}
    household_secret = {"value": "household-tier-secret"}
    system_secret = {"value": "system-tier-secret"}

    # 1. Seed all three tiers via the HTTP surface.
    with _client() as client:
        put_user = client.put(
            f"/api/v1/me/connector-credentials/{_TEST_CONNECTOR}",
            json={"payload": user_secret},
            headers=alice_hdr,
        )
        assert put_user.status_code == 200, put_user.text

        put_household = client.put(
            f"{_TIER_PREFIX['household']}/{_TEST_CONNECTOR}",
            json={"payload": household_secret},
            headers=alice_hdr,
        )
        assert put_household.status_code == 200, put_household.text

        put_system = client.put(
            f"{_TIER_PREFIX['system']}/{_TEST_CONNECTOR}",
            json={"payload": system_secret},
            headers=alice_hdr,
        )
        assert put_system.status_code == 200, put_system.text

    # 2. With all three present, user wins.
    _reinstall_singletons(store)
    resolved = resolve_runtime_credential(alice_id, _TEST_CONNECTOR)
    assert resolved.tier == "user"
    assert resolved.payload == user_secret

    # 3. Delete user tier; resolver falls through to household.
    with _client() as client:
        del_user = client.delete(
            f"/api/v1/me/connector-credentials/{_TEST_CONNECTOR}",
            headers=alice_hdr,
        )
        assert del_user.status_code == 204, del_user.text

    _reinstall_singletons(store)
    resolved = resolve_runtime_credential(alice_id, _TEST_CONNECTOR)
    assert resolved.tier == "household"
    assert resolved.payload == household_secret

    # 4. Delete household tier; resolver falls through to system.
    with _client() as client:
        del_household = client.delete(
            f"{_TIER_PREFIX['household']}/{_TEST_CONNECTOR}",
            headers=alice_hdr,
        )
        assert del_household.status_code == 204, del_household.text

    _reinstall_singletons(store)
    resolved = resolve_runtime_credential(alice_id, _TEST_CONNECTOR)
    assert resolved.tier == "system"
    assert resolved.payload == system_secret

    # 5. Delete system tier; nothing left, no env fallback ⇒
    #    ConnectorNotConfigured (AUTH_ENABLED=true forbids env).
    with _client() as client:
        del_system = client.delete(
            f"{_TIER_PREFIX['system']}/{_TEST_CONNECTOR}",
            headers=alice_hdr,
        )
        assert del_system.status_code == 204, del_system.text

    _reinstall_singletons(store)
    with pytest.raises(ConnectorNotConfigured):
        resolve_runtime_credential(alice_id, _TEST_CONNECTOR)


# ---------------------------------------------------------------------------
# CalDAV smoke — resolver-direct (NOT through caldav_credentials).
# Pinned by plan §Integration tests step 8.
# ---------------------------------------------------------------------------


def test_resolver_direct_caldav_household_smoke(store, auth_env):
    """A household-tier ``caldav`` payload is delivered by the resolver.

    Migrating ``services.caldav_credentials.load_connection`` to consume
    the resolver is a SEPARATE follow-up chunk (see plan §Interoperability
    notes); this smoke proves the necessary precondition: the resolver
    itself can deliver a household-tier ``caldav`` payload with the
    right shape for the future migration.
    """
    from services.credential_tiers import resolve_runtime_credential

    alice_id = _seed_user_account(store, email="alice@home.lan", role="admin")
    alice_hdr = {"Authorization": f"Bearer {_mint_jwt(alice_id, 'admin')}"}

    caldav_secret = {
        "base_url": "https://caldav.example.com/dav/",
        "username": "shared-household",
        "password": "household-shared-pwd",
    }

    with _client() as client:
        put_caldav = client.put(
            f"{_TIER_PREFIX['household']}/{_CALDAV_CONNECTOR}",
            json={"payload": caldav_secret},
            headers=alice_hdr,
        )
        assert put_caldav.status_code == 200, put_caldav.text

    _reinstall_singletons(store)
    resolved = resolve_runtime_credential(alice_id, _CALDAV_CONNECTOR)

    assert resolved.tier == "household"
    assert set(resolved.payload.keys()) >= {"base_url", "username", "password"}
    assert resolved.payload["base_url"] == caldav_secret["base_url"]
