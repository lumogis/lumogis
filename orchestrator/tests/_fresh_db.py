# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Scratch-Postgres-database fixtures shared by tests (LUM-980, LUM-981).

``superuser`` connects as a superuser (skips, or fails under
``LUMOGIS_REQUIRE_INTEGRATION_DB=1``, when Postgres is not reachable) and
``fresh_db`` hands out a factory for new empty databases that are dropped at
teardown. The migration runner is pointed at one through ``POSTGRES_DB`` and
``LUMOGIS_MIGRATIONS_DIR``.

Moved out of ``test_db_migrations_baseline.py`` with no behaviour change.

Follow-up (in-code note, LUM-981): ``test_migration_074/075/078/084`` still carry
their own copies of this fixture. Converging them onto this module is a
separate, behaviour-free cleanup.
"""

from __future__ import annotations

import os
import uuid

import psycopg2
import pytest


def _conn_kwargs(dbname: str | None = None) -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": dbname or os.environ.get("POSTGRES_DB", "lumogis"),
        "connect_timeout": 3,
    }


@pytest.fixture(scope="module")
def superuser():
    try:
        conn = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # noqa: BLE001 — diagnostic skip
        if os.environ.get("LUMOGIS_REQUIRE_INTEGRATION_DB") == "1":
            raise
        pytest.skip(f"Postgres not reachable for the baseline tests: {exc}")
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        if not cur.fetchone()[0]:
            conn.close()
            pytest.skip("connection is not superuser; cannot create test databases")
    yield conn
    conn.close()


@pytest.fixture
def fresh_db(superuser, monkeypatch):
    """A new empty database; the runner is pointed at it through POSTGRES_DB."""
    created: list[str] = []

    def make() -> str:
        name = f"lum980_{uuid.uuid4().hex[:10]}"
        with superuser.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
        created.append(name)
        return name

    yield make
    with superuser.cursor() as cur:
        for name in created:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
