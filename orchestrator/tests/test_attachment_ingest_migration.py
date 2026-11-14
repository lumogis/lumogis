# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Migration 067 shape (LUM-702) — static SQL assertions, no DB required.

Two of these are not shape-checking for its own sake.

``signal_id TEXT`` is asserted because the sweep's central query LEFT JOINs this
table against ``email_attachment`` (066), whose ``signal_id`` is TEXT. Declaring
it UUID here parses fine and fails at runtime — Postgres will not join UUID to
TEXT without an explicit cast — so the feature would have broken on its first
real run against a real database.

The two unique indexes differ in a way that reads like an inconsistency and is
not: ``uq_..._active`` is **partial** so a skip row does not occupy the content
slot, and ``uq_..._part`` is **full** so an undo sticks. Both predicates are
pinned here, because "fixing" either one silently changes product behaviour.

The real round-trip — that Postgres actually *refuses* the second insert — lives
in ``tests/integration/test_attachment_ingest_indexes_live.py``. A static read of
the DDL cannot prove a constraint is enforced.
"""

from __future__ import annotations

import pathlib

import pytest

_SQL = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "postgres"
    / "migrations"
    / "067-email-attachment-ingest.sql"
).read_text()
_NORM = " ".join(_SQL.split())


def _statements(prefix: str) -> list[str]:
    """Lines that START a statement — the file's prose mentions these phrases,
    so a substring count over the whole file is not the same thing."""
    return [ln for ln in _SQL.splitlines() if ln.strip().upper().startswith(prefix)]


def test_migration_is_additive_and_idempotent():
    assert len(_statements("CREATE TABLE IF NOT EXISTS")) == 1
    assert len(_statements("ALTER TABLE")) == 1
    assert "ADD COLUMN IF NOT EXISTS attachment_ingest_enabled" in _NORM
    assert "ADD COLUMN IF NOT EXISTS attachment_ingest_since" in _NORM


def test_the_opt_in_defaults_to_off():
    """No mailbox files anything until someone says so."""
    assert "attachment_ingest_enabled BOOLEAN NOT NULL DEFAULT FALSE" in _NORM


def test_the_watermark_is_nullable_so_never_opted_in_is_representable():
    assert "attachment_ingest_since TIMESTAMPTZ" in _NORM
    assert "attachment_ingest_since TIMESTAMPTZ NOT NULL" not in _NORM


@pytest.mark.parametrize(
    "col",
    [
        "user_id",
        "source_id",
        "signal_id",
        "part_index",
        "filename",
        "mime_type",
        "size_bytes",
        "content_sha256",
        "logical_path",
        "chunk_count",
        "ingested_at",
        "reversed_at",
        "skipped_reason",
    ],
)
def test_ledger_columns(col):
    assert col in _SQL


def test_signal_id_is_text_to_match_email_attachment():
    """A UUID/TEXT join does not execute in Postgres without an explicit cast.

    ``email_attachment.signal_id`` (066) is TEXT and the sweep LEFT JOINs the
    two, so this column type is load-bearing, not cosmetic.
    """
    assert "signal_id TEXT NOT NULL" in _NORM
    assert "signal_id UUID" not in _NORM


def test_source_id_cascades_so_disconnecting_a_mailbox_removes_its_filings():
    assert "source_id UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE" in _NORM


def test_the_active_index_is_partial_so_a_skip_row_frees_the_content_slot():
    """Dedupe is a database invariant, not a check-then-insert race.

    The predicate is what lets a ``duplicate`` skip row exist without itself
    claiming the slot, and what frees the slot when a filing is reversed.
    """
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_email_attachment_ingest_active "
        "ON email_attachment_ingest_log (user_id, source_id, content_sha256) "
        "WHERE reversed_at IS NULL AND skipped_reason IS NULL" in _NORM
    )


def test_the_part_index_is_full_so_undo_sticks():
    """One verdict per part, EVER.

    A partial index here would let the next tick re-file an attachment the user
    just undid — an undo that silently reverts itself. If someone "fixes" this
    to match its partial sibling, this test is the tripwire.
    """
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_email_attachment_ingest_part "
        "ON email_attachment_ingest_log (user_id, signal_id, part_index)" in _NORM
    )
    part_stmt = _NORM.split("uq_email_attachment_ingest_part", 1)[1].split(";", 1)[0]
    assert "WHERE" not in part_stmt.upper(), (
        "uq_..._part must stay FULL — a partial predicate here lets a later tick "
        "re-file an undone attachment"
    )


def test_rollback_is_documented():
    assert "DROP TABLE IF EXISTS email_attachment_ingest_log" in _SQL


# --- 32 ---------------------------------------------------------------------


def test_the_ledger_is_registered_in_scoped_tables_and_user_export_tables():
    """Named rather than left to be discovered.

    Both registrations have been missed three times in this repo's history. The
    existing guards enforce them, but a plan-level case is what makes the
    omission visible at review time rather than at the default-user flip.

    Without the remap a filed document survives the flip while its undo path
    does not; without the export the archive carries documents whose origin is
    unexplainable.
    """
    from db_default_user_remap import _SCOPED_TABLES
    from services.user_export import _USER_EXPORT_TABLES

    assert "email_attachment_ingest_log" in _SCOPED_TABLES
    assert "email_attachment_ingest_log" in _USER_EXPORT_TABLES
