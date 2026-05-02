# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``services/connector_credentials.py``.

Covers every public-API entry point + the registry-strictness model
+ the fingerprint stability contract + the rotation algorithm
(which re-seals every row whose ``key_version`` differs from the
current primary key fingerprint, using ``MultiFernet.rotate``) +
the audit emission shape (no ciphertext, no plaintext, only
``{actor, key_version}`` / ``{ok}``).

Pinned by the test matrix in plan
``.cursor/plans/per_user_connector_credentials.plan.md``.

Uses a small in-memory ``_FakeStore`` (mirrors the
``_AuditAwareStore`` in ``tests/test_mcp_tokens.py`` but scoped to
``user_connector_credentials`` plus the ``audit_log`` writes the
service emits via :func:`actions.audit.write_audit`).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import datetime
from datetime import timezone
from typing import Any

import pytest
from cryptography.fernet import Fernet

# ---------------------------------------------------------------------------
# In-memory store: knows about ``user_connector_credentials`` rows and
# ``audit_log`` rows.
# ---------------------------------------------------------------------------


class _FakeStore:
    """In-memory MetadataStore covering the queries the service issues.

    Recognised statements (normalised: lowercased, single-spaced):

      * SELECT ciphertext FROM user_connector_credentials WHERE …
      * SELECT user_id, connector, … FROM user_connector_credentials WHERE user_id = %s AND connector = %s
      * SELECT user_id, connector, … FROM user_connector_credentials WHERE user_id = %s ORDER BY connector ASC
      * SELECT user_id, connector, ciphertext, key_version FROM user_connector_credentials ORDER BY …
      * INSERT INTO user_connector_credentials (...) ON CONFLICT … RETURNING …
      * UPDATE user_connector_credentials SET ciphertext = %s, key_version = %s, updated_at = NOW(), updated_by = %s WHERE user_id = %s AND connector = %s
      * DELETE FROM user_connector_credentials WHERE user_id = %s AND connector = %s RETURNING key_version
      * INSERT INTO audit_log (...) RETURNING id
    """

    def __init__(self) -> None:
        # rows keyed by (user_id, connector)
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []
        self.exec_log: list[tuple[str, tuple]] = []

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    @contextlib.contextmanager
    def transaction(self):
        yield

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    # ---- helpers -----------------------------------------------------

    def insert_raw(
        self,
        *,
        user_id: str,
        connector: str,
        ciphertext: bytes,
        key_version: int,
        created_by: str = "self",
        updated_by: str = "self",
    ) -> None:
        """Test helper: insert a row directly, bypassing the service."""
        now = datetime.now(timezone.utc)
        self.rows[(user_id, connector)] = {
            "user_id": user_id,
            "connector": connector,
            "ciphertext": ciphertext,
            "key_version": key_version,
            "created_at": now,
            "updated_at": now,
            "created_by": created_by,
            "updated_by": updated_by,
        }

    # ---- MetadataStore protocol --------------------------------------

    def execute(self, query: str, params: tuple | None = None) -> None:
        self.exec_log.append((query, params or ()))
        q = self._norm(query)
        p = params or ()

        if q.startswith("update user_connector_credentials set ciphertext"):
            ciphertext, key_version, updated_by, user_id, connector = p
            row = self.rows.get((user_id, connector))
            if row is not None:
                row["ciphertext"] = ciphertext
                row["key_version"] = key_version
                row["updated_by"] = updated_by
                row["updated_at"] = datetime.now(timezone.utc)
            return

        if q.startswith("update user_connector_credentials set updated_by_corrupt"):
            return

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        q = self._norm(query)
        p = params or ()

        if q.startswith("select ciphertext from user_connector_credentials"):
            user_id, connector = p
            row = self.rows.get((user_id, connector))
            return {"ciphertext": row["ciphertext"]} if row else None

        if (
            q.startswith("select user_id, connector, created_at, updated_at,")
            and "where user_id = %s and connector = %s" in q
        ):
            user_id, connector = p
            row = self.rows.get((user_id, connector))
            return self._project_record(row) if row else None

        if q.startswith("insert into user_connector_credentials"):
            user_id, connector, ciphertext, key_version, created_by, updated_by = p
            now = datetime.now(timezone.utc)
            existing = self.rows.get((user_id, connector))
            if existing is not None:
                existing["ciphertext"] = ciphertext
                existing["key_version"] = key_version
                existing["updated_at"] = now
                existing["updated_by"] = updated_by
                row = existing
            else:
                row = {
                    "user_id": user_id,
                    "connector": connector,
                    "ciphertext": ciphertext,
                    "key_version": key_version,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": created_by,
                    "updated_by": updated_by,
                }
                self.rows[(user_id, connector)] = row
            return self._project_record(row)

        if q.startswith("delete from user_connector_credentials"):
            user_id, connector = p
            row = self.rows.pop((user_id, connector), None)
            return {"key_version": row["key_version"]} if row else None

        if q.startswith("insert into audit_log"):
            row_id = len(self.audit) + 1
            self.audit.append(
                {
                    "id": row_id,
                    "user_id": params[0],
                    "action_name": params[1],
                    "connector": params[2],
                    "mode": params[3],
                    "input_summary": params[4],
                    "result_summary": params[5],
                }
            )
            return {"id": row_id}

        return None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        q = self._norm(query)
        p = params or ()

        if (
            q.startswith("select user_id, connector, created_at, updated_at,")
            and "where user_id = %s order by connector asc" in q
        ):
            (user_id,) = p
            rows = [
                self._project_record(r) for (uid, _conn), r in self.rows.items() if uid == user_id
            ]
            rows.sort(key=lambda r: r["connector"])
            return rows

        if q.startswith(
            "select user_id, connector, ciphertext, key_version from user_connector_credentials"
        ):
            rows = [
                {
                    "user_id": r["user_id"],
                    "connector": r["connector"],
                    "ciphertext": r["ciphertext"],
                    "key_version": r["key_version"],
                }
                for r in self.rows.values()
            ]
            rows.sort(key=lambda r: (r["user_id"], r["connector"]))
            return rows

        return []

    @staticmethod
    def _project_record(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": row["user_id"],
            "connector": row["connector"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "created_by": row["created_by"],
            "updated_by": row["updated_by"],
            "key_version": row["key_version"],
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def store(monkeypatch, fernet_key: str):
    """Install a `_FakeStore` and seed a fresh single-key environment.

    Also flushes the process-scoped MultiFernet cache before and after
    so per-test ``monkeypatch.setenv`` actually takes effect.
    """
    import config as _config
    from services import connector_credentials as svc

    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", fernet_key)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    svc.reset_for_tests()

    s = _FakeStore()
    _config._instances["metadata_store"] = s
    yield s
    _config._instances.pop("metadata_store", None)
    svc.reset_for_tests()


# ---------------------------------------------------------------------------
# Round-trip + record return type
# ---------------------------------------------------------------------------


def test_put_then_get_roundtrip(store):
    from services.connector_credentials import get_payload
    from services.connector_credentials import put_payload

    put_payload("u1", "testconnector", {"k": "v"}, actor="self")
    assert get_payload("u1", "testconnector") == {"k": "v"}


def test_put_returns_credential_record(store):
    from services.connector_credentials import CredentialRecord
    from services.connector_credentials import _key_fingerprint
    from services.connector_credentials import _load_keys
    from services.connector_credentials import put_payload

    record = put_payload("u1", "testconnector", {"k": "v"}, actor="self")
    assert isinstance(record, CredentialRecord)
    assert record.user_id == "u1"
    assert record.connector == "testconnector"
    assert record.created_by == "self"
    assert record.updated_by == "self"
    assert record.key_version == _key_fingerprint(_load_keys()[0])
    assert record.created_at == record.updated_at


def test_get_record_returns_metadata_only_no_decrypt(store):
    from services.connector_credentials import CredentialRecord
    from services.connector_credentials import get_record
    from services.connector_credentials import put_payload

    put_payload("u1", "testconnector", {"k": "v"}, actor="self")
    record = get_record("u1", "testconnector")
    assert isinstance(record, CredentialRecord)
    assert record.user_id == "u1"
    assert record.connector == "testconnector"
    assert record.key_version > 0


def test_get_record_works_on_corrupt_ciphertext_without_decrypt(store):
    """Corrupt rows still yield metadata (proves no decrypt path)."""
    from services.connector_credentials import _current_key_version
    from services.connector_credentials import get_record

    store.insert_raw(
        user_id="u1",
        connector="testconnector",
        ciphertext=b"not-a-valid-fernet-token",
        key_version=_current_key_version(),
    )
    record = get_record("u1", "testconnector")
    assert record is not None
    assert record.connector == "testconnector"


def test_get_record_missing_returns_none(store):
    from services.connector_credentials import get_record

    assert get_record("u1", "testconnector") is None


def test_get_record_does_not_raise_connector_not_configured(store):
    from services.connector_credentials import ConnectorNotConfigured
    from services.connector_credentials import get_record

    try:
        result = get_record("u1", "testconnector")
    except ConnectorNotConfigured:
        pytest.fail("get_record must return None on miss, never raise")
    assert result is None


def test_list_records_returns_user_rows_sorted(store):
    from connectors.registry import register
    from services.connector_credentials import list_records
    from services.connector_credentials import put_payload

    register("aregisteredconnector", description="test fixture")
    try:
        put_payload("alice", "testconnector", {"k": 1}, actor="self")
        put_payload("alice", "aregisteredconnector", {"k": 2}, actor="self")
        put_payload("bob", "testconnector", {"k": 3}, actor="self")

        records = list_records("alice")
        assert [r.connector for r in records] == [
            "aregisteredconnector",
            "testconnector",
        ]
        assert all(r.user_id == "alice" for r in records)
    finally:
        from connectors import registry as reg

        reg.CONNECTORS.pop("aregisteredconnector", None)
        reg.REGISTERED_CONNECTORS = frozenset(reg.CONNECTORS.keys())


def test_list_records_empty_when_user_has_no_rows(store):
    from services.connector_credentials import list_records

    assert list_records("u_no_rows") == []


def test_list_records_includes_unregistered_connectors(store):
    """Operator visibility: stale rows MUST appear in list_records."""
    from services.connector_credentials import _current_key_version
    from services.connector_credentials import list_records

    store.insert_raw(
        user_id="alice",
        connector="historic_connector",
        ciphertext=b"",
        key_version=_current_key_version(),
    )
    records = list_records("alice")
    assert any(r.connector == "historic_connector" for r in records)


# ---------------------------------------------------------------------------
# put_payload — persistence + overwrite + audit shape
# ---------------------------------------------------------------------------


def test_put_persists_metadata(store):
    from services.connector_credentials import _key_fingerprint
    from services.connector_credentials import _load_keys
    from services.connector_credentials import put_payload

    put_payload("u1", "testconnector", {"k": "v"}, actor="self")
    row = store.rows[("u1", "testconnector")]
    assert row["created_by"] == "self"
    assert row["updated_by"] == "self"
    assert row["key_version"] == _key_fingerprint(_load_keys()[0])


def test_put_overwrites_payload(store):
    from services.connector_credentials import get_payload
    from services.connector_credentials import put_payload

    put_payload("u1", "testconnector", {"k": "v1"}, actor="self")
    put_payload("u1", "testconnector", {"k": "v2"}, actor="self")
    assert get_payload("u1", "testconnector") == {"k": "v2"}


# ---------------------------------------------------------------------------
# get_payload — None on miss
# ---------------------------------------------------------------------------


def test_get_missing_returns_none(store):
    from services.connector_credentials import get_payload

    assert get_payload("u1", "testconnector") is None


# ---------------------------------------------------------------------------
# delete_payload
# ---------------------------------------------------------------------------


def test_delete_existing_returns_true_and_audits(store):
    from services.connector_credentials import ACTION_CRED_DELETED
    from services.connector_credentials import delete_payload
    from services.connector_credentials import put_payload

    put_payload("u1", "testconnector", {"k": "v"}, actor="self")
    audit_count_before = len(store.audit)
    assert delete_payload("u1", "testconnector", actor="self") is True
    assert ("u1", "testconnector") not in store.rows
    new_audits = store.audit[audit_count_before:]
    assert any(a["action_name"] == ACTION_CRED_DELETED for a in new_audits)


def test_delete_missing_returns_false_no_audit(store):
    from services.connector_credentials import ACTION_CRED_DELETED
    from services.connector_credentials import delete_payload

    audit_before = len(store.audit)
    assert delete_payload("u1", "testconnector", actor="self") is False
    assert all(a["action_name"] != ACTION_CRED_DELETED for a in store.audit[audit_before:])


# ---------------------------------------------------------------------------
# resolve — env fallback gating
# ---------------------------------------------------------------------------


def test_resolve_auth_enabled_true_no_row_raises(store, monkeypatch):
    from services.connector_credentials import ConnectorNotConfigured
    from services.connector_credentials import resolve

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    with pytest.raises(ConnectorNotConfigured):
        resolve("u1", "testconnector", fallback_env="OPENAI_API_KEY")


def test_resolve_auth_enabled_false_uses_fallback(store, monkeypatch):
    from services.connector_credentials import resolve

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-xxx")
    assert resolve("u1", "testconnector", fallback_env="OPENAI_API_KEY") == {
        "value": "sk-xxx",
    }


def test_resolve_auth_enabled_false_no_fallback_raises(store, monkeypatch):
    from services.connector_credentials import ConnectorNotConfigured
    from services.connector_credentials import resolve

    monkeypatch.setenv("AUTH_ENABLED", "false")
    with pytest.raises(ConnectorNotConfigured):
        resolve("u1", "testconnector", fallback_env=None)


def test_resolve_returns_decrypted_payload_when_row_present(store, monkeypatch):
    from services.connector_credentials import put_payload
    from services.connector_credentials import resolve

    monkeypatch.setenv("AUTH_ENABLED", "true")
    put_payload("u1", "testconnector", {"k": "v"}, actor="self")
    assert resolve("u1", "testconnector") == {"k": "v"}


# ---------------------------------------------------------------------------
# Decrypt failure paths
# ---------------------------------------------------------------------------


def test_corrupt_ciphertext_raises_credential_unavailable(store):
    from services.connector_credentials import CredentialUnavailable
    from services.connector_credentials import _current_key_version
    from services.connector_credentials import get_payload

    store.insert_raw(
        user_id="u1",
        connector="testconnector",
        ciphertext=b"not-a-valid-fernet-token",
        key_version=_current_key_version(),
    )
    with pytest.raises(CredentialUnavailable):
        get_payload("u1", "testconnector")


# ---------------------------------------------------------------------------
# Registry-strictness model
# ---------------------------------------------------------------------------


def test_put_payload_still_rejects_unregistered_connector(store):
    from connectors.registry import UnknownConnector
    from services.connector_credentials import put_payload

    with pytest.raises(UnknownConnector):
        put_payload("u1", "not_in_registry", {"k": "v"}, actor="self")


def test_get_payload_still_rejects_unregistered_connector(store):
    from connectors.registry import UnknownConnector
    from services.connector_credentials import get_payload

    with pytest.raises(UnknownConnector):
        get_payload("u1", "not_in_registry")


def test_resolve_still_rejects_unregistered_connector(store):
    from connectors.registry import UnknownConnector
    from services.connector_credentials import resolve

    with pytest.raises(UnknownConnector):
        resolve("u1", "not_in_registry")


def test_get_record_returns_metadata_for_unregistered_connector(store):
    from services.connector_credentials import CredentialRecord
    from services.connector_credentials import _current_key_version
    from services.connector_credentials import get_record

    store.insert_raw(
        user_id="u1",
        connector="historic_connector",
        ciphertext=b"",
        key_version=_current_key_version(),
    )
    record = get_record("u1", "historic_connector")
    assert isinstance(record, CredentialRecord)
    assert record.connector == "historic_connector"


def test_get_record_still_rejects_bad_format(store):
    from services.connector_credentials import get_record

    with pytest.raises(ValueError):
        get_record("u1", "Bad-Name")


def test_delete_payload_succeeds_for_unregistered_connector(store):
    from services.connector_credentials import ACTION_CRED_DELETED
    from services.connector_credentials import _current_key_version
    from services.connector_credentials import delete_payload

    store.insert_raw(
        user_id="u1",
        connector="historic_connector",
        ciphertext=b"",
        key_version=_current_key_version(),
    )
    audit_before = len(store.audit)
    assert delete_payload("u1", "historic_connector", actor="admin:carol") is True
    assert ("u1", "historic_connector") not in store.rows
    new_audits = store.audit[audit_before:]
    assert any(
        a["action_name"] == ACTION_CRED_DELETED and a["connector"] == "historic_connector"
        for a in new_audits
    )


def test_delete_payload_still_rejects_bad_format(store):
    from services.connector_credentials import delete_payload

    with pytest.raises(ValueError):
        delete_payload("u1", "Bad-Name", actor="self")


def test_list_records_full_operator_cleanup_roundtrip(store):
    from services.connector_credentials import _current_key_version
    from services.connector_credentials import delete_payload
    from services.connector_credentials import get_record
    from services.connector_credentials import list_records
    from services.connector_credentials import put_payload

    put_payload("u1", "testconnector", {"k": "v"}, actor="self")
    store.insert_raw(
        user_id="u1",
        connector="historic_connector",
        ciphertext=b"",
        key_version=_current_key_version(),
    )

    rows = list_records("u1")
    assert {r.connector for r in rows} == {"testconnector", "historic_connector"}

    assert get_record("u1", "historic_connector") is not None
    assert delete_payload("u1", "historic_connector", actor="admin:c") is True

    rows = list_records("u1")
    assert {r.connector for r in rows} == {"testconnector"}


# ---------------------------------------------------------------------------
# Actor format
# ---------------------------------------------------------------------------


def test_actor_admin_format_recorded(store):
    from services.connector_credentials import put_payload

    put_payload("u1", "testconnector", {"k": "v"}, actor="admin:abc123")
    assert store.rows[("u1", "testconnector")]["created_by"] == "admin:abc123"


def test_actor_invalid_format_raises(store):
    from services.connector_credentials import put_payload

    with pytest.raises(ValueError):
        put_payload("u1", "testconnector", {"k": "v"}, actor="random_string")


def test_actor_admin_empty_user_id_rejected(store):
    from services.connector_credentials import put_payload

    with pytest.raises(ValueError):
        put_payload("u1", "testconnector", {"k": "v"}, actor="admin:")


def test_actor_admin_too_long_user_id_rejected(store):
    from services.connector_credentials import put_payload

    with pytest.raises(ValueError):
        put_payload(
            "u1",
            "testconnector",
            {"k": "v"},
            actor="admin:" + "x" * 65,
        )


def test_payload_with_non_serialisable_raises(store):
    from services.connector_credentials import put_payload

    with pytest.raises(TypeError):
        put_payload("u1", "testconnector", {"x": object()}, actor="self")


# ---------------------------------------------------------------------------
# Multifernet + key_version semantics
# ---------------------------------------------------------------------------


def test_multifernet_csv_takes_precedence(store, monkeypatch):
    from services.connector_credentials import _key_fingerprint
    from services.connector_credentials import put_payload

    from services import connector_credentials as svc

    k_old = Fernet.generate_key().decode()
    k_new1 = Fernet.generate_key().decode()
    k_new2 = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", k_old)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", f"{k_new2},{k_new1}")
    svc.reset_for_tests()

    record = put_payload("u1", "testconnector", {"k": "v"}, actor="self")
    assert record.key_version == _key_fingerprint(k_new2.encode("ascii"))


def test_key_version_is_stable_across_list_reorder(store, monkeypatch):
    from services.connector_credentials import _key_fingerprint
    from services.connector_credentials import put_payload

    from services import connector_credentials as svc

    k1 = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", k1)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEY", raising=False)
    svc.reset_for_tests()

    rec_initial = put_payload("u1", "testconnector", {"k": "v"}, actor="self")
    fp_k1 = _key_fingerprint(k1.encode("ascii"))
    assert rec_initial.key_version == fp_k1

    k2 = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", f"{k2},{k1}")
    svc.reset_for_tests()

    persisted = store.rows[("u1", "testconnector")]
    assert persisted["key_version"] == fp_k1


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


