# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Migration 074's `pg_trgm` privilege behaviour (LUM-828).

Migration 074 (LUM-819) carries the repo's first `CREATE EXTENSION`. Because
`db_migrations.main` returns 2 on the boot path, a failure here is not a
missing feature -- the orchestrator does not start.

**The premise this file exists to pin down.** LUM-828 was filed saying the
migration fails "on an instance whose application role is not a superuser".
Measured on Postgres 16.13, that is not the rule: `pg_trgm` is a TRUSTED
extension (PG13+), so ownership of the database -- or plain CREATE on it --
is enough. The three cases below are the actual contract, and each is
asserted rather than assumed:

1. Non-superuser that OWNS the database        -> succeeds.
2. Non-superuser with neither ownership nor CREATE -> fails, fail-closed,
   with a message naming the extension and the remedy.
3. Any role, once an administrator pre-installed it -> succeeds via
   IF NOT EXISTS. This is the documented pre-step, verified here rather
   than merely written in a runbook.

Case 1 is the one that matters commercially: it is the shape managed Postgres
normally hands you, so RDS / Cloud SQL / Supabase are not blocked by this line.
If a future Postgres ever un-trusts `pg_trgm`, case 1 flips and this file is
where that shows up.

Like the other migration regressions here (see
`test_credential_tiers_migration.py`), these need a real Postgres and skip
when none is reachable. They additionally need a **superuser** connection,
because they create roles and databases; they skip rather than fail when the
connection is not superuser, so a developer pointed at a restricted instance
still gets a green suite.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")
from psycopg2 import sql  # noqa: E402 — after importorskip

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATION_PATH = _REPO_ROOT / "postgres" / "migrations" / "074-email-body-text-search.sql"

#: Throwaway roles need a password: compose ``pg_hba`` is scram/md5, not
#: trust. Connecting without one fails with ``fe_sendauth: no password
#: supplied`` — which is not the privilege contract these tests exist to pin.
_ROLE_PASSWORD = "t74-role-pass-not-secret"


def _conn_kwargs(dbname: str | None = None) -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": dbname or os.environ.get("POSTGRES_DB", "lumogis"),
        "connect_timeout": 3,
    }


def _extension_block() -> str:
    """Return just the `DO $$ ... $$;` block that installs the extension.

    The rest of 074 needs a `signals` table to reference; the privilege
    contract lives entirely in this block, so the test exercises it in
    isolation against throwaway databases. Extracted from the real file --
    never a copy -- so editing the migration cannot leave this test asserting
    against text that no longer ships.
    """
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")
    match = re.search(r"^DO \$\$.*?^\$\$;", sql, re.DOTALL | re.MULTILINE)
    assert match, "migration 074 no longer contains a DO $$ ... $$; block"
    return match.group(0)


@pytest.fixture(scope="module")
def superuser():
    """A superuser connection, or skip. Needed to create roles/databases."""
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001 — diagnostic skip
        pytest.skip(f"Postgres not reachable for migration 074 privilege test: {exc}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        row = cur.fetchone()
    if not row or not row[0]:
        conn.close()
        pytest.skip("connection is not superuser; cannot create test roles/databases")
    yield conn
    conn.close()


@pytest.fixture
def scratch(superuser):
    """Yield a factory for throwaway roles and databases, cleaned up after."""
    tag = uuid.uuid4().hex[:10]
    made_dbs: list[str] = []
    made_roles: list[str] = []

    def role(suffix: str) -> str:
        name = f"t74_{suffix}_{tag}"
        with superuser.cursor() as cur:
            cur.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER PASSWORD {}").format(
                    sql.Identifier(name),
                    sql.Literal(_ROLE_PASSWORD),
                )
            )
        made_roles.append(name)
        return name

    def database(suffix: str, owner: str | None = None) -> str:
        name = f"t74db_{suffix}_{tag}"
        with superuser.cursor() as cur:
            if owner:
                cur.execute(f'CREATE DATABASE "{name}" OWNER "{owner}"')
            else:
                cur.execute(f'CREATE DATABASE "{name}"')
        made_dbs.append(name)
        return name

    yield role, database

    for db in made_dbs:
        with superuser.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
    for r in made_roles:
        with superuser.cursor() as cur:
            cur.execute(f'DROP ROLE IF EXISTS "{r}"')


def _run_block_as(role_name: str, dbname: str) -> None:
    """Execute the extension block as ``role_name``. Raises on failure."""
    kwargs = _conn_kwargs(dbname)
    kwargs["user"] = role_name
    kwargs["password"] = _ROLE_PASSWORD
    conn = psycopg2.connect(**kwargs)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(_extension_block())
    finally:
        conn.close()


def _lock_down(superuser, dbname: str, role_name: str) -> None:
    with superuser.cursor() as cur:
        cur.execute(f'REVOKE CREATE ON DATABASE "{dbname}" FROM PUBLIC')
        cur.execute(f'GRANT CONNECT ON DATABASE "{dbname}" TO "{role_name}"')


def test_a_non_superuser_database_owner_can_install_it(scratch):
    """pg_trgm is TRUSTED -- superuser is not the requirement.

    This is the case LUM-828's premise got wrong, and the one that decides
    whether managed Postgres is blocked. If this ever starts failing, the
    extension stopped being trusted and the deployment story changes.
    """
    role, database = scratch
    owner = role("owner")
    db = database("owned", owner=owner)

    _run_block_as(owner, db)  # must not raise


def test_a_role_without_ownership_or_create_fails_closed_and_says_why(scratch, superuser):
    """The real failure case -- and it must name the remedy, not just deny.

    Fail-closed is deliberate: a partial schema is worse than a refusal. What
    is being asserted is that the operator gets something actionable in the
    boot log instead of a bare ``permission denied to create extension``.
    """
    role, database = scratch
    locked = role("locked")
    db = database("locked")
    _lock_down(superuser, db, locked)

    with pytest.raises(psycopg2.Error) as exc_info:
        _run_block_as(locked, db)

    text = str(exc_info.value)
    assert "pg_trgm" in text, "the error must name the extension"
    assert "CREATE EXTENSION pg_trgm" in text, "the error must state the remedy verbatim"
    assert "trusted" in text.lower(), "the error must correct the superuser misconception"


def test_a_locked_down_role_succeeds_once_an_admin_preinstalled_it(scratch, superuser):
    """The documented pre-step, verified rather than hoped for.

    ``IF NOT EXISTS`` means an unprivileged role sails past the extension
    line when the extension is already present -- which is what makes
    "ask your DBA to run one statement" a real answer.
    """
    role, database = scratch
    locked = role("pre")
    db = database("pre")
    _lock_down(superuser, db, locked)
    with psycopg2.connect(**_conn_kwargs(db)) as admin:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    _run_block_as(locked, db)  # must not raise


def test_the_migration_still_declares_the_extension_at_all():
    """Cheap guard: the block must not be refactored away silently.

    Every trigram index later in 074 depends on it, and dropping the
    declaration would surface as a confusing ``operator class gin_trgm_ops
    does not exist`` far from the cause.
    """
    sql = _MIGRATION_PATH.read_text(encoding="utf-8")

    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in sql
    assert "gin_trgm_ops" in sql, "the trigram indexes are what the extension is for"
