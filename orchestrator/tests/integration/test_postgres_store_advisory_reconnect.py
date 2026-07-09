# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Postgres singleton reconnect drops session advisory locks (LUM-358 / LUM-576).

Documents the known gap on ``PostgresStore``'s process-wide ``_conn`` path
(used by ``signals.digest``): ``_ensure_conn()`` reconnect after a broken
session releases any ``pg_advisory_lock`` held on that backend.

``consolidation_lock`` avoids this by using dedicated checkout connections.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras
import pytest

from signals.digest import ADVISORY_LOCK_KEY1
from signals.digest import ADVISORY_LOCK_KEY2


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


@pytest.fixture
def dsn():
    raw = _integration_dsn()
    if not raw:
        pytest.skip(
            "LUMOGIS_INTEGRATION_POSTGRES_DSN unset — Postgres integration "
            "not configured for this workstation",
        )
    return raw


def test_singleton_reconnect_releases_session_advisory_lock(dsn):
    """Broken singleton session reconnect drops digest advisory lock (LUM-576)."""
    store = _postgres_store(dsn)
    try:
        got = store.fetch_one(
            "SELECT pg_try_advisory_lock(%s::integer, %s::integer) AS ok",
            (ADVISORY_LOCK_KEY1, ADVISORY_LOCK_KEY2),
        )
        assert got is not None and got["ok"] is True

        store._conn.close()
        store._ensure_conn()

        challenger = psycopg2.connect(dsn)
        try:
            with challenger.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(%s::integer, %s::integer) AS ok",
                    (ADVISORY_LOCK_KEY1, ADVISORY_LOCK_KEY2),
                )
                row = cur.fetchone()
                assert row is not None and row["ok"] is True
        finally:
            try:
                with challenger.cursor() as cur:
                    cur.execute(
                        "SELECT pg_advisory_unlock(%s::integer, %s::integer)",
                        (ADVISORY_LOCK_KEY1, ADVISORY_LOCK_KEY2),
                    )
            finally:
                challenger.close()
    finally:
        try:
            store.close()
        except Exception:
            pass
