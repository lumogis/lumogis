# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-676 — email signals persist to a REAL Postgres `signals` table.

The house fake store string-matches SQL and never exercises the UUID-PRIMARY-KEY
cast, so the bug (email `signal_id=""` → `INSERT … VALUES ('', …)` raises
`invalid input syntax for type uuid`, silently swallowed by `_persist`) was
invisible to the unit suite. This proves against the real init.sql `signals`
schema that a `_build_signal`-produced (deterministic uuid5) id inserts and is
queryable by url, and that the old empty-string value would have failed.

Skips when no Postgres is reachable so a laptop unit run stays green.
"""

from __future__ import annotations

import os
import uuid

import pytest
from adapters.email_imap_source import EmailImapSource
from models.signals import SourceConfig
from tests.integration._pgguard import require_postgres

psycopg2 = pytest.importorskip("psycopg2")

# Real signals-table shape (init.sql:222+) — signal_id is UUID PRIMARY KEY, no default.
_SIGNALS_DDL = """
CREATE TABLE signals (
    signal_id        UUID PRIMARY KEY,
    user_id          TEXT NOT NULL DEFAULT 'default',
    source_id        TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL DEFAULT '',
    url              TEXT NOT NULL DEFAULT '',
    published_at     TIMESTAMPTZ,
    content_summary  TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
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


def _build(sid: str, *, uid: int, uidvalidity: int = 100):
    src = SourceConfig(
        id=sid,
        name="m",
        source_type="imap",
        url="imap://x",
        category="general",
        active=True,
        poll_interval=3600,
        extraction_method="imap_metadata",
        css_selector_override=None,
        last_polled_at=None,
        last_signal_at=None,
    )
    adapter = EmailImapSource.__new__(EmailImapSource)
    adapter.source = src
    return adapter._build_signal(uid=uid, uidvalidity=uidvalidity, headers={"Subject": "hi"})


@pytest.fixture
def schema():
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001 — diagnostic skip
        require_postgres(exc)
    conn.autocommit = True
    name = f"test_email_persist_{uuid.uuid4().hex[:12]}"
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            cur.execute(_SIGNALS_DDL)
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{name}" CASCADE')
        conn.close()


def test_email_signal_persists_and_is_queryable_by_url(schema):
    sig = _build("11111111-1111-1111-1111-111111111111", uid=5)
    with schema.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, url, source_id) VALUES (%s, %s, %s)",
            (sig.signal_id, sig.url, sig.source_id),
        )
        cur.execute("SELECT signal_id FROM signals WHERE url = %s", (sig.url,))
        row = cur.fetchone()
    assert row is not None and str(row[0]) == sig.signal_id


def test_distinct_emails_persist_as_distinct_rows(schema):
    a = _build("11111111-1111-1111-1111-111111111111", uid=5)
    b = _build("11111111-1111-1111-1111-111111111111", uid=6)
    assert a.signal_id != b.signal_id
    with schema.cursor() as cur:
        for s in (a, b):
            cur.execute(
                "INSERT INTO signals (signal_id, url) VALUES (%s, %s)", (s.signal_id, s.url)
            )
        cur.execute("SELECT COUNT(*) FROM signals")
        assert cur.fetchone()[0] == 2  # not collapsed to 1 (the old "" PK collision)


def test_empty_signal_id_would_fail_the_uuid_cast(schema):
    # Regression proof: the pre-fix "" value cannot be inserted into the UUID PK.
    with schema.cursor() as cur:
        with pytest.raises(psycopg2.errors.InvalidTextRepresentation):
            cur.execute(
                "INSERT INTO signals (signal_id, url) VALUES (%s, %s)", ("", "imap://x/1/1")
            )
