# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Smoke test for the operator entrypoint
``orchestrator/scripts/rotate_credential_key.py``.

The rotation algorithm itself is exhaustively covered in
``test_connector_credentials_service.py``; this file only proves that
the thin script wrapper:

* invokes :func:`services.connector_credentials.reencrypt_all_to_current_version`
  with the resolved ``--actor`` (default ``system``),
* serialises the ``{rotated, skipped, failed}`` summary as JSON to
  stdout, and
* exits ``0`` when ``failed == 0`` and ``1`` when ``failed > 0``.

Plan reference: ``.cursor/plans/per_user_connector_credentials.plan.md``
§"orchestrator/scripts/rotate_credential_key.py".
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from cryptography.fernet import Fernet

# Re-use the service-layer fake store so this smoke test stays in
# lock-step with the canonical SQL surface the service issues; if the
# service grows a new query, the service tests' FakeStore picks it up
# and this script test inherits the fix.
from tests.test_connector_credentials_service import _FakeStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(monkeypatch):
    import config as _config
    from services import connector_credentials as svc

    # Single-key environment so the initial put is sealed under the
    # only available primary key.
    k1 = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", k1)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    svc.reset_for_tests()

    s = _FakeStore()
    _config._instances["metadata_store"] = s
    s._k1 = k1  # let tests rotate by prepending a new key
    yield s
    _config._instances.pop("metadata_store", None)
    svc.reset_for_tests()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _seed_one_row(store, *, user_id: str = "u1", connector: str = "testconnector"):
    """Helper: PUT one credential under the current primary key."""
    from services.connector_credentials import put_payload

    put_payload(user_id, connector, {"secret": "v"}, actor="self")


def test_script_exits_zero_when_nothing_to_rotate(store, monkeypatch, capsys):
    """No prepended key ⇒ every row is already current ⇒ all skipped.

    ADR ``credential_scopes_shared_system`` widens the rotation summary
    to a per-tier breakdown — assert on totals so this smoke is
    insensitive to the addition of further tiers.
    """
    from scripts.rotate_credential_key import main

    _seed_one_row(store)

    rc = main([])

    assert rc == 0
    captured = capsys.readouterr()
    summary = json.loads(captured.out.strip())
    assert summary["rotated"] == 0
    assert summary["skipped"] == 1
    assert summary["failed"] == 0


def test_script_rotates_after_key_prepend(store, monkeypatch, capsys):
    """Prepending a new primary key ⇒ existing row is rotated."""
    from scripts.rotate_credential_key import main

    from services import connector_credentials as svc

    _seed_one_row(store)

    new_key = Fernet.generate_key().decode()
    monkeypatch.setenv(
        "LUMOGIS_CREDENTIAL_KEYS",
        f"{new_key},{store._k1}",
    )
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEY", raising=False)
    svc.reset_for_tests()

    rc = main([])

    assert rc == 0
    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["rotated"] == 1
    assert summary["skipped"] == 0
    assert summary["failed"] == 0


def test_script_exits_nonzero_when_a_row_fails(store, monkeypatch, capsys):
    """A row with un-decryptable ciphertext ⇒ script exits 1."""
    # Plant a bogus ciphertext that no key in the keyring can decrypt.
    # ``key_version`` is set to a fingerprint that does NOT match the
    # current primary so the rotation loop will *attempt* (and fail)
    # to re-seal the row instead of skipping it.
    from datetime import datetime
    from datetime import timezone

    from scripts.rotate_credential_key import main
    from services.connector_credentials import _key_fingerprint

    from services import connector_credentials as svc

    bogus_key = Fernet.generate_key().decode()
    now = datetime.now(timezone.utc)
    store.rows[("u1", "testconnector")] = {
        "user_id": "u1",
        "connector": "testconnector",
        "ciphertext": b"this-is-not-a-valid-fernet-token",
        "key_version": _key_fingerprint(bogus_key.encode("ascii")),
        "created_at": now,
        "updated_at": now,
        "created_by": "self",
        "updated_by": "self",
    }
    svc.reset_for_tests()

    rc = main([])

    assert rc == 1
    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["rotated"] == 0
    assert summary["skipped"] == 0
    assert summary["failed"] == 1


def test_script_passes_actor_through_to_service(store, monkeypatch, capsys):
    """``--actor`` is forwarded verbatim to the service function."""
    import services.connector_credentials as ccs
    from scripts.rotate_credential_key import main

    captured: dict[str, Any] = {}

    def _fake(*, actor: str, tables: tuple = ()):
        captured["actor"] = actor
        captured["tables"] = tables
        return {"rotated": 0, "skipped": 0, "failed": 0, "by_tier": {}}

    monkeypatch.setattr(ccs, "reencrypt_all_to_current_version", _fake)

    rc = main(["--actor", "admin:alice"])

    assert rc == 0
    assert captured["actor"] == "admin:alice"
    summary = json.loads(capsys.readouterr().out.strip())
    assert summary["rotated"] == 0
    assert summary["failed"] == 0


def test_script_default_actor_is_system(store, monkeypatch, capsys):
    """No ``--actor`` flag ⇒ defaults to ``system``."""
    import services.connector_credentials as ccs
    from scripts.rotate_credential_key import main

    captured: dict[str, Any] = {}

    def _fake(*, actor: str, tables: tuple = ()):
        captured["actor"] = actor
        captured["tables"] = tables
        return {"rotated": 0, "skipped": 0, "failed": 0, "by_tier": {}}

    monkeypatch.setattr(ccs, "reencrypt_all_to_current_version", _fake)

    rc = main([])

    assert rc == 0
    assert captured["actor"] == "system"
    # Default is all three tier tables.
    assert set(captured["tables"]) == {
        "user_connector_credentials",
        "household_connector_credentials",
        "instance_system_connector_credentials",
    }
