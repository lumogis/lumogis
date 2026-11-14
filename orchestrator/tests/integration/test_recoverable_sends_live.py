# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Failed-send recovery against REAL Postgres (LUM-793).

**Why this lane and not the unit one.** Three of this chunk's mutations are
predicate mutations, and the house fake cannot detect a predicate that is simply
gone: `FakeProposalQueueStore` pattern-matches SQL *strings* and re-implements
the filtering in Python, so deleting `AND user_id = %s` from a WHERE clause
changes what the fake matches on and the assertion still passes. LUM-762 proved
that empirically after a mutation was written down as "killed" without being
run. So:

* **Mutation A** — drop `user_id` from `list_recoverable_sends` → must fail
  `test_recoverable_sends_denies_cross_user` here.
* **Mutation B** — remove the 7-day grace predicate → must fail
  `test_dead_attachments_survive_grace_then_sweep`.
* **Mutation C** — extend the grace to non-terminal states → must fail
  `test_live_proposal_attachments_still_never_swept`, the LUM-762 invariant.

A skip here is a **blocker**, not a pass (LUM-718): `require_postgres` turns it
into a failure whenever CI promises a database.
"""

from __future__ import annotations

import os
import uuid

import pytest
from tests.integration._pgguard import require_postgres

import config
from services import action_proposals
from services import email_attachment_staging as staging

psycopg2 = pytest.importorskip("psycopg2")

# The real shapes: migration 022 (action_proposals), 066 (staging) + 072's column.
_DDL = """
CREATE TABLE action_proposals (
    id            BIGSERIAL PRIMARY KEY,
    user_id       TEXT        NOT NULL DEFAULT 'default',
    action_name   TEXT        NOT NULL,
    payload       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    annotation    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status        TEXT        NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','approved','claimed','executing',
                                    'done','dead','rejected','archived')),
    attempt       INTEGER     NOT NULL DEFAULT 0,
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ NULL
);

CREATE TABLE email_attachment_staged (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      TEXT        NOT NULL DEFAULT 'default',
    filename     TEXT        NOT NULL,
    mime_type    TEXT        NOT NULL DEFAULT '',
    size_bytes   BIGINT      NOT NULL DEFAULT 0,
    storage_key  TEXT        NOT NULL,
    proposal_id  INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    consumed_at  TIMESTAMPTZ
);

CREATE TABLE email_sent_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      TEXT NOT NULL DEFAULT 'default',
    source_id    TEXT NOT NULL,
    to_addrs     TEXT[] NOT NULL DEFAULT '{}',
    cc_addrs     TEXT[] NOT NULL DEFAULT '{}',
    bcc_addrs    TEXT[] NOT NULL DEFAULT '{}',
    subject      TEXT NOT NULL DEFAULT '',
    message_id   TEXT NOT NULL DEFAULT '',
    in_reply_to  TEXT,
    action       TEXT NOT NULL DEFAULT 'compose',
    proposal_id  BIGINT NULL,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _conn_kwargs() -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": os.environ.get("POSTGRES_DB", "lumogis"),
        "connect_timeout": 3,
    }


class _SchemaStore:
    """A real psycopg2 store pinned to one throwaway schema.

    Mirrors `PostgresStore`'s autocommit posture (`postgres_store.py:21`) — the
    production store runs autocommit, and a transactional test store would hide
    exactly the visibility questions this lane exists to answer.
    """

    def __init__(self, conn):
        self.conn = conn

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def _run(self, sql, params):
        cur = self.conn.cursor()
        cur.execute(sql, params or ())
        return cur

    def execute(self, sql, params=None) -> None:
        self._run(sql, params).close()

    def fetch_one(self, sql, params=None):
        cur = self._run(sql, params)
        try:
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        finally:
            cur.close()

    def fetch_all(self, sql, params=None):
        cur = self._run(sql, params)
        try:
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            cur.close()


@pytest.fixture
def store():
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001 — diagnostic skip/fail
        require_postgres(exc)
    conn.autocommit = True
    name = f"test_recoverable_{uuid.uuid4().hex[:12]}"
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{name}"')
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute(_DDL)
    s = _SchemaStore(conn)
    config._instances["metadata_store"] = s
    try:
        yield s
    finally:
        config._instances.pop("metadata_store", None)
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{name}" CASCADE')
        conn.close()


def _seed_dead(store, *, user_id="alice", action="email_compose", ago_days=0, subject="Dinner"):
    row = store.fetch_one(
        "INSERT INTO action_proposals "
        "(user_id, action_name, payload, status, error, finished_at) "
        "VALUES (%s, %s, %s::jsonb, 'dead', %s, NOW() - (%s * INTERVAL '1 day')) "
        "RETURNING id",
        (
            user_id,
            action,
            '{"source_id": "src-1", "to": ["x@example.com"], '
            f'"subject": "{subject}", "body": "hi"}}',
            "smtp_send_unavailable: reset",
            ago_days,
        ),
    )
    return int(row["id"])


