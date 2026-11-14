# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Migration 068 against a real Postgres (LUM-727).

``test_attachment_filing_decisions_migration.py`` asserts the SQL *text*. That
proves the file says ``ON DELETE SET NULL``; it cannot prove Postgres does it.
The distinction is not academic — the whole reason that clause is there is a
runtime behaviour, and a static assertion would pass just as happily if the FK
had never been created at all.

Each case here is asserted by **behaviour**: delete the parent, read the child.
"""

from __future__ import annotations

import os
import uuid

import pytest
from tests.integration._pgguard import require_postgres

psycopg2 = pytest.importorskip("psycopg2")


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


@pytest.fixture
def pg():
    """The PROVISIONED schema, not a hand-built one.

    LUM-702's sibling learned this the expensive way twice: a copied ``CREATE
    TABLE`` omits every column a later migration adds, so the stub agrees with
    the query while production does not. LUM-718 guarantees CI has a real one.
    """
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001
        require_postgres(exc)
    conn.autocommit = True
    made: list[tuple[str, str]] = []
    try:
        yield conn, made
    finally:
        with conn.cursor() as cur:
            for table, ident in reversed(made):
                cur.execute(f"DELETE FROM {table} WHERE id::text = %s", (ident,))
        conn.close()


def _source(pg) -> str:
    conn, made = pg
    sid = str(uuid.uuid4())
    with conn.cursor() as cur:
        # `sources` is UNIQUE (user_id, url), so the url has to vary — two
        # mailboxes for one user is the whole point of the per-source cases.
        cur.execute(
            "INSERT INTO sources (id, user_id, name, source_type, url) "
            "VALUES (%s, %s, 'test', 'imap', %s)",
            (sid, USER, f"imap://test/{sid}"),
        )
    made.append(("sources", sid))
    return sid


def _rule(pg, source_id, *, match_type="sender", match_value="billing@utility.example") -> str:
    conn, made = pg
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_attachment_rule (user_id, source_id, match_type, match_value) "
            "VALUES (%s, %s, %s, %s) RETURNING id::text",
            (USER, source_id, match_type, match_value),
        )
        rid = cur.fetchone()[0]
    made.append(("email_attachment_rule", rid))
    return rid


def _filing(pg, source_id, rule_id, *, signal_id="s1", digest="hash-a") -> int:
    conn, _ = pg
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_attachment_ingest_log "
            "(user_id, source_id, signal_id, part_index, filename, content_sha256, "
            " logical_path, filed_by_rule_id) "
            "VALUES (%s, %s, %s, 0, 'f.pdf', %s, 'p', %s) RETURNING id",
            (USER, source_id, signal_id, digest, rule_id),
        )
        return cur.fetchone()[0]


# --- the FK that exists to prevent an orphaned document ----------------------


def test_deleting_a_rule_keeps_the_documents_it_filed(pg):
    """**The finding this column's ON DELETE SET NULL exists to avoid.**

    Under CASCADE the ledger row would go while the Qdrant chunks stayed: the
    document remains searchable and undo, which works off the ledger, can no
    longer remove it. That is precisely the orphan hole LUM-702 closed.
    """
    conn, _ = pg
    src = _source(pg)
    rule_id = _rule(pg, src)
    log_id = _filing(pg, src, rule_id)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM email_attachment_rule WHERE id = %s::uuid", (rule_id,))
        cur.execute(
            "SELECT filed_by_rule_id FROM email_attachment_ingest_log WHERE id = %s",
            (log_id,),
        )
        row = cur.fetchone()

    assert row is not None, "the filing must survive its rule — undo depends on it"
    assert row[0] is None, "attribution is dropped, the filing is not"


def test_a_hand_picked_filing_records_no_rule(pg):
    """NULL is the ordinary case: the user picked it. The review surface uses
    the difference to say which rule acted and offer to stop it."""
    conn, _ = pg
    src = _source(pg)
    log_id = _filing(pg, src, None)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT filed_by_rule_id FROM email_attachment_ingest_log WHERE id = %s",
            (log_id,),
        )
        assert cur.fetchone()[0] is None


def test_a_rule_dies_with_its_mailbox(pg):
    """``source_id`` CASCADEs, unlike ``filed_by_rule_id``. A rule outliving its
    mailbox would reference a source_id that no longer resolves."""
    conn, _ = pg
    src = _source(pg)
    rule_id = _rule(pg, src)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM sources WHERE id = %s::uuid", (src,))
        cur.execute("SELECT 1 FROM email_attachment_rule WHERE id = %s::uuid", (rule_id,))
        assert cur.fetchone() is None


# --- constraints Postgres actually enforces ----------------------------------


def test_the_same_rule_cannot_be_created_twice(pg):
    """What the route's 409 rests on. Enforced by the database, so two
    concurrent creates cannot both win the application-level check."""
    src = _source(pg)
    _rule(pg, src)

    with pytest.raises(psycopg2.errors.UniqueViolation):
        _rule(pg, src)


def test_the_same_sender_in_two_mailboxes_is_allowed(pg):
    """Rules are per-source: the same sender writing to a second mailbox is not
    covered by the first mailbox's decision."""
    src_a, src_b = _source(pg), _source(pg)
    _rule(pg, src_a)

    _rule(pg, src_b)  # must not raise


