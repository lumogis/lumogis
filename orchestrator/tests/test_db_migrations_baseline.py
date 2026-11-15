# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Where the migration runner finds its SQL, and the fresh-database baseline (LUM-980).

An install from the two release files mounts only the install folder at
``/project``, so the schema has to come from the image, and nothing runs
``init.sql`` through Postgres's initdb mount. The runner therefore:

* resolves the migrations directory (env, then ``/project/postgres``, then the
  copy baked into the image);
* applies ``init.sql`` itself, but only to an empty database (no ``file_index``);
* exits 3 when no schema is possible, which the entrypoint turns into a stop.

The directory tests need nothing. The baseline tests need a real Postgres and a
superuser (they create throwaway databases); they skip otherwise, like
``test_migration_074_extension_privilege.py``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import db_migrations
import psycopg2
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATIONS = _REPO_ROOT / "postgres" / "migrations"
_INIT_SQL = _REPO_ROOT / "postgres" / "init.sql"


# ---------------------------------------------------------------------------
# Directory resolution (no database)
# ---------------------------------------------------------------------------


def test_explicit_env_wins_even_when_missing(tmp_path):
    missing = tmp_path / "nope"
    got, explicit = db_migrations.resolve_migrations_dir(
        env={"LUMOGIS_MIGRATIONS_DIR": str(missing)},
        project_dir=tmp_path,
        baked_dir=tmp_path,
    )
    assert (got, explicit) == (missing, True)


def test_project_checkout_preferred_over_baked_copy(tmp_path):
    project, baked = tmp_path / "project", tmp_path / "baked"
    project.mkdir()
    baked.mkdir()
    got, explicit = db_migrations.resolve_migrations_dir(
        env={}, project_dir=project, baked_dir=baked
    )
    assert (got, explicit) == (project, False)


def test_baked_copy_used_when_no_checkout(tmp_path):
    baked = tmp_path / "baked"
    baked.mkdir()
    got, _ = db_migrations.resolve_migrations_dir(
        env={}, project_dir=tmp_path / "absent", baked_dir=baked
    )
    assert got == baked


def test_nothing_resolves(tmp_path):
    got, explicit = db_migrations.resolve_migrations_dir(
        env={}, project_dir=tmp_path / "a", baked_dir=tmp_path / "b"
    )
    assert (got, explicit) == (None, False)


def test_baseline_beside_migrations_unless_overridden(tmp_path):
    mig = tmp_path / "postgres" / "migrations"
    assert db_migrations.resolve_baseline(mig, env={}) == tmp_path / "postgres" / "init.sql"
    override = tmp_path / "custom.sql"
    assert (
        db_migrations.resolve_baseline(mig, env={"LUMOGIS_BASELINE_SQL": str(override)}) == override
    )


