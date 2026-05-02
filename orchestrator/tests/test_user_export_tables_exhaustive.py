# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression: every user-scoped Postgres table is declared exhaustive.

Scans ``postgres/init.sql`` and every file in ``postgres/migrations/``
for ``CREATE TABLE`` statements containing a ``user_id`` column. Every
such table must appear in either:

* ``services.user_export._USER_EXPORT_TABLES`` (rows belong to a
  specific user; gets exported), or
* :data:`_INTENTIONAL_EXCLUSIONS` (global / per-instance state that
  should NOT travel with a user export).

When this test fails, a future migration has added a new user-scoped
table without telling the export path. Pick one of:

1. Add the table to ``_USER_EXPORT_TABLES`` (and ``_TABLES_WITH_SCOPE``
   if it has a ``scope`` column).
2. Add it to ``_INTENTIONAL_EXCLUSIONS`` here, with a comment
   explaining why a per-user export should ignore it.
"""

from __future__ import annotations

import re
from pathlib import Path

from services.user_export import _OMITTED_USER_TABLES
from services.user_export import _USER_EXPORT_TABLES

# Global / per-instance tables that intentionally do NOT travel with
# per-user exports. Update with a comment when adding a new entry.
#
# Note: tables that *do* have a per-user shape but are deliberately
# stripped from the standard zip export are sourced from
# ``services.user_export._OMITTED_USER_TABLES`` (canonical owner) and
# merged in below — keeping the test in lock-step with the service
# without two places to update.
_INTENTIONAL_EXCLUSIONS: frozenset[str] = frozenset(
    {
        # Postgres-side users table — re-created via NewUserSpec on import,
        # not bulk-copied from the archive.
        "users",
        # MCP tokens table (plan ``mcp_token_user_map`` D9): rows store the
        # SHA-256 of an opaque bearer credential. Exporting the hashes would
        # leak operationally useful material (collision search, replay if a
        # client cached the plaintext); exporting the row metadata without the
        # hash would be confusing dead state at the destination since the
        # plaintext was shown exactly once at mint time on the source. Per-
        # user MCP integrations are reconfigured at the destination — the
        # archive intentionally carries no token state.
        "mcp_tokens",
    }
) | frozenset(_OMITTED_USER_TABLES)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INIT_SQL = _REPO_ROOT / "postgres" / "init.sql"
_MIGRATIONS_DIR = _REPO_ROOT / "postgres" / "migrations"

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)

# Migrations may retro-fit user_id onto a previously-global table via
# ``ALTER TABLE ... ADD COLUMN user_id ...`` (see migration 016 for
# routine_do_tracking). Such a table is user-scoped post-migration even
# though its CREATE TABLE in init.sql has no user_id column.
_ALTER_ADD_USER_ID_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(\w+)\s+"
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?user_id\s+",
    re.IGNORECASE,
)


def _user_scoped_tables_from_sql() -> set[str]:
    """Extract every table that defines (or later acquires) a ``user_id`` column."""
    files = [_INIT_SQL] + sorted(_MIGRATIONS_DIR.glob("*.sql"))
    out: set[str] = set()
    for path in files:
        if not path.is_file():
            continue
        sql = path.read_text(encoding="utf-8")
        for match in _CREATE_TABLE_RE.finditer(sql):
            table, body = match.group(1), match.group(2)
            # Match ``user_id`` as a standalone column declaration. The
            # body is the inside of the parentheses; column names are
            # word-prefixed with whitespace or comma.
            if re.search(r"(?:^|,|\(|\n)\s*user_id\s+", body, re.IGNORECASE):
                out.add(table)
        for match in _ALTER_ADD_USER_ID_RE.finditer(sql):
            out.add(match.group(1))
    return out


def test_user_export_tables_covers_every_user_scoped_table():
    discovered = _user_scoped_tables_from_sql()
    declared = set(_USER_EXPORT_TABLES) | _INTENTIONAL_EXCLUSIONS
    missing = discovered - declared
    assert not missing, (
        "New user-scoped Postgres table(s) detected without an entry in "
        "services.user_export._USER_EXPORT_TABLES (or the test's "
        "_INTENTIONAL_EXCLUSIONS allowlist). Update one of them.\n"
        f"Missing: {sorted(missing)}"
    )


def test_user_connector_credentials_is_omitted_not_exported():
    """``user_connector_credentials`` is sealed under the household
    Fernet key (per ADR ``per_user_connector_credentials``), which is
    not part of the export bundle. The table must never land in the
    plaintext per-user allowlist; it is declared in
    ``_OMITTED_USER_TABLES`` so the manifest records the omission."""
    assert "user_connector_credentials" in _OMITTED_USER_TABLES, (
        "user_connector_credentials must be declared in "
        "services.user_export._OMITTED_USER_TABLES with a reason."
    )
    assert "user_connector_credentials" not in _USER_EXPORT_TABLES, (
        "user_connector_credentials must NOT be in _USER_EXPORT_TABLES "
        "— it stores Fernet ciphertext sealed with the household key."
    )


def test_omitted_user_tables_have_unique_reason_strings():
    """Each ``_OMITTED_USER_TABLES`` entry must carry a distinct
    reason — copy/paste from a future addition would silently lose
    operator-facing context in the manifest."""
    reasons = list(_OMITTED_USER_TABLES.values())
    assert len(reasons) == len(set(reasons)), (
        "Duplicate reason strings in _OMITTED_USER_TABLES; each "
        "omission must explain itself.\n"
        f"reasons: {reasons}"
    )
    for table, reason in _OMITTED_USER_TABLES.items():
        assert reason and reason.strip(), (
            f"_OMITTED_USER_TABLES['{table}'] must have a non-empty reason string."
        )


def test_no_stale_entries_in_user_export_tables():
    """Every entry in ``_USER_EXPORT_TABLES`` actually exists in the schema."""
    discovered = _user_scoped_tables_from_sql()
    stale = set(_USER_EXPORT_TABLES) - discovered
    # Be lenient when running against a pruned checkout (e.g. only
    # init.sql, no migrations) so the check doesn't false-positive.
    if stale and not _MIGRATIONS_DIR.is_dir():
        return
    assert not stale, (
        "Entries in _USER_EXPORT_TABLES no longer have a matching "
        "user_id-bearing CREATE TABLE in the SQL schema. Either restore "
        "the table or drop it from _USER_EXPORT_TABLES.\n"
        f"Stale: {sorted(stale)}"
    )
