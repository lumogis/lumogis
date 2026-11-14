# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Migration 068 shape (LUM-727) — static SQL assertions, no DB required.

Mirrors ``test_attachment_migration.py``. The real round-trip runs in the
integration lane (``tests/integration/test_attachment_filing_rules_live.py``),
which LUM-718 made mandatory in CI.

Two assertions here are load-bearing rather than descriptive, and both encode a
decision the arbitration made explicitly:

* ``filed_by_rule_id`` is **ON DELETE SET NULL**, never CASCADE. Under CASCADE,
  deleting a rule would delete the ledger rows of everything it filed while the
  Qdrant chunks stayed — documents searchable with nothing able to remove them,
  which is exactly the orphan hole LUM-702 closed.
* There is **no** ``active`` column, diverging from ``email_sender_rule`` on
  purpose: deletion is hard there too, so that table's flag is permanently TRUE
  and therefore vestigial.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_PATH = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "postgres"
    / "migrations"
    / "068-attachment-filing-decisions.sql"
)
_SQL = _PATH.read_text()
_NORM = " ".join(_SQL.split())


def _statements(prefix: str) -> list[str]:
    """Lines that START a statement.

    The file's prose names ``ALTER TABLE`` in its rollback comment, so a
    substring count over the whole text would not be the same thing.
    """
    return [ln for ln in _SQL.splitlines() if ln.strip().upper().startswith(prefix)]


def test_the_migration_is_additive_and_idempotent():
    """Re-runnable on an existing volume: one CREATE, one ADD COLUMN, both guarded."""
    assert len(_statements("CREATE TABLE IF NOT EXISTS")) == 1
    assert len(_statements("CREATE INDEX IF NOT EXISTS")) == 1
    assert len(_statements("ALTER TABLE")) == 1
    assert "ADD COLUMN IF NOT EXISTS filed_by_rule_id" in _NORM
    # Nothing destructive: no data migration, no rewrite of LUM-702's rows.
    for banned in ("DROP TABLE", "DROP COLUMN", "DELETE FROM", "UPDATE "):
        assert (
            banned
            not in " ".join(
                ln for ln in _SQL.splitlines() if not ln.strip().startswith("--")
            ).upper()
        )


@pytest.mark.parametrize(
    "col",
    ["id", "user_id", "source_id", "match_type", "match_value", "action", "created_at"],
)
def test_email_attachment_rule_columns(col):
    assert re.search(rf"^\s+{col}\s", _SQL, re.MULTILINE)


def test_there_is_no_active_column():
    """The deliberate divergence from ``email_sender_rule``.

    Deletion is a hard delete in both, which leaves the sibling's ``active`` flag
    permanently TRUE — a column that is always TRUE is one a later reader
    eventually treats as meaningful. Asserted so a "consistency" patch that adds
    it back has to argue with this test first.
    """
    assert not re.search(r"^\s+active\s", _SQL, re.MULTILINE)


def test_one_rule_per_sender_or_category_per_mailbox():
    """The uniqueness the create path's 409 depends on."""
    assert "UNIQUE (user_id, source_id, match_type, match_value)" in _SQL


def test_match_type_and_action_are_constrained_in_the_database():
    """v1 has exactly one action. A future ``'ignore'`` must widen the CHECK
    deliberately rather than arriving as an unvalidated string."""
    assert "CHECK (match_type IN ('sender','category'))" in _NORM
    assert "CHECK (action IN ('file'))" in _NORM


def test_a_rule_dies_with_its_mailbox():
    """``source_id`` CASCADEs: a deleted mailbox cannot leave rules that would
    fire against a source_id that no longer resolves."""
    assert re.search(
        r"source_id\s+UUID\s+NOT NULL REFERENCES sources\(id\) ON DELETE CASCADE", _NORM
    )


def test_deleting_a_rule_does_not_delete_what_it_filed():
    """**The finding this column exists to avoid.**

    ON DELETE SET NULL: the filing loses its attribution and keeps its undo path.
    CASCADE here would remove the ledger row while the Qdrant chunks remained,
    recreating the orphaned-document hole LUM-702 closed.
    """
    assert re.search(
        r"filed_by_rule_id\s+UUID\s+REFERENCES email_attachment_rule\(id\) ON DELETE SET NULL",
        _NORM,
    )
    assert "REFERENCES email_attachment_rule(id) ON DELETE CASCADE" not in _NORM


def test_the_lookup_index_matches_how_rules_are_actually_read():
    """``_rules_for`` selects by user and filters by source; the index leads with
    both, so the per-candidate lookup is not a sequential scan once a user has
    accumulated rules across several mailboxes."""
    assert "ON email_attachment_rule (user_id, source_id)" in _NORM


def test_the_repurposed_column_is_documented_where_a_dba_will_look():
    """``attachment_ingest_enabled`` changed MEANING without changing name —
    from "file everything" to "propose for review". A COMMENT is the only place
    that survives into a schema dump."""
    assert "COMMENT ON COLUMN sources.attachment_ingest_enabled IS" in _SQL
