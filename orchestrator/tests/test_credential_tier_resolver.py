# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Edge-case + precedence tests for ``resolve_runtime_credential``.

Pinned by ADR ``credential_scopes_shared_system`` and the
implementation plan §"Test cases / Edge cases
(``tests/test_credential_tier_resolver.py``)".

Backed by the same in-memory ``_TiersFakeStore`` from
``tests/test_credential_tiers_service.py`` — keeps a single canonical
SQL pattern catalogue and prevents the per-user tier stub from drifting
out of sync with the fake store the service-layer tests already exercise.

Cases covered (ID ↔ plan §):

* #37 — user-tier wins over household + system
* #38 — household wins when user is missing
* #39 — system wins when user + household are missing
* #40 — env fallback under ``AUTH_ENABLED=false``
* #41 — env fallback silently ignored under ``AUTH_ENABLED=true``
* #42 — no rows + no env fallback → ``ConnectorNotConfigured``
* #43 — unregistered connector → ``UnknownConnector``
* #44 — ``ResolvedCredential`` is a frozen dataclass
* #44a — ``ResolvedCredential.__repr__`` redacts plaintext payload
* #45 — decrypt failure on user tier raises ``CredentialUnavailable``
        and does NOT fall through to a valid system-tier row
        (privilege-escalation-by-tampering regression).
* #46 — caller_user_id only affects the per-user tier read
* #46a — empty / whitespace / ``None`` ``caller_user_id`` → ``ValueError``
* #46b — unanticipated exception class is logged + re-raised
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Any

import pytest
from cryptography.fernet import Fernet
from tests.test_credential_tiers_service import _TiersFakeStore

# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


_TEST_CONNECTOR = "testconnector"


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def store(monkeypatch, fernet_key: str):
    """Install the tiers fake store + fresh single-key Fernet env.

    Mirrors the service-layer test fixture so the resolver tests use
    exactly the same SQL dispatch contract — see plan §`_TiersFakeStore`
    mandatory dispatch fallthrough rule.
    """
    import config as _config
    from services import _credential_internals as ci

    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", fernet_key)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    ci.reset_for_tests()

    s = _TiersFakeStore()
    _config._instances["metadata_store"] = s
    yield s
    _config._instances.pop("metadata_store", None)
    ci.reset_for_tests()


# ---------------------------------------------------------------------------
# Seed helpers — write encrypted rows directly to the fake store so the
# resolver's per-tier reads exercise the real decrypt path.
# ---------------------------------------------------------------------------


def _seed_user(store: _TiersFakeStore, user_id: str, payload: dict[str, Any]) -> None:
    from services import _credential_internals as ci

    ciphertext = ci._encrypt_payload(payload)
    store.insert_user_raw(
        user_id=user_id,
        connector=_TEST_CONNECTOR,
        ciphertext=ciphertext,
        key_version=ci._current_key_version(),
    )


def _seed_household(store: _TiersFakeStore, payload: dict[str, Any]) -> None:
    from services import _credential_internals as ci

    ciphertext = ci._encrypt_payload(payload)
    store.insert_household_raw(
        connector=_TEST_CONNECTOR,
        ciphertext=ciphertext,
        key_version=ci._current_key_version(),
    )


def _seed_system(store: _TiersFakeStore, payload: dict[str, Any]) -> None:
    from services import _credential_internals as ci

    ciphertext = ci._encrypt_payload(payload)
    store.insert_system_raw(
        connector=_TEST_CONNECTOR,
        ciphertext=ciphertext,
        key_version=ci._current_key_version(),
    )


# ---------------------------------------------------------------------------
# #37 — user wins.
# ---------------------------------------------------------------------------


def test_resolver_user_tier_wins_over_household_and_system(store):
    from services.credential_tiers import resolve_runtime_credential

    _seed_user(store, "alice", {"value": "user-tier-secret"})
    _seed_household(store, {"value": "household-tier-secret"})
    _seed_system(store, {"value": "system-tier-secret"})

    resolved = resolve_runtime_credential("alice", _TEST_CONNECTOR)

    assert resolved.tier == "user"
    assert resolved.payload == {"value": "user-tier-secret"}


