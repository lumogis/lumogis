# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""MetadataStore adapter for PostgreSQL."""

import logging
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

_log = logging.getLogger(__name__)


class PostgresStore:
    def __init__(self, host: str, port: int, user: str, password: str, dbname: str) -> None:
        self._dsn = dict(host=host, port=port, user=user, password=password, dbname=dbname)
        self._conn = self._connect()

    def _connect(self):
        conn = psycopg2.connect(**self._dsn)
        conn.autocommit = True
        return conn

    def _ensure_conn(self):
        """Reconnect if the connection is closed or broken."""
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception:
            _log.warning("Postgres connection lost — reconnecting")
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = self._connect()

    def ping(self) -> bool:
        try:
            self._ensure_conn()
            return True
        except Exception:
            return False

    def execute(self, query: str, params: tuple | None = None) -> None:
        self._ensure_conn()
        with self._conn.cursor() as cur:
            cur.execute(query, params)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        self._ensure_conn()
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        self._ensure_conn()
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self):
        """Open a single explicit transaction.

        Toggles ``autocommit`` off for the duration of the ``with`` block.
        Commits on clean exit, rolls back on any exception. Restores
        ``autocommit=True`` either way so subsequent ``execute`` calls
        keep their per-call commit semantics. Re-entry is not supported —
        nesting will raise.

        Used by the per-user import path so refuse-mid-flight
        (parent UUID collision) leaves Postgres untouched. See
        ``per_user_backup_export`` plan Pass 0 step 6.
        """
        self._ensure_conn()
        if not self._conn.autocommit:
            raise RuntimeError("PostgresStore.transaction(): nested transactions not supported")
        self._conn.autocommit = False
        try:
            yield
            self._conn.commit()
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                _log.exception("rollback failed during transaction()")
            raise
        finally:
            self._conn.autocommit = True