def _stage(store, *, proposal_id, user_id="alice"):
    row = store.fetch_one(
        "INSERT INTO email_attachment_staged "
        "(user_id, filename, mime_type, size_bytes, storage_key, proposal_id) "
        "VALUES (%s, 'menu.pdf', 'application/pdf', 10, %s, %s) RETURNING id",
        (user_id, f"{uuid.uuid4().hex}/blob", proposal_id),
    )
    return str(row["id"])


# --------------------------------------------------------------------------- #
# MUTATION A — drop `user_id` from `list_recoverable_sends`.
# --------------------------------------------------------------------------- #


def test_recoverable_sends_denies_cross_user(store):
    """Alice's failed send is invisible to Bob.

    The security core of the chunk: this listing returns MESSAGE BODIES, so a
    dropped predicate here leaks private correspondence across household
    members. Proven against real SQL because the fake cannot prove it.
    """
    alice = _seed_dead(store, user_id="alice")
    bob = _seed_dead(store, user_id="bob")

    assert [r["proposal_id"] for r in action_proposals.list_recoverable_sends("alice")] == [alice]
    assert [r["proposal_id"] for r in action_proposals.list_recoverable_sends("bob")] == [bob]
    assert action_proposals.list_recoverable_sends("carol") == []


def test_outcome_resolver_never_reads_another_users_sent_log(store):
    """The sent log is looked up by proposal id — which is a guessable integer,
    so the id alone must never be sufficient."""
    from services import send_outcome

    pid = _seed_dead(store, user_id="alice")
    store.execute(
        "INSERT INTO email_sent_log (user_id, source_id, to_addrs, subject, proposal_id) "
        "VALUES ('alice', 'src-1', ARRAY['x@example.com'], 'Dinner', %s)",
        (pid,),
    )
    (row,) = action_proposals.list_recoverable_sends("alice")

    assert send_outcome.resolve_send_outcome(row, user_id="alice").state == "sent"
    # Same row, same id, wrong user: no evidence may be returned.
    assert send_outcome.resolve_send_outcome(row, user_id="bob").state == "not_sent"


def test_dismiss_denies_cross_user(store):
    pid = _seed_dead(store, user_id="alice")
    with pytest.raises(Exception):
        action_proposals.dismiss_recoverable_send(pid, user_id="bob")
    row = store.fetch_one("SELECT status FROM action_proposals WHERE id = %s", (pid,))
    assert row["status"] == "dead"


def test_recoverable_sends_window_and_action_set_hold_in_sql(store):
    """The window and action-set predicates, executed by Postgres rather than
    re-implemented by a fake."""
    recent = _seed_dead(store, ago_days=29)
    _seed_dead(store, ago_days=31)
    _seed_dead(store, action="email_unsubscribe")
    store.execute(
        "INSERT INTO action_proposals (user_id, action_name, status, finished_at) "
        "VALUES ('alice', 'email_compose', 'done', NOW())",
        (),
    )
    assert [r["proposal_id"] for r in action_proposals.list_recoverable_sends("alice")] == [recent]


def test_dismiss_moves_dead_to_archived_in_sql(store):
    pid = _seed_dead(store)
    out = action_proposals.dismiss_recoverable_send(pid, user_id="alice")
    assert out["status"] == "archived"
    # `archived` is in migration 022's CHECK — this transition is legal, not a
    # constraint violation waiting to happen in production.
    row = store.fetch_one("SELECT status FROM action_proposals WHERE id = %s", (pid,))
    assert row["status"] == "archived"
    assert action_proposals.list_recoverable_sends("alice") == []


# --------------------------------------------------------------------------- #
# MUTATIONS B and C — the attachment grace period.
# --------------------------------------------------------------------------- #


@pytest.fixture
def no_disk(monkeypatch, tmp_path):
    """The sweep deletes files as well as rows; point it at a temp root so the
    row-level assertions below are about the predicate, not the filesystem."""
    monkeypatch.setenv("EMAIL_ATTACHMENT_STAGING_ROOT", str(tmp_path))
    return tmp_path


def _count_staged(store) -> int:
    return int(store.fetch_one("SELECT COUNT(*) AS n FROM email_attachment_staged", ())["n"])


