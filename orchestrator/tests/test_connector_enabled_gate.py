# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-834 chunk B — the ``enabled`` gate, asserted where it actually bites.

## Why these tests are shaped as adjacent pairs

The gate's whole difficulty is that **disabled and absent look identical** once
``get_payload`` carries ``AND enabled``: both return ``None``. Every consumer
that used to read ``None`` as "no row, use the env fallback" would therefore
read a switched-off connector the same way — and under the default
``AUTH_ENABLED=false`` that means *keep working, from the env*.

So almost every test here has a twin written next to it. One asserts the
disabled behaviour, the other asserts that the *absent* behaviour is unchanged.
Neither is meaningful alone: a gate that stops both is not a gate, it is an
outage, and a gate that stops neither is what shipping this without the second
read would have produced.

## The auth-off cases are the important ones

``AUTH_ENABLED`` defaults to **false** (``auth.py:7,61``). That is the ordinary
single-user deployment, and it is the one where every env fallback is live. A
suite that only exercised ``AUTH_ENABLED=true`` would have gone green over a
toggle that changed nothing for most users.
"""

from __future__ import annotations

import pytest
from connectors.registry import CALDAV
from connectors.registry import NTFY
from connectors.registry import PAPERLESS
from connectors.registry import TESTCONNECTOR
from tests.test_connector_credentials_service import _FakeStore

from services import connector_credentials as ccs

_TEST_FERNET_KEY = "OlGLYckGIbBSt54y8XVmgb441LgKJWvvYoHnpQ_cv9A="


@pytest.fixture
def store(monkeypatch):
    import config as _config

    monkeypatch.setenv("LUMOGIS_CREDENTIAL_KEY", _TEST_FERNET_KEY)
    monkeypatch.delenv("LUMOGIS_CREDENTIAL_KEYS", raising=False)
    s = _FakeStore()
    _config._instances["metadata_store"] = s
    ccs.reset_for_tests()
    yield s
    _config._instances.pop("metadata_store", None)
    ccs.reset_for_tests()


def _seed(store, connector: str, payload: dict, *, user: str = "alice") -> None:
    ccs.put_payload(user, connector, payload, actor="self")


def _disable(store, connector: str, *, user: str = "alice") -> None:
    ccs.set_enabled(user, connector, enabled=False, actor="self")


# ---------------------------------------------------------------------------
# The primitive
# ---------------------------------------------------------------------------


def test_a_disabled_connector_raises_rather_than_loading(store):
    """``get_payload`` yields no plaintext for a switched-off row.

    The security half of the gate, and it is unconditional — it does not
    depend on the deployment's auth mode.
    """
    _seed(store, TESTCONNECTOR, {"k": "v"})
    assert ccs.get_payload("alice", TESTCONNECTOR) == {"k": "v"}

    _disable(store, TESTCONNECTOR)
    assert ccs.get_payload("alice", TESTCONNECTOR) is None


def test_disabling_does_not_delete_the_credential(store):
    """G3's entire user-facing promise: turn it off, turn it on, no retyping."""
    _seed(store, TESTCONNECTOR, {"k": "v"})
    _disable(store, TESTCONNECTOR)
    assert ccs.get_payload("alice", TESTCONNECTOR) is None

    ccs.set_enabled("alice", TESTCONNECTOR, enabled=True, actor="self")
    assert ccs.get_payload("alice", TESTCONNECTOR) == {"k": "v"}, (
        "re-enabling must restore the stored payload, not require a fresh PUT"
    )


def test_is_disabled_says_False_for_an_absent_row(store):
    """The contract that makes the tier walk work.

    An absent row must NOT report as disabled. If it did, the tier-1 check
    would stop the walk for every user who has no row of their own — which is
    most of them — and household credentials would become unreachable.
    """
    assert ccs.is_disabled("nobody", TESTCONNECTOR) is False


