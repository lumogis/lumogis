# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-584 — admin_unshare service: owner-independent retract, oracle-safe,
teardown-verified, audit-loud.

These tests exercise the *governance-specific* logic in
:mod:`services.admin_unshare` — the shared-only lookup (no existence oracle
over private items), owner resolution from the projection, the post-teardown
Qdrant verification, and the fail-loud audit. Projection teardown internals
(``unproject_*``) have their own tests; here the registry's ``unproject`` is
a spy so the security seams are isolated.
"""
from __future__ import annotations

import pytest
from auth import UserContext
from tests._fakes import MockVectorStore

import config
from services import admin_unshare as svc

ADMIN = UserContext(user_id="carol-admin", role="admin", is_authenticated=True)
OWNER = "bob-uid"


class _SharedProjectionStore:
    """Metadata store answering only the shared-projection lookup + audit insert.

    ``shared`` maps ``(table, published_from_as_str) -> owner_user_id`` for
    rows that are currently shared. The lookup SELECT filters on
    ``scope = 'shared'``; a personal-only pk is simply absent → None.
    """

    def __init__(self, shared: dict[tuple[str, str], str]):
        self.shared = shared
        self.audit_rows: list[tuple] = []
        self.audit_should_fail = False

    def fetch_one(self, query: str, params=None):
        q = " ".join(query.split()).lower()
        if q.startswith("insert into audit_log"):
            if self.audit_should_fail:
                raise RuntimeError("simulated audit write failure")
            self.audit_rows.append(params)
            return {"id": len(self.audit_rows)}
        if "from" in q and "where published_from = %s and scope = 'shared'" in q:
            table = q.split(" from ")[1].split(" where")[0].strip()
            owner = self.shared.get((table, str(params[0])))
            return {"user_id": owner} if owner is not None else None
        return None

    def fetch_all(self, query: str, params=None):
        return []


@pytest.fixture
def vs():
    store = MockVectorStore()
    config._instances["vector_store"] = store
    yield store
    config._instances.pop("vector_store", None)


def _seed_shared_point(vs, collection, published_from, owner=OWNER):
    vs.upsert(
        collection,
        f"point-{published_from}",
        [0.0],
        {"published_from": published_from, "scope": "shared", "user_id": owner},
    )


def _install_store(monkeypatch, store):
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)


def _spy_unproject(monkeypatch, resource, fn):
    """Replace a registry unproject with a spy (registry holds import-time refs)."""
    monkeypatch.setitem(svc._RESOURCE[resource], "unproject", fn)


def test_admin_unshare_removes_foreign_share(monkeypatch, vs):
    """Admin retracts member B's shared note; teardown runs, audit written."""
    store = _SharedProjectionStore({("notes", "note-1"): OWNER})
    _install_store(monkeypatch, store)
    _seed_shared_point(vs, "conversations", "note-1")

    torn: dict = {}

    def fake_unproject(pk, target_scope="shared"):
        torn["pk"] = pk
        vs.delete_where(
            "conversations",
            {"must": [{"key": "published_from", "match": {"value": pk}}]},
        )
        return 1

    _spy_unproject(monkeypatch, "notes", fake_unproject)

    result = svc.admin_unshare(actor=ADMIN, resource="notes", pk="note-1")

    assert torn["pk"] == "note-1"
    assert result == {
        "resource_type": "notes",
        "resource_id": "note-1",
        "source_owner_id": OWNER,
        "unshared": True,
    }
    # audit row written with acting admin + source owner
    assert len(store.audit_rows) == 1
    params = store.audit_rows[0]
    assert params[1] == "admin_unshare"  # action_name
    assert params[2] == "admin"  # connector
    assert params[3] == "DO"  # mode
    assert ADMIN.user_id in params  # acting admin recorded
    assert OWNER in params[5]  # result_summary carries source owner


def test_admin_unshare_never_shared_item_404s(monkeypatch, vs):
    """A pk that exists only as a personal (never-shared) item is opaque 404.

    Closes the existence oracle: the lookup never reads personal rows, so a
    private item and a nonexistent pk are indistinguishable to the admin.
    """
    store = _SharedProjectionStore({})  # nothing is shared
    _install_store(monkeypatch, store)

    def must_not_run(pk, target_scope="shared"):
        pytest.fail("teardown ran for a not-shared item")

    _spy_unproject(monkeypatch, "notes", must_not_run)

    with pytest.raises(svc.SharedItemNotFound):
        svc.admin_unshare(actor=ADMIN, resource="notes", pk="private-note")
    assert store.audit_rows == []  # no audit for a no-op