def test_dead_attachments_survive_grace_then_sweep(store, no_disk, monkeypatch):
    """Inside 7 days: kept. Outside: swept.

    MUTATION B (remove the grace predicate) fails the first half — a failed send
    listed for 30 days would lose its files on the next hourly tick, about an
    hour after failing, making "Save to Drafts with your attachments" a lie.
    """
    monkeypatch.setenv("EMAIL_ATTACHMENT_DEAD_GRACE_HOURS", "168")

    fresh = _seed_dead(store, ago_days=1)
    _stage(store, proposal_id=fresh)
    staging.sweep()
    assert _count_staged(store) == 1, "a 1-day-old failed send must keep its files"

    old = _seed_dead(store, ago_days=8)
    _stage(store, proposal_id=old)
    staging.sweep()
    # The 8-day-old one goes; the 1-day-old one stays. The grace is bounded, and
    # LUM-762's leak does not silently return.
    remaining = store.fetch_all("SELECT proposal_id FROM email_attachment_staged", ())
    assert [r["proposal_id"] for r in remaining] == [fresh]


def test_live_proposal_attachments_still_never_swept(store, no_disk, monkeypatch):
    """MUTATION C — extend the grace to non-terminal states.

    The LUM-762 invariant, unchanged: proposals never expire, so a file whose
    proposal is pending/approved/claimed/executing is NEVER swept however old.
    The grace period must not become a wall-clock TTL on live rows by the back
    door — that is the Friday-staged, Monday-approved bug LUM-695 called out.
    """
    monkeypatch.setenv("EMAIL_ATTACHMENT_DEAD_GRACE_HOURS", "168")
    monkeypatch.setenv("EMAIL_ATTACHMENT_STAGING_TTL_HOURS", "24")

    for state in ("pending", "approved", "claimed", "executing"):
        row = store.fetch_one(
            "INSERT INTO action_proposals (user_id, action_name, status, created_at) "
            "VALUES ('alice', 'email_compose', %s, NOW() - INTERVAL '400 days') RETURNING id",
            (state,),
        )
        pid = int(row["id"])
        store.execute(
            "INSERT INTO email_attachment_staged "
            "(user_id, filename, mime_type, size_bytes, storage_key, proposal_id, created_at) "
            "VALUES ('alice','f.pdf','application/pdf',1,%s,%s, NOW() - INTERVAL '400 days')",
            (f"{uuid.uuid4().hex}/blob", pid),
        )

    staging.sweep()
    assert _count_staged(store) == 4, "a live proposal's attachments are never swept on age"


def test_unreferenced_files_still_age_out_on_the_ttl(store, no_disk, monkeypatch):
    """The leak LUM-762 closed stays closed: the grace applies to terminal rows
    only, never to files with no proposal at all."""
    monkeypatch.setenv("EMAIL_ATTACHMENT_STAGING_TTL_HOURS", "24")
    store.execute(
        "INSERT INTO email_attachment_staged "
        "(user_id, filename, mime_type, size_bytes, storage_key, created_at) "
        "VALUES ('alice','f.pdf','application/pdf',1,%s, NOW() - INTERVAL '48 hours')",
        (f"{uuid.uuid4().hex}/blob",),
    )
    staging.sweep()
    assert _count_staged(store) == 0


def test_terminal_row_with_null_finished_at_is_swept_immediately(store, no_disk):
    """A NULL `finished_at` must not mean "keep forever" — the COALESCE defaults
    to the epoch, deliberately, so the grace cannot become an accidental pin."""
    row = store.fetch_one(
        "INSERT INTO action_proposals (user_id, action_name, status) "
        "VALUES ('alice','email_compose','dead') RETURNING id",
        (),
    )
    _stage(store, proposal_id=int(row["id"]))
    staging.sweep()
    assert _count_staged(store) == 0


def test_available_attachments_reads_only_owned_unconsumed_rows(store):
    pid = _seed_dead(store)
    kept = _stage(store, proposal_id=pid)
    consumed = _stage(store, proposal_id=pid)
    store.execute(
        "UPDATE email_attachment_staged SET consumed_at = NOW() WHERE id = %s", (consumed,)
    )
    _stage(store, proposal_id=pid, user_id="bob")

    files = staging.available_attachments(pid, user_id="alice")
    assert [f.id for f in files] == [kept]
    assert staging.available_attachments(pid, user_id="bob") != files


# --------------------------------------------------------------------------- #
# Migration shape
# --------------------------------------------------------------------------- #


def test_sent_log_proposal_id_migration_is_idempotent(store):
    """Applying 072 twice against a live schema is a no-op the second time."""
    import pathlib

    sql = (
        pathlib.Path(__file__).resolve().parents[3]
        / "postgres"
        / "migrations"
        / "072-email-sent-log-proposal-id.sql"
    ).read_text()

    # The fixture's DDL already carries the column, which is the realistic
    # "already migrated" case — so this proves re-runnability, not first apply.
    for _ in range(2):
        with store.conn.cursor() as cur:
            cur.execute(sql)

    row = store.fetch_one(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = 'email_sent_log' AND column_name = 'proposal_id' "
        "AND table_schema = current_schema()",
        (),
    )
    assert row is not None
    assert row["data_type"] == "bigint"
    assert row["is_nullable"] == "YES"

    idx = store.fetch_one(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema() "
        "AND indexname = 'idx_email_sent_log_proposal'",
        (),
    )
    assert idx is not None
    assert "proposal_id IS NOT NULL" in idx["indexdef"]


