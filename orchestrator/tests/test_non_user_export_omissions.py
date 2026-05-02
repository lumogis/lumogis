# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression: non-user (household / instance-system) credential tables
are explicitly omitted from per-user exports.

Pinned by ADR ``credential_scopes_shared_system`` § Modified files /
``services/user_export.py``.

Mechanism: the canonical declarative omission registry lives at
``services.user_export._OMITTED_NON_USER_TABLES``. The plan-mandated
test contract (Test 14 / "non-user export omission registry") is:

1.  Both shared-tier credential tables (``household_connector_credentials``
    and ``instance_system_connector_credentials``) MUST appear in
    ``_OMITTED_NON_USER_TABLES`` with a non-empty reason string.
2.  Neither table MAY appear in ``_USER_EXPORT_TABLES`` (the per-user
    export allowlist) — they are not user-owned at all.
3.  Reason strings MUST be unique (mirrors the existing
    ``_OMITTED_USER_TABLES`` uniqueness invariant; copy/paste from a
    future addition would silently lose operator-facing context).
4.  Every table named in ``_OMITTED_NON_USER_TABLES`` MUST exist in the
    Postgres schema (init.sql + migrations) — guards against the
    omission registry going stale after a table rename or drop.
5.  Every table named in ``_OMITTED_NON_USER_TABLES`` MUST NOT have a
    ``user_id`` column in the schema — that's the whole reason it's
    declared in the *non-user* registry rather than ``_OMITTED_USER_TABLES``.
