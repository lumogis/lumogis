# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""PostgreSQL regression: migration ``023-auth-sessions-and-token-version.sql``.

Ensures ``auth_sessions`` lands without mutating migration ``003``'s ``sessions``
(chat/memory table) — naming collision is a silent failure mode (LUM-29).
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import pytest

psycopg2 = pytest.importorskip("psycopg2")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATION_PATH = _REPO_ROOT / "postgres" / "migrations" / "023-auth-sessions-and-token-version.sql"


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
def _pg_available() -> bool:
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001 — diagnostic skip
        pytest.skip(f"Postgres not reachable for migration regression test: {exc}")
    conn.close()
    return True


@pytest.fixture
def isolated_schema(_pg_available):
    name = f"test_mig029_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA "{name}"')
            cur.execute(f'SET search_path TO "{name}"')
            cur.execute(
                """
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY
                );
                """
            )
            cur.execute(
                """
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL,
                    refresh_token_jti TEXT
                );
                """
            )
        sql = _MIGRATION_PATH.read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{name}"')
            cur.execute(sql)

        yield conn, name
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        conn.close()


def test_migration_023_auth_sessions_and_preserves_sessions_table(isolated_schema):
    conn, schema = isolated_schema
    with conn.cursor() as cur:
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'auth_sessions'
            ORDER BY ordinal_position
            """,
            (schema,),
        )
        cols = [r[0] for r in cur.fetchall()]
        assert "user_id" in cols
        assert "family_id" in cols
        assert "refresh_token_hash" in cols
        cur.execute(f'SET search_path TO "{schema}"')
        cur.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'sessions'
            """,
            (schema,),
        )
        legacy = [r[0] for r in cur.fetchall()]
        assert legacy == ["id"]


class _IsolationMetadataStore:
    """Bind migration regression ``conn`` + schema for orchestrator MetadataStore APIs."""

    def __init__(self, conn, schema: str) -> None:
        self._conn = conn
        self._schema = schema

    def execute(self, query: str, params: tuple | None = None) -> None:
        with self._conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{self._schema}"')
            cur.execute(query, params or ())

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        import psycopg2.extras

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f'SET search_path TO "{self._schema}"')
            cur.execute(query, params or ())
            row = cur.fetchone()
            return dict(row) if row else None

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        import psycopg2.extras

        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f'SET search_path TO "{self._schema}"')
            cur.execute(query, params or ())
            return [dict(r) for r in cur.fetchall()]

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    @contextmanager
    def transaction(self):
        prev = self._conn.autocommit
        self._conn.autocommit = False
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._conn.autocommit = prev


def test_jwt_sid_revocation_gate_observes_revoked_auth_session_row(isolated_schema, monkeypatch):
    conn, schema = isolated_schema

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "jwt-sid-int-test-secret-do-not-use-prod-value!!")
    monkeypatch.setenv("LUMOGIS_REQUIRE_SID_REVOCATION_CHECK", "true")

    from auth import invalidate_sid_cache
    from auth import jwt_revocation_failure_reason
    from auth import mint_access_token
    from auth import verify_token

    import config as cfg

    store = _IsolationMetadataStore(conn, schema)
    prev = cfg._instances.pop("metadata_store", None)
    cfg._instances["metadata_store"] = store
    try:
        uid = uuid.uuid4().hex
        sid = uuid.uuid4().hex
        rf_hash = "f" * 64
        ip_h = "a" * 64
        ua_h = "b" * 64
        now = datetime.now(timezone.utc)
        exp = now + timedelta(days=1)

        with conn.cursor() as cur:
            cur.execute(f'SET search_path TO "{schema}"')
            cur.execute(
                "INSERT INTO users (id, email) VALUES (%s, %s)",
                (uid, "sidgate@example.lan"),
            )
            cur.execute(
                "INSERT INTO auth_sessions "
                "(id, user_id, family_id, refresh_token_hash, expires_at, "
                "device_label, ip_hash, ua_hash) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (sid, uid, sid, rf_hash, exp, "Desktop · mig-test", ip_h, ua_h),
            )

        tok = mint_access_token(uid, "admin", session_id=sid, token_version=1)
        payload = verify_token(tok)
        assert payload is not None
        assert jwt_revocation_failure_reason(payload) is None

        store.execute(
            "UPDATE auth_sessions SET revoked_at = NOW() WHERE id = %s",
            (sid,),
        )
        invalidate_sid_cache(sid)
        assert jwt_revocation_failure_reason(payload) == "invalid token"
    finally:
        cfg._instances.pop("metadata_store", None)
        if prev is not None:
            cfg._instances["metadata_store"] = prev
