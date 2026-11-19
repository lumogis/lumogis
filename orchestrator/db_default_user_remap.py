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

The WARN is only for real leftovers (LUM-916). No target is not a problem
when no ``user_id='default'`` row is left, which is every boot after the
switch once the bootstrap env is unset. Nor is it one in the pre-uvicorn
pass of the switch itself: ``LUMOGIS_BOOTSTRAP_ADMIN_EMAIL`` is set but the
``users`` table is still empty because the orchestrator creates the admin
later, then runs this script again (``post_bootstrap=True``). Both cases
log INFO and exit 0.

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
    # Core library / inbox surfaces
    "file_index",
    "sessions",
    "notes",
    "audio_memos",
    "web_conversations",
    "web_messages",
    "captures",
    "capture_attachments",
    "capture_transcripts",
    # Knowledge graph + memory
    "entities",
    "entity_relations",
    "entity_edges",
    "memories",
    "sources",
    "external_documents",
    "signals",
    "signal_triage",  # LUM-643: per-(user, signal) triage state
    "relevance_profiles",
    "edge_scores",
    "known_distinct_entity_pairs",
    "entity_name_rules",  # LUM-967
    "entity_merges",  # LUM-965: undo records (user_id column only; JSONB copies re-keyed at undo)
    "entity_hidden",  # LUM-966: a member's Hide of another member's shared entry
    "entity_deletions",  # LUM-966: ids-only delete retry tombstones
    # KG quality / review
    "review_queue",
    "review_decisions",
    "constraint_violations",
    "deduplication_runs",
    "dedup_candidates",
    "feedback_log",
    # Connectors, routines, jobs
    "connector_permissions",
    "routine_do_tracking",
    "routines",
    "user_batch_jobs",
    "action_proposals",
    # Email taxonomy + rules (user-editable classification config, LUM-629)
    "email_category_taxonomy",
    "email_classification_rule",
    # Email operational logs/jobs (migrations 060-062)
    "email_organize_log",
    "email_cleanup_job",
    "email_sent_log",
    # LUM-819 — stored message text for search. Carries user_id, so omitting it
    # would strand legacy 'default'-attributed body rows on the AUTH_ENABLED
    # false->true flip and silently empty a user's body search.
    "email_body_text",
    # LUM-820 — bounce records follow their sends. Without this a default
    # install that becomes multi-user would strand them on the old id, and
    # the user's sends would silently look delivered again.
    "email_bounce",
    # LUM-697 — both are user-scoped; omitting them here silently orphans
    # rows on the AUTH_ENABLED false->true remap (the bug fixed in d235447).
    "email_unsubscribe_target",
    "email_sender_rule",
    # LUM-695 — inbound attachment metadata + the outbound staging index. Both
    # carry user_id and both must be remapped, or a staged file becomes
    # unresolvable and an attachment list silently empties after the flip.
    "email_attachment",
    "email_attachment_staged",
    # LUM-702 — the auto-ingest provenance/reversal ledger. Without the remap a
    # filed document survives the flip while its undo path does not, leaving the
    # user a library row they cannot take back.
    "email_attachment_ingest_log",
    # LUM-727 — the "always file this" rules. Without the remap a user's
    # filing rules stop firing after the flip while their mail keeps arriving.
    "email_attachment_rule",
    # Credentials + tokens (per-user rows; export may omit but remap must run)
    "user_connector_credentials",
    "mcp_tokens",
    # Auth/session bookkeeping tied to a user_id
    "auth_sessions",
    "auth_refresh_revocations",
    # LUM-65 — forgot-password tokens. Today the email reset path requires
    # AUTH_ENABLED, so a user_id='default' row should be unreachable; it is
    # listed anyway because the cost of a no-op remap is nothing and the cost
    # of the LUM-697 failure mode (silently orphaned rows after the flip) is a
    # user holding a reset link that resolves to nobody.
    "password_reset_tokens",
    # Notifications + privacy prefs
    "notification_preferences",
    "notification_user_settings",
    "privacy_user_settings",
    "webpush_subscriptions",
    # Purge tombstones + audit
    "purged_conversations",
    "purged_documents",
    "action_log",
    "audit_log",
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