def test_rotation_reencrypts_old_rows(store, monkeypatch):
    from services.connector_credentials import _key_fingerprint
    from services.connector_credentials import put_payload
    from services.connector_credentials import reencrypt_all_to_current_version

    from services import connector_credentials as svc

    k1 = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", k1)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEY", raising=False)
    svc.reset_for_tests()
    put_payload("u1", "testconnector", {"k": "v"}, actor="self")
    old_ct = bytes(store.rows[("u1", "testconnector")]["ciphertext"])

    k2 = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", f"{k2},{k1}")
    svc.reset_for_tests()

    summary = reencrypt_all_to_current_version(actor="system")
    # ADR ``credential_scopes_shared_system`` widens the rotation
    # summary to a per-tier breakdown; per-user counts now live under
    # ``by_tier["user"]`` and the top-level totals aggregate all walked
    # tiers. With only the per-user table populated, totals + the
    # per-user breakdown both equal the legacy single-tier numbers.
    assert summary["rotated"] == 1
    assert summary["skipped"] == 0
    assert summary["failed"] == 0
    assert summary["by_tier"]["user"] == {"rotated": 1, "skipped": 0, "failed": 0}

    new_ct = bytes(store.rows[("u1", "testconnector")]["ciphertext"])
    assert new_ct != old_ct
    assert store.rows[("u1", "testconnector")]["key_version"] == _key_fingerprint(
        k2.encode("ascii")
    )