def test_is_disabled_distinguishes_disabled_from_absent(store):
    _seed(store, TESTCONNECTOR, {"k": "v"})
    assert ccs.is_disabled("alice", TESTCONNECTOR) is False
    _disable(store, TESTCONNECTOR)
    assert ccs.is_disabled("alice", TESTCONNECTOR) is True


def test_delivery_paused_and_enabled_are_independent(store):
    """Two flags, two owners: `delivery_paused` is system-set, `enabled` user-set.

    Merging them would let a delivery failure overwrite a deliberate off, and
    let recovery silently switch a connector back on.
    """
    _seed(store, NTFY, {"topic": "t"})
    ccs.set_delivery_paused("alice", NTFY, paused=True, reason="gone", detail=None, actor="system")
    rec = ccs.get_record("alice", NTFY)
    assert rec is not None and rec.delivery_paused is True and rec.enabled is True

    ccs.set_enabled("alice", NTFY, enabled=False, actor="self")
    rec = ccs.get_record("alice", NTFY)
    assert rec is not None and rec.delivery_paused is True and rec.enabled is False


# ---------------------------------------------------------------------------
# put_payload must not touch `enabled`
# ---------------------------------------------------------------------------


def test_put_payload_leaves_enabled_unchanged(store):
    """Saving a credential must not silently switch it back on.

    The card lets a user edit a disabled connector on purpose — fix the
    credential first, then re-enable. If the UPSERT reset `enabled` the way it
    resets `delivery_paused`, that flow would re-enable behind their back.
    """
    _seed(store, TESTCONNECTOR, {"k": "v"})
    _disable(store, TESTCONNECTOR)

    ccs.put_payload("alice", TESTCONNECTOR, {"k": "v2"}, actor="self")

    rec = ccs.get_record("alice", TESTCONNECTOR)
    assert rec is not None and rec.enabled is False, (
        "a re-PUT re-enabled a connector the user had switched off"
    )


def test_a_system_actor_refresh_cannot_re_enable(store):
    """The background OAuth token refresh writes through `put_payload(actor="system")`.

    That path runs with no user present. If the UPSERT reset `enabled`, a
    connector would switch itself back on with no user action at all — the
    worst version of the previous test, because there is nobody to notice.
    """
    _seed(store, TESTCONNECTOR, {"k": "v"})
    _disable(store, TESTCONNECTOR)

    ccs.put_payload("alice", TESTCONNECTOR, {"k": "refreshed"}, actor="system")

    rec = ccs.get_record("alice", TESTCONNECTOR)
    assert rec is not None and rec.enabled is False


# ---------------------------------------------------------------------------
# configured_keys — the three states
# ---------------------------------------------------------------------------


def test_configured_keys_is_written_from_the_payload_on_put(store):
    _seed(store, CALDAV, {"base_url": "u", "username": "n", "password": "p"})
    rec = ccs.get_record("alice", CALDAV)
    assert rec is not None
    assert rec.configured_keys == ["base_url", "password", "username"]


def test_configured_keys_and_the_payload_cannot_disagree(store):
    """The claim the second-copy design rests on, asserted rather than argued.

    `configured_keys` is written in the same INSERT as the ciphertext it
    describes, from that exact payload — so there is no code path that writes
    one without the other.
    """
    payload = {"base_url": "u", "username": "n", "password": "p"}
    _seed(store, CALDAV, payload)
    rec = ccs.get_record("alice", CALDAV)
    assert rec is not None
    assert rec.configured_keys == sorted(payload.keys())
    assert ccs.get_payload("alice", CALDAV) == payload


def test_rotating_a_credential_rewrites_configured_keys(store):
    """A re-PUT with different keys must not leave the old list behind."""
    _seed(store, CALDAV, {"base_url": "u", "username": "n", "password": "p"})
    ccs.put_payload("alice", CALDAV, {"base_url": "u", "token": "t"}, actor="self")
    rec = ccs.get_record("alice", CALDAV)
    assert rec is not None
    assert rec.configured_keys == ["base_url", "token"]
    assert "password" not in (rec.configured_keys or [])


