# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Headline integration test for the per-user connector credentials chunk.

End-to-end exercise of the full lifecycle for the synthetic
``testconnector`` registered in :mod:`connectors.registry`:

    PUT  /api/v1/me/connector-credentials/testconnector  (HTTP, route layer)
        -> services.connector_credentials.put_payload
        -> Fernet encrypt with the household key
        -> UPSERT into user_connector_credentials (fake metadata store)
        -> __connector_credential__.put audit row

    GET  /api/v1/me/connector-credentials/testconnector  (HTTP)
        -> services.connector_credentials.get_record (metadata only)
        -> response carries CredentialRecord projection — no plaintext

    services.connector_credentials.resolve(user_id, "testconnector")
        -> Fernet decrypt with the same household key
        -> returns the JSON dict the caller PUT in step one
        -> verifies plaintext round-trips byte-for-byte

    DELETE /api/v1/me/connector-credentials/testconnector  (HTTP)
        -> services.connector_credentials.delete_payload
        -> __connector_credential__.deleted audit row
        -> subsequent GET → 404, subsequent resolve() → ConnectorNotConfigured

If any step in this chain regresses (route → service → crypto → store →
audit → reverse), this single test catches it. Per-step contract
assertions live in :mod:`tests.test_connector_credentials_service` and
:mod:`tests.test_connector_credentials_routes`; this module focuses on
the round-trip seam those modules cover individually.

