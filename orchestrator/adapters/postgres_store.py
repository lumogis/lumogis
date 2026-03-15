"""MetadataStore adapter for PostgreSQL."""

import logging

import psycopg2
import psycopg2.extras

_log = logging.getLogger(__name__)


class PostgresStore:
    def __init__(self, host: str, port: int, user: str, password: str, dbname: str) -> None:
        self._conn = psycopg2.connect(
            host=host, port=port, user=user, password=password, dbname=dbname
        )
        self._conn.autocommit = True

    def ping(self) -> bool:
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        except Exception:
            return False

    def execute(self, query: str, params: tuple | None = None) -> None:
        with self._conn.cursor() as cur:
            cur.execute(query, params)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            return [dict(row) for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()
