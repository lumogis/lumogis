# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Migration 065 shape (LUM-697) — static SQL assertions, no DB required.

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
    / "065-email-unsubscribe.sql"
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
    assert len(_statements("CREATE INDEX IF NOT EXISTS")) == 2


@pytest.mark.parametrize(
    "col",
    [
        "user_id",
        "source_id",
        "from_addr",
        "from_display",
        "mailto_addr",
        "mailto_subject",
        "https_uri",
        "one_click",
        "message_count",
        "first_seen_at",
        "last_seen_at",
        "unsubscribed_at",
    ],
)
def test_unsubscribe_target_columns(col):
    assert col in _SQL


def test_both_tables_are_user_scoped_with_the_default():
    assert _NORM.count("user_id TEXT NOT NULL DEFAULT 'default'") == 2


def test_both_tables_cascade_on_mailbox_delete():
    """Matches email_organize_log (migration 060). A subscription row and a
    suppression rule are meaningless without their mailbox — unlike `signals`,
    which LUM-701 deliberately keeps as readable history."""
    assert _NORM.count("source_id UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE") == 2


def test_one_row_per_sender_per_mailbox():
    assert "UNIQUE (user_id, source_id, from_addr)" in _SQL


def test_v1_is_hide_only_and_enforced_by_a_check_constraint():
    # mark_read / trash are deferred (LUM-709), not silently accepted-and-ignored.
    assert "CHECK (action IN ('hide'))" in _SQL


def test_the_subscriptions_query_shape_is_indexed():
    assert "(user_id, source_id, message_count DESC)" in _NORM


def test_rule_lookup_index_is_partial_on_active():
    assert "(user_id, source_id, from_addr) WHERE active" in _NORM


def test_the_no_server_side_fetch_decision_is_recorded_in_the_schema():
    """The constraint has to survive outside `.cursor/`. A future reader looking
    at the column needs to know the omission is a decision."""
    assert "NEVER fetched server-side" in _SQL
    assert "no inbound DKIM verification" in _SQL
