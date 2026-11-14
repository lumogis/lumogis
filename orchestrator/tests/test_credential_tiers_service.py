# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for ``services/credential_tiers.py``.

Pinned by ADR ``credential_scopes_shared_system`` and the
implementation plan §"Test cases / Unit tests".

Covers, for BOTH the household + instance/system tiers:

* CRUD round-trips (put → get_payload, get_record metadata, list_records,
  delete_payload).
* Audit emission with the new ``tier`` field in ``input_summary``,
  including the **count-first precondition rule** described in
  ``## Test infrastructure / _FakeAuditCapture precondition rule``.
* ``audit_log.user_id`` semantics — the acting admin's bare id parsed
  from ``admin:<id>``, or ``"default"`` for ``actor="system"``.
* Actor-string validation (``self`` rejected for tier paths; ``system``
  + ``admin:<id>`` accepted).
* Registry-strictness split mirroring the per-user tier (PUT / GET
  payload registry-strict; record / delete format-strict only).
* Rotation skip-vs-rotate behaviour using ``MultiFernet.rotate``.
* Diagnostic counters (``count_rows_by_key_version``).

Plus the cross-tier no-drift regressions on the per-user path:

* The per-user PUT / DELETE / ROTATED audit ``user_id`` is **still**
  the row's owner (NOT the acting admin), and ``input_summary["tier"]``
  is ``"user"``.

Backing store: :class:`_TiersFakeStore` — an in-memory Postgres-shaped
fake that recognises every SQL prefix the service emits and **raises
AssertionError on unknown SQL** (mandatory dispatch fallthrough rule;
prevents the silent-no-op cascade flagged in the plan critique).
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime
from datetime import timezone
from typing import Any

import pytest
from cryptography.fernet import Fernet

# ---------------------------------------------------------------------------
# `_TiersFakeStore` — see plan §`_TiersFakeStore` SQL pattern catalogue.
# ---------------------------------------------------------------------------