def test_rotation_skips_already_current(store):
    from services.connector_credentials import ACTION_CRED_ROTATED
    from services.connector_credentials import put_payload
    from services.connector_credentials import reencrypt_all_to_current_version

    put_payload("u1", "testconnector", {"k": "v"}, actor="self")
    ct_before = bytes(store.rows[("u1", "testconnector")]["ciphertext"])
    audit_before = len(store.audit)

    summary = reencrypt_all_to_current_version(actor="system")
    assert summary["rotated"] == 0
    assert summary["skipped"] == 1
    assert summary["failed"] == 0
    assert summary["by_tier"]["user"] == {"rotated": 0, "skipped": 1, "failed": 0}
    assert bytes(store.rows[("u1", "testconnector")]["ciphertext"]) == ct_before
    assert all(a["action_name"] != ACTION_CRED_ROTATED for a in store.audit[audit_before:])


def test_rotation_after_prepend_rotates_only_old_rows(store, monkeypatch):
    from connectors.registry import register
    from services.connector_credentials import _key_fingerprint
    from services.connector_credentials import put_payload
    from services.connector_credentials import reencrypt_all_to_current_version

    from services import connector_credentials as svc

    register("conn_a", description="test fixture")
    register("conn_b", description="test fixture")
    register("conn_c", description="test fixture")
    register("conn_d", description="test fixture")
    register("conn_e", description="test fixture")
    register("conn_f", description="test fixture")
    register("conn_g", description="test fixture")
    register("conn_h", description="test fixture")

    try:
        k1 = Fernet.generate_key().decode()
        monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", k1)
        monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEY", raising=False)
        svc.reset_for_tests()
        for c in ["conn_a", "conn_b", "conn_c", "conn_d", "conn_e"]:
            put_payload("u1", c, {"k": c}, actor="self")

        k2 = Fernet.generate_key().decode()
        monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", f"{k2},{k1}")
        svc.reset_for_tests()
        for c in ["conn_f", "conn_g", "conn_h"]:
            put_payload("u1", c, {"k": c}, actor="self")

        summary = reencrypt_all_to_current_version(actor="system")
        assert summary["rotated"] == 5
        assert summary["skipped"] == 3
        assert summary["failed"] == 0
        assert summary["by_tier"]["user"] == {"rotated": 5, "skipped": 3, "failed": 0}

        fp_k2 = _key_fingerprint(k2.encode("ascii"))
        for c in [
            "conn_a",
            "conn_b",
            "conn_c",
            "conn_d",
            "conn_e",
            "conn_f",
            "conn_g",
            "conn_h",
        ]:
            assert store.rows[("u1", c)]["key_version"] == fp_k2
    finally:
        from connectors import registry as reg

        for c in [
            "conn_a",
            "conn_b",
            "conn_c",
            "conn_d",
            "conn_e",
            "conn_f",
            "conn_g",
            "conn_h",
        ]:
            reg.CONNECTORS.pop(c, None)
        reg.REGISTERED_CONNECTORS = frozenset(reg.CONNECTORS.keys())


