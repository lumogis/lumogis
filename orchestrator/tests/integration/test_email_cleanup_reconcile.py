# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-646 — email_cleanup against REAL Postgres.

The house fake string-matches SQL and cannot execute the pending-move LEFT JOIN, the
cleanup_job_id attribution filter, the partial-unique idempotency index, or the
per-source single-active-job unique index. Those correctness-critical mechanics are proven
here against the migration 060 + 061 schema with a recording adapter (no real IMAP).

Crucially loads migration 060 (creates email_organize_log) THEN 061 (ALTERs it to add
cleanup_job_id) — 061 depends on 060. Skips when no Postgres is reachable.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from tests.integration._pgguard import require_postgres

import config

psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("psycopg2.extras")

from services import email_cleanup as ec  # noqa: E402
from services import email_organize as eo  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MIGRATION_060 = _REPO_ROOT / "postgres" / "migrations" / "060-email-organize.sql"
_MIGRATION_061 = _REPO_ROOT / "postgres" / "migrations" / "061-email-cleanup.sql"

_SID = "11111111-1111-1111-1111-111111111111"

_SOURCES_DDL = """
CREATE TABLE sources (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       TEXT NOT NULL DEFAULT 'default',
    url           TEXT NOT NULL DEFAULT '',
    organize_label_enabled BOOLEAN NOT NULL DEFAULT FALSE
);
"""
_SIGNALS_DDL = """
CREATE TABLE signals (
    signal_id   UUID PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'default',
    source_id   TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
    def __init__(self, conn):
        self._conn = conn

    def fetch_one(self, query, params=None):
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone() if cur.description else None
            return dict(row) if row else None

    def fetch_all(self, query, params=None):
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def execute(self, query, params=None):
        with self._conn.cursor() as cur:
            cur.execute(query, params)


class _RecordingAdapter:
    def __init__(self, *, junk="Junk", trash="Trash", junk_uids=(1, 2)):
        self.junk, self.trash, self.junk_uids = junk, trash, list(junk_uids)
        self.applied, self.reversed = [], []

    def organize_session(self):
        return object()

    def close_session(self, client):
        pass

    def inbox_seed(self):
        return 100, 0

    def _role_folder(self, client, role):
        return {"junk": self.junk, "trash": self.trash}.get(role)

    def folder_uidnext(self, client, folder):
        return 100, 10

    def enumerate_role_folder(self, client, *, role, after_uid, max_uid, limit=200):
        if self._role_folder(client, role) is None:
            return None, 0, [], after_uid, True
        if not self.junk_uids:
            return self.junk, 100, [], after_uid, True
        return self.junk, 100, list(self.junk_uids), max(self.junk_uids), False

    def apply_organize(self, *, uidvalidity, uid, op, label, slug="", mailbox="INBOX", client=None):
        self.applied.append((op, uid, mailbox))
        return {
            "op": "move" if op == "trash" else op,
            "mailbox": mailbox,
            "message_id": f"<mid-{mailbox}-{uid}>",
            "target": self.trash if op == "trash" else f"Lumogis/{label}",
            "label_kind": None,
        }

    def reverse_writeback(self, *, op, mailbox, message_id, target=None, flag=None, client=None):
        self.reversed.append((op, mailbox, message_id))


class _Src:
    id = _SID
    user_id = "u"
    url = "imap://imap.gmail.com:993/p"


@pytest.fixture
def env(monkeypatch):
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001
        require_postgres(exc)
    conn.autocommit = True
    name = f"test_email_cleanup_{uuid.uuid4().hex[:12]}"
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{name}"')
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute(_SOURCES_DDL)
        cur.execute(_SIGNALS_DDL)
        cur.execute(_MIGRATION_060.read_text(encoding="utf-8"))  # creates email_organize_log
        cur.execute(_MIGRATION_061.read_text(encoding="utf-8"))  # ALTERs it + adds cleanup tables
        cur.execute(
            "INSERT INTO sources (id, user_id, url) VALUES (%s, 'u', %s)",
            (_SID, "imap://imap.gmail.com:993/p"),
        )
    store = _SchemaStore(conn)
    adapter = _RecordingAdapter()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(eo, "_adapter_for", lambda sid, uid: adapter)
    monkeypatch.setattr(
        eo,
        "_label_for",
        lambda uid, slug: {
            "promotional_bulk": "Promotional / bulk",
            "bills_finance": "Bills & finance",
        }.get(slug),
    )
    try:
        yield store, conn, adapter, name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{name}" CASCADE')
        conn.close()


def _seed_ready_window_job(conn, name, *, items):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute(
            "INSERT INTO email_cleanup_job (user_id, source_id, kind, state, "
            "backfill_max_uid) VALUES ('u', %s, 'window', 'ready', 100) RETURNING id",
            (_SID,),
        )
        jid = cur.fetchone()["id"]
        for url, cat in items:
            cur.execute(
                "INSERT INTO email_cleanup_item (job_id, signal_url, category) VALUES (%s,%s,%s)",
                (jid, url, cat),
            )
    return str(jid)


def _active_rows(conn, name, *, cleanup_job_id=None):
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{name}"')
        if cleanup_job_id is None:
            cur.execute(
                "SELECT signal_url, op FROM email_organize_log "
                "WHERE reversed_at IS NULL AND cleanup_job_id IS NULL ORDER BY signal_url"
            )
        else:
            cur.execute(
                "SELECT signal_url, op FROM email_organize_log "
                "WHERE reversed_at IS NULL AND cleanup_job_id = %s ORDER BY signal_url",
                (cleanup_job_id,),
            )
        return cur.fetchall()


def test_execute_moves_selected_and_undo_is_job_scoped(env):
    store, conn, adapter, name = env
    jid = _seed_ready_window_job(
        conn,
        name,
        items=[
            (f"imap://{_SID}/100/1", "promotional_bulk"),
            (f"imap://{_SID}/100/2", "bills_finance"),
        ],
    )
    # A STANDING organize row (cleanup_job_id NULL) on the SAME signal as an item.
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute(
            "INSERT INTO email_organize_log (user_id, source_id, signal_url, op, reverse_token) "
            "VALUES ('u', %s, %s, 'label', %s::jsonb)",
            (
                _SID,
                f"imap://{_SID}/100/1",
                '{"op":"label","mailbox":"INBOX","message_id":"<standing>","target":"Lumogis/x"}',
            ),
        )
    # Approve move for promotional_bulk only, then run the execute tick.
    ec.approve(jid, "u", _SID, [{"category": "promotional_bulk", "move": True}])
    ec.run_cleanup_tick(_Src())
    # Only promotional_bulk moved; a cleanup_job_id-attributed move row exists.
    cleanup_rows = _active_rows(conn, name, cleanup_job_id=jid)
    assert (f"imap://{_SID}/100/1", "move") in cleanup_rows
    assert all(op != "move" or url == f"imap://{_SID}/100/1" for url, op in cleanup_rows)
    # The standing label row is untouched by the cleanup.
    assert (f"imap://{_SID}/100/1", "label") in _active_rows(conn, name)  # cleanup_job_id IS NULL

    # Undo the job: reverses ONLY the cleanup rows; the standing label survives.
    ec.undo_job(jid, "u", _SID, ["label", "move", "trash"])
    ec.run_cleanup_tick(_Src())
    assert _active_rows(conn, name, cleanup_job_id=jid) == []  # cleanup rows reversed
    assert (f"imap://{_SID}/100/1", "label") in _active_rows(conn, name)  # standing STILL active


def test_spam_job_trashes_junk_and_records_membership(env):
    store, conn, adapter, name = env
    res = ec.start_spam_job(_SID, "u")
    jid = res["job_id"]
    ec.run_cleanup_tick(_Src())  # kind='spam' walk
    # Both \Junk uids trashed, sourced from the junk folder, attributed to the job.
    assert [op for op, _uid, _mb in adapter.applied] == ["trash", "trash"]
    assert all(mb == "Junk" for _op, _uid, mb in adapter.applied)
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute("SELECT DISTINCT category FROM email_cleanup_item WHERE job_id = %s", (jid,))
        assert cur.fetchall() == [("__spam__",)]
        cur.execute("SELECT count(*) FROM email_organize_log WHERE cleanup_job_id = %s", (jid,))
        assert cur.fetchone()[0] == 2


def test_second_job_while_active_is_409(env):
    store, conn, adapter, name = env
    _seed_ready_window_job(conn, name, items=[(f"imap://{_SID}/100/1", "promotional_bulk")])
    # A window job is active (ready) → starting a spam job is rejected (mutual exclusion).
    with pytest.raises(ec.CleanupError) as exc:
        ec.start_spam_job(_SID, "u")
    assert exc.value.code == "cleanup_active"
