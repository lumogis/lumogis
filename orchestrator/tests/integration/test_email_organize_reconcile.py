# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-645 — email_organize reconcile/undo against REAL Postgres (LUM-678).

The house fake store string-matches SQL and cannot execute the per-op category-scoped
LEFT JOIN, the partial-unique idempotency index, or the `organize_since` watermark, so
those correctness-critical mechanics are proven here against the migration-060 schema
with a recording adapter (no real IMAP). Skips when no Postgres is reachable.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from tests.integration._pgguard import require_postgres

import config

psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("psycopg2.extras")

from services import email_organize as eo  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MIGRATION_060 = _REPO_ROOT / "postgres" / "migrations" / "060-email-organize.sql"
# 061 adds email_organize_log.cleanup_job_id, which the production INSERT writes.
# Applying only 060 gave a schema production never has, and the resulting
# UndefinedColumn was swallowed by a bare except and logged as "duplicate_row".
_MIGRATION_061 = _REPO_ROOT / "postgres" / "migrations" / "061-email-cleanup.sql"
# 071 adds sources.organize_undo_ticks, which `undo_tick` SELECTs. Same class of gap
# as 061 above: a schema production never has, surfacing as UndefinedColumn.
_MIGRATION_071 = _REPO_ROOT / "postgres" / "migrations" / "071-organize-undo-bound.sql"