# ---------------------------------------------------------------------------
# #38 — household wins when user missing.
# ---------------------------------------------------------------------------


def test_resolver_household_wins_when_user_missing(store):
    from services.credential_tiers import resolve_runtime_credential

    _seed_household(store, {"value": "household-tier-secret"})
    _seed_system(store, {"value": "system-tier-secret"})

    resolved = resolve_runtime_credential("alice", _TEST_CONNECTOR)

    assert resolved.tier == "household"
    assert resolved.payload == {"value": "household-tier-secret"}


# ---------------------------------------------------------------------------
# #39 — system wins when user + household missing.
# ---------------------------------------------------------------------------


def test_resolver_system_wins_when_user_and_household_missing(store):
    from services.credential_tiers import resolve_runtime_credential

    _seed_system(store, {"value": "system-tier-secret"})

    resolved = resolve_runtime_credential("alice", _TEST_CONNECTOR)

    assert resolved.tier == "system"
    assert resolved.payload == {"value": "system-tier-secret"}


# ---------------------------------------------------------------------------
# #40 — env fallback under AUTH_ENABLED=false.
# ---------------------------------------------------------------------------


def test_resolver_env_fallback_under_auth_disabled(store, monkeypatch):
    from services.credential_tiers import resolve_runtime_credential

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("LUMOGIS_TEST_FALLBACK", "secret123")

    resolved = resolve_runtime_credential(
        "alice",
        _TEST_CONNECTOR,
        fallback_env="LUMOGIS_TEST_FALLBACK",
    )

    assert resolved.tier == "env"
    assert resolved.payload == {"value": "secret123"}


# ---------------------------------------------------------------------------
# #41 — env fallback silently ignored under AUTH_ENABLED=true.
# ---------------------------------------------------------------------------


def test_resolver_env_fallback_silently_ignored_under_auth_enabled(
    store,
    monkeypatch,
):
    from services._credential_internals import ConnectorNotConfigured
    from services.credential_tiers import resolve_runtime_credential

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_TEST_FALLBACK", "secret123")

    with pytest.raises(ConnectorNotConfigured):
        resolve_runtime_credential(
            "alice",
            _TEST_CONNECTOR,
            fallback_env="LUMOGIS_TEST_FALLBACK",
        )


# ---------------------------------------------------------------------------
# #42 — no rows, no fallback → ConnectorNotConfigured.
# ---------------------------------------------------------------------------


def test_resolver_no_match_no_fallback_raises_ConnectorNotConfigured(store):
    from services._credential_internals import ConnectorNotConfigured
    from services.credential_tiers import resolve_runtime_credential

    with pytest.raises(ConnectorNotConfigured):
        resolve_runtime_credential("alice", _TEST_CONNECTOR)


# ---------------------------------------------------------------------------
# #43 — unregistered connector → UnknownConnector.
# ---------------------------------------------------------------------------


def test_resolver_unknown_connector_raises_UnknownConnector(store):
    from connectors.registry import UnknownConnector
    from services.credential_tiers import resolve_runtime_credential

    with pytest.raises(UnknownConnector):
        resolve_runtime_credential("alice", "definitely_not_registered")


# ---------------------------------------------------------------------------
# #44 — ResolvedCredential is a frozen dataclass.
# ---------------------------------------------------------------------------


def test_resolver_returns_frozen_dataclass():
    from services.credential_tiers import ResolvedCredential

    rc = ResolvedCredential(payload={"k": "v"}, tier="user")

    assert dataclasses.is_dataclass(rc)
    with pytest.raises(dataclasses.FrozenInstanceError):
        rc.tier = "household"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# #44a — __repr__ redacts plaintext payload (D5.5).
# ---------------------------------------------------------------------------


def test_resolved_credential_repr_redacts_payload():
    from services.credential_tiers import ResolvedCredential

    rc = ResolvedCredential(
        payload={"password": "secret123"},
        tier="household",
    )
    rendered = repr(rc)

    assert "<redacted" in rendered
    assert "secret123" not in rendered
    # Tier is non-secret resolution metadata and stays visible.
    assert "household" in rendered