def test_main_exits_3_when_no_migrations_dir(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LUMOGIS_MIGRATIONS_DIR", raising=False)
    monkeypatch.setattr(db_migrations, "PROJECT_MIGRATIONS_DIR", tmp_path / "a")
    monkeypatch.setattr(db_migrations, "BAKED_MIGRATIONS_DIR", tmp_path / "b")
    assert db_migrations.main([]) == 3
    assert "migrations_dir_missing" in capsys.readouterr().err


def test_main_explicit_missing_dir_still_skips(tmp_path, monkeypatch, capsys):
    """Callers that set the env var (Makefile, backup round trip) keep exit 0."""
    monkeypatch.setenv("LUMOGIS_MIGRATIONS_DIR", str(tmp_path / "absent"))
    assert db_migrations.main([]) == 0
    assert "skipping" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Baseline (real Postgres, superuser)
# ---------------------------------------------------------------------------


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


def _use(monkeypatch, dbname: str, migrations: Path = _MIGRATIONS) -> None:
    monkeypatch.setenv("POSTGRES_DB", dbname)
    monkeypatch.setenv("LUMOGIS_MIGRATIONS_DIR", str(migrations))
    monkeypatch.delenv("LUMOGIS_BASELINE_SQL", raising=False)


def _has_table(dbname: str, table: str) -> bool:
    with psycopg2.connect(**_conn_kwargs(dbname)) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
        return bool(cur.fetchone()[0])


def _schema(dbname: str) -> dict:
    """Columns, indexes and constraints of the public schema, in a comparable form."""
    with psycopg2.connect(**_conn_kwargs(dbname)) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT table_name, column_name, data_type, is_nullable,
                      coalesce(column_default, '')
                 FROM information_schema.columns WHERE table_schema = 'public'
                ORDER BY 1, 2"""
        )
        columns = cur.fetchall()
        cur.execute(
            "SELECT tablename, indexname, indexdef FROM pg_indexes "
            "WHERE schemaname = 'public' ORDER BY 1, 2"
        )
        indexes = cur.fetchall()
        cur.execute(
            """SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
                 FROM pg_constraint
                WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2"""
        )
        constraints = cur.fetchall()
    return {"columns": columns, "indexes": indexes, "constraints": constraints}


def test_baseline_applied_to_empty_database_then_never_again(fresh_db, monkeypatch, capsys):
    db = fresh_db()
    _use(monkeypatch, db)
    assert db_migrations.main([]) == 0
    out = capsys.readouterr().out
    assert "baseline_applied" in out
    assert "applied" in out and "new migration" in out
    assert _has_table(db, "file_index") and _has_table(db, "schema_migrations")

    # Second start: initialised database, so no baseline and nothing to apply.
    assert db_migrations.main([]) == 0
    out = capsys.readouterr().out
    assert "baseline_applied" not in out
    assert "up to date" in out


def test_baseline_skipped_when_initdb_already_ran(fresh_db, monkeypatch, capsys):
    """Checkouts and test stacks: Postgres ran init.sql, so the runner must not."""
    db = fresh_db()
    with psycopg2.connect(**_conn_kwargs(db)) as conn, conn.cursor() as cur:
        cur.execute(_INIT_SQL.read_text(encoding="utf-8"))
    _use(monkeypatch, db)
    assert db_migrations.main([]) == 0
    out = capsys.readouterr().out
    assert "baseline_applied" not in out
    # The line prepare_dbs.py parses (LUM-894) is unchanged.
    assert "applied" in out and "new migration(s)" in out


def test_fresh_baseline_matches_initdb_path(fresh_db, monkeypatch, capsys):
    initdb_db, baseline_db = fresh_db(), fresh_db()
    with psycopg2.connect(**_conn_kwargs(initdb_db)) as conn, conn.cursor() as cur:
        cur.execute(_INIT_SQL.read_text(encoding="utf-8"))
    _use(monkeypatch, initdb_db)
    assert db_migrations.main([]) == 0
    _use(monkeypatch, baseline_db)
    assert db_migrations.main([]) == 0
    capsys.readouterr()
    a, b = _schema(initdb_db), _schema(baseline_db)
    assert a["columns"] == b["columns"]
    assert a["indexes"] == b["indexes"]
    assert a["constraints"] == b["constraints"]


def test_missing_baseline_exits_3_and_applies_nothing(fresh_db, monkeypatch, tmp_path, capsys):
    db = fresh_db()
    _use(monkeypatch, db)
    monkeypatch.setenv("LUMOGIS_BASELINE_SQL", str(tmp_path / "missing.sql"))
    assert db_migrations.main([]) == 3
    assert "baseline_missing" in capsys.readouterr().err
    assert not _has_table(db, "file_index")
    assert not _has_table(db, "schema_migrations")


def test_failing_baseline_exits_3_and_rolls_back(fresh_db, monkeypatch, tmp_path, capsys):
    db = fresh_db()
    bad = tmp_path / "bad.sql"
    bad.write_text("CREATE TABLE file_index (id INT);\nSELECT * FROM no_such_table;\n")
    _use(monkeypatch, db)
    monkeypatch.setenv("LUMOGIS_BASELINE_SQL", str(bad))
    assert db_migrations.main([]) == 3
    assert "baseline_failed" in capsys.readouterr().err
    # One transaction: the CREATE before the failing statement is rolled back.
    assert not _has_table(db, "file_index")
    assert not _has_table(db, "schema_migrations")


def test_dry_run_reports_baseline_without_applying(fresh_db, monkeypatch, capsys):
    db = fresh_db()
    _use(monkeypatch, db)
    assert db_migrations.main(["--dry-run"]) == 0
    assert "baseline would be applied" in capsys.readouterr().out
    assert not _has_table(db, "file_index")