def _setup_admin_user_id(conn) -> str | None:
    """The enabled admin recorded in ``instance_setup`` (LUM-981), or ``None``.

    ``to_regclass`` guards the query because the remap also runs on databases
    where migration 085 has not applied yet.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.instance_setup')")
        row = cur.fetchone()
        if not row or row[0] is None:
            return None
        cur.execute(
            "SELECT s.admin_user_id FROM instance_setup s "
            "JOIN users u ON u.id = s.admin_user_id "
            "WHERE s.id = 1 AND NOT u.disabled"
        )
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def _resolve_target_user_id(conn) -> str | None:
    """Resolve the target user_id per the documented precedence order.

    Returns the user_id string, or ``None`` if cleanup is genuinely
    ambiguous (caller will log a remediation message and exit 2). The
    dev-mode "no-op" branch returns the sentinel ``__DEV_NOOP__`` so the
    caller can distinguish "nothing to do" from "ambiguous".
    """
    explicit = os.environ.get("INBOX_OWNER_USER_ID", "").strip()
    if explicit:
        auth_enabled = os.environ.get("AUTH_ENABLED", "false").strip().lower() == "true"
        if explicit == "default" and auth_enabled:
            _log.warning(
                "INBOX_OWNER_USER_ID='default' is ignored when AUTH_ENABLED=true — "
                "remap would be a no-op; falling through to bootstrap email resolution."
            )
        else:
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
        if _users_is_empty(conn):
            # The switch to sign-in: the orchestrator has not created the
            # bootstrap admin yet. Not a misconfiguration (LUM-916).
            _log.info(
                "LUMOGIS_BOOTSTRAP_ADMIN_EMAIL='%s' is set and `users` is empty — "
                "the bootstrap admin is not created yet.",
                bootstrap_email,
            )
        else:
            _log.warning(
                "LUMOGIS_BOOTSTRAP_ADMIN_EMAIL='%s' is set but no enabled user with "
                "that email was found in `users`. Falling through to dev-mode check.",
                bootstrap_email,
            )

    auth_enabled = os.environ.get("AUTH_ENABLED", "false").strip().lower() == "true"
    if auth_enabled:
        # LUM-981: the admin created at first-visit setup. After the env tiers,
        # so a set bootstrap email still wins; parity with bootstrap-env installs.
        setup_admin = _setup_admin_user_id(conn)
        if setup_admin:
            _log.info("Resolved target via instance_setup admin → user_id=%s", setup_admin)
            return setup_admin
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
        if not _scoped_table_has_user_id(cur, table):
            return 0
        cur.execute(
            f"UPDATE {table} SET user_id = %s WHERE user_id = 'default'",
            (target,),
        )
        return cur.rowcount or 0


def _scoped_table_has_user_id(cur, table: str) -> bool:
    cur.execute(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = current_schema() "
        "  AND table_name = %s AND column_name = 'user_id'",
        (table,),
    )
    return cur.fetchone() is not None


def _count_default_rows(conn) -> int:
    """Number of ``user_id='default'`` rows left across ``_SCOPED_TABLES``."""
    total = 0
    with conn.cursor() as cur:
        for table in _SCOPED_TABLES:
            if not _scoped_table_has_user_id(cur, table):
                continue
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = 'default'")
            row = cur.fetchone()
            total += int(row[0]) if row else 0
    return total


def _bootstrap_admin_pending(conn) -> bool:
    """True when the bootstrap admin email is set but no user exists yet."""
    if not os.environ.get("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "").strip():
        return False
    return _users_table_exists(conn) and _users_is_empty(conn)


def _setup_pending(conn) -> bool:
    """True with sign-in on, no users and no bootstrap setting: setup will create the admin.

    Mirrors ``services.users.bootstrap_env_intended`` (this script runs before
    the app and keeps its imports minimal): any of email, password or password
    file means the admin comes from the environment, not from setup.
    """
    if os.environ.get("AUTH_ENABLED", "false").strip().lower() != "true":
        return False
    if any(
        os.environ.get(name, "").strip()
        for name in (
            "LUMOGIS_BOOTSTRAP_ADMIN_EMAIL",
            "LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD",
            "LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD_FILE",
        )
    ):
        return False
    return _users_table_exists(conn) and _users_is_empty(conn)


def run_post_bootstrap_safely(context: str) -> int:
    """Run the in-app remap pass without ever raising (main.py and the setup claim).

    ``main()`` can end in ``sys.exit`` (``_wait_for_postgres``) and can return 2;
    both are logged here, once, with ``context`` naming the caller. Returns the
    exit code, or 1 when it raised.
    """
    try:
        rc = main(post_bootstrap=True)
    except (Exception, SystemExit):  # _wait_for_postgres() ends in sys.exit(1)
        _log.exception("%s: default-user remap failed", context)
        return 1
    if rc != 0:
        _log.warning(
            "%s: default-user remap exited %d — legacy 'default' rows may remain "
            "until the next restart",
            context,
            rc,
        )
    return rc


def main(post_bootstrap: bool = False) -> int:
    """Run the remap. ``post_bootstrap`` is True for the orchestrator's second pass."""
    _wait_for_postgres()
    with _conn() as conn:
        conn.autocommit = False
        target = _resolve_target_user_id(conn)
        conn.commit()

        if target is None:
            leftover = _count_default_rows(conn)
            conn.commit()
            if leftover == 0:
                _log.info("No legacy user_id='default' rows remain. Nothing to remap.")
                return 0
            if not post_bootstrap and _bootstrap_admin_pending(conn):
                conn.commit()
                _log.info(
                    "%d legacy user_id='default' rows wait for the bootstrap admin. "
                    "The orchestrator remaps them after it creates the admin.",
                    leftover,
                )
                return 0
            if _setup_pending(conn):
                conn.commit()
                _log.info(
                    "%d legacy user_id='default' rows wait for the admin created at "
                    "setup. The claim moves them as soon as the account exists.",
                    leftover,
                )
                return 0
            _log.warning(
                "db_default_user_remap.py exited non-zero — %d legacy 'default'-user "
                "rows remain unattributed. Symptom: ALL users will see the "
                "inbox/sessions/notes/etc. of the legacy single-user period appear "
                "under nobody's account (their own retrieval surfaces will be "
                "missing those rows). Remediation: set "
                "INBOX_OWNER_USER_ID=<real-user-id> in .env and restart, OR set "
                "LUMOGIS_BOOTSTRAP_ADMIN_EMAIL=<existing-admin-email>.",
                leftover,
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