_SOURCES_DDL = """
CREATE TABLE sources (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       TEXT NOT NULL DEFAULT 'default',
    name          TEXT NOT NULL DEFAULT 'm',
    source_type   TEXT NOT NULL DEFAULT 'imap',
    url           TEXT NOT NULL DEFAULT ''
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

_SID = "11111111-1111-1111-1111-111111111111"


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
    """Minimal MetadataStore over a schema-scoped connection for email_organize."""

    def __init__(self, conn):
        self._conn = conn

    def fetch_one(self, query, params=None):
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def fetch_all(self, query, params=None):
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(r) for r in cur.fetchall()]

    def execute(self, query, params=None):
        with self._conn.cursor() as cur:
            cur.execute(query, params)


class _RecordingAdapter:
    def __init__(self):
        self.calls = []
        self.reversed = []
        self.sessions = 0

    def organize_session(self):
        self.sessions += 1
        return object()

    def close_session(self, client):
        pass

    def apply_organize(self, *, uidvalidity, uid, op, label, slug="", mailbox="INBOX", client=None):
        self.calls.append((op, slug))
        return {
            "op": op,
            "mailbox": mailbox,
            "message_id": f"<mid-{uid}>",
            "target": f"Lumogis/{label}",
            "label_kind": "gmail" if op == "label" else None,
        }

    def reverse_writeback(self, *, op, mailbox, message_id, target=None, flag=None, client=None):
        self.reversed.append((op, target))


@pytest.fixture
def env(monkeypatch):
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001 — diagnostic skip
        require_postgres(exc)
    conn.autocommit = True
    name = f"test_email_organize_{uuid.uuid4().hex[:12]}"
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{name}"')
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute(_SOURCES_DDL)
        cur.execute(_SIGNALS_DDL)
        cur.execute(_MIGRATION_060.read_text(encoding="utf-8"))
        cur.execute(_MIGRATION_061.read_text(encoding="utf-8"))
        cur.execute(_MIGRATION_071.read_text(encoding="utf-8"))
    store = _SchemaStore(conn)
    adapter = _RecordingAdapter()
    eo._opt_in_cache.clear()
    monkeypatch.setattr(config, "get_metadata_store", lambda: store)
    monkeypatch.setattr(eo, "_adapter_for", lambda sid, uid: adapter)
    monkeypatch.setattr(
        eo,
        "_label_for",
        lambda uid, slug: {
            "bills_finance": "Bills & finance",
            "promotional_bulk": "Promotional / bulk",
        }.get(slug),
    )
    try:
        yield store, conn, adapter, name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{name}" CASCADE')
        conn.close()


def _seed(conn, name, *, folder_cats, since_epoch=True):
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute(
            "INSERT INTO sources (id, user_id, organize_label_enabled, organize_folder_enabled, "
            "organize_folder_categories, organize_since) "
            "VALUES (%s, 'u', TRUE, TRUE, %s::jsonb, "
            "  CASE WHEN %s THEN 'epoch'::timestamptz ELSE NOW() END)",
            (_SID, json.dumps(folder_cats), since_epoch),
        )
        for uid, cat in ((1, "bills_finance"), (2, "promotional_bulk")):
            url = f"imap://{_SID}/100/{uid}"
            cur.execute(
                # created_at is set explicitly to an HOUR AGO so these really are
                # "pre-existing" signals, as the go-forward-only test's comment
                # claims. The fixture used to let created_at default to NOW() while
                # inserting the source (organize_since = NOW()) FIRST, so the
                # signals landed microseconds AFTER the watermark and were
                # correctly in scope — the opposite of what that test asserts.
                "INSERT INTO signals (signal_id, user_id, source_id, url, category, created_at) "
                "VALUES (%s, 'u', %s, %s, %s, NOW() - interval '1 hour')",
                # 4 placeholders (user_id is the literal 'u'), so 4 params — this
                # carried a duplicated `url`, which psycopg2 rejects as
                # "not all arguments converted during string formatting".
                (str(uuid.uuid4()), _SID, url, cat),
            )


def _active(conn, name) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute(
            "SELECT signal_url, op FROM email_organize_log WHERE reversed_at IS NULL "
            "ORDER BY signal_url, op"
        )
        return cur.fetchall()


def test_reconcile_applies_per_op_and_is_idempotent(env):
    store, conn, adapter, name = env
    _seed(conn, name, folder_cats=["promotional_bulk"])
    out = eo.reconcile_tick(source_id=_SID, user_id="u")
    # Both labeled; only promotional_bulk moved (category-scoped move query).
    assert out["labeled"] == 2 and out["moved"] == 1
    rows = _active(conn, name)
    assert (f"imap://{_SID}/100/1", "label") in rows
    assert (f"imap://{_SID}/100/2", "label") in rows
    assert (f"imap://{_SID}/100/2", "move") in rows
    assert (f"imap://{_SID}/100/1", "move") not in rows  # bills not selected
    # Idempotent — a second tick applies nothing new (ledger LEFT JOIN excludes done).
    out2 = eo.reconcile_tick(source_id=_SID, user_id="u")
    assert out2["labeled"] == 0 and out2["moved"] == 0


def test_undo_reverses_and_clears_scope(env):
    store, conn, adapter, name = env
    _seed(conn, name, folder_cats=["promotional_bulk"])
    eo.reconcile_tick(source_id=_SID, user_id="u")
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute("UPDATE sources SET organize_undo_scope = 'all' WHERE id::text = %s", (_SID,))
    eo._opt_in_cache.clear()
    out = eo.undo_tick(source_id=_SID, user_id="u")
    assert out["reversed"] == 3 and out["remaining"] == 0
    assert _active(conn, name) == []
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute("SELECT organize_undo_scope FROM sources WHERE id::text = %s", (_SID,))
        assert cur.fetchone()[0] is None  # scope cleared when nothing remains


def test_organize_since_scopes_go_forward_only(env):
    store, conn, adapter, name = env
    # since = NOW() (not epoch): pre-existing signals (created just before) are out of scope.
    _seed(conn, name, folder_cats=["promotional_bulk"], since_epoch=False)
    eo._opt_in_cache.clear()
    out = eo.reconcile_tick(source_id=_SID, user_id="u")
    # created_at < organize_since → nothing applied (go-forward-only default).
    assert out["labeled"] == 0 and out["moved"] == 0
