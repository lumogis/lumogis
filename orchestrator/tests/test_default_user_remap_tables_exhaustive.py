# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression: every user-scoped Postgres table is covered by default-user remap.

Scans ``postgres/init.sql`` and ``postgres/migrations/`` for tables with a
``user_id`` column (same discovery logic as
``test_user_export_tables_exhaustive``). Every such table must appear in either:

* ``db_default_user_remap._SCOPED_TABLES`` (legacy ``user_id='default'`` rows
  are remapped on the single→multi auth flip), or
* :data:`_REMAP_INTENTIONAL_EXCLUSIONS` (identity tables that never carry
  owned rows under ``user_id``).

When this test fails, a future migration added a user-scoped table without
updating the remap allowlist — the auth-on flip would strand legacy rows.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_REMAP_SCRIPT = Path(__file__).resolve().parents[1] / "db_default_user_remap.py"
_INIT_SQL = _REPO_ROOT / "postgres" / "init.sql"
_MIGRATIONS_DIR = _REPO_ROOT / "postgres" / "migrations"

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
_ALTER_ADD_USER_ID_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+"
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?user_id\s+",
    re.IGNORECASE,
)

# Tables with a ``user_id`` column that are never *owned* rows to remap.
# ``users`` is the identity table (``id`` is the PK, not an ownership column).
_REMAP_INTENTIONAL_EXCLUSIONS: frozenset[str] = frozenset({"users"})


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
            if re.search(r"(?:^|,|\(|\n)\s*user_id\s+", body, re.IGNORECASE):
                out.add(table)
        for match in _ALTER_ADD_USER_ID_RE.finditer(sql):
            out.add(match.group(1))
    return out


def _scoped_tables_from_source() -> frozenset[str]:
    """Parse ``_SCOPED_TABLES`` without importing psycopg2."""
    tree = ast.parse(_REMAP_SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id != "_SCOPED_TABLES" or not isinstance(node.value, ast.Tuple):
                continue
            names = []
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.append(elt.value)
            return frozenset(names)
    raise AssertionError("_SCOPED_TABLES tuple not found in db_default_user_remap.py")


def test_default_user_remap_covers_every_user_scoped_table():
    discovered = _user_scoped_tables_from_sql()
    declared = _scoped_tables_from_source() | _REMAP_INTENTIONAL_EXCLUSIONS
    missing = discovered - declared
    assert not missing, (
        "New user-scoped Postgres table(s) detected without an entry in "
        "db_default_user_remap._SCOPED_TABLES (or _REMAP_INTENTIONAL_EXCLUSIONS). "
        "Legacy user_id='default' rows would be stranded on the auth-on flip.\n"
        f"Missing: {sorted(missing)}"
    )


def test_no_stale_entries_in_scoped_tables():
    discovered = _user_scoped_tables_from_sql()
    scoped = _scoped_tables_from_source()
    stale = scoped - discovered
    migrations = _REPO_ROOT / "postgres" / "migrations"
    if stale and not migrations.is_dir():
        return
    assert not stale, (
        "Entries in _SCOPED_TABLES no longer have a matching user_id-bearing "
        "CREATE TABLE in the SQL schema. Drop them from _SCOPED_TABLES.\n"
        f"Stale: {sorted(stale)}"
    )


def test_user_export_tables_are_subset_of_remap_tables():
    """Every user-export table must also be remapped on the auth flip."""
    export_path = Path(__file__).resolve().parents[1] / "services" / "user_export.py"
    tree = ast.parse(export_path.read_text(encoding="utf-8"))
    export_tables: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id != "_USER_EXPORT_TABLES" or not isinstance(node.value, ast.Tuple):
                continue
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    export_tables.add(elt.value)

    scoped = _scoped_tables_from_source()
    missing = export_tables - scoped
    assert not missing, (
        "User-export tables missing from db_default_user_remap._SCOPED_TABLES — "
        "exported user content would be invisible after auth-on flip.\n"
        f"Missing: {sorted(missing)}"
    )