Plan reference: ``per_user_connector_credentials.plan.md`` §New
files / file 12 ("integration/test_testconnector_roundtrip.py — End-to-
end: PUT → GET → resolve → DELETE for the synthetic ``testconnector``").
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from tests.test_connector_credentials_routes import (  # noqa: E402
    _RoutesFakeStore,
    _TEST_FERNET_KEY,
)


# ---------------------------------------------------------------------------
# Fixtures — single-user dev mode (caller is "default", role="admin"). The
# round-trip contract does not depend on multi-user auth; the cross-user
# isolation pin lives in tests/test_connector_credentials_routes.py.
# ---------------------------------------------------------------------------


@pytest.fixture
def store(monkeypatch, mock_vector_store, mock_embedder, mock_scheduler):
    """Install the composite store and reset the MultiFernet cache.

    Depends on the autouse mock fixtures so we can re-install them
    after each ``with _client():`` exit (the lifespan's
    ``config.shutdown()`` clears every singleton, including the mocks
    the autouse fixture installed). :func:`_reinstall_singletons` does
    the re-install; this fixture just keeps the mocks reachable.
    """
    import config as _config
    from services import connector_credentials as ccs

    s = _RoutesFakeStore()
    s._vector_store = mock_vector_store
    s._embedder = mock_embedder
    s._scheduler = mock_scheduler
    _config._instances["metadata_store"] = s
    ccs.reset_for_tests()
    yield s
    _config._instances.pop("metadata_store", None)
    ccs.reset_for_tests()


def _reinstall_singletons(store) -> None:
    """Re-bind ``config._instances`` after a TestClient lifespan teardown.

    ``main.lifespan``'s shutdown calls ``config.shutdown()`` which
    clears every singleton — without re-installing them, any
    subsequent ``config.get_metadata_store()`` (e.g. inside the
    direct-service ``resolve()`` call between two HTTP rounds) tries
    to instantiate the real Postgres / Qdrant adapters and crashes
    on missing optional dependencies (``psycopg2``, ``qdrant_client``)
    in the local test venv. Re-binding restores the autouse mocks
    plus our connector-credential fake.
    """
    import config as _config
    from services import connector_credentials as ccs

    _config._instances["metadata_store"] = store
    _config._instances["vector_store"] = store._vector_store
    _config._instances["embedder"] = store._embedder
    _config._instances["scheduler"] = store._scheduler
    _config._instances["reranker"] = None
    ccs.reset_for_tests()


@pytest.fixture
def env(monkeypatch):
    """Single-user dev mode + a real Fernet key so the service operates."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.delenv("LUMOGIS_PUBLIC_ORIGIN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    yield


@contextmanager
def _client():
    """Boot the live FastAPI app inside a TestClient (lifespan executes)."""
    import main
    with TestClient(main.app) as client:
        yield client


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_testconnector_full_roundtrip_put_get_resolve_delete(store, env):
    """The headline integration test for this chunk.

    Walks the full lifecycle exactly once with a single secret value.
    Asserting at each step rather than only at the end keeps failures
    localisable when a single layer regresses.
    """
    from services import connector_credentials as ccs
    from connectors.registry import TESTCONNECTOR

    secret_payload = {
        "api_key": "sk-roundtrip-abc-123",
        "metadata": {"environment": "test", "owner": "alice"},
    }

    # The TestClient lifespan calls config.shutdown() on exit which
    # clears every adapter singleton; :func:`_reinstall_singletons`
    # re-binds the autouse mocks + this test's fake store so the
    # in-memory rows survive across the round-trip's HTTP and
    # direct-service phases.

    with _client() as client:
        # 1. PUT — fresh row.
        put_resp = client.put(
            f"/api/v1/me/connector-credentials/{TESTCONNECTOR}",
            json={"payload": secret_payload},
        )
        assert put_resp.status_code == 200, put_resp.text
        put_body = put_resp.json()
        assert put_body["connector"] == TESTCONNECTOR
        assert put_body["created_by"] == "self"
        assert put_body["updated_by"] == "self"
        assert "payload" not in put_body
        # The Fernet key fingerprint is a stable per-key value; pin that
        # the route returns it (used by the dashboard to surface "this
        # row was sealed under your current household key").
        first_key_version = put_body["key_version"]
        assert isinstance(first_key_version, int)

        # 2. GET — metadata only. Response must NOT contain the secret.
        get_resp = client.get(
            f"/api/v1/me/connector-credentials/{TESTCONNECTOR}",
        )
        assert get_resp.status_code == 200, get_resp.text
        get_body = get_resp.json()
        assert get_body["connector"] == TESTCONNECTOR
        assert get_body["key_version"] == first_key_version
        for forbidden in ("payload", "ciphertext"):
            assert forbidden not in get_body
        assert "sk-roundtrip-abc-123" not in str(get_body)

    _reinstall_singletons(store)

    # 3. resolve() — direct service call (no route surface for this path
    #    in this chunk; future runtime consumers will call resolve()
    #    themselves per D6b future-consumer guidance). Verifies the
    #    plaintext round-trips byte-for-byte through the encrypt /
    #    store / decrypt seam.
    resolved = ccs.resolve("default", TESTCONNECTOR)
    assert resolved == secret_payload

    # 4. Audit — exactly one PUT row, no DELETE row yet.
    put_audits = [a for a in store.audit if a["action_name"] == "__connector_credential__.put"]
    delete_audits = [a for a in store.audit if a["action_name"] == "__connector_credential__.deleted"]
    assert len(put_audits) == 1
    assert delete_audits == []
    assert "self" in put_audits[0]["input_summary"]
    assert put_audits[0]["connector"] == TESTCONNECTOR

    # 5. DELETE — round-trip the lifecycle. Subsequent GET → 404,
    #    subsequent resolve() → ConnectorNotConfigured.
    with _client() as client:
        del_resp = client.delete(
            f"/api/v1/me/connector-credentials/{TESTCONNECTOR}",
        )
        assert del_resp.status_code == 204, del_resp.text
        post_get = client.get(
            f"/api/v1/me/connector-credentials/{TESTCONNECTOR}",
        )
        assert post_get.status_code == 404

    _reinstall_singletons(store)

    with pytest.raises(ccs.ConnectorNotConfigured):
        ccs.resolve("default", TESTCONNECTOR)

    # 6. Audit after delete — exactly one PUT and one DELETE row, in
    #    that order, both for the same connector.
    put_audits = [a for a in store.audit if a["action_name"] == "__connector_credential__.put"]
    delete_audits = [a for a in store.audit if a["action_name"] == "__connector_credential__.deleted"]
    assert len(put_audits) == 1
    assert len(delete_audits) == 1
    assert delete_audits[0]["connector"] == TESTCONNECTOR


def test_testconnector_put_then_put_is_upsert_no_duplicate(store, env):
    """Two PUTs against the same id store one row, with updated_by/at advancing.

    Pins the UPSERT contract end-to-end (the route does not differentiate
    create vs update via status code — both return 200; the response
    body's ``created_at`` / ``updated_at`` distinguish them).
    """
    from connectors.registry import TESTCONNECTOR

    with _client() as client:
        first = client.put(
            f"/api/v1/me/connector-credentials/{TESTCONNECTOR}",
            json={"payload": {"v": 1}},
        )
        assert first.status_code == 200
        time.sleep(0.01)  # ensure updated_at advances visibly
        second = client.put(
            f"/api/v1/me/connector-credentials/{TESTCONNECTOR}",
            json={"payload": {"v": 2}},
        )
        assert second.status_code == 200

    # Exactly one row in the store, despite two PUTs.
    matching = [k for k in store.creds if k[1] == TESTCONNECTOR]
    assert len(matching) == 1, f"UPSERT regression: {matching!r}"

    a = first.json()
    b = second.json()
    assert a["created_at"] == b["created_at"]
    assert b["updated_at"] >= a["updated_at"]