def test_a_sender_and_a_category_rule_coexist(pg):
    """Both may match; precedence is resolved in the service (sender wins), not
    by refusing to store them."""
    src = _source(pg)
    _rule(pg, src, match_type="sender", match_value="billing@utility.example")

    _rule(pg, src, match_type="category", match_value="bills")  # must not raise


def test_an_unknown_match_type_is_refused_by_the_database(pg):
    src = _source(pg)

    with pytest.raises(psycopg2.errors.CheckViolation):
        _rule(pg, src, match_type="subject", match_value="invoice")


def test_an_unknown_action_is_refused_by_the_database(pg):
    """v1 files, and nothing else. A future ``'ignore'`` must widen the CHECK
    deliberately rather than arriving as an unvalidated string."""
    conn, _ = pg
    src = _source(pg)

    with conn.cursor() as cur, pytest.raises(psycopg2.errors.CheckViolation):
        cur.execute(
            "INSERT INTO email_attachment_rule "
            "(user_id, source_id, match_type, match_value, action) "
            "VALUES (%s, %s, 'sender', 'a@b.example', 'ignore')",
            (USER, src),
        )


# --- the declined verdict ----------------------------------------------------


def test_a_declined_part_cannot_be_judged_twice(pg):
    """``uq_..._part`` is FULL (067), so a decline is terminal in the database
    and the sweep can never re-offer what the user said no to."""
    conn, _ = pg
    src = _source(pg)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_attachment_ingest_log "
            "(user_id, source_id, signal_id, part_index, filename, content_sha256, "
            " logical_path, skipped_reason) "
            "VALUES (%s, %s, 's-decl', 0, 'f.pdf', 'h1', 'p', 'declined')",
            (USER, src),
        )

        with pytest.raises(psycopg2.errors.UniqueViolation):
            cur.execute(
                "INSERT INTO email_attachment_ingest_log "
                "(user_id, source_id, signal_id, part_index, filename, content_sha256, "
                " logical_path) "
                "VALUES (%s, %s, 's-decl', 0, 'f.pdf', 'h2', 'p')",
                (USER, src),
            )


def test_a_declined_row_does_not_occupy_the_content_slot(pg):
    """``uq_..._active`` is partial on ``skipped_reason IS NULL``: declining one
    copy must not stop the user filing the same bytes from another message."""
    conn, _ = pg
    src = _source(pg)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO email_attachment_ingest_log "
            "(user_id, source_id, signal_id, part_index, filename, content_sha256, "
            " logical_path, skipped_reason) "
            "VALUES (%s, %s, 's-a', 0, 'f.pdf', 'same-bytes', 'p', 'declined')",
            (USER, src),
        )

        cur.execute(  # must not raise
            "INSERT INTO email_attachment_ingest_log "
            "(user_id, source_id, signal_id, part_index, filename, content_sha256, "
            " logical_path) "
            "VALUES (%s, %s, 's-b', 0, 'f.pdf', 'same-bytes', 'p')",
            (USER, src),
        )


def test_the_rules_lookup_query_executes_against_the_provisioned_schema(pg):
    """The service's own SELECT, run for real.

    Three of this feature family's joins cross a UUID/TEXT boundary and none
    executes without a cast; this query casts ``id`` and ``source_id`` to text on
    the way out. Executing it IS the assertion.
    """
    conn, _ = pg
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id::text AS id, source_id::text AS source_id, match_type, "
            "       match_value, action, created_at "
            "FROM email_attachment_rule WHERE user_id = %s",
            (USER,),
        )
        cur.fetchall()
