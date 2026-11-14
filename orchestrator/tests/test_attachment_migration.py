# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Migration 066 shape (LUM-695) — static SQL assertions, no DB required.

Mirrors the existing migration-shape tests in this suite. The real round-trip is
covered by the CI/Docker-gated integration lane.
"""

from __future__ import annotations

import pathlib

import pytest

_SQL = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "postgres"
    / "migrations"
    / "066-email-attachments.sql"
).read_text()
_NORM = " ".join(_SQL.split())


def _statements(prefix: str) -> list[str]:
    """Lines that START a statement — the file's own prose mentions these
    phrases, so a substring count over the whole file is not the same thing."""
    return [ln for ln in _SQL.splitlines() if ln.strip().upper().startswith(prefix)]


def test_migration_exists_and_is_idempotent():
    # Additive only — safe to re-run on an existing volume.
    assert len(_statements("CREATE TABLE IF NOT EXISTS")) == 2
    assert not _statements("ALTER TABLE")
    assert len(_statements("CREATE INDEX IF NOT EXISTS")) == 3


@pytest.mark.parametrize(
    "col",
    ["user_id", "signal_id", "part_index", "filename", "mime_type", "size_bytes"],
)
def test_email_attachment_columns(col):
    assert col in _SQL


@pytest.mark.parametrize(
    "col",
    [
        "user_id",
        "filename",
        "mime_type",
        "size_bytes",
        "storage_key",
        "proposal_id",
        "created_at",
        "consumed_at",
    ],
)
def test_email_attachment_staged_columns(col):
    assert col in _SQL


def test_both_tables_are_user_scoped_with_the_default():
    assert _NORM.count("user_id TEXT NOT NULL DEFAULT 'default'") == 2


def test_one_row_per_part_per_signal():
    """The key ON CONFLICT DO NOTHING keys on — a reprocessed signal is a no-op."""
    assert "UNIQUE (user_id, signal_id, part_index)" in _SQL


def test_no_fk_on_signal_id():
    """`signals.signal_id` is TEXT and the email family does not FK it
    (LUM-701/697 precedent). The owner-scoped read makes an orphan harmless."""
    assert "REFERENCES signals" not in _SQL


def test_the_sweep_has_both_of_its_indexes():
    """Two sweep queries: unreferenced-and-old, and referenced-but-terminal."""
    assert "(created_at) WHERE consumed_at IS NULL AND proposal_id IS NULL" in _NORM
    assert "(proposal_id) WHERE consumed_at IS NULL" in _NORM


def test_the_proposal_link_the_sweep_keys_on_exists_and_is_nullable():
    """Without this column the proposal-state sweep cannot be written at all."""
    assert "proposal_id  INTEGER" in _SQL
    assert "proposal_id  INTEGER NOT NULL" not in _SQL


def test_the_no_stored_bytes_decision_is_recorded_in_the_schema():
    """The constraint has to survive outside `.cursor/`. A reader looking at the
    table needs to know the absence of a bytes column is a decision, not a gap —
    and what it costs."""
    assert "BYTES are never stored" in _SQL
    assert "demand from IMAP" in _SQL
    assert "needs the mailbox reachable" in _SQL


def test_the_server_controlled_leaf_is_recorded_on_the_column():
    assert "client filename is NEVER a path component" in _SQL


def test_the_decoded_size_contract_is_recorded_on_the_column():
    """Comparing a base64 wire size against a 25 MB cap would refuse 18 MB files."""
    assert "DECODED size" in _SQL


def test_the_sweep_is_documented_as_proposal_state_not_wall_clock():
    assert "PROPOSAL STATE, not wall clock" in _SQL
