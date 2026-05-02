#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Legacy ``user_id='default'`` row remap.

Runs once on every container boot, immediately after ``db_migrations.py``
(see ``docker-entrypoint.sh``). Remaps any leftover ``user_id='default'``
rows on every scoped table to a real user, so the post-013 scope model
(``visible_filter`` union of personal-mine + shared + system) does not
silently strand legacy rows under nobody's account.

Resolution order for the target user (first non-empty wins):

1. ``INBOX_OWNER_USER_ID`` env var — explicit operator choice.
2. ``LUMOGIS_BOOTSTRAP_ADMIN_EMAIL`` env var → resolved to ``users.id``
   for that email.
3. If ``AUTH_ENABLED=false`` *and* the ``users`` table is empty →
   single-user dev mode; rows stay as ``'default'``. No-op.
4. Otherwise → log a remediation ``WARN`` line and exit non-zero. The
   entrypoint catches non-zero and continues boot in degraded mode.

This script is **idempotent** — a second run is a no-op (no rows match
``user_id='default'`` after the first remap). The entrypoint invokes it
on every boot, so the AUTH_ENABLED-flip transition (dev → family-LAN)
triggers the env-aware remap automatically once the operator sets one of
the env vars and restarts.

Per-table writes are individually transactional, so a transient DB error
on table N leaves tables 1..N-1 done and tables N+1.. untouched. The
next boot continues from where this one left off.

Exit codes:
  0  success (rows remapped, or no rows to remap, or dev-mode no-op)
  1  could not reach Postgres after the boot timeout
  2  cleanup is genuinely ambiguous and operator action is required
     (logs the symptom + remediation env var names; entrypoint warns
     but continues)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path  # noqa: F401  (kept for parity with db_migrations.py)

import psycopg2

_log = logging.getLogger("db_default_user_remap")
logging.basicConfig(level=logging.INFO, format="[remap] %(message)s")

WAIT_TIMEOUT_S = int(os.environ.get("LUMOGIS_DB_WAIT_TIMEOUT_S", "120"))

# Tables that carry a `user_id` column and may contain legacy
# `'default'`-attributed rows from the pre-multi-user period. Order is
# parent → child for cosmetic per-table summaries; functionally each
# UPDATE is independent because no FK references `user_id`.
_SCOPED_TABLES: tuple[str, ...] = (
    "file_index",
    "entities",
    "entity_relations",
    "review_queue",
    "connector_permissions",
    "routine_do_tracking",
    "action_log",
    "sources",
    "signals",
    "relevance_profiles",
    "feedback_log",
    "audit_log",
    "routines",
    "user_batch_jobs",
    "constraint_violations",
    "edge_scores",
    "known_distinct_entity_pairs",
    "review_decisions",
    "deduplication_runs",
    "dedup_candidates",
    "sessions",
    "notes",
    "audio_memos",
)


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
    _log.error("ERROR: Postgres unreachable after %ds: %s", WAIT_TIMEOUT_S, last_err)
    sys.exit(1)


def _users_table_exists(conn) -> bool:
    """Return True iff `users` table is present (post-010 migration)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = 'users'"
        )
        return cur.fetchone() is not None


def _users_is_empty(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM users LIMIT 1")
        return cur.fetchone() is None


def _resolve_target_user_id(conn) -> str | None:
    """Resolve the target user_id per the documented precedence order.

    Returns the user_id string, or ``None`` if cleanup is genuinely
    ambiguous (caller will log a remediation message and exit 2). The
    dev-mode "no-op" branch returns the sentinel ``__DEV_NOOP__`` so the
    caller can distinguish "nothing to do" from "ambiguous".
    """
    explicit = os.environ.get("INBOX_OWNER_USER_ID", "").strip()
    if explicit:
        _log.info("Resolved target via INBOX_OWNER_USER_ID = %s", explicit)
        return explicit

    bootstrap_email = os.environ.get("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "").strip()
    if bootstrap_email:
        if not _users_table_exists(conn):
            _log.warning(
                "LUMOGIS_BOOTSTRAP_ADMIN_EMAIL is set but the `users` table does not "
                "exist (migration 010 not yet applied). Skipping remap."
            )
            return "__DEV_NOOP__"
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM users WHERE LOWER(email) = LOWER(%s) AND disabled = FALSE",
                (bootstrap_email,),
            )
            row = cur.fetchone()
        if row:
            _log.info(
                "Resolved target via LUMOGIS_BOOTSTRAP_ADMIN_EMAIL='%s' → user_id=%s",
                bootstrap_email,
                row[0],
            )
            return row[0]
        _log.warning(
            "LUMOGIS_BOOTSTRAP_ADMIN_EMAIL='%s' is set but no enabled user with "
            "that email was found in `users`. Falling through to dev-mode check.",
            bootstrap_email,
        )

    auth_enabled = os.environ.get("AUTH_ENABLED", "false").strip().lower() == "true"
    if not auth_enabled:
        if not _users_table_exists(conn) or _users_is_empty(conn):
            _log.info(
                "AUTH_ENABLED=false and `users` is empty/absent — single-user "
                "dev mode. Leaving any user_id='default' rows in place. No-op."
            )
            return "__DEV_NOOP__"

    return None


def _remap_table(conn, table: str, target: str) -> int:
    """Remap one table, in its own transaction. Returns the row count."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        )
        if cur.fetchone() is None:
            return 0
        cur.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "  AND table_name = %s AND column_name = 'user_id'",
            (table,),
        )
        if cur.fetchone() is None:
            return 0
        cur.execute(
            f"UPDATE {table} SET user_id = %s WHERE user_id = 'default'",
            (target,),
        )
        return cur.rowcount or 0


def main() -> int:
    _wait_for_postgres()
    with _conn() as conn:
        conn.autocommit = False
        target = _resolve_target_user_id(conn)
        conn.commit()

        if target is None:
            _log.warning(
                "db_default_user_remap.py exited non-zero — legacy 'default'-user "
                "rows remain unattributed. Symptom: ALL users will see the "
                "inbox/sessions/notes/etc. of the legacy single-user period appear "
                "under nobody's account (their own retrieval surfaces will be "
                "missing those rows). Remediation: set "
                "INBOX_OWNER_USER_ID=<real-user-id> in .env and restart, OR set "
                "LUMOGIS_BOOTSTRAP_ADMIN_EMAIL=<existing-admin-email>."
            )
            return 2

        if target == "__DEV_NOOP__":
            return 0

        total = 0
        per_table: list[tuple[str, int]] = []
        for table in _SCOPED_TABLES:
            try:
                count = _remap_table(conn, table, target)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                _log.error(
                    "remap of %s failed: %s — skipping; next boot will retry",
                    table,
                    exc,
                )
                count = -1
            per_table.append((table, count))
            if count > 0:
                total += count
            _log.info("%-32s %s rows", table, count if count >= 0 else "ERROR")

        _log.info("remap target user_id=%s; total rows updated = %d", target, total)
        return 0


if __name__ == "__main__":
    sys.exit(main())