# ---------------------------------------------------------------------------
# #45 — decrypt failure on user tier MUST raise CredentialUnavailable
# and MUST NOT fall through to a valid system-tier row. This is the
# privilege-escalation-by-tampering regression (D4.2).
# ---------------------------------------------------------------------------


def test_resolver_decrypt_failure_raises_CredentialUnavailable_no_fallthrough(
    store,
):
    from services._credential_internals import CredentialUnavailable
    from services._credential_internals import _current_key_version
    from services.credential_tiers import resolve_runtime_credential

    # Per-user row with corrupt ciphertext.
    store.insert_user_raw(
        user_id="alice",
        connector=_TEST_CONNECTOR,
        ciphertext=b"definitely-not-a-valid-fernet-token",
        key_version=_current_key_version(),
    )
    # Valid system-tier row that MUST NOT be returned by the resolver.
    _seed_system(store, {"value": "system-tier-secret"})

    with pytest.raises(CredentialUnavailable):
        resolve_runtime_credential("alice", _TEST_CONNECTOR)


# ---------------------------------------------------------------------------
# #46 — caller_user_id only affects the per-user tier read.
# ---------------------------------------------------------------------------


def test_resolver_per_user_caller_id_used_only_for_user_tier_lookup(store):
    from services.credential_tiers import resolve_runtime_credential

    _seed_household(store, {"value": "household-tier-secret"})

    resolved_alice = resolve_runtime_credential("alice", _TEST_CONNECTOR)
    resolved_bob = resolve_runtime_credential("bob", _TEST_CONNECTOR)

    assert resolved_alice.tier == "household"
    assert resolved_bob.tier == "household"
    assert resolved_alice.payload == resolved_bob.payload
    assert resolved_alice.payload == {"value": "household-tier-secret"}


# ---------------------------------------------------------------------------
# #46a — empty / whitespace / None caller_user_id → ValueError before
# the connector registry lookup runs.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_resolver_empty_caller_user_id_raises_value_error(store, bad):
    from services.credential_tiers import resolve_runtime_credential

    with pytest.raises(ValueError) as excinfo:
        resolve_runtime_credential(bad, _TEST_CONNECTOR)  # type: ignore[arg-type]

    assert "caller_user_id" in str(excinfo.value)


def test_resolver_caller_user_id_validated_before_registry_check(store):
    """Empty caller_user_id MUST surface as ``ValueError`` even when
    the connector id is also unregistered. Pins the validation order
    contract from plan §Resolver caller_user_id validation.
    """
    from services.credential_tiers import resolve_runtime_credential

    with pytest.raises(ValueError):
        resolve_runtime_credential("", "never_registered_connector")


# ---------------------------------------------------------------------------
# #46b — unanticipated exception class re-raised with a structured log.
# ---------------------------------------------------------------------------


def test_resolver_unexpected_exception_re_raised_with_log(
    store,
    monkeypatch,
    caplog,
):
    """A fresh ``KeyError`` from ``ccs.get_payload`` must propagate
    AND emit one ERROR-level structured log line so the fault domain
    stays observable but bounded (per plan §Exception contract).
    """
    from services import connector_credentials as ccs

    def _explode(*args, **kwargs):
        raise KeyError("synthetic")

    monkeypatch.setattr(ccs, "get_payload", _explode)

    from services.credential_tiers import resolve_runtime_credential

    with caplog.at_level(logging.ERROR, logger="services.credential_tiers"):
        with pytest.raises(KeyError):
            resolve_runtime_credential("alice", _TEST_CONNECTOR)

    matches = [
        rec
        for rec in caplog.records
        if "resolve_runtime_credential.unexpected_exception" in rec.getMessage()
    ]
    assert matches, (
        "expected one ERROR log line containing "
        "'resolve_runtime_credential.unexpected_exception'; "
        f"got: {[r.getMessage() for r in caplog.records]!r}"
    )