def test_rotation_failed_row_left_untouched(store, monkeypatch, caplog):
    from services.connector_credentials import _key_fingerprint
    from services.connector_credentials import put_payload
    from services.connector_credentials import reencrypt_all_to_current_version

    from services import connector_credentials as svc

    k1 = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", k1)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEY", raising=False)
    svc.reset_for_tests()
    put_payload("u1", "testconnector", {"k": "v"}, actor="self")

    store.insert_raw(
        user_id="u1",
        connector="historic_connector",
        ciphertext=b"junk-bytes",
        key_version=_key_fingerprint(k1.encode("ascii")),
    )

    k2 = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", f"{k2},{k1}")
    svc.reset_for_tests()

    summary = reencrypt_all_to_current_version(actor="system")
    assert summary["failed"] == 1
    assert summary["rotated"] == 1

    bad_row = store.rows[("u1", "historic_connector")]
    assert bytes(bad_row["ciphertext"]) == b"junk-bytes"
    assert b"junk-bytes" not in caplog.text.encode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Audit row content
# ---------------------------------------------------------------------------


def test_audit_row_carries_no_ciphertext_or_plaintext(store):
    from services.connector_credentials import ACTION_CRED_PUT
    from services.connector_credentials import put_payload

    put_payload("u1", "testconnector", {"secret": "shh-do-not-leak"}, actor="self")
    put_audits = [a for a in store.audit if a["action_name"] == ACTION_CRED_PUT]
    assert put_audits, "expected at least one __connector_credential__.put audit"
    a = put_audits[-1]
    inp = json.loads(a["input_summary"])
    res = json.loads(a["result_summary"])
    # ADR ``credential_scopes_shared_system`` adds a ``tier`` discriminator
    # to the audit ``input_summary`` for every credential family event.
    # Per-user writes carry ``tier == "user"``.
    assert set(inp.keys()) == {"actor", "key_version", "tier"}
    assert inp["tier"] == "user"
    assert res == {"ok": True}
    assert "shh-do-not-leak" not in a["input_summary"]
    assert "shh-do-not-leak" not in a["result_summary"]