"""

from __future__ import annotations

import re
from pathlib import Path

from services.user_export import _OMITTED_NON_USER_TABLES
from services.user_export import _OMITTED_USER_TABLES
from services.user_export import _USER_EXPORT_TABLES

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INIT_SQL = _REPO_ROOT / "postgres" / "init.sql"
_MIGRATIONS_DIR = _REPO_ROOT / "postgres" / "migrations"

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)


def _all_create_table_bodies() -> dict[str, str]:
    """Return ``{table_name: body_inside_parens}`` for every CREATE TABLE."""
    files = [_INIT_SQL] + sorted(_MIGRATIONS_DIR.glob("*.sql"))
    out: dict[str, str] = {}
    for path in files:
        if not path.is_file():
            continue
        sql = path.read_text(encoding="utf-8")
        for match in _CREATE_TABLE_RE.finditer(sql):
            table = match.group(1)
            body = match.group(2)
            # Last writer wins — migrations don't redefine these tables,
            # but if a future migration ever did, the most recent shape
            # is what matters.
            out[table] = body
    return out


# Tables the plan REQUIRES be present in the omission registry. Pinned
# explicitly here (rather than only relying on len(...) == 2) so a
# future addition to the registry doesn't silently weaken these
# regressions.
_REQUIRED_OMITTED_TABLES: frozenset[str] = frozenset(
    {
        "household_connector_credentials",
        "instance_system_connector_credentials",
    }
)


def test_required_shared_tier_tables_are_in_omission_registry():
    """Both new tier tables MUST appear in ``_OMITTED_NON_USER_TABLES``.

    Without this entry the operator-facing manifest of "things deliberately
    omitted from this export" loses the credentials line, which would
    make a recipient think they got the household secrets when they did
    not.
    """
    missing = _REQUIRED_OMITTED_TABLES - set(_OMITTED_NON_USER_TABLES)
    assert not missing, (
        "Tables required by ADR credential_scopes_shared_system are "
        "missing from services.user_export._OMITTED_NON_USER_TABLES. "
        "Add them with a non-empty reason string.\n"
        f"Missing: {sorted(missing)}"
    )


def test_omitted_non_user_tables_never_listed_in_user_export_allowlist():
    """The shared-tier tables MUST NOT be in ``_USER_EXPORT_TABLES``.

    If either table ever lands in the per-user allowlist, the export
    bundle would carry household-scope ciphertext into a per-user zip.
    The recipient would not have the household Fernet key and the row
    would be useless — but more importantly, the bundle would *contain*
    sealed material that crossed a tier boundary.
    """
    overlap = set(_OMITTED_NON_USER_TABLES) & set(_USER_EXPORT_TABLES)
    assert not overlap, (
        "Tables in _OMITTED_NON_USER_TABLES must NOT also appear in "
        "_USER_EXPORT_TABLES — that would re-include sealed shared-tier "
        "ciphertext in a per-user export bundle.\n"
        f"Overlap: {sorted(overlap)}"
    )


def test_omitted_non_user_tables_have_unique_non_empty_reason_strings():
    """Each entry must carry a distinct, non-empty reason.

    Mirrors the ``_OMITTED_USER_TABLES`` uniqueness invariant — copy/
    paste from a future addition would silently lose operator-facing
    context in the manifest.
    """
    reasons = list(_OMITTED_NON_USER_TABLES.values())
    assert len(reasons) == len(set(reasons)), (
        "Duplicate reason strings in _OMITTED_NON_USER_TABLES; each "
        "omission must explain itself.\n"
        f"reasons: {reasons}"
    )
    for table, reason in _OMITTED_NON_USER_TABLES.items():
        assert reason and reason.strip(), (
            f"_OMITTED_NON_USER_TABLES['{table}'] must have a non-empty reason string."
        )


def test_omitted_non_user_tables_disjoint_from_user_omission_registry():
    """The two omission registries MUST partition the omitted-table space.

    A table is either user-shape but stripped from the per-user export
    (``_OMITTED_USER_TABLES``) OR shared/system-shape and entirely
    out-of-scope for per-user exports (``_OMITTED_NON_USER_TABLES``).
    Listing the same table in both would produce ambiguous manifest
    semantics ("is this user-shape or not?") and is a sign of a
    classification bug.
    """
    overlap = set(_OMITTED_NON_USER_TABLES) & set(_OMITTED_USER_TABLES)
    assert not overlap, (
        "Tables appear in BOTH _OMITTED_NON_USER_TABLES and "
        "_OMITTED_USER_TABLES — pick exactly one based on whether the "
        "table has a user_id column.\n"
        f"Overlap: {sorted(overlap)}"
    )


def test_omitted_non_user_tables_actually_exist_in_schema():
    """Every entry MUST correspond to a real CREATE TABLE in the schema.

    Guards against the registry going stale after a table rename or
    drop — if the omission silently points at a no-longer-existing
    table the operator manifest lies about what is being skipped.
    """
    bodies = _all_create_table_bodies()
    if not bodies:
        # Pruned checkout (e.g. only init.sql, no migrations) — be
        # lenient rather than false-positive.
        return
    missing = set(_OMITTED_NON_USER_TABLES) - set(bodies)
    assert not missing, (
        "Tables in _OMITTED_NON_USER_TABLES no longer exist in the "
        "Postgres schema (init.sql + migrations). Either restore the "
        "table or drop the omission entry.\n"
        f"Missing: {sorted(missing)}"
    )


def test_omitted_non_user_tables_have_no_user_id_column():
    """The whole point of the *non-user* registry: these tables don't
    have a ``user_id`` column.

    If one of them ever sprouts a ``user_id`` column it should move to
    ``_OMITTED_USER_TABLES`` (or, more likely, be re-classified as a
    proper per-user export with its own filter rules). This test
    catches the migration-without-classification-update case.
    """
    bodies = _all_create_table_bodies()
    if not bodies:
        return
    offenders: list[str] = []
    for table in _OMITTED_NON_USER_TABLES:
        body = bodies.get(table)
        if body is None:
            continue
        if re.search(
            r"(?:^|,|\(|\n)\s*user_id\s+",
            body,
            re.IGNORECASE,
        ):
            offenders.append(table)
    assert not offenders, (
        "Tables in _OMITTED_NON_USER_TABLES now have a ``user_id`` "
        "column — they should be moved to _OMITTED_USER_TABLES (or "
        "re-classified as a proper user-shape export).\n"
        f"Offenders: {sorted(offenders)}"
    )
