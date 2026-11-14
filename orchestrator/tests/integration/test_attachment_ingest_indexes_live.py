# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Migration 067's unique indexes, against a real Postgres (LUM-702).

The plan classified this "blocker-adjacent" and deferred it only because the lane
did not run. LUM-718 made it run, so it lands here instead of as a follow-up.

Why it cannot be a unit test: **dedupe is a database invariant**, and the house
fake store cannot enforce a unique index. The unit cases in
``tests/test_email_attachment_ingest.py`` prove the application *intends* to
refuse a second copy; only these prove Postgres actually *does* — which is what
makes the guarantee hold under two concurrent ticks, where the application-level
SELECT races itself.

Two indexes, and they differ on purpose:

* ``uq_..._active`` is **partial** — a ``duplicate`` skip row must exist (so the
  sweep stops reconsidering it) without claiming the content slot, and reversing
  a filing must free the slot for a later message carrying the same bytes.
* ``uq_..._part`` is **full** — one verdict per part, ever. That is what makes
  undo stick; a partial predicate here would let the next tick re-file what the
  user just removed.

Each is asserted by *behaviour* (does the second INSERT raise?), not by reading
``pg_indexes`` — a predicate can be present and still not mean what was intended.
"""

from __future__ import annotations

import os
import uuid

import pytest
from tests.integration._pgguard import require_postgres

psycopg2 = pytest.importorskip("psycopg2")


_MIGRATION = "067-email-attachment-ingest.sql"
USER = "alice"


def _conn_kwargs() -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": os.environ.get("POSTGRES_DB", "lumogis"),
        "connect_timeout": 3,
    }


@pytest.fixture(scope="module")
def _pg():
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001
        require_postgres(exc)
    conn.close()


@pytest.fixture
def schema(_pg):
    """An isolated schema carrying only what migration 067 needs.

    ``sources`` is created by hand rather than by applying the whole base schema:
    the ledger's only structural dependency is that one FK, and a self-contained
    fixture keeps this test about the indexes.
    """
    import pathlib

    name = f"test_lum702_{uuid.uuid4().hex[:12]}"
    sql = (
        pathlib.Path(__file__).resolve().parent.parent.parent.parent
        / "postgres"
        / "migrations"
        / _MIGRATION
    ).read_text()

    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            cur.execute("CREATE TABLE sources (id UUID PRIMARY KEY)")
            cur.execute(sql)
        yield conn, name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        conn.close()


def _real_ddl(table: str) -> str:
    """The real ``CREATE TABLE`` for ``table``, lifted from the shipped schema.

    Searches ``postgres/init.sql`` then the migrations, because base tables and
    later additions live in different files (``signals`` in init, this feature's
    ``email_attachment`` in 066).

    Read rather than retyped, and that is the point: an earlier version of this
    test hand-wrote a ``signals`` stub with a TEXT ``signal_id`` and passed —
    proving only that the stub agreed with the query, while the real column is
    UUID and the query could not execute at all.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent.parent.parent / "postgres"
    candidates = [root / "init.sql", *sorted((root / "migrations").glob("*.sql"))]
    match = None
    for path in candidates:
        match = re.search(
            rf"CREATE TABLE IF NOT EXISTS {table}\s*\(.*?\n\);",
            path.read_text(),
            re.DOTALL | re.IGNORECASE,
        )
        if match:
            break
    if match is None:  # pragma: no cover - a rename should fail loudly
        raise AssertionError(f"no CREATE TABLE for {table!r} in postgres/")
    # Strip FK clauses whole — REFERENCES plus any ON DELETE/UPDATE tail. This
    # isolated schema carries only the tables under test, and the column TYPES are
    # the point here, not the constraints.
    body = re.sub(
        r"\s*REFERENCES\s+\w+\s*(\([^)]*\))?(\s+ON\s+(DELETE|UPDATE)\s+\w+(\s+\w+)?)*",
        "",
        match.group(0),
        flags=re.IGNORECASE,
    )
    return body


def _source(conn, schema_name: str) -> str:
    sid = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute("INSERT INTO sources (id) VALUES (%s)", (sid,))
    return sid


def _insert(
    conn,
    schema_name: str,
    *,
    source_id: str,
    signal_id: str,
    part_index: int = 0,
    digest: str = "hash-a",
    skipped_reason: str | None = None,
    reversed_at_now: bool = False,
):
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema_name}"')
        cur.execute(
            "INSERT INTO email_attachment_ingest_log "
            "(user_id, source_id, signal_id, part_index, filename, content_sha256, "
            " logical_path, skipped_reason, reversed_at) "
            "VALUES (%s, %s, %s, %s, 'f.pdf', %s, 'p', %s, "
            f"{'NOW()' if reversed_at_now else 'NULL'})",
            (USER, source_id, signal_id, part_index, digest, skipped_reason),
        )


# --- uq_..._active — dedupe is enforced by the DATABASE ----------------------


def test_postgres_refuses_a_second_active_filing_of_the_same_bytes(schema):
    """The guarantee the fake store cannot make."""
    conn, name = schema
    src = _source(conn, name)
    _insert(conn, name, source_id=src, signal_id="s1")

    with pytest.raises(psycopg2.errors.UniqueViolation):
        _insert(conn, name, source_id=src, signal_id="s2")


