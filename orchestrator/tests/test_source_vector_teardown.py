# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Deleting a source must take its vectors AND its library rows with it.

Found by the paperless-coexistence exploration. The per-path ledgers
(``external_documents``, ``email_attachment_ingest_log``) are the only per-source
record of these documents, and both CASCADE on ``sources(id)``.

**The premise moved under this test.** It was written when neither external
ingest path wrote a ``file_index`` row; LUM-724 Phase 2 made both write one. So
deleting the vectors alone now strands the library rows — phantom entries
pointing at documents that no longer exist, the exact inverse of the bug this
function was added to fix. The teardown removes both, and the last test here is
what stops that premise drifting again.

So ``DELETE FROM sources`` used to remove the bookkeeping in the same statement
that stranded the data. Every document the source had filed stayed in Qdrant and
stayed searchable, with nothing left to enumerate it from. There was no error and
no row: the first symptom would be a user searching for something they believed
they had deleted.

**Ordering is the contract**, exactly as it is for attachment undo: read the
ledger and delete the points FIRST, then delete the source. The mutation test at
the bottom is what keeps that ordering from being "optimised" into a tidier
sequence that silently does nothing.
"""

from __future__ import annotations

import pytest

import config
from services import document_purge

USER = "alice"
SOURCE = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"


class _Store:
    def __init__(self, external=(), attachments=()):
        self.external = list(external)
        self.attachments = list(attachments)

    def fetch_all(self, sql, params=None):
        user_id, source_id = params
        if "FROM external_documents" in sql:
            rows = self.external
        elif "FROM email_attachment_ingest_log" in sql:
            rows = self.attachments
            # The route's query carries this predicate; the fake honours it
            # rather than ignoring it, or the "skips have no vectors" case
            # would pass without the SQL actually saying so.
            assert "skipped_reason IS NULL" in sql
            rows = [r for r in rows if not r.get("skipped_reason")]
        else:  # pragma: no cover - a new ledger should fail loudly
            raise AssertionError(f"unexpected ledger query: {sql!r}")
        return [
            r for r in rows if r["user_id"] == user_id and str(r["source_id"]) == str(source_id)
        ]


class _Vectors:
    def __init__(self, fail_on=None):
        self.deleted: list[dict] = []
        self.fail_on = fail_on

    def delete_where(self, collection, filter):  # noqa: A002 - mirrors the real signature
        clauses = {c["key"]: c["match"]["value"] for c in filter["must"]}
        if self.fail_on and clauses.get("file_path") == self.fail_on:
            raise RuntimeError("qdrant unreachable")
        self.deleted.append({"collection": collection, **clauses})

    def paths(self) -> list[str]:
        return [d["file_path"] for d in self.deleted]


def _ext(path, user_id=USER, source_id=SOURCE):
    return {"user_id": user_id, "source_id": source_id, "logical_path": path}


def _att(path, user_id=USER, source_id=SOURCE, skipped_reason=None):
    return {
        "user_id": user_id,
        "source_id": source_id,
        "logical_path": path,
        "skipped_reason": skipped_reason,
    }


@pytest.fixture
def wire(monkeypatch):
    def _wire(store, vectors):
        monkeypatch.setattr(config, "get_metadata_store", lambda: store)
        monkeypatch.setattr(config, "get_vector_store", lambda: vectors)
        return store, vectors

    return _wire


def test_a_paperless_documents_vectors_are_deleted(wire):
    store, vs = wire(_Store(external=[_ext("paperless://s/documents/7")]), _Vectors())

    document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE)

    assert vs.paths() == ["paperless://s/documents/7"]


def test_a_filed_attachments_vectors_are_deleted(wire):
    store, vs = wire(_Store(attachments=[_att("email-attachment://s/sig/0")]), _Vectors())

    document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE)

    assert vs.paths() == ["email-attachment://s/sig/0"]


def test_both_ledgers_are_swept_not_just_one(wire):
    """The bug is shared by both external paths, so a fix that reads one ledger
    only would look correct on whichever half the author had in mind."""
    store, vs = wire(
        _Store(
            external=[_ext("paperless://s/documents/7")],
            attachments=[_att("email-attachment://s/sig/0")],
        ),
        _Vectors(),
    )

    document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE)

    assert sorted(vs.paths()) == [
        "email-attachment://s/sig/0",
        "paperless://s/documents/7",
    ]


def test_a_skipped_attachment_has_no_vectors_to_delete(wire):
    """A skip — `declined` above all, which LUM-727 can make the majority of
    rows — never created a vector. Issuing deletes for them would be a no-op
    per row and a large amount of pointless traffic on a busy mailbox."""
    store, vs = wire(
        _Store(
            attachments=[
                _att("email-attachment://s/sig/0", skipped_reason="declined"),
                _att("email-attachment://s/sig/1", skipped_reason="no_text"),
                _att("email-attachment://s/sig/2"),
            ]
        ),
        _Vectors(),
    )

    document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE)

    assert vs.paths() == ["email-attachment://s/sig/2"]


def test_another_sources_vectors_are_untouched(wire):
    store, vs = wire(
        _Store(
            external=[
                _ext("paperless://mine/documents/1"),
                _ext("paperless://theirs/documents/2", source_id=OTHER),
            ]
        ),
        _Vectors(),
    )

    document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE)

    assert vs.paths() == ["paperless://mine/documents/1"]


def test_another_users_vectors_are_untouched(wire):
    store, vs = wire(
        _Store(
            external=[
                _ext("paperless://s/documents/1"),
                _ext("paperless://s/documents/2", user_id="bob"),
            ]
        ),
        _Vectors(),
    )

    document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE)

    assert vs.paths() == ["paperless://s/documents/1"]


def test_the_delete_is_scoped_by_user_as_well_as_path(wire):
    """The filter carries both keys. A path-only filter would delete another
    household member's copy of a shared logical path."""
    store, vs = wire(_Store(external=[_ext("paperless://s/documents/7")]), _Vectors())

    document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE)

    assert vs.deleted[0]["user_id"] == USER
    assert vs.deleted[0]["collection"] == "documents"