def test_audit_mode_is_do(store):
    from services.connector_credentials import ACTION_CRED_PUT
    from services.connector_credentials import put_payload

    put_payload("u1", "testconnector", {"k": "v"}, actor="self")
    put_audits = [a for a in store.audit if a["action_name"] == ACTION_CRED_PUT]
    assert put_audits[-1]["mode"] == "DO"


# ---------------------------------------------------------------------------
# _load_keys behaviour
# ---------------------------------------------------------------------------


def test_load_keys_raises_runtimeerror_when_unset(monkeypatch):
    from services import connector_credentials as svc

    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEY", raising=False)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    svc.reset_for_tests()

    with pytest.raises(RuntimeError):
        svc._load_keys()


def test_load_keys_raises_runtimeerror_for_placeholder(monkeypatch):
    from services import connector_credentials as svc

    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", "change-me-in-production")
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    svc.reset_for_tests()

    with pytest.raises(RuntimeError):
        svc._load_keys()


def test_load_keys_raises_runtimeerror_for_alt_placeholder(monkeypatch):
    from services import connector_credentials as svc

    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", "__GENERATE_ME__")
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    svc.reset_for_tests()

    with pytest.raises(RuntimeError):
        svc._load_keys()


def test_load_keys_raises_under_auth_disabled_too(monkeypatch):
    """_load_keys() does NOT consult auth_enabled — fails everywhere."""
    from services import connector_credentials as svc

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEY", raising=False)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    svc.reset_for_tests()

    with pytest.raises(RuntimeError):
        svc._load_keys()