class _TiersFakeStore:
    """In-memory Postgres-shaped backing store covering the SQL the
    tier service + the per-user service emit.

    Mandatory dispatch fallthrough rule: every public method raises
    ``AssertionError(f"_TiersFakeStore: unhandled SQL: {sql!r}")`` on
    an unknown SQL prefix. Returning ``None`` / ``[]`` for an
    unrecognised statement is forbidden — that would let a future
    schema-touching change silently no-op a test.
    """

    def __init__(self) -> None:
        self.household: dict[str, dict[str, Any]] = {}
        self.system: dict[str, dict[str, Any]] = {}
        self.user: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.audit_log_rows: list[dict[str, Any]] = []
        self.exec_log: list[tuple[str, tuple]] = []

    @staticmethod
    def _norm(query: str) -> str:
        return " ".join(query.split()).lower()

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    @contextlib.contextmanager
    def transaction(self):
        yield

    def insert_household_raw(self, **kwargs) -> None:
        now = datetime.now(timezone.utc)
        kwargs.setdefault("created_at", now)
        kwargs.setdefault("updated_at", now)
        kwargs.setdefault("created_by", "admin:bootstrap")
        kwargs.setdefault("updated_by", "admin:bootstrap")
        self.household[kwargs["connector"]] = kwargs

    def insert_system_raw(self, **kwargs) -> None:
        now = datetime.now(timezone.utc)
        kwargs.setdefault("created_at", now)
        kwargs.setdefault("updated_at", now)
        kwargs.setdefault("created_by", "admin:bootstrap")
        kwargs.setdefault("updated_by", "admin:bootstrap")
        self.system[kwargs["connector"]] = kwargs

    def insert_user_raw(self, **kwargs) -> None:
        now = datetime.now(timezone.utc)
        kwargs.setdefault("created_at", now)
        kwargs.setdefault("updated_at", now)
        kwargs.setdefault("created_by", "self")
        kwargs.setdefault("updated_by", "self")
        kwargs.setdefault("instance", "")
        self.user[(kwargs["user_id"], kwargs["connector"], kwargs["instance"])] = kwargs

    @staticmethod
    def _project_household(row: dict) -> dict:
        return {
            "connector": row["connector"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "created_by": row["created_by"],
            "updated_by": row["updated_by"],
            "key_version": row["key_version"],
        }

    _project_system = _project_household

    @staticmethod
    def _project_user(row: dict) -> dict:
        return {
            "user_id": row["user_id"],
            "connector": row["connector"],
            "instance": row.get("instance", ""),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "created_by": row["created_by"],
            "updated_by": row["updated_by"],
            "key_version": row["key_version"],
            # LUM-834 chunk B. `configured_keys` is `.get(...)` with no default
            # on purpose: NULL is a real state (pre-migration row) and coercing
            # it to [] would make a working credential report no fields.
            "enabled": row.get("enabled", True),
            "configured_keys": row.get("configured_keys"),
        }

    def execute(self, query: str, params: tuple | None = None) -> int:
        self.exec_log.append((query, params or ()))
        q = self._norm(query)
        p = params or ()

        if q.startswith("update household_connector_credentials set ciphertext"):
            ciphertext, key_version, updated_by, connector = p
            row = self.household.get(connector)
            if row is not None:
                row["ciphertext"] = ciphertext
                row["key_version"] = key_version
                row["updated_by"] = updated_by
                row["updated_at"] = datetime.now(timezone.utc)
            return 1 if row else 0

        if q.startswith("update instance_system_connector_credentials set ciphertext"):
            ciphertext, key_version, updated_by, connector = p
            row = self.system.get(connector)
            if row is not None:
                row["ciphertext"] = ciphertext
                row["key_version"] = key_version
                row["updated_by"] = updated_by
                row["updated_at"] = datetime.now(timezone.utc)
            return 1 if row else 0

        if q.startswith("update user_connector_credentials set ciphertext"):
            ciphertext, key_version, updated_by, user_id, connector, instance = p
            row = self.user.get((user_id, connector, instance))
            if row is not None:
                row["ciphertext"] = ciphertext
                row["key_version"] = key_version
                row["updated_by"] = updated_by
                row["updated_at"] = datetime.now(timezone.utc)
            return 1 if row else 0

        raise AssertionError(f"_TiersFakeStore: unhandled SQL in execute(): {query!r} params={p!r}")

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        self.exec_log.append((query, params or ()))
        q = self._norm(query)
        p = params or ()

        # Household table
        if q.startswith(
            "select connector, created_at, updated_at, created_by, updated_by, "
            "key_version from household_connector_credentials"
        ):
            (connector,) = p
            row = self.household.get(connector)
            return self._project_household(row) if row else None

        if q.startswith("select ciphertext from household_connector_credentials"):
            (connector,) = p
            row = self.household.get(connector)
            return {"ciphertext": row["ciphertext"]} if row else None

        if q.startswith("insert into household_connector_credentials"):
            connector, ciphertext, key_version, created_by, updated_by = p
            now = datetime.now(timezone.utc)
            existing = self.household.get(connector)
            if existing is None:
                row = {
                    "connector": connector,
                    "ciphertext": ciphertext,
                    "key_version": key_version,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": created_by,
                    "updated_by": updated_by,
                }
            else:
                # ON CONFLICT DO UPDATE: created_at + created_by preserved
                row = dict(existing)
                row["ciphertext"] = ciphertext
                row["key_version"] = key_version
                row["updated_at"] = now
                row["updated_by"] = updated_by
            self.household[connector] = row
            return self._project_household(row)

        if q.startswith("delete from household_connector_credentials"):
            (connector,) = p
            row = self.household.pop(connector, None)
            return {"key_version": row["key_version"]} if row else None

        # Instance/system table
        if q.startswith(
            "select connector, created_at, updated_at, created_by, updated_by, "
            "key_version from instance_system_connector_credentials"
        ):
            (connector,) = p
            row = self.system.get(connector)
            return self._project_system(row) if row else None

        if q.startswith("select ciphertext from instance_system_connector_credentials"):
            (connector,) = p
            row = self.system.get(connector)
            return {"ciphertext": row["ciphertext"]} if row else None

        if q.startswith("insert into instance_system_connector_credentials"):
            connector, ciphertext, key_version, created_by, updated_by = p
            now = datetime.now(timezone.utc)
            existing = self.system.get(connector)
            if existing is None:
                row = {
                    "connector": connector,
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
            self.system[connector] = row
            return self._project_system(row)

        if q.startswith("delete from instance_system_connector_credentials"):
            (connector,) = p
            row = self.system.pop(connector, None)
            return {"key_version": row["key_version"]} if row else None

        # User table (delegate path used by the resolver / per-user audit
        # no-drift tests).
        # LUM-834 chunk B — is_disabled(). Absent row => False (not
        # disabled): a connector nobody configured is not one somebody
        # switched off, and answering otherwise would stop the tier walk
        # for every user without a row.
        # LUM-834 chunk B — set_enabled(). Never touches ciphertext.
        if q.startswith("update user_connector_credentials set enabled = %s"):
            enabled, actor, user_id, connector, instance = p
            row = self.user.get((user_id, connector, instance))
            if row is None:
                return None
            row["enabled"] = bool(enabled)
            row["updated_by"] = actor
            return self._project_user(row)

        if q.startswith("select enabled from user_connector_credentials"):
            user_id, connector, instance = p
            row = self.user.get((user_id, connector, instance))
            if row is None:
                return None
            return {"enabled": bool(row.get("enabled", True))}

        if q.startswith("select ciphertext from user_connector_credentials"):
            # LUM-834 chunk B: the real query carries `AND enabled`, so a
            # disabled row yields no ciphertext and is indistinguishable from an
            # absent one.
            #
            # The filter is applied ONLY when the statement actually contains
            # `and enabled`. Applying it unconditionally would move the gate
            # INTO THE FAKE: delete `AND enabled` from `get_payload` or
            # `resolve` and every gate test would still pass, because the fake
            # would keep enforcing what production had stopped enforcing. §5a —
            # the fake must fail the way the real collaborator fails, and real
            # Postgres has no opinion about a predicate that is not there.
            user_id, connector, instance = p
            row = self.user.get((user_id, connector, instance))
            if row is None:
                return None
            if "and enabled" in q and not row.get("enabled", True):
                return None
            return {"ciphertext": row["ciphertext"]}

        if (
            q.startswith("select user_id, connector, instance, created_at, updated_at,")
            and "where user_id = %s and connector = %s and instance = %s" in q
        ):
            user_id, connector, instance = p
            row = self.user.get((user_id, connector, instance))
            return self._project_user(row) if row else None

        if q.startswith("insert into user_connector_credentials"):
            (
                user_id,
                connector,
                instance,
                ciphertext,
                key_version,
                created_by,
                updated_by,
                configured_keys,
            ) = p
            now = datetime.now(timezone.utc)
            existing = self.user.get((user_id, connector, instance))
            if existing is None:
                row = {
                    "user_id": user_id,
                    "connector": connector,
                    "instance": instance,
                    "ciphertext": ciphertext,
                    "key_version": key_version,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": created_by,
                    "updated_by": updated_by,
                    "enabled": True,
                    "configured_keys": configured_keys,
                }
            else:
                row = dict(existing)
                row["ciphertext"] = ciphertext
                row["key_version"] = key_version
                row["updated_at"] = now
                row["updated_by"] = updated_by
                # Replaced, not merged — and `enabled` is deliberately
                # NOT touched, mirroring the real DO UPDATE SET.
                row["configured_keys"] = configured_keys
            self.user[(user_id, connector, instance)] = row
            return self._project_user(row)

        if q.startswith("delete from user_connector_credentials"):
            user_id, connector, instance = p
            row = self.user.pop((user_id, connector, instance), None)
            return {"key_version": row["key_version"]} if row else None

        # Audit insert (canonical path through actions.audit.write_audit).
        if q.startswith("insert into audit_log"):
            row_id = len(self.audit_log_rows) + 1
            self.audit_log_rows.append(
                {
                    "id": row_id,
                    "user_id": p[0],
                    "action_name": p[1],
                    "connector": p[2],
                    "mode": p[3],
                    "input_summary": p[4],
                    "result_summary": p[5],
                }
            )
            return {"id": row_id}

        raise AssertionError(
            f"_TiersFakeStore: unhandled SQL in fetch_one(): {query!r} params={p!r}"
        )

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        self.exec_log.append((query, params or ()))
        q = self._norm(query)
        p = params or ()

        if q.startswith(
            "select connector, created_at, updated_at, created_by, updated_by, "
            "key_version from household_connector_credentials "
            "order by connector asc"
        ):
            return sorted(
                (self._project_household(r) for r in self.household.values()),
                key=lambda r: r["connector"],
            )

        if q.startswith(
            "select connector, created_at, updated_at, created_by, updated_by, "
            "key_version from instance_system_connector_credentials "
            "order by connector asc"
        ):
            return sorted(
                (self._project_system(r) for r in self.system.values()),
                key=lambda r: r["connector"],
            )

        if q.startswith("select connector, ciphertext, key_version") and (
            "from household_connector_credentials" in q
        ):
            return sorted(
                (
                    {
                        "connector": r["connector"],
                        "ciphertext": r["ciphertext"],
                        "key_version": r["key_version"],
                    }
                    for r in self.household.values()
                ),
                key=lambda r: r["connector"],
            )

        if q.startswith("select connector, ciphertext, key_version") and (
            "from instance_system_connector_credentials" in q
        ):
            return sorted(
                (
                    {
                        "connector": r["connector"],
                        "ciphertext": r["ciphertext"],
                        "key_version": r["key_version"],
                    }
                    for r in self.system.values()
                ),
                key=lambda r: r["connector"],
            )

        if q.startswith("select user_id, connector, instance, ciphertext, key_version"):
            return sorted(
                (
                    {
                        "user_id": r["user_id"],
                        "connector": r["connector"],
                        "instance": r.get("instance", ""),
                        "ciphertext": r["ciphertext"],
                        "key_version": r["key_version"],
                    }
                    for r in self.user.values()
                ),
                key=lambda r: (r["user_id"], r["connector"], r.get("instance", "")),
            )

        # Per-user listing route: ORDER BY connector, instance for one user.
        if (
            q.startswith("select user_id, connector, instance, created_at, updated_at,")
            and "from user_connector_credentials" in q
            and "where user_id = %s" in q
        ):
            (user_id,) = p
            return sorted(
                (self._project_user(r) for r in self.user.values() if r["user_id"] == user_id),
                key=lambda r: (r["connector"], r.get("instance", "")),
            )

        if q.startswith("select key_version, count") and (
            "from household_connector_credentials" in q
        ):
            counts: dict[int, int] = {}
            for row in self.household.values():
                counts[row["key_version"]] = counts.get(row["key_version"], 0) + 1
            return [{"key_version": k, "n": v} for k, v in sorted(counts.items())]

        if q.startswith("select key_version, count") and (
            "from instance_system_connector_credentials" in q
        ):
            counts: dict[int, int] = {}
            for row in self.system.values():
                counts[row["key_version"]] = counts.get(row["key_version"], 0) + 1
            return [{"key_version": k, "n": v} for k, v in sorted(counts.items())]

        raise AssertionError(
            f"_TiersFakeStore: unhandled SQL in fetch_all(): {query!r} params={p!r}"
        )


# ---------------------------------------------------------------------------
# Audit-capture helper. Implements the count-first precondition rule
# pinned in ``## Test infrastructure / _FakeAuditCapture precondition``.
# ---------------------------------------------------------------------------


def _captured_audits(store: _TiersFakeStore) -> list[dict[str, Any]]:
    """Return the audit_log rows the test wrote, in insertion order."""
    return list(store.audit_log_rows)


def _assert_one_audit(
    store: _TiersFakeStore,
    *,
    before: int = 0,
) -> dict[str, Any]:
    """Count-first audit assertion (D6.2 silent-no-op killer).

    Returns the single newly-emitted audit row. Raises an AssertionError
    with a precise message before any field-level assertion can be
    written against an empty list (which would silently succeed via
    ``IndexError`` getting converted to ``pytest.skip(...)`` in some
    refactors).
    """
    audits = _captured_audits(store)
    new = audits[before:]
    assert len(new) == 1, (
        f"expected exactly 1 new audit row since index {before}, got {len(new)}: {new!r}"
    )
    return new[0]


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def fernet_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def store(monkeypatch, fernet_key: str):
    """Install the tiers fake store + fresh single-key Fernet env."""
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
# Per-tier round-trip (parametrised over both tiers — explicit functions
# per ADR Open Question #6 for failure-message clarity, but consolidated
# here to keep the file readable).
# ---------------------------------------------------------------------------


_TIER_LABELS = ("household", "system")


def _put_payload_for(tier: str):
    from services import credential_tiers as cts

    return cts.household_put_payload if tier == "household" else cts.system_put_payload


def _get_payload_for(tier: str):
    from services import credential_tiers as cts

    return cts.household_get_payload if tier == "household" else cts.system_get_payload


def _get_record_for(tier: str):
    from services import credential_tiers as cts

    return cts.household_get_record if tier == "household" else cts.system_get_record


def _list_records_for(tier: str):
    from services import credential_tiers as cts

    return cts.household_list_records if tier == "household" else cts.system_list_records


def _delete_payload_for(tier: str):
    from services import credential_tiers as cts

    return cts.household_delete_payload if tier == "household" else cts.system_delete_payload


def _count_for(tier: str):
    from services import credential_tiers as cts

    return (
        cts.household_count_rows_by_key_version
        if tier == "household"
        else cts.system_count_rows_by_key_version
    )


def _reencrypt_for(tier: str):
    from services import credential_tiers as cts

    return (
        cts.reencrypt_household_to_current_version
        if tier == "household"
        else cts.reencrypt_system_to_current_version
    )


def _store_dict_for(store: _TiersFakeStore, tier: str) -> dict:
    return store.household if tier == "household" else store.system


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_put_payload_inserts_and_returns_record(store, tier):
    from services._credential_internals import _current_key_version

    record = _put_payload_for(tier)(
        "testconnector",
        {"k": "v"},
        actor="admin:alice",
    )
    assert record.connector == "testconnector"
    assert record.created_by == "admin:alice"
    assert record.updated_by == "admin:alice"
    assert record.key_version == _current_key_version()


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_put_then_get_payload_round_trips(store, tier):
    _put_payload_for(tier)("testconnector", {"k": "v"}, actor="admin:alice")
    assert _get_payload_for(tier)("testconnector") == {"k": "v"}


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_put_payload_updates_existing_row_preserves_created(store, tier):
    """ON CONFLICT DO UPDATE preserves created_at + created_by — the
    audit-attribution invariant pinned by the plan."""
    _put_payload_for(tier)("testconnector", {"k": "v1"}, actor="admin:alice")
    backing = _store_dict_for(store, tier)
    original = dict(backing["testconnector"])

    _put_payload_for(tier)("testconnector", {"k": "v2"}, actor="admin:bob")
    updated = backing["testconnector"]
    assert updated["created_at"] == original["created_at"]
    assert updated["created_by"] == original["created_by"]
    assert updated["updated_by"] == "admin:bob"
    assert _get_payload_for(tier)("testconnector") == {"k": "v2"}


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_put_payload_emits_audit_with_tier_in_input_summary(store, tier):
    before = len(store.audit_log_rows)
    _put_payload_for(tier)("testconnector", {"k": "v"}, actor="admin:alice")
    audit = _assert_one_audit(store, before=before)
    assert audit["action_name"] == "__connector_credential__.put"
    assert audit["user_id"] == "alice"
    summary = json.loads(audit["input_summary"])
    assert summary["tier"] == tier
    assert summary["actor"] == "admin:alice"


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_put_payload_actor_self_rejected(store, tier):
    with pytest.raises(ValueError):
        _put_payload_for(tier)("testconnector", {"k": "v"}, actor="self")


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_put_payload_actor_system_uses_default_user_id(store, tier):
    before = len(store.audit_log_rows)
    _put_payload_for(tier)("testconnector", {"k": "v"}, actor="system")
    audit = _assert_one_audit(store, before=before)
    assert audit["user_id"] == "default"
    assert json.loads(audit["input_summary"])["tier"] == tier


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_put_payload_unknown_connector_raises_unknown_connector(store, tier):
    from connectors.registry import UnknownConnector

    with pytest.raises(UnknownConnector):
        _put_payload_for(tier)(
            "definitely_not_registered",
            {"k": "v"},
            actor="admin:alice",
        )


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_put_payload_bad_format_raises_value_error(store, tier):
    from connectors.registry import UnknownConnector

    with pytest.raises((ValueError, UnknownConnector)):
        _put_payload_for(tier)(
            "BAD CONNECTOR",
            {"k": "v"},
            actor="admin:alice",
        )


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_get_record_returns_metadata_only(store, tier):
    _put_payload_for(tier)("testconnector", {"k": "v"}, actor="admin:alice")
    record = _get_record_for(tier)("testconnector")
    assert record is not None
    assert record.connector == "testconnector"
    assert record.created_by == "admin:alice"


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_get_record_returns_none_on_miss(store, tier):
    assert _get_record_for(tier)("testconnector") is None


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_get_payload_returns_none_on_miss(store, tier):
    assert _get_payload_for(tier)("testconnector") is None


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_get_payload_invalid_token_raises_credential_unavailable(
    store,
    tier,
):
    from services._credential_internals import CredentialUnavailable
    from services._credential_internals import _current_key_version

    backing = _store_dict_for(store, tier)
    backing["testconnector"] = {
        "connector": "testconnector",
        "ciphertext": b"not-a-valid-fernet-token",
        "key_version": _current_key_version(),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "created_by": "admin:alice",
        "updated_by": "admin:alice",
    }
    with pytest.raises(CredentialUnavailable):
        _get_payload_for(tier)("testconnector")


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_list_records_orders_by_connector_asc(store, tier):
    from connectors import registry as reg

    reg.register("aregisteredconnector", description="test fixture")
    try:
        _put_payload_for(tier)("testconnector", {"k": 1}, actor="admin:alice")
        _put_payload_for(tier)(
            "aregisteredconnector",
            {"k": 2},
            actor="admin:alice",
        )
        records = _list_records_for(tier)()
        assert [r.connector for r in records] == [
            "aregisteredconnector",
            "testconnector",
        ]
    finally:
        reg.CONNECTORS.pop("aregisteredconnector", None)
        reg.REGISTERED_CONNECTORS = frozenset(reg.CONNECTORS.keys())


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_delete_payload_returns_true_and_audits(store, tier):
    _put_payload_for(tier)("testconnector", {"k": "v"}, actor="admin:alice")
    before = len(store.audit_log_rows)
    assert _delete_payload_for(tier)("testconnector", actor="admin:alice") is True

    audit = _assert_one_audit(store, before=before)
    assert audit["action_name"] == "__connector_credential__.deleted"
    assert audit["user_id"] == "alice"
    assert json.loads(audit["input_summary"])["tier"] == tier


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_delete_payload_returns_false_on_miss_and_no_audit(store, tier):
    before = len(store.audit_log_rows)
    assert _delete_payload_for(tier)("testconnector", actor="admin:alice") is False
    assert len(store.audit_log_rows) == before


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_count_rows_by_key_version_returns_dict(store, tier):
    from services._credential_internals import _current_key_version

    assert _count_for(tier)() == {}
    _put_payload_for(tier)("testconnector", {"k": "v"}, actor="admin:alice")
    assert _count_for(tier)() == {_current_key_version(): 1}


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_reencrypt_skips_when_key_version_matches_current(store, tier):
    _put_payload_for(tier)("testconnector", {"k": "v"}, actor="admin:alice")
    result = _reencrypt_for(tier)(actor="system")
    assert result == {"rotated": 0, "skipped": 1, "failed": 0}


@pytest.mark.parametrize("tier", _TIER_LABELS)
def test_tier_reencrypt_rotates_when_key_version_differs(
    store,
    tier,
    monkeypatch,
    fernet_key,
):
    """Seed under key K1; introduce K2 as primary; rotate; assert
    one row rotated and the new ciphertext decrypts to the original
    payload under the new primary."""
    from services import _credential_internals as ci

    _put_payload_for(tier)("testconnector", {"k": "v"}, actor="admin:alice")
    old_key_version = ci._current_key_version()

    new_primary = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", new_primary)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", f"{new_primary},{fernet_key}")
    ci.reset_for_tests()
    new_key_version = ci._current_key_version()
    assert new_key_version != old_key_version

    result = _reencrypt_for(tier)(actor="system")
    assert result == {"rotated": 1, "skipped": 0, "failed": 0}

    backing = _store_dict_for(store, tier)
    assert backing["testconnector"]["key_version"] == new_key_version
    assert _get_payload_for(tier)("testconnector") == {"k": "v"}


# ---------------------------------------------------------------------------
# Internals — actor validation + audit shape.
# ---------------------------------------------------------------------------


def test_actor_str_tiered_rejects_self():
    from services._credential_internals import _actor_str_tiered

    with pytest.raises(ValueError):
        _actor_str_tiered("self")


def test_actor_str_tiered_accepts_system_and_admin():
    from services._credential_internals import _actor_str_tiered

    assert _actor_str_tiered("system") == "system"
    assert _actor_str_tiered("admin:alice") == "admin:alice"


def test_emit_audit_includes_tier_in_input_summary(store):
    """Direct unit test on the shared internal helper.

    Pinned by ## Test infrastructure / _FakeAuditCapture precondition
    rule — count-first BEFORE any field assertion.
    """
    from services._credential_internals import _emit_audit

    before = len(store.audit_log_rows)
    _emit_audit(
        "alice",
        "testconnector",
        "__connector_credential__.put",
        actor="admin:alice",
        key_version=12345,
        tier="household",
    )
    audit = _assert_one_audit(store, before=before)
    summary = json.loads(audit["input_summary"])
    assert summary["tier"] == "household"
    assert summary["actor"] == "admin:alice"
    assert summary["key_version"] == 12345


def test_emit_audit_default_str_serialises_non_serialisable_actor(store):
    """D6.9: ``json.dumps(..., default=str)`` exercises the fallback
    so a non-trivial actor object stringifies cleanly without raising."""
    from services._credential_internals import _emit_audit

    before = len(store.audit_log_rows)
    actor_obj = datetime(2026, 4, 22, tzinfo=timezone.utc)
    _emit_audit(
        "alice",
        "testconnector",
        "__connector_credential__.put",
        actor=actor_obj,
        key_version=1,
        tier="household",
    )
    audit = _assert_one_audit(store, before=before)
    summary = json.loads(audit["input_summary"])
    assert "2026-04-22" in summary["actor"]


def test_get_multifernet_is_process_cached(store):
    from services import _credential_internals as ci

    a = ci._get_multifernet()
    b = ci._get_multifernet()
    assert a is b

    ci.reset_for_tests()
    c = ci._get_multifernet()
    assert c is not a


def test_load_keys_rejects_placeholder(monkeypatch):
    from services import _credential_internals as ci

    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", "change-me-in-production")
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    ci.reset_for_tests()
    with pytest.raises(RuntimeError):
        ci._load_keys()


# ---------------------------------------------------------------------------
# Per-user audit no-drift regressions (the critical assertion the user
# explicitly preserved).
# ---------------------------------------------------------------------------


def test_audit_per_user_put_still_uses_target_user_id_and_tier_user(store):
    """After the helper move, per-user PUT must STILL emit
    ``audit_log.user_id == target_user_id`` (NOT the acting admin's
    id) and ``input_summary.tier == "user"``."""
    from services.connector_credentials import put_payload

    before = len(store.audit_log_rows)
    put_payload("alice", "testconnector", {"k": "v"}, actor="admin:bob")
    audit = _assert_one_audit(store, before=before)
    assert audit["user_id"] == "alice"
    assert json.loads(audit["input_summary"])["tier"] == "user"


def test_audit_per_user_delete_still_uses_target_user_id_and_tier_user(store):
    """Regression on the per-user DELETE path (D5.2 / D6.3)."""
    from services.connector_credentials import delete_payload
    from services.connector_credentials import put_payload

    put_payload("alice", "testconnector", {"k": "v"}, actor="admin:bob")
    before = len(store.audit_log_rows)
    delete_payload("alice", "testconnector", actor="admin:bob")
    audit = _assert_one_audit(store, before=before)
    assert audit["action_name"] == "__connector_credential__.deleted"
    assert audit["user_id"] == "alice"
    assert json.loads(audit["input_summary"])["tier"] == "user"


def test_audit_per_user_rotate_still_uses_target_user_id(
    store,
    monkeypatch,
    fernet_key,
):
    """Per-user rotation preserves the row's owner identity in
    audit (NOT the rotation actor "system")."""
    from services.connector_credentials import put_payload
    from services.connector_credentials import reencrypt_all_to_current_version

    from services import _credential_internals as ci

    put_payload("alice", "testconnector", {"k": "v"}, actor="self")

    new_primary = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", new_primary)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", f"{new_primary},{fernet_key}")
    ci.reset_for_tests()

    before = len(store.audit_log_rows)
    reencrypt_all_to_current_version(actor="system")

    rotation_audits = [
        a
        for a in store.audit_log_rows[before:]
        if a["action_name"] == "__connector_credential__.rotated"
    ]
    user_rotation = [
        a for a in rotation_audits if json.loads(a["input_summary"]).get("tier") == "user"
    ]
    assert len(user_rotation) == 1, (
        f"expected exactly 1 per-user rotation audit, got {len(user_rotation)}: {user_rotation!r}"
    )
    assert user_rotation[0]["user_id"] == "alice"