def test_a_pre_migration_row_reports_configured_keys_as_UNKNOWN_not_empty(store):
    """The blocking defect the plan's self-review caught, pinned at the dataclass.

    A row written before migration 079 has `configured_keys IS NULL`, and NULL
    means **unknown** — not "no fields set". Coercing it to `[]` would render a
    fully configured, working connector as unconfigured: ADR-199's named
    failure mode arriving through a migration default.

    Asserted at the RECORD layer, not only at the wire: `_record_from_row`
    contains the opposite pattern one line away (`instance=... or ""`), so the
    coercion would happen here first and the wire test would never see it.
    """
    _seed(store, TESTCONNECTOR, {"k": "v"})
    # Simulate the pre-migration row: the column exists, the value is NULL.
    store.rows[("alice", TESTCONNECTOR, "")]["configured_keys"] = None

    rec = ccs.get_record("alice", TESTCONNECTOR)
    assert rec is not None
    assert rec.configured_keys is None, (
        "NULL was coerced to a list; a working credential now claims no fields"
    )
    assert rec.configured_keys != []


def test_the_next_put_populates_configured_keys_on_a_legacy_row(store):
    """Unknown is a transient state, not a permanent one — no backfill needed.

    Specifically no *decrypt-everything* backfill, which would be worse than
    the problem it solved.
    """
    _seed(store, TESTCONNECTOR, {"k": "v"})
    store.rows[("alice", TESTCONNECTOR, "")]["configured_keys"] = None
    assert ccs.get_record("alice", TESTCONNECTOR).configured_keys is None

    ccs.put_payload("alice", TESTCONNECTOR, {"k": "v", "extra": "e"}, actor="self")
    assert ccs.get_record("alice", TESTCONNECTOR).configured_keys == ["extra", "k"]


def test_an_empty_configured_keys_array_is_distinct_from_unknown(store):
    """The third arm of migration 014's triad.

    `'{}'` is impossible for a real credential — `put_payload` rejects an empty
    payload upstream — so it means a data error, and it must not be confused
    with NULL. The two reach the record as different values, which is what lets
    the card treat them differently.
    """
    _seed(store, TESTCONNECTOR, {"k": "v"})
    store.rows[("alice", TESTCONNECTOR, "")]["configured_keys"] = []
    assert ccs.get_record("alice", TESTCONNECTOR).configured_keys == []

    store.rows[("alice", TESTCONNECTOR, "")]["configured_keys"] = None
    assert ccs.get_record("alice", TESTCONNECTOR).configured_keys is None


# ---------------------------------------------------------------------------
# ccs.resolve — the third SQL path AND its own env fallback
# ---------------------------------------------------------------------------


def test_ccs_resolve_also_honours_enabled(store, monkeypatch):
    """`resolve` duplicates the ciphertext SELECT rather than calling get_payload.

    Gating one and not the other would leave a decrypt path wide open.
    """
    monkeypatch.setenv("AUTH_ENABLED", "true")
    _seed(store, TESTCONNECTOR, {"k": "v"})
    assert ccs.resolve("alice", TESTCONNECTOR) == {"k": "v"}

    _disable(store, TESTCONNECTOR)
    with pytest.raises(ccs.ConnectorNotConfigured):
        ccs.resolve("alice", TESTCONNECTOR)


