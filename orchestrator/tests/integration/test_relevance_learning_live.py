# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-649 — feedback → relevance learning executes against REAL Postgres.

The house fake store cannot exercise the JSONB UPSERT, the migration-058 columns,
or the visibility-scoped signal SELECT against the real schema. This companion
applies migration 058 on top of a minimal signals + relevance_profiles schema and
proves: (1) ``apply_signal_feedback``'s ``ON CONFLICT (user_id)`` UPSERT round-trips
the JSONB weight maps and clamps hold; (2) a first-ever feedback creates the row;
(3) ``compute_learned_relevance_delta`` computed over real-fetched weights is
non-zero after feedback and zero before.

Skips when no Postgres is reachable (laptop unit runs stay green); runs in CI /
``make compose-test``. An isolated schema per test avoids parallel collisions.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from auth import UserContext
from tests.integration._pgguard import require_postgres

import config
from services import relevance_learning as rl

psycopg2 = pytest.importorskip("psycopg2")
pytest.importorskip("psycopg2.extras")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_MIGRATION_058 = _REPO_ROOT / "postgres" / "migrations" / "058-relevance-learned-weights.sql"

_SIGNALS_DDL = """
CREATE TABLE signals (
    signal_id        UUID PRIMARY KEY,
    user_id          TEXT NOT NULL DEFAULT 'default',
    topics           JSONB NOT NULL DEFAULT '[]',
    entities         JSONB NOT NULL DEFAULT '[]',
    from_addr        TEXT NOT NULL DEFAULT '',
    category         TEXT NOT NULL DEFAULT '',
    scope            TEXT NOT NULL DEFAULT 'personal'
);
"""

# relevance_profiles WITHOUT the learned_* columns — migration 058 adds them,
# proving the migration is what makes the feature's storage exist.
_PROFILES_BASE_DDL = """
CREATE TABLE relevance_profiles (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           TEXT NOT NULL DEFAULT 'default',
    tracked_locations JSONB NOT NULL DEFAULT '[]',
    tracked_topics    JSONB NOT NULL DEFAULT '[]',
    tracked_entities  JSONB NOT NULL DEFAULT '[]',
    tracked_keywords  JSONB NOT NULL DEFAULT '[]',
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(user_id)
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
    """Minimal MetadataStore over a schema-scoped connection for the service layer."""

    def __init__(self, conn):
        self._conn = conn

    def fetch_one(self, query, params=None):
        with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def execute(self, query, params=None):
        with self._conn.cursor() as cur:
            cur.execute(query, params)


@pytest.fixture(scope="module")
def _pg_available() -> bool:
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001 — diagnostic skip
        require_postgres(exc)
    conn.close()
    return True


@pytest.fixture
def store(_pg_available, monkeypatch):
    name = f"test_rl_{uuid.uuid4().hex[:12]}"
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA "{name}"')
        cur.execute(f'SET search_path TO "{name}"')
        cur.execute(_SIGNALS_DDL)
        cur.execute(_PROFILES_BASE_DDL)
        cur.execute(_MIGRATION_058.read_text(encoding="utf-8"))
    s = _SchemaStore(conn)
    monkeypatch.setattr(config, "get_metadata_store", lambda: s)
    try:
        yield s, conn
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA "{name}" CASCADE')
        conn.close()


def _add_signal(conn, *, signal_id, user_id, scope="personal", topics=None, category=""):
    import json

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, user_id, scope, topics, category) "
            "VALUES (%s, %s, %s, %s::jsonb, %s)",
            (signal_id, user_id, scope, json.dumps(topics or []), category),
        )


def _user(user_id="A"):
    return UserContext(user_id=user_id, is_authenticated=True, role="user", allows_shared=True)


def test_apply_feedback_upsert_roundtrips_jsonb(store):
    _, conn = store
    sid = str(uuid.uuid4())
    _add_signal(conn, signal_id=sid, user_id="A", topics=["finance"], category="newsletter")

    effect = rl.apply_signal_feedback(user=_user("A"), signal_id=sid, positive=False)
    assert set(effect["demoted"]) == {"finance", "newsletter"}

    weights = rl.load_profile_weights("A")
    assert weights["topics"]["finance"] == pytest.approx(-0.15)
    assert weights["categories"]["newsletter"] == pytest.approx(-0.15)


def test_first_feedback_creates_profile_row(store):
    _, conn = store
    sid = str(uuid.uuid4())
    _add_signal(conn, signal_id=sid, user_id="A", topics=["work"])
    assert rl.load_profile_weights("A") == {"topics": {}, "entities": {}, "categories": {}}

    rl.apply_signal_feedback(user=_user("A"), signal_id=sid, positive=True)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM relevance_profiles WHERE user_id = %s", ("A",))
        assert cur.fetchone()[0] == 1


def test_repeated_feedback_clamps_in_real_db(store):
    _, conn = store
    sid = str(uuid.uuid4())
    _add_signal(conn, signal_id=sid, user_id="A", topics=["work"])
    for _ in range(10):
        rl.apply_signal_feedback(user=_user("A"), signal_id=sid, positive=False)
    assert rl.load_profile_weights("A")["topics"]["work"] == pytest.approx(rl.WEIGHT_MIN)


def test_delta_over_real_weights_zero_before_nonzero_after(store):
    _, conn = store
    sid = str(uuid.uuid4())
    _add_signal(conn, signal_id=sid, user_id="A", topics=["work"])

    before = rl.compute_learned_relevance_delta(
        rl.load_profile_weights("A"), topics=["work"], entities=[], from_addr="", category=""
    )
    assert before == 0.0

    rl.apply_signal_feedback(user=_user("A"), signal_id=sid, positive=True)
    after = rl.compute_learned_relevance_delta(
        rl.load_profile_weights("A"), topics=["work"], entities=[], from_addr="", category=""
    )
    assert after > 0.0


def test_invisible_shared_signal_when_opted_out(store, monkeypatch):
    _, conn = store
    sid = str(uuid.uuid4())
    _add_signal(conn, signal_id=sid, user_id="B", scope="shared", topics=["shared"])
    # A opted out of shared visibility → cannot feedback on B's shared signal.
    opted_out = UserContext(user_id="A", is_authenticated=True, role="user", allows_shared=False)
    effect = rl.apply_signal_feedback(user=opted_out, signal_id=sid, positive=False)
    assert effect == {"demoted": [], "promoted": []}
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM relevance_profiles WHERE user_id = %s", ("A",))
        assert cur.fetchone()[0] == 0
