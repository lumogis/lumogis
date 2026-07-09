# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Postgres integration PoC for entity write isolation (LUM-358).

Three scenarios from the plan acceptance sketch:
(a) concurrent summary commits — one wins, one conflicts;
(b) stale promote after interleaved live write;
(c) cross-session advisory lock mutual exclusion.
"""

from __future__ import annotations

import os
import time
from urllib.parse import urlparse
from uuid import uuid4

import psycopg2
import psycopg2.extras
import pytest

from auth import UserContext


def _integration_dsn() -> str | None:
    return os.environ.get("LUMOGIS_INTEGRATION_POSTGRES_DSN")


def _postgres_store(raw: str):
    from adapters.postgres_store import PostgresStore

    u = urlparse(raw)
    return PostgresStore(
        host=u.hostname or "localhost",
        port=int(u.port or 5432),
        user=u.username or "lumogis",
        password=u.password or "",
        dbname=(u.path or "/lumogis").strip("/").split("?")[0] or "lumogis",
    )


def _insert_entity(cur, entity_id, user_id: str, scope: str, version: int = 1, summary: str = "initial"):
    cur.execute(
        """
        INSERT INTO entities (
            entity_id, name, entity_type, user_id, scope, version, summary
        ) VALUES (%s, 'poc-entity', 'PERSON', %s, %s, %s, %s)
        ON CONFLICT (entity_id) DO UPDATE SET
            version = EXCLUDED.version,
            summary = EXCLUDED.summary,
            scope = EXCLUDED.scope,
            user_id = EXCLUDED.user_id
        """,
        (str(entity_id), user_id, scope, version, summary),
    )


def _delete_entity(cur, entity_id) -> None:
    cur.execute("DELETE FROM entities WHERE entity_id = %s", (str(entity_id),))


@pytest.fixture
def dsn():
    raw = _integration_dsn()
    if not raw:
        pytest.skip(
            "LUMOGIS_INTEGRATION_POSTGRES_DSN unset — Postgres integration "
            "not configured for this workstation",
        )
    return raw


@pytest.fixture
def pg_stores(dsn, monkeypatch):
    import config as cfg

    store_a = _postgres_store(dsn)
    store_b = _postgres_store(dsn)
    prev = cfg._instances.pop("metadata_store", None)
    cfg._instances["metadata_store"] = store_a
    try:
        yield store_a, store_b, dsn
    finally:
        cfg._instances.pop("metadata_store", None)
        if prev is not None:
            cfg._instances["metadata_store"] = prev
        store_a.close()
        store_b.close()


def test_concurrent_summary_commits_one_wins(pg_stores, monkeypatch):
    """Two writers on independent store connections — OCC rejects the loser."""
    import config as cfg
    from services import entity_write_guard as guard

    store_a, store_b, dsn = pg_stores
    entity_id = uuid4()
    caller = UserContext(user_id="alice", is_authenticated=True, role="member")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            _insert_entity(cur, entity_id, "alice", "personal", version=1)
        conn.commit()
    finally:
        conn.close()

    try:
        snap = guard.read_entity_summary(entity_id, caller=caller)
        assert snap is not None and snap.version == 1

        cfg._instances["metadata_store"] = store_a
        first = guard.commit_summary_update(
            entity_id,
            caller=caller,
            read_version=1,
            new_summary="writer-a",
        )
        cfg._instances["metadata_store"] = store_b
        second = guard.commit_summary_update(
            entity_id,
            caller=caller,
            read_version=1,
            new_summary="writer-b",
        )

        assert first.ok is True
        assert second.ok is False
        assert second.conflict is True
    finally:
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                _delete_entity(cur, entity_id)
            conn.commit()
        finally:
            conn.close()


def test_stale_promote_rejected_after_interleaved_live_write(pg_stores, monkeypatch):
    """Read → sleep → live write on second connection → stale promote fails."""
    import config as cfg
    from services import entity_write_guard as guard

    store_a, store_b, dsn = pg_stores
    entity_id = uuid4()
    caller = UserContext(user_id="alice", is_authenticated=True, role="member")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            _insert_entity(cur, entity_id, "alice", "personal", version=1)
            cur.execute(
                "UPDATE entities SET staged_summary = %s WHERE entity_id = %s",
                ("staged text", str(entity_id)),
            )
        conn.commit()
    finally:
        conn.close()

    try:
        cfg._instances["metadata_store"] = store_a
        snap = guard.read_entity_summary(entity_id, caller=caller)
        assert snap is not None and snap.version == 1

        time.sleep(0.05)

        cfg._instances["metadata_store"] = store_b
        live = guard.commit_summary_update(
            entity_id,
            caller=caller,
            read_version=1,
            new_summary="live overwrite",
        )
        assert live.ok is True

        cfg._instances["metadata_store"] = store_a
        promote = guard.promote_staged_summary(
            entity_id,
            caller=caller,
            read_version=1,
        )
        assert promote.ok is False
        assert promote.conflict is True
    finally:
        conn = psycopg2.connect(dsn)
        try:
            with conn.cursor() as cur:
                _delete_entity(cur, entity_id)
            conn.commit()
        finally:
            conn.close()


def test_consolidation_advisory_lock_cross_session_exclusion(dsn):
    """Holder on raw session blocks try_acquire on a second checkout connection."""
    from services.consolidation_lock import CONSOLIDATION_ADVISORY_KEY1
    from services.consolidation_lock import advisory_key2
    from services.consolidation_lock import try_acquire_consolidation_lock

    key2 = advisory_key2("alice", "PERSON")
    holder = psycopg2.connect(dsn)
    try:
        with holder.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(%s::integer, %s::integer) AS ok",
                (CONSOLIDATION_ADVISORY_KEY1, key2),
            )
            row = cur.fetchone()
            assert row is not None and row["ok"] is True

        blocked = try_acquire_consolidation_lock("alice", "PERSON", dsn=dsn)
        assert blocked is None
    finally:
        try:
            with holder.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(%s::integer, %s::integer)",
                    (CONSOLIDATION_ADVISORY_KEY1, key2),
                )
        finally:
            holder.close()