# ---------------------------------------------------------------------------
# Sanity: key_version fingerprint matches the documented formula
# ---------------------------------------------------------------------------


def test_key_fingerprint_matches_documented_formula():
    from services.connector_credentials import _key_fingerprint

    key = Fernet.generate_key()
    expected = int.from_bytes(hashlib.sha256(key).digest()[:4], "big")
    assert _key_fingerprint(key) == expected
    assert 0 <= expected <= 2**32 - 1


# ---------------------------------------------------------------------------
# Change listeners (plan llm_provider_keys_per_user_migration Pass 1.3)
# ---------------------------------------------------------------------------


def test_listener_fires_on_put_with_action_put(store):
    from services import connector_credentials as svc

    events: list[dict] = []
    svc.reset_listeners_for_tests()
    svc.register_change_listener(
        lambda *, user_id, connector, action: events.append(
            {"user_id": user_id, "connector": connector, "action": action}
        )
    )
    try:
        svc.put_payload("alice", "testconnector", {"k": "v"}, actor="self")
        assert events == [{"user_id": "alice", "connector": "testconnector", "action": "put"}]
    finally:
        svc.reset_listeners_for_tests()


def test_listener_fires_on_delete_with_action_delete(store):
    from services import connector_credentials as svc

    svc.put_payload("alice", "testconnector", {"k": "v"}, actor="self")

    events: list[dict] = []
    svc.reset_listeners_for_tests()
    svc.register_change_listener(
        lambda *, user_id, connector, action: events.append(
            {"user_id": user_id, "connector": connector, "action": action}
        )
    )
    try:
        assert svc.delete_payload("alice", "testconnector", actor="self") is True
        assert events == [{"user_id": "alice", "connector": "testconnector", "action": "delete"}]
    finally:
        svc.reset_listeners_for_tests()