def test_ccs_resolve_does_not_fall_back_to_env_for_a_disabled_row(store, monkeypatch):
    """Written adjacent to the test above, because that one goes GREEN without this.

    `test_ccs_resolve_also_honours_enabled` passes no `fallback_env`, so there
    is no fallback to take and it cannot see the hole. `resolve` has its OWN
    env fallback eight lines below the SELECT: with `AND enabled` alone, a
    disabled row and an absent one are identical inside the function, and
    `_resolve_env_fallback` hands back the deployment's env credential under
    the default `AUTH_ENABLED=false`.

    That is the same "the toggle appears broken" no-op the five loader guards
    exist to stop, reproduced inside the one function this chunk singles out.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("TESTCONNECTOR_SECRET", "from-the-environment")
    _seed(store, TESTCONNECTOR, {"k": "v"})
    _disable(store, TESTCONNECTOR)

    with pytest.raises(ccs.ConnectorNotConfigured):
        ccs.resolve("alice", TESTCONNECTOR, fallback_env="TESTCONNECTOR_SECRET")


def test_an_absent_row_still_uses_the_env_fallback(store, monkeypatch):
    """The twin: absent is NOT disabled, and the env fallback still works.

    Without this, the test above would pass just as well against a `resolve`
    that had lost its env fallback entirely.
    """
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("TESTCONNECTOR_SECRET", "from-the-environment")

    assert ccs.resolve("nobody", TESTCONNECTOR, fallback_env="TESTCONNECTOR_SECRET") == {
        "value": "from-the-environment"
    }


# ---------------------------------------------------------------------------
# Audit + change listeners
# ---------------------------------------------------------------------------


def test_set_enabled_does_not_overload_ok_in_the_audit(store):
    """`ok` keeps meaning "the write succeeded".

    Passing `ok=enabled` would make `ok=false` mean two different things in one
    vocabulary — "the write failed" everywhere else, "turned off" here — and
    every operator filtering for failed writes would get disables as noise.
    """
    import json

    _seed(store, TESTCONNECTOR, {"k": "v"})
    before = len(store.audit)
    ccs.set_enabled("alice", TESTCONNECTOR, enabled=False, actor="self")

    rows = store.audit[before:]
    enabled_rows = [r for r in rows if r["action_name"] == ccs.ACTION_CRED_ENABLED]
    assert len(enabled_rows) == 1, f"expected one enabled audit row, got {rows!r}"
    row = enabled_rows[0]
    assert json.loads(row["result_summary"]) == {"ok": True}
    assert json.loads(row["input_summary"])["enabled"] is False


def test_disabling_fires_the_change_listeners(store):
    """Without this, disabling an `llm_*` leaves the cached adapter holding the key.

    `set_enabled` is shaped after `set_delivery_paused`, which fires NOTHING —
    so copying it verbatim would have left chat working off a stale cached
    adapter until the next restart, falsifying "disabling stops actions".
    """
    seen: list[tuple[str, str, str]] = []
    ccs.reset_listeners_for_tests()
    ccs.register_change_listener(
        lambda user_id, connector, action, instance="": seen.append((user_id, connector, action))
    )
    try:
        _seed(store, TESTCONNECTOR, {"k": "v"})
        seen.clear()
        ccs.set_enabled("alice", TESTCONNECTOR, enabled=False, actor="self")
    finally:
        ccs.reset_listeners_for_tests()

    assert seen == [("alice", TESTCONNECTOR, "enabled")]


def test_set_enabled_on_a_missing_row_returns_None(store):
    """The route maps this to 404 — nothing saved yet to enable or disable."""
    assert ccs.set_enabled("nobody", TESTCONNECTOR, enabled=False, actor="self") is None


# ---------------------------------------------------------------------------
# Enumeration stays decrypt-free
# ---------------------------------------------------------------------------


def test_list_records_does_not_decrypt(store):
    """The chunk's headline security property, asserted rather than reviewed.

    Field presence is served from a stored key-name column precisely so this
    stays true. If `ciphertext` ever appears in the enumeration statement, the
    connectors screen decrypts every credential on every render — the surface
    ADR-199 removed.
    """
    _seed(store, CALDAV, {"base_url": "u", "username": "n", "password": "p"})
    _seed(store, PAPERLESS, {"base_url": "u", "token": "t"})

    store.seen_sql.clear()
    records = ccs.list_records("alice")

    assert len(records) == 2
    enumeration_sql = [s for s in store.seen_sql if "user_connector_credentials" in s]
    assert enumeration_sql, "the enumeration issued no SQL — the assertion is vacuous"
    for sql in enumeration_sql:
        assert "ciphertext" not in sql, f"list_records selected ciphertext: {sql!r}"


def test_field_presence_is_served_without_a_decrypt(store, monkeypatch):
    """The same property from the other side: spy the decrypt, enumerate, assert zero calls."""
    _seed(store, CALDAV, {"base_url": "u", "username": "n", "password": "p"})

    calls: list[int] = []
    real = ccs._decrypt_payload
    monkeypatch.setattr(ccs, "_decrypt_payload", lambda ct: (calls.append(1), real(ct))[1])

    records = ccs.list_records("alice")

    assert [r.configured_keys for r in records] == [["base_url", "password", "username"]]
    assert calls == [], "enumerating credentials decrypted one"


# ---------------------------------------------------------------------------
# Migration 079 — the upgrade path
# ---------------------------------------------------------------------------


def _migration_path():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parent.parent.parent
        / "postgres"
        / "migrations"
        / "079-connector-enabled-and-configured-keys.sql"
    )
    assert path.exists(), f"migration 079 is missing at {path}"
    return path


def _migration_sql() -> str:
    return _migration_path().read_text(encoding="utf-8")


def _migration_ddl() -> str:
    """The migration's STATEMENTS, with `--` comment lines stripped.

    Necessary rather than fussy: the file's own prose explains why `DEFAULT
    FALSE` would be catastrophic, so a naive substring check over the whole
    file matches the explanation and asserts nothing about the schema. This is
    the same class of error as testing a log message instead of the code.
    """
    lines = [ln for ln in _migration_sql().splitlines() if not ln.lstrip().startswith("--")]
    return " ".join(" ".join(lines).split()).lower()


def test_the_migration_defaults_existing_rows_to_enabled():
    """`DEFAULT TRUE` is the only safe default, and it is the upgrade path.

    Every existing credential is in use right now. `DEFAULT FALSE` would
    silently disable every connector in every household on upgrade — ingest
    stops, actions stop, and the symptom is "nothing works" with no user action
    to explain it.
    """
    ddl = _migration_ddl()
    assert "add column if not exists enabled boolean not null default true" in ddl, (
        "migration 079 does not add `enabled` with DEFAULT TRUE"
    )
    assert "default false" not in ddl


def test_configured_keys_is_nullable_with_no_default():
    """NULL is load-bearing, so the column must not be given one.

    `NOT NULL DEFAULT '{}'` would give every pre-migration row an empty array,
    and the migration cannot populate it truthfully without decrypting every
    credential — the exact thing this design exists to avoid. Every existing
    credential would then report "no fields configured", rendering a working
    connector as unconfigured.
    """
    ddl = _migration_ddl()
    assert "add column if not exists configured_keys text[];" in ddl, (
        "configured_keys is not declared as a bare nullable TEXT[]"
    )
    # The two ways the safe default gets undone, named so the mutation check
    # has something specific to break.
    assert "configured_keys text[] not null" not in ddl
    assert "configured_keys text[] default" not in ddl


def test_the_migration_carries_a_rollback_comment():
    """House style — 060, 067, 077 and 078 each carry one.

    NOT 021, which this docstring used to cite and which has no rollback
    comment at all (`grep -ci rollback postgres/migrations/021-*.sql` -> 0).
    Corrected rather than dropped: the convention is real, and a test that
    justifies itself with a false example invites the next reader to
    disbelieve the rule.
    """
    sql = _migration_sql().lower()
    assert "rollback:" in sql
    assert "drop column if exists enabled" in sql
    assert "drop column if exists configured_keys" in sql
