# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Migration 062 shape (LUM-650) — a cheap, DB-free content check.

The real round-trip (INSERT/SELECT against the applied schema, including the
first-contact GIN lookup and the action_proposals.annotation read) lives in the
CI-gated tests/integration/test_smtp_send_live.py, which loads migrations 022
(action_proposals) then 062."""

from __future__ import annotations

from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2] / "postgres" / "migrations" / "062-email-smtp-send.sql"
)


def _sql() -> str:
    return _MIGRATION.read_text(encoding="utf-8")


def test_migration_exists():
    assert _MIGRATION.is_file()


def test_creates_email_sent_log_idempotently():
    sql = _sql().lower()
    assert "create table if not exists email_sent_log" in sql
    for col in ("user_id", "source_id", "to_addrs", "message_id", "in_reply_to", "action"):
        assert col in sql, col
    # First-contact membership lookup rides a GIN index on to_addrs.
    assert "using gin (to_addrs)" in sql


def test_adds_action_proposals_annotation_column():
    sql = _sql().lower()
    assert "alter table action_proposals" in sql
    assert "add column if not exists annotation jsonb" in sql
    assert "default '{}'::jsonb" in sql


def test_is_transactional():
    sql = _sql().lower()
    assert "begin;" in sql and "commit;" in sql