def test_the_same_bytes_in_a_different_mailbox_are_allowed(schema):
    """Dedupe is scoped per source — the documented trade-off, proven."""
    conn, name = schema
    src_a, src_b = _source(conn, name), _source(conn, name)
    _insert(conn, name, source_id=src_a, signal_id="s1")

    _insert(conn, name, source_id=src_b, signal_id="s2")  # must not raise


def test_a_duplicate_skip_row_does_not_occupy_the_content_slot(schema):
    """``skipped_reason IS NULL`` in the predicate, proven behaviourally.

    Without it, recording the duplicate would itself claim the hash and the
    original could never be re-filed after an undo.
    """
    conn, name = schema
    src = _source(conn, name)
    _insert(conn, name, source_id=src, signal_id="s1")
    _insert(conn, name, source_id=src, signal_id="s2", skipped_reason="duplicate")

    # Two skip rows with the same hash are also fine — none of them is active.
    _insert(conn, name, source_id=src, signal_id="s3", skipped_reason="duplicate")


def test_reversing_a_filing_frees_the_content_slot(schema):
    """``reversed_at IS NULL`` in the predicate: a later message carrying the
    same bytes can be filed once the original is undone."""
    conn, name = schema
    src = _source(conn, name)
    _insert(conn, name, source_id=src, signal_id="s1")

    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute("UPDATE email_attachment_ingest_log SET reversed_at = NOW()")

    _insert(conn, name, source_id=src, signal_id="s2")  # must not raise


# --- uq_..._part — one verdict per part, EVER --------------------------------


def test_postgres_refuses_a_second_verdict_for_the_same_part(schema):
    conn, name = schema
    src = _source(conn, name)
    _insert(conn, name, source_id=src, signal_id="s1", part_index=0)

    with pytest.raises(psycopg2.errors.UniqueViolation):
        _insert(conn, name, source_id=src, signal_id="s1", part_index=0, digest="hash-b")


def test_a_reversed_verdict_still_blocks_a_second_one(schema):
    """**This is what makes undo stick.**

    If ``uq_..._part`` were partial on ``reversed_at IS NULL`` — the "fix" the
    arbitration rejected — this INSERT would succeed, and the next tick would
    re-file the attachment the user had just removed.
    """
    conn, name = schema
    src = _source(conn, name)
    _insert(conn, name, source_id=src, signal_id="s1", part_index=0, reversed_at_now=True)

    with pytest.raises(psycopg2.errors.UniqueViolation):
        _insert(conn, name, source_id=src, signal_id="s1", part_index=0, digest="hash-b")


def test_a_skip_verdict_still_blocks_a_second_one(schema):
    """A skip is a decision, so the part is judged and must not be revisited."""
    conn, name = schema
    src = _source(conn, name)
    _insert(conn, name, source_id=src, signal_id="s1", skipped_reason="no_extractor")

    with pytest.raises(psycopg2.errors.UniqueViolation):
        _insert(conn, name, source_id=src, signal_id="s1", digest="hash-b")


def test_different_parts_of_one_message_are_independent(schema):
    conn, name = schema
    src = _source(conn, name)
    _insert(conn, name, source_id=src, signal_id="s1", part_index=0, digest="a")

    _insert(conn, name, source_id=src, signal_id="s1", part_index=1, digest="b")


# --- the column type the arbitration caught ----------------------------------


def test_the_sweep_selection_query_executes_against_the_provisioned_schema(_pg):
    """**Three** of this feature's joins cross a type boundary, and none executes
    without a cast:

    * ``email_attachment.signal_id`` is TEXT (066) vs ``signals.signal_id`` UUID
    * ``email_attachment_ingest_log.signal_id`` is TEXT (067) vs the same
    * ``signals.source_id`` is **TEXT** vs ``sources.id`` **UUID**

    The third was introduced by LUM-727's own fix for a critique finding and
    shipped the very defect the critique was about. This test caught it.

    Deliberately run against the **provisioned public schema**, not an isolated
    hand-built one. An earlier version created `signals` from its ``CREATE
    TABLE`` in ``init.sql`` — which omits every column a later migration adds
    (``category`` among them), so the stub disagreed with production in a second
    way while claiming to be the real definition. A copied schema can only ever
    prove the copy is consistent; the shipped schema is the only thing worth
    executing against, and LUM-718 guarantees CI has one.
    """
    from services.email_attachment_ingest import _CANDIDATES_SQL

    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            # Executing it IS the assertion — every type mismatch raises here.
            cur.execute(
                _CANDIDATES_SQL,
                (str(uuid.uuid4()), USER, "epoch", 26214400, ["pdf"], 50),
            )
            assert cur.fetchall() == [], "an unknown source has no candidates"
    finally:
        conn.close()


def test_the_filed_list_query_executes_against_real_postgres(schema):
    """The same boundary, on the read path — and this join is LEFT, so a missing
    cast fails just as hard while looking more innocent."""
    conn, name = schema
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute(_real_ddl("signals"))
        cur.execute(
            "SELECT l.id, l.signal_id, s.from_addr, s.from_display, s.title "
            "FROM email_attachment_ingest_log l "
            "LEFT JOIN signals s ON s.signal_id::text = l.signal_id "
            "AND s.user_id = l.user_id "
            "WHERE l.user_id = %s ORDER BY l.ingested_at DESC LIMIT %s",
            (USER, 50),
        )

        assert cur.fetchall() == []