def test_one_failing_delete_does_not_abandon_the_rest(wire):
    """Best-effort per document. The alternative — abort on the first failure —
    leaves a partially-purged source AND a source the user cannot delete."""
    store, vs = wire(
        _Store(
            external=[
                _ext("paperless://s/documents/1"),
                _ext("paperless://s/documents/2"),
                _ext("paperless://s/documents/3"),
            ]
        ),
        _Vectors(fail_on="paperless://s/documents/2"),
    )

    done = document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE)

    assert done == 2
    assert sorted(vs.paths()) == ["paperless://s/documents/1", "paperless://s/documents/3"]


def test_a_source_that_filed_nothing_touches_the_vector_store_at_all(wire):
    store, vs = wire(_Store(), _Vectors())

    assert document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE) == 0
    assert vs.deleted == []


def test_a_missing_ledger_table_does_not_block_the_delete(wire):
    """An older volume may predate one of the migrations. Failing here would
    make the source undeletable, which is worse than the leak it guards."""

    class _Broken(_Store):
        def fetch_all(self, sql, params=None):
            if "external_documents" in sql:
                raise RuntimeError('relation "external_documents" does not exist')
            return super().fetch_all(sql, params)

    store, vs = wire(_Broken(attachments=[_att("email-attachment://s/sig/0")]), _Vectors())

    document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE)

    assert vs.paths() == ["email-attachment://s/sig/0"], "the other ledger still sweeps"


# --- the ordering contract ---------------------------------------------------