def test_extract_admin_caller_user_id_normalises_actors():
    from services.credential_tiers import _extract_admin_caller_user_id

    assert _extract_admin_caller_user_id("system") == "default"
    assert _extract_admin_caller_user_id("admin:carol") == "carol"
    with pytest.raises(ValueError):
        _extract_admin_caller_user_id("self")
    with pytest.raises(ValueError):
        _extract_admin_caller_user_id("admin:")
    with pytest.raises(ValueError):
        _extract_admin_caller_user_id("admin:" + "x" * 65)


def test_audit_rotation_actor_system_uses_default_user_id_per_tier(
    store,
    monkeypatch,
    fernet_key,
):
    """D5.2 / D6.3 sister test for tier rotation:
    ``actor="system"`` ⇒ ``audit.user_id == "default"`` for both tiers."""
    from services.credential_tiers import household_put_payload
    from services.credential_tiers import reencrypt_household_to_current_version
    from services.credential_tiers import reencrypt_system_to_current_version
    from services.credential_tiers import system_put_payload

    from services import _credential_internals as ci

    household_put_payload("testconnector", {"k": "h"}, actor="admin:alice")
    system_put_payload("testconnector", {"k": "s"}, actor="admin:alice")

    new_primary = Fernet.generate_key().decode()
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", new_primary)
    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEYS", f"{new_primary},{fernet_key}")
    ci.reset_for_tests()

    before = len(store.audit_log_rows)
    reencrypt_household_to_current_version(actor="system")
    reencrypt_system_to_current_version(actor="system")

    rotation_audits = [
        a
        for a in store.audit_log_rows[before:]
        if a["action_name"] == "__connector_credential__.rotated"
    ]
    tier_rotations = [
        a
        for a in rotation_audits
        if json.loads(a["input_summary"]).get("tier") in {"household", "system"}
    ]
    assert len(tier_rotations) == 2, (
        f"expected 2 tier rotation audits, got {len(tier_rotations)}: {tier_rotations!r}"
    )
    for a in tier_rotations:
        assert a["user_id"] == "default"