def test_admin_unshare_idempotent_second_call_404s(monkeypatch, vs):
    """Once unshared, the projection is gone → a second retract is 404."""
    store = _SharedProjectionStore({("notes", "note-1"): OWNER})
    _install_store(monkeypatch, store)
    _seed_shared_point(vs, "conversations", "note-1")

    def fake_unproject(pk, target_scope="shared"):
        store.shared.pop(("notes", str(pk)), None)
        vs.delete_where(
            "conversations",
            {"must": [{"key": "published_from", "match": {"value": pk}}]},
        )
        return 1

    _spy_unproject(monkeypatch, "notes", fake_unproject)

    svc.admin_unshare(actor=ADMIN, resource="notes", pk="note-1")
    with pytest.raises(svc.SharedItemNotFound):
        svc.admin_unshare(actor=ADMIN, resource="notes", pk="note-1")


def test_admin_unshare_unknown_resource_type(monkeypatch, vs):
    _install_store(monkeypatch, _SharedProjectionStore({}))
    with pytest.raises(svc.UnknownResource):
        svc.admin_unshare(actor=ADMIN, resource="widgets", pk="x")


def test_admin_unshare_file_pk_coerced_to_int(monkeypatch, vs):
    """INTEGER-PK resources look up + verify by int (documents chunks store int)."""
    store = _SharedProjectionStore({("file_index", "42"): OWNER})
    _install_store(monkeypatch, store)
    _seed_shared_point(vs, "documents", 42)

    seen: dict = {}

    def fake_unproject(pk, target_scope="shared"):
        seen["pk"] = pk
        vs.delete_where(
            "documents",
            {"must": [{"key": "published_from", "match": {"value": pk}}]},
        )
        return 1

    _spy_unproject(monkeypatch, "files", fake_unproject)

    result = svc.admin_unshare(actor=ADMIN, resource="files", pk="42")
    assert seen["pk"] == 42 and isinstance(seen["pk"], int)
    assert result["unshared"] is True


def test_admin_unshare_bad_int_pk_is_404_not_500(monkeypatch, vs):
    _install_store(monkeypatch, _SharedProjectionStore({}))
    with pytest.raises(svc.SharedItemNotFound):
        svc.admin_unshare(actor=ADMIN, resource="files", pk="not-an-int")


def test_admin_unshare_incomplete_teardown_is_audited_and_retriable(monkeypatch, vs):
    """A survivor → 500, but the intent IS audited (write-ahead) and the retract
    is genuinely retriable to completion once Qdrant recovers (LUM-584 review P2).
    """
    store = _SharedProjectionStore({("notes", "note-1"): OWNER})
    _install_store(monkeypatch, store)
    _seed_shared_point(vs, "conversations", "note-1")

    # Teardown deletes the authoritative Postgres row but its Qdrant mirror is
    # swallowed and survives — the exact stranding scenario the review flagged.
    recovered = {"ok": False}

    def flaky_unproject(pk, target_scope="shared"):
        store.shared.pop(("notes", str(pk)), None)  # PG row gone
        if recovered["ok"]:
            vs.delete_where(
                "conversations",
                {"must": [{"key": "published_from", "match": {"value": pk}}]},
            )
        return 1

    _spy_unproject(monkeypatch, "notes", flaky_unproject)

    # First attempt: Qdrant survived → 500, but the intent was recorded.
    with pytest.raises(svc.TeardownIncomplete):
        svc.admin_unshare(actor=ADMIN, resource="notes", pk="note-1")
    assert len(store.audit_rows) == 1  # write-ahead intent, not a silent action

    # Retry once Qdrant recovers: the PG row is already gone, but the orphan
    # count is non-zero, so the service re-runs teardown instead of 404-ing.
    recovered["ok"] = True
    result = svc.admin_unshare(actor=ADMIN, resource="notes", pk="note-1")
    assert result["unshared"] is True
    assert len(store.audit_rows) == 2  # second attempt also recorded (benign dup)


def test_admin_unshare_verification_failure_raises(monkeypatch, vs):
    """If the post-teardown count itself errors, fail (cannot claim success)."""
    store = _SharedProjectionStore({("notes", "note-1"): OWNER})
    _install_store(monkeypatch, store)

    class _Boom(MockVectorStore):
        def count_where(self, collection, filter):
            raise RuntimeError("qdrant down")

    boom = _Boom()
    config._instances["vector_store"] = boom
    _spy_unproject(monkeypatch, "notes", lambda pk, target_scope="shared": 1)

    with pytest.raises(svc.TeardownIncomplete):
        svc.admin_unshare(actor=ADMIN, resource="notes", pk="note-1")