def test_the_route_purges_vectors_before_deleting_the_source(monkeypatch):
    """**This is the whole fix.**

    Reversing these two steps costs nothing visible and silently restores the
    bug: the CASCADE fires first, the ledgers are gone, and the sweep that runs
    afterwards reads two empty tables and reports success.
    """
    from auth import UserContext
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import signals as mod

    order: list[str] = []

    class _RouteStore:
        def fetch_one(self, sql, params=None):
            if sql.strip().upper().startswith("DELETE FROM SOURCES"):
                order.append("delete_source_row")
                return {"id": SOURCE}
            if "FROM sources" in sql:
                return {"id": SOURCE, "source_type": "paperless"}
            return None

        def fetch_all(self, sql, params=None):
            return []

        def execute(self, sql, params=None):
            return None

    monkeypatch.setattr(config, "get_metadata_store", lambda: _RouteStore())
    monkeypatch.setattr(
        document_purge,
        "purge_source_vectors",
        lambda **kw: order.append("purge_vectors") or 0,
    )

    app = FastAPI()
    app.include_router(mod.router)
    app.dependency_overrides[mod.require_user] = lambda: UserContext(
        user_id=USER, role="member", allows_shared=True
    )
    app.dependency_overrides[mod.require_same_origin] = lambda: None

    TestClient(app).delete(f"/sources/{SOURCE}")

    assert order == ["purge_vectors", "delete_source_row"], (
        "the ledger CASCADEs away with the source row — sweeping afterwards "
        "reads two empty tables and reports success"
    )


# --- LUM-724 Phase 2: the premise that moved ---------------------------------
#
# This function was written when NEITHER external ingest path wrote a
# `file_index` row — its docstring said so, and argued from it. Phase 2 makes
# both write one. Deleting the vectors alone therefore now leaves the library
# rows behind: phantom entries pointing at documents that no longer exist, which
# is the exact inverse of the bug this function was added to fix.
#
# Three review passes compared the Phase 2 plan against the codebase and none
# connected it to the phase shipped an hour earlier. These tests are what stop
# the premise drifting a second time.


def test_the_library_rows_go_too_not_just_the_vectors(wire, monkeypatch):
    removed: list[str] = []
    from services import external_document_index as ext_index

    monkeypatch.setattr(
        ext_index,
        "remove_for_source",
        lambda *, user_id, logical_paths: removed.extend(logical_paths) or len(logical_paths),
    )
    store, vs = wire(
        _Store(
            external=[_ext("paperless://s/documents/7")],
            attachments=[_att("email-attachment://s/sig/0")],
        ),
        _Vectors(),
    )

    document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE)

    assert sorted(removed) == [
        "email-attachment://s/sig/0",
        "paperless://s/documents/7",
    ], "a deleted source must leave no library rows behind"


def test_the_same_paths_are_used_for_vectors_and_for_rows(wire, monkeypatch):
    """One enumeration drives both deletions.

    Two separate queries would be two chances to drift — and drift here means
    either an orphaned vector or a phantom library row, both silent.
    """
    seen: list[str] = []
    from services import external_document_index as ext_index

    monkeypatch.setattr(
        ext_index,
        "remove_for_source",
        lambda *, user_id, logical_paths: seen.extend(logical_paths) or 0,
    )
    store, vs = wire(
        _Store(external=[_ext("paperless://s/documents/1"), _ext("paperless://s/documents/2")]),
        _Vectors(),
    )

    document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE)

    assert sorted(seen) == sorted(vs.paths())


def test_a_failing_row_removal_does_not_abort_the_teardown(wire, monkeypatch):
    """Best-effort, matching the vector arm. A source the user cannot delete is
    worse than a row left behind, and the row is repairable."""
    from services import external_document_index as ext_index

    def _boom(**kw):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(ext_index, "remove_for_source", _boom)
    store, vs = wire(_Store(external=[_ext("paperless://s/documents/7")]), _Vectors())

    with pytest.raises(RuntimeError):
        document_purge.purge_source_vectors(user_id=USER, source_id=SOURCE)

    # The vectors were still deleted before the failure — the ordering contract
    # holds even when the second arm fails.
    assert vs.paths() == ["paperless://s/documents/7"]