# ---------------------------------------------------------------------------
# LUM-834 chunk B — the disabled/absent pair at tier 1
#
# These two are the reason `resolve_runtime_credential` needs a SECOND READ.
# Once `get_payload` carries `AND enabled`, a disabled user row and an absent
# one both arrive at tier 1 as `None` — one bit — and they must produce
# OPPOSITE outcomes. Written adjacent because neither is meaningful alone:
# either one passes against an implementation that gets the other wrong.
# ---------------------------------------------------------------------------


def test_a_disabled_user_row_does_NOT_fall_through_to_household(store):
    """A user who switched their own credential off has not asked to start
    using the household one instead.

    Silently substituting the household credential would be the worst kind of
    "it still works": the connector keeps running, on someone else's account,
    and nothing on the screen says so.
    """
    from connectors.registry import TESTCONNECTOR
    from services.credential_tiers import household_put_payload
    from services.credential_tiers import resolve_runtime_credential

    from services import connector_credentials as ccs

    ccs.put_payload("alice", TESTCONNECTOR, {"k": "user"}, actor="self")
    household_put_payload(TESTCONNECTOR, {"k": "household"}, actor="admin:root")
    assert resolve_runtime_credential("alice", TESTCONNECTOR).tier == "user"

    ccs.set_enabled("alice", TESTCONNECTOR, enabled=False, actor="self")

    with pytest.raises(ccs.ConnectorNotConfigured):
        resolve_runtime_credential("alice", TESTCONNECTOR)


def test_an_absent_user_row_still_falls_through_to_household(store):
    """The twin, and the half that keeps every existing deployment working.

    Most users have no row of their own; if `is_disabled` answered True for an
    absent row — or if the gate simply stopped the walk on any tier-1 miss —
    household credentials would become unreachable for all of them.
    """
    from connectors.registry import TESTCONNECTOR
    from services.credential_tiers import household_put_payload
    from services.credential_tiers import resolve_runtime_credential

    household_put_payload(TESTCONNECTOR, {"k": "household"}, actor="admin:root")

    resolved = resolve_runtime_credential("alice", TESTCONNECTOR)

    assert resolved.tier == "household"
    assert resolved.payload == {"k": "household"}