def test_listener_does_not_fire_on_delete_miss(store):
    from services import connector_credentials as svc

    events: list[dict] = []
    svc.reset_listeners_for_tests()
    svc.register_change_listener(lambda *, user_id, connector, action: events.append(action))
    try:
        assert svc.delete_payload("alice", "testconnector", actor="self") is False
        assert events == []
    finally:
        svc.reset_listeners_for_tests()


def test_listener_exception_does_not_break_put(store, caplog):
    """A misbehaving listener must not fail the user-facing write."""
    import logging

    from services import connector_credentials as svc

    def boom(**_kwargs):
        raise RuntimeError("listener exploded")

    svc.reset_listeners_for_tests()
    svc.register_change_listener(boom)
    try:
        with caplog.at_level(logging.ERROR, logger="services.connector_credentials"):
            record = svc.put_payload("alice", "testconnector", {"k": "v"}, actor="self")
        assert record.connector == "testconnector"
        assert any("listener" in r.message for r in caplog.records)
    finally:
        svc.reset_listeners_for_tests()


def test_listener_order_preserved_and_all_fire(store):
    from services import connector_credentials as svc

    seen: list[str] = []
    svc.reset_listeners_for_tests()
    svc.register_change_listener(lambda **_kw: seen.append("first"))
    svc.register_change_listener(lambda **_kw: seen.append("second"))
    try:
        svc.put_payload("alice", "testconnector", {"k": "v"}, actor="self")
        assert seen == ["first", "second"]
    finally:
        svc.reset_listeners_for_tests()


def test_reset_listeners_for_tests_clears_all(store):
    from services import connector_credentials as svc

    seen: list[str] = []
    svc.register_change_listener(lambda **_kw: seen.append("x"))
    svc.reset_listeners_for_tests()
    svc.put_payload("alice", "testconnector", {"k": "v"}, actor="self")
    assert seen == []
