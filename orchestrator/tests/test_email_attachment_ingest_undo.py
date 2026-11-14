# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Undoing an auto-filed attachment (LUM-702).

Two of these encode decisions that are easy to reverse by accident.

**Ordering.** Vectors are deleted *before* ``reversed_at`` is set. The other order
leaves a row marked reversed with its chunks still searchable — the user is told
the document is gone and it keeps turning up in search. A crash between the two
steps leaves orphaned-but-unreferenced vectors that the next attempt cleans up,
precisely because ``reversed_at`` is still NULL.

**Stickiness.** ``test_an_undone_attachment_is_never_re_filed_by_a_later_tick``
replaces an earlier case that asserted the opposite. The full ``uq_..._part``
index means one verdict per part forever, so the sweep never reconsiders a part
the user undid. An undo that silently reverts itself fifteen minutes later is
worse than no undo. Re-filing is a deliberate follow-up, not an accident of the
schema.
"""

from __future__ import annotations

import pytest
from tests.test_email_attachment_ingest import SIG_A
from tests.test_email_attachment_ingest import SOURCE
from tests.test_email_attachment_ingest import USER
from tests.test_email_attachment_ingest import _Imap
from tests.test_email_attachment_ingest import _Row
from tests.test_email_attachment_ingest import _Store

from services import email_attachment_ingest as sweep


@pytest.fixture
def wired(monkeypatch):
    """A store with one filed row, and a recorder for vector deletes."""
    store = _Store([]).add_rule("category", "bills_finance")
    store.ledger.append(
        {
            "id": 1,
            "user_id": USER,
            "source_id": SOURCE,
            "signal_id": SIG_A,
            "part_index": 0,
            "filename": "a.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 10,
            "content_sha256": "deadbeef",
            "logical_path": f"email-attachment://{SOURCE}/{SIG_A}/0",
            "chunk_count": 3,
            "reversed_at": None,
            "skipped_reason": None,
            "ingested_at": 1,
        }
    )
    import config

    monkeypatch.setitem(config._instances, "metadata_store", store)

    calls: list[str] = []
    monkeypatch.setattr(
        sweep, "_discard_vectors", lambda **kw: calls.append(f"delete:{kw['logical_path']}")
    )
    real_execute = store.execute

    def _spy(sql, params=None):
        if sql.startswith("UPDATE email_attachment_ingest_log"):
            calls.append("mark_reversed")
        return real_execute(sql, params)

    store.execute = _spy
    return store, calls


# --- 21 ---------------------------------------------------------------------


def test_undo_deletes_the_vectors_before_marking_reversed(wired):
    store, calls = wired

    assert sweep.undo_row(USER, 1) is True

    assert calls == [f"delete:email-attachment://{SOURCE}/{SIG_A}/0", "mark_reversed"], (
        "marking reversed first would tell the user a document is gone while it is still searchable"
    )
    assert store.ledger[0]["reversed_at"] is not None


# --- 22 ---------------------------------------------------------------------


def test_a_failed_vector_delete_leaves_reversed_at_null(wired, monkeypatch):
    """So the next attempt retries, rather than reporting a document gone that
    is still in the index."""
    store, calls = wired

    def _boom(**kw):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(sweep, "_discard_vectors", _boom)

    assert sweep.undo_row(USER, 1) is False

    assert store.ledger[0]["reversed_at"] is None
    assert "mark_reversed" not in calls


def test_a_retry_after_a_failed_delete_succeeds(wired, monkeypatch):
    store, calls = wired
    state = {"fail": True}

    def _flaky(**kw):
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("qdrant down")
        calls.append("delete")

    monkeypatch.setattr(sweep, "_discard_vectors", _flaky)

    assert sweep.undo_row(USER, 1) is False
    assert sweep.undo_row(USER, 1) is True
    assert store.ledger[0]["reversed_at"] is not None


# --- 23 ---------------------------------------------------------------------


def test_undo_for_source_reverses_every_active_row_and_returns_the_count(wired):
    store, _ = wired
    for i in (2, 3):
        row = dict(store.ledger[0])
        row.update({"id": i, "part_index": i, "content_sha256": f"hash{i}"})
        store.ledger.append(row)

    assert sweep.undo_for_source(USER, SOURCE) == 3
    assert all(r["reversed_at"] is not None for r in store.ledger)


def test_undo_for_source_excludes_rows_whose_delete_failed(wired, monkeypatch):
    """Reporting five removed when four were is the same broken promise as the
    wrong ordering."""
    store, _ = wired
    row = dict(store.ledger[0])
    row.update({"id": 2, "part_index": 1, "content_sha256": "hash2"})
    store.ledger.append(row)

    def _selective(**kw):
        if kw["part_index"] == 1:
            raise RuntimeError("qdrant down")

    monkeypatch.setattr(sweep, "_discard_vectors", _selective)

    assert sweep.undo_for_source(USER, SOURCE) == 1


def test_undo_for_source_leaves_skip_rows_alone(wired):
    """A skip was never a filing — there is nothing to take back."""
    store, _ = wired
    row = dict(store.ledger[0])
    row.update({"id": 2, "part_index": 1, "skipped_reason": "no_extractor"})
    store.ledger.append(row)

    assert sweep.undo_for_source(USER, SOURCE) == 1
    assert store.ledger[1]["reversed_at"] is None


# --- 24 ---------------------------------------------------------------------


def test_undo_is_idempotent(wired):
    store, calls = wired

    assert sweep.undo_row(USER, 1) is True
    assert sweep.undo_row(USER, 1) is False, "an already-reversed row is a no-op"

    assert calls.count("mark_reversed") == 1
    assert len([c for c in calls if c.startswith("delete:")]) == 1


# --- 26 ---------------------------------------------------------------------


def test_another_users_row_is_not_reversible(wired):
    store, calls = wired

    assert sweep.undo_row("mallory", 1) is False

    assert store.ledger[0]["reversed_at"] is None
    assert calls == []


# --- 25' — the case the arbitration replaced --------------------------------


def test_an_undone_attachment_is_never_re_filed_by_a_later_tick(monkeypatch):
    """Undo sticks. This is the whole reason ``uq_..._part`` is a FULL index.

    If the selection query gained a ``reversed_at IS NULL`` predicate — or the
    index became partial to match its sibling — the next tick would re-file the
    attachment the user just removed, and they would find it back tomorrow with
    no idea why.
    """
    store = _Store([_Row(SIG_A, 0, "a.pdf")]).add_rule("category", "bills_finance")
    imap = _Imap()

    import config

    monkeypatch.setitem(config._instances, "metadata_store", store)
    from services import email_attachment_rules

    email_attachment_rules.invalidate_cache()
    monkeypatch.setattr(sweep, "_adapter_for", lambda s, u: imap)
    monkeypatch.setattr(sweep, "_message_id_for", lambda s, u: None)
    monkeypatch.setattr(sweep, "_organize_folder_for", lambda s, u: None)
    monkeypatch.setattr(config, "get_extractors", lambda: {".pdf": lambda p: "text"})
    monkeypatch.setattr(sweep, "_discard_vectors", lambda **kw: None)

    filed: list[dict] = []

    def _fake_ingest(**kw):
        filed.append(kw)
        from models.ingest import IngestResult

        return IngestResult(file_path="p", chunk_count=2)

    monkeypatch.setattr("services.ingest.ingest_email_attachment", _fake_ingest)

    sweep.ingest_tick()
    assert len(store.active()) == 1
    assert len(filed) == 1

    assert sweep.undo_row(USER, store.ledger[0]["id"]) is True

    sweep.ingest_tick()
    sweep.ingest_tick()

    assert len(filed) == 1, "a later tick must not re-file what the user undid"
    assert store.active() == []
    assert len(imap.calls) == 1, "the undone part must not even be fetched again"


# --- LUM-721 — the explicit way back -----------------------------------------


def _wire_refile(monkeypatch, store, imap, *, extractor=lambda p: "text"):
    import config

    monkeypatch.setitem(config._instances, "metadata_store", store)
    monkeypatch.setattr(sweep, "_adapter_for", lambda s, u: imap)
    monkeypatch.setattr(sweep, "_message_id_for", lambda s, u: None)
    monkeypatch.setattr(sweep, "_organize_folder_for", lambda s, u: None)
    monkeypatch.setattr(sweep, "_signal_url_for", lambda s, u: f"imap://{SOURCE}/7/101")
    monkeypatch.setattr(config, "get_extractors", lambda: {".pdf": extractor})
    monkeypatch.setattr(sweep, "_discard_vectors", lambda **kw: None)

    filed: list[dict] = []

    def _fake_ingest(**kw):
        filed.append(kw)
        from models.ingest import IngestResult

        return IngestResult(file_path="email-attachment://x/1/0", chunk_count=2)

    monkeypatch.setattr("services.ingest.ingest_email_attachment", _fake_ingest)
    return filed


def _reversed_store():
    store = _Store([]).add_rule("category", "bills_finance")
    store.ledger.append(
        {
            "id": 1,
            "user_id": USER,
            "source_id": SOURCE,
            "signal_id": SIG_A,
            "part_index": 0,
            "filename": "a.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 10,
            "content_sha256": "deadbeef",
            "logical_path": f"email-attachment://{SOURCE}/{SIG_A}/0",
            "chunk_count": 3,
            "reversed_at": "yesterday",
            "skipped_reason": None,
            "ingested_at": 1,
        }
    )
    return store


def test_refiling_a_reversed_row_puts_it_back(monkeypatch):
    store = _reversed_store()
    filed = _wire_refile(monkeypatch, store, _Imap())

    assert sweep.refile_row(USER, 1) == sweep.REFILE_OK

    assert len(filed) == 1, "the bytes were never stored, so the whole path re-runs"
    assert store.ledger[0]["reversed_at"] is None


def test_refiling_does_not_weaken_the_part_index(monkeypatch):
    """It clears `reversed_at` on the EXISTING row rather than inserting a second
    verdict — `uq_..._part` is what makes undo trustworthy and must not be spent
    buying convenience here."""
    store = _reversed_store()
    _wire_refile(monkeypatch, store, _Imap())

    sweep.refile_row(USER, 1)

    assert len(store.ledger) == 1, "one verdict row per part, still"
    assert store.ledger[0]["id"] == 1


def test_refiling_an_active_row_is_not_found(monkeypatch):
    """Only a REVERSED filing can be put back. An active one is already there."""
    store = _reversed_store()
    store.ledger[0]["reversed_at"] = None
    _wire_refile(monkeypatch, store, _Imap())

    assert sweep.refile_row(USER, 1) == sweep.REFILE_NOT_FOUND


def test_refiling_a_skip_row_is_not_found(monkeypatch):
    """A skip never created a document — there is nothing to put back."""
    store = _reversed_store()
    store.ledger[0]["skipped_reason"] = "no_extractor"
    _wire_refile(monkeypatch, store, _Imap())

    assert sweep.refile_row(USER, 1) == sweep.REFILE_NOT_FOUND


def test_another_users_row_cannot_be_refiled(monkeypatch):
    store = _reversed_store()
    filed = _wire_refile(monkeypatch, store, _Imap())

    assert sweep.refile_row("mallory", 1) == sweep.REFILE_NOT_FOUND

    assert filed == [], "no bytes are fetched for a row that is not the caller's"
    assert store.ledger[0]["reversed_at"] == "yesterday"


def test_a_message_deleted_since_the_undo_reports_unavailable(monkeypatch):
    """The standing cost of never storing the bytes, surfaced honestly."""
    from adapters.email_imap_source import AttachmentFetchError

    store = _reversed_store()
    _wire_refile(monkeypatch, store, _Imap(raises=AttachmentFetchError("message_gone", "gone")))

    assert sweep.refile_row(USER, 1) == sweep.REFILE_UNAVAILABLE
    assert store.ledger[0]["reversed_at"] == "yesterday", "still reversed — nothing changed"


def test_an_unreadable_attachment_reports_unavailable(monkeypatch):
    store = _reversed_store()
    _wire_refile(monkeypatch, store, _Imap(), extractor=lambda p: "")

    assert sweep.refile_row(USER, 1) == sweep.REFILE_UNAVAILABLE


def test_refiling_content_already_filed_elsewhere_is_a_duplicate(monkeypatch):
    """The content slot is enforced by the DATABASE.

    Clearing `reversed_at` re-enters the partial `uq_..._active` index, so if the
    same bytes arrived from another message while this one was undone, the UPDATE
    raises and we leave the row reversed rather than creating a second document.
    """
    store = _reversed_store()
    discarded: list[str] = []
    _wire_refile(monkeypatch, store, _Imap())
    monkeypatch.setattr(
        sweep, "_discard_vectors", lambda **kw: discarded.append(kw["logical_path"])
    )

    def _slot_taken(sql, params=None):
        if sql.startswith("UPDATE email_attachment_ingest_log") and "reversed_at = NULL" in sql:
            raise sweep._UniqueViolation("uq_email_attachment_ingest_active")
        return None

    store.execute = _slot_taken

    assert sweep.refile_row(USER, 1) == sweep.REFILE_DUPLICATE

    assert discarded, "the redundant copy's vectors must not be left searchable"
    assert store.ledger[0]["reversed_at"] == "yesterday"
