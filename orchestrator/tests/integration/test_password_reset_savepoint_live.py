# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-65 — a caught statement error still poisons a Postgres transaction.

The missing layer behind two defects that shipped and passed review twice.

Postgres aborts the **entire** transaction on any statement error. After one
statement raises, every later statement fails with `InFailedSqlTransaction`, and
`COMMIT` silently degrades to `ROLLBACK`. So `try/except` around a statement
does not mean "carry on" — it means "carry on inside a transaction that can no
longer do anything", which is worse than the error it caught because it looks
handled and reports success.

Two LUM-65 call sites were written on the opposite assumption:

* `password_reset.invalidate_for_user` caught `UndefinedTable` when migration
  073 was absent and returned 0 — and thereby broke the CLI lockout-recovery
  path it existed to protect, because the password `UPDATE` in the same
  transaction could no longer commit.
* `mint()`'s collision-retry loop retried an `INSERT` after a unique violation,
  where the retry could only ever fail with a different error.

**Neither was reachable from the unit suite.** The fakes raise plain Python
exceptions, which abort nothing, so a fake-backed test of the guard passes
whether or not the guard works. That is the same gap that let the original four
P0s ship behind a green suite — found again, one layer down.

Requires a reachable Postgres; skips otherwise (fatal in CI — LUM-718). The
`integration` marker is applied by this directory's conftest.
"""

from __future__ import annotations

import os
import uuid

import pytest
from adapters.postgres_store import PostgresStore
from tests.integration._pgguard import require_postgres

psycopg2 = pytest.importorskip("psycopg2")


def _conn_kwargs() -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": os.environ.get("POSTGRES_DB", "lumogis"),
    }


@pytest.fixture
def store():
    try:
        st = PostgresStore(**_conn_kwargs())
    except Exception as exc:  # pragma: no cover - environment-dependent
        require_postgres(exc)
    table = f"lum65_sp_probe_{uuid.uuid4().hex[:12]}"
    st.execute(f"CREATE TABLE {table} (tag TEXT PRIMARY KEY)")
    try:
        yield st, table
    finally:
        try:
            st._conn.autocommit = True
            st.execute(f"DROP TABLE IF EXISTS {table}")
            st._conn.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


def _committed(table: str) -> set[str]:
    conn = psycopg2.connect(**_conn_kwargs())
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT tag FROM {table}")
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def test_a_caught_error_without_a_savepoint_poisons_the_whole_transaction(store):
    """The hazard itself — this is what the bare try/except actually produced.

    Asserted first and on its own, because every other test here is only
    meaningful if this one holds. If Postgres ever stopped behaving this way,
    the savepoints below would be harmless ceremony rather than load-bearing.
    """
    st, table = store
    poisoned = None

    with pytest.raises(Exception):
        with st.isolated_transaction() as tx:
            tx.execute(f"INSERT INTO {table} VALUES (%s)", ("before",))
            try:
                tx.fetch_all("SELECT 1 FROM a_table_that_does_not_exist")
            except Exception:
                pass  # "handled" — and the transaction is now unusable
            # The next statement is the one that reveals it.
            tx.execute(f"INSERT INTO {table} VALUES (%s)", ("after",))

    poisoned = _committed(table)
    assert poisoned == set(), (
        "the caught error aborted the transaction, so even the write that "
        "preceded it is gone — a guard that swallows an error here does not "
        "protect the enclosing work, it destroys it"
    )


def test_savepoint_makes_a_failing_statement_genuinely_optional(store):
    """The fix — the enclosing transaction survives and commits."""
    st, table = store

    with st.isolated_transaction() as tx:
        tx.execute(f"INSERT INTO {table} VALUES (%s)", ("before",))
        try:
            with tx.savepoint():
                tx.fetch_all("SELECT 1 FROM a_table_that_does_not_exist")
        except Exception:
            pass  # genuinely handled now: ROLLBACK TO SAVEPOINT restored the tx
        tx.execute(f"INSERT INTO {table} VALUES (%s)", ("after",))

    assert _committed(table) == {"before", "after"}


def test_savepoint_commits_its_own_work_when_nothing_fails(store):
    """Release must not discard the savepoint's writes."""
    st, table = store

    with st.isolated_transaction() as tx:
        with tx.savepoint():
            tx.execute(f"INSERT INTO {table} VALUES (%s)", ("inner",))
        tx.execute(f"INSERT INTO {table} VALUES (%s)", ("outer",))

    assert _committed(table) == {"inner", "outer"}


def test_retry_after_a_unique_violation_succeeds_inside_a_savepoint(store):
    """`mint()`'s retry shape — the collision loop only works with a savepoint.

    Without one the retried INSERT raises `InFailedSqlTransaction`, whose text
    does not contain "unique", so `mint` re-raises and `_dispatch_or_pad`'s bare
    except turns it into `{"ok": true}` with no token and no mail.
    """
    st, table = store

    with st.isolated_transaction() as tx:
        tx.execute(f"INSERT INTO {table} VALUES (%s)", ("taken",))
        collided = False
        for candidate in ("taken", "free"):
            try:
                with tx.savepoint():
                    tx.execute(f"INSERT INTO {table} VALUES (%s)", (candidate,))
                break
            except Exception as exc:
                assert "unique" in str(exc).lower(), f"expected a unique violation, got {exc!r}"
                collided = True
                continue

    assert collided, "the first candidate should have collided"
    assert _committed(table) == {"taken", "free"}, "the retry must land"


def test_invalidate_for_user_survives_a_missing_table_inside_a_transaction(store):
    """End-to-end on the real guard: the enclosing password write must commit.

    `password_reset_tokens` does not exist in this scratch schema, which is
    exactly the half-applied-migration state the guard exists for.
    """
    from services import password_reset

    st, table = store

    with st.isolated_transaction() as tx:
        tx.execute(f"INSERT INTO {table} VALUES (%s)", ("password-write",))
        assert password_reset.invalidate_for_user(tx, "some-user") == 0
        tx.execute(f"INSERT INTO {table} VALUES (%s)", ("session-revoke",))

    assert _committed(table) == {"password-write", "session-revoke"}, (
        "the CLI lockout-recovery path must still change the password when "
        "migration 073 is absent — that is the whole point of the guard"
    )
