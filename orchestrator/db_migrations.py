#!/usr/bin/env python3
"""
Postgres migration runner.

Applies SQL files from /project/postgres/migrations/ in lexical order, tracking
applied filenames in a `schema_migrations` table so each migration runs at most
once. Waits for Postgres to accept connections before starting.

Designed to be invoked from orchestrator/docker-entrypoint.sh on every boot.
Idempotent and safe to re-run; does NOT recreate tables already provisioned by
postgres/init.sql (migrations themselves use IF NOT EXISTS / OR REPLACE).

Migration history (informational — runtime discovery is by glob, this list is
for human readers cross-referencing plans/ADRs):

  001 add-user-id-to-file-index
  002 app-settings
  003 sessions-notes-audio-graph-tracking
  004 kg-quality-entities
  005 kg-quality-constraints
  006 kg-quality-edge-scores
  007 kg-quality-dedup
  008 kg-quality-splink
  009 kg-settings
  010 users-and-roles
  011 per-user-file-index
  012 entity-relations-evidence-uniq
  013 memory-scopes  (personal/shared/system + projection-row partial UNIQUE
                     indexes + signals.{source_url,source_label} backfill +
                     (user_id, scope) composite indexes; see plan
                     `personal_shared_system_memory_scopes`)
  014 mcp-tokens     (per-user `mcp_tokens` table — SHA-256 hashes of opaque
                     bearer credentials with optional `expires_at`; see plan
                     `mcp_token_user_map`)
  015 user-connector-credentials  (encrypted per-user external-service
                     secrets sealed with `LUMOGIS_CREDENTIAL_KEY[S]`;
                     `key_version` is BIGINT carrying the unsigned 32-bit
                     SHA-256 fingerprint of the sealing key; see plan + ADR
                     `per_user_connector_credentials`)
  016 per-user-connector-permissions (lifts connector_permissions and
                     routine_do_tracking from deployment-wide singletons
                     to strict per-(user_id, connector[, action_type])
                     ownership; closes audit A2; see plan + ADR
                     `per_user_connector_permissions`)
  017 per-user-batch-jobs (per-user durable batch job ledger; closes
                     audit B7; see plan + ADR `per_user_batch_jobs`)
  018 household-and-instance-system-connector-credentials
                    (two new credential tier tables — household-shared
                    and operator-owned — sealed by the same
                    `LUMOGIS_CREDENTIAL_KEY[S]` MultiFernet that seals
                    015. PK is `(connector)` only; `created_by` /
                    `updated_by` reject the literal `'self'` because
                    no user owns these rows. Both tables are omitted
                    from the per-user export path; restore requires
                    the matching key. See plan + ADR
                    `credential_scopes_shared_system`.)

The 013 chunk also wires `db_default_user_remap.py` from
`docker-entrypoint.sh` immediately after this runner — that step is NOT a
SQL migration (it depends on env vars and the live `users` table) and is
intentionally separate to keep this runner pure-SQL.

Exit codes:
  0  success (or nothing to apply)
  1  could not reach Postgres after the boot timeout
  2  a migration file failed to apply
"""
from __future__ import annotations

import glob
import hashlib
import os
import sys
import time
from pathlib import Path

import psycopg2

MIGRATIONS_DIR = Path(os.environ.get("LUMOGIS_MIGRATIONS_DIR", "/project/postgres/migrations"))
WAIT_TIMEOUT_S = int(os.environ.get("LUMOGIS_DB_WAIT_TIMEOUT_S", "120"))


def _conn():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "lumogis"),
        password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        dbname=os.environ.get("POSTGRES_DB", "lumogis"),
    )


def _wait_for_postgres() -> None:
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return
        except Exception as exc:
            last_err = exc
            time.sleep(2)
    print(f"[migrations] ERROR: Postgres unreachable after {WAIT_TIMEOUT_S}s: {last_err}", file=sys.stderr)
    sys.exit(1)


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename     TEXT PRIMARY KEY,
                checksum     TEXT NOT NULL,
                applied_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )


def _applied(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT filename FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def _record(conn, filename: str, checksum: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s) "
            "ON CONFLICT (filename) DO NOTHING",
            (filename, checksum),
        )


def main() -> int:
    if not MIGRATIONS_DIR.is_dir():
        print(f"[migrations] no migrations directory at {MIGRATIONS_DIR}; skipping")
        return 0

    files = sorted(glob.glob(str(MIGRATIONS_DIR / "*.sql")))
    if not files:
        print(f"[migrations] no .sql files in {MIGRATIONS_DIR}; skipping")
        return 0

    _wait_for_postgres()

    with _conn() as conn:
        conn.autocommit = False
        _ensure_table(conn)
        conn.commit()
        applied = _applied(conn)

        # Pre-seed: if migration files already match what's in the live schema
        # (e.g. an existing install upgraded today), record them as applied
        # without re-executing on the very first runner pass.
        # We do this opportunistically by trying each file; failures are surfaced.

        new_count = 0
        for path_str in files:
            path = Path(path_str)
            name = path.name
            if name in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]
            print(f"[migrations] applying {name} ({checksum})")
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                _record(conn, name, checksum)
                conn.commit()
                new_count += 1
            except Exception as exc:
                conn.rollback()
                print(f"[migrations] ERROR while applying {name}: {exc}", file=sys.stderr)
                return 2

        if new_count == 0:
            print(f"[migrations] up to date ({len(files)} files, {len(applied)} previously applied)")
        else:
            print(f"[migrations] applied {new_count} new migration(s)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