def test_admin_unshare_audit_failure_tears_down_nothing(monkeypatch, vs):
    """Audit is write-ahead: a failed audit fails loud AND leaves the share fully
    intact, so the retract is retriable (never an untraceable-but-effective
    override — LUM-584 review P2).
    """
    store = _SharedProjectionStore({("notes", "note-1"): OWNER})
    store.audit_should_fail = True
    _install_store(monkeypatch, store)
    _seed_shared_point(vs, "conversations", "note-1")

    torn = {"ran": False}

    def spy(pk, target_scope="shared"):
        torn["ran"] = True
        return 1

    _spy_unproject(monkeypatch, "notes", spy)

    with pytest.raises(svc.AuditWriteFailed):
        svc.admin_unshare(actor=ADMIN, resource="notes", pk="note-1")

    assert torn["ran"] is False  # teardown never ran — audit precedes it
    assert ("notes", "note-1") in store.shared  # PG share intact → retriable
    assert (
        vs.count_where(
            "conversations",
            {"must": [{"key": "published_from", "match": {"value": "note-1"}}]},
        )
        == 1
    )  # Qdrant mirror untouched


# ---------------------------------------------------------------------------
# All six resource types through the real service logic (registry coverage).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "resource,collection,pk",
    [
        ("notes", "conversations", "n-uuid"),
        ("audio_memos", "conversations", "a-uuid"),
        ("sessions", "conversations", "s-uuid"),
        ("entities", "entities", "e-uuid"),
        ("signals", "signals", "g-uuid"),
        ("files", "documents", "7"),
    ],
)
def test_admin_unshare_all_types_happy_path(monkeypatch, vs, resource, collection, pk):
    """Each registry entry's table / collection / pk-type flows end to end:
    shared lookup → write-ahead audit → teardown → Qdrant verification → result.
    """
    cfg = svc._RESOURCE[resource]
    coerced = int(pk) if cfg["pk_type"] == "int" else pk
    store = _SharedProjectionStore({(cfg["table"], str(coerced)): OWNER})
    _install_store(monkeypatch, store)
    _seed_shared_point(vs, collection, coerced)

    def spy(p, target_scope="shared"):
        vs.delete_where(
            collection, {"must": [{"key": "published_from", "match": {"value": p}}]}
        )
        return 1

    _spy_unproject(monkeypatch, resource, spy)

    result = svc.admin_unshare(actor=ADMIN, resource=resource, pk=pk)
    assert result == {
        "resource_type": resource,
        "resource_id": str(pk),
        "source_owner_id": OWNER,
        "unshared": True,
    }
    assert len(store.audit_rows) == 1


# ---------------------------------------------------------------------------
# admin_list_shared_items — aggregation, resource_id=published_from, per-arm
# failure isolation, label truncation.
# ---------------------------------------------------------------------------


class _ListStore:
    """fetch_all answering the per-type list query; one table can be made to raise."""

    def __init__(self, rows_by_table, fail_tables=()):
        self.rows_by_table = rows_by_table
        self.fail_tables = set(fail_tables)

    def fetch_all(self, query, params=None):
        q = " ".join(query.split()).lower()
        table = q.split(" from ")[1].split(" where")[0].strip()
        if table in self.fail_tables:
            raise RuntimeError("simulated query failure")
        return list(self.rows_by_table.get(table, []))

    def fetch_one(self, *a, **k):
        return None


def test_admin_list_shared_items_aggregates_isolates_and_truncates(monkeypatch):
    rows = {
        "file_index": [{"published_from": 7, "user_id": "bob", "label": "insurance.pdf"}],
        "notes": [{"published_from": "n-1", "user_id": "amy", "label": "x" * 200}],
    }
    # The signals arm raises — it must not blank the whole list (per-arm isolation).
    store = _ListStore(rows, fail_tables={"signals"})
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)

    items = svc.admin_list_shared_items()

    files = next(i for i in items if i["resource_type"] == "files")
    assert files["resource_id"] == "7"  # source pk (published_from), stringified
    assert files["source_owner_id"] == "bob"
    assert files["label"] == "insurance.pdf"

    notes = next(i for i in items if i["resource_type"] == "notes")
    assert notes["resource_id"] == "n-1"
    assert len(notes["label"]) <= 120  # _short truncation

    assert all(i["resource_type"] != "signals" for i in items)  # failed arm isolated
    assert len(items) == 2