def test_sent_log_write_populates_proposal_id(store):
    """The column is not decoration: prove a real `_record_sent_log` call fills it.

    This is step 1 of the plan's implementation sequence — "prove the column
    populates on a real send" — reduced to the part that does not need an SMTP
    server: the INSERT itself, with its full 10-column list bound.
    """
    from actions.handlers import smtp_send

    pid = _seed_dead(store)
    smtp_send._record_sent_log(
        user_id="alice",
        source_id="src-1",
        to_addrs=["X@Example.com"],
        subject="Dinner",
        message_id="<m@1>",
        in_reply_to=None,
        action="compose",
        cc_addrs=["c@example.com"],
        bcc_addrs=[],
        proposal_id=pid,
    )
    row = store.fetch_one(
        "SELECT proposal_id, to_addrs, cc_addrs FROM email_sent_log WHERE proposal_id = %s",
        (pid,),
    )
    assert row is not None
    assert int(row["proposal_id"]) == pid
    # Normalised, and to/cc kept distinct (migration 064).
    assert row["to_addrs"] == ["x@example.com"]
    assert row["cc_addrs"] == ["c@example.com"]


def test_partial_send_resolves_end_to_end_against_real_rows(store):
    """The whole chain on real SQL: a partial writes a log row for the accepted
    recipients, the proposal dies naming the refused, and the resolver reports
    `partially_sent` — not `sent`, which would lock recovery shut."""
    from services import send_outcome

    pid = _seed_dead(store)
    store.execute(
        "UPDATE action_proposals SET payload = %s::jsonb, error = %s WHERE id = %s",
        (
            '{"source_id": "src-1", "to": ["a@example.com", "b@example.com"], '
            '"subject": "Dinner", "body": "hi"}',
            f"{send_outcome.RECIPIENTS_REFUSED_PREFIX}b@example.com",
            pid,
        ),
    )
    store.execute(
        "INSERT INTO email_sent_log (user_id, source_id, to_addrs, subject, proposal_id) "
        "VALUES ('alice','src-1', ARRAY['a@example.com'], 'Dinner', %s)",
        (pid,),
    )

    (row,) = action_proposals.list_recoverable_sends("alice")
    outcome = send_outcome.resolve_send_outcome(row, user_id="alice")
    assert outcome.state == "partially_sent"
    assert outcome.refused == ["b@example.com"]
    assert outcome.is_sent is False


def test_heuristic_resolves_a_pre_migration_row_in_sql(store):
    """A sent-log row with NULL `proposal_id` — everything written before 072 —
    resolves through the `finished_at`-anchored fallback."""
    from services import send_outcome

    pid = _seed_dead(store, ago_days=2)
    store.execute(
        "INSERT INTO email_sent_log (user_id, source_id, to_addrs, subject, sent_at) "
        "SELECT 'alice', 'src-1', ARRAY['x@example.com'], 'Dinner', finished_at "
        "FROM action_proposals WHERE id = %s",
        (pid,),
    )
    (row,) = action_proposals.list_recoverable_sends("alice")
    assert row["finished_at"] is not None

    outcome = send_outcome.resolve_send_outcome(row, user_id="alice")
    assert outcome.state == "sent"

    # And the window is real: move the log row outside it and the match is gone.
    store.execute(
        "UPDATE email_sent_log SET sent_at = sent_at - (%s * INTERVAL '1 minute')",
        (send_outcome.HEURISTIC_WINDOW_MINUTES + 5,),
    )
    assert send_outcome.resolve_send_outcome(row, user_id="alice").state == "not_sent"


def test_heuristic_does_not_cross_users(store):
    """The fallback matches on subject + mailbox + time, all of which two
    household members can share. `user_id` is what keeps it honest."""
    from services import send_outcome

    pid = _seed_dead(store, user_id="alice", ago_days=1)
    store.execute(
        "INSERT INTO email_sent_log (user_id, source_id, to_addrs, subject, sent_at) "
        "SELECT 'bob', 'src-1', ARRAY['x@example.com'], 'Dinner', finished_at "
        "FROM action_proposals WHERE id = %s",
        (pid,),
    )
    (row,) = action_proposals.list_recoverable_sends("alice")
    assert send_outcome.resolve_send_outcome(row, user_id="alice").state == "not_sent"
