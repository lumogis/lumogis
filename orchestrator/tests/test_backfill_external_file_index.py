# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""The LUM-724 backfill — a one-way, all-users, out-of-band write.

Everything ingested before Phase 2 has no `file_index` row and stays invisible in
the library. This repairs that, and the risk profile is what the tests are about:
it crosses every user, it cannot be undone by re-running, and it runs against a
live system.

Two predicates carry almost all of the safety, and both are review findings
rather than first-draft text:

* **`skipped_reason IS NULL`** — a skip never created a document, and since
  LUM-727 `declined` can be the *majority* of attachment rows. Without it the
  backfill would file everything the user said **no** to, inverting LUM-727
  across the whole existing corpus in a script with no undo.
* **`purged_at IS NULL`** — purge retains and marks the row, so without this a
  re-run resurrects every document the user has ever deleted.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

import config

_SPEC = importlib.util.spec_from_file_location(
    "backfill_external_file_index",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "backfill_external_file_index.py",
)
backfill = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backfill)


class _Store:
    def __init__(self, attachments=(), external=(), indexed=()):
        self.attachments = list(attachments)
        self.external = list(external)
        self.indexed = set(indexed)
        self.queries: list[str] = []

    def fetch_all(self, sql, params=None):
        norm = " ".join(sql.split())
        self.queries.append(norm)
        if "email_attachment_ingest_log" in norm:
            return list(self.attachments)
        if "external_documents" in norm:
            return list(self.external)
        raise AssertionError(f"unexpected query: {norm!r}")

    def fetch_one(self, sql, params=None):
        # The "already indexed?" probe.
        return {"1": 1} if params and params[1] in self.indexed else None


def _att(path, filename="invoice.pdf", user_id="alice"):
    return {
        "user_id": user_id,
        "logical_path": path,
        "file_hash": "h",
        "filename": filename,
        "chunk_count": 3,
    }


def _ext(path, external_id="42", user_id="alice"):
    return {
        "user_id": user_id,
        "logical_path": path,
        "file_hash": "h",
        "chunk_count": 2,
        "external_id": external_id,
    }


@pytest.fixture
def wire(monkeypatch):
    written: list[dict] = []

    def _install(store):
        from services import external_document_index as ext_index

        monkeypatch.setattr(config, "get_metadata_store", lambda: store)
        monkeypatch.setattr(ext_index, "record", lambda **kw: written.append(kw) or 1)
        return written

    return _install


# --- the two safety predicates, asserted on the SQL itself -------------------


def test_declined_and_skipped_attachments_are_excluded(wire):
    """**The finding that matters most.**

    Since LUM-727 a `declined` row is the user saying no. Backfilling those would
    invert the whole feature for every document already in the system, in a
    script with no undo.
    """
    store = _Store()
    wire(store)

    backfill.run(dry_run=True)

    att_sql = next(q for q in store.queries if "email_attachment_ingest_log" in q)
    assert "skipped_reason IS NULL" in att_sql
    assert "reversed_at IS NULL" in att_sql


def test_purged_documents_are_never_resurrected(wire):
    """Purge marks rather than deletes (D7), so both ledgers still hold the row.
    Without this predicate a re-run undoes every deletion the user has made."""
    store = _Store()
    wire(store)

    backfill.run(dry_run=True)

    for q in store.queries:
        assert "purged_at IS NULL" in q, f"missing purge guard in: {q}"


# --- idempotence -------------------------------------------------------------


def test_a_row_that_already_exists_is_left_alone(wire):
    """Skipping — rather than re-UPSERTing — is what keeps `updated_at` stable.
    That column orders the library, so a re-run would otherwise silently re-sort
    it and the idempotence claim would be false in a user-visible way."""
    path = "email-attachment://s/sig/0"
    store = _Store(attachments=[_att(path)], indexed={path})
    written = wire(store)

    backfill.run(dry_run=False)

    assert written == []


def test_a_missing_row_is_written(wire):
    store = _Store(attachments=[_att("email-attachment://s/sig/0")])
    written = wire(store)

    backfill.run(dry_run=False)

    assert len(written) == 1
    assert written[0]["logical_path"] == "email-attachment://s/sig/0"


def test_dry_run_writes_nothing(wire):
    store = _Store(
        attachments=[_att("email-attachment://s/sig/0")],
        external=[_ext("paperless://s/documents/42")],
    )
    written = wire(store)

    backfill.run(dry_run=True)

    assert written == []


# --- both ledgers, and the names they produce --------------------------------


def test_both_ledgers_are_covered(wire):
    store = _Store(
        attachments=[_att("email-attachment://s/sig/0")],
        external=[_ext("paperless://s/documents/42")],
    )
    written = wire(store)

    backfill.run(dry_run=False)

    assert sorted(w["logical_path"] for w in written) == [
        "email-attachment://s/sig/0",
        "paperless://s/documents/42",
    ]


def test_an_attachment_keeps_the_filename_the_user_saw(wire):
    store = _Store(attachments=[_att("email-attachment://s/sig/0", filename="July bill.pdf")])
    written = wire(store)

    backfill.run(dry_run=False)

    assert written[0]["display_name"] == "July bill.pdf"
    assert written[0]["file_type"] == "pdf"


def test_a_paperless_document_never_backfills_as_a_bare_number(wire):
    """No title is available from the ledger, but `42` is not an acceptable
    fallback — that is the defect this whole chunk exists to remove."""
    store = _Store(external=[_ext("paperless://s/documents/42", external_id="42")])
    written = wire(store)

    backfill.run(dry_run=False)

    assert not written[0]["display_name"].isdigit()
    assert "42" in written[0]["display_name"]


def test_paperless_rows_are_marked_as_ocr(wire):
    """Paperless text IS OCR output; claiming otherwise misreports provenance."""
    store = _Store(external=[_ext("paperless://s/documents/42")])
    written = wire(store)

    backfill.run(dry_run=False)

    assert written[0]["ocr_used"] is True


def test_an_absent_ledger_table_does_not_abort_the_other(wire):
    """An older volume may predate one migration. Failing there would leave the
    other half unrepaired for no reason."""

    class _Partial(_Store):
        def fetch_all(self, sql, params=None):
            if "external_documents" in sql:
                raise RuntimeError('relation "external_documents" does not exist')
            return super().fetch_all(sql, params)

    store = _Partial(attachments=[_att("email-attachment://s/sig/0")])
    written = wire(store)

    backfill.run(dry_run=False)

    assert len(written) == 1


def test_rows_are_written_under_their_own_user(wire):
    """It crosses every user_id by design — so the one thing it must never do is
    attribute a row to the wrong one."""
    store = _Store(
        attachments=[
            _att("email-attachment://s/sig/0", user_id="alice"),
            _att("email-attachment://s/sig/1", user_id="bob"),
        ]
    )
    written = wire(store)

    backfill.run(dry_run=False)

    assert {w["user_id"] for w in written} == {"alice", "bob"}
    assert all(w["logical_path"].endswith("0") == (w["user_id"] == "alice") for w in written)
