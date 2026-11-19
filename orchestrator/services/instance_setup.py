# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""First-admin setup (LUM-981): who may create the first account, and how.

With sign-in on and no user, Core boots into a setup state instead of refusing,
and the first visitor creates the admin through ``POST /api/v1/setup/claim``.
This module decides whether setup is open and performs the race-safe claim.

State is evaluated once at boot (:func:`evaluate_boot_state`) and kept in this
module. That is safe because Core runs a single worker
(``main._enforce_single_worker``). While setup is open no other path creates a
user (the bootstrap env needs a restart; admin routes and invites need an
admin), so the flag never needs a database read.

The claim is one transaction:

1. ``SET LOCAL lock_timeout`` and a transaction-scoped advisory lock;
2. a re-check, under the lock, of the ``instance_setup`` row and the user count;
3. ``create_user`` (the row below references it);
4. ``INSERT INTO instance_setup ... ON CONFLICT (id) DO NOTHING RETURNING id``,
   rolling back when no row comes back.

The singleton primary key (``id = 1``) is the real guarantee: even with the
lock removed there is exactly one admin. The lock only turns the loser's wait
into a clean :class:`SetupNotOpen` (HTTP 409). The row survives deleting the
admin (``ON DELETE SET NULL``), so setup never reopens.

All SQL here goes through five private helpers so unit tests can replace them
and live tests can exercise the real thing.
"""

from __future__ import annotations

import enum
import logging
from typing import Callable

import services.users as users_svc
from models.auth import InternalUser

import config

_log = logging.getLogger(__name__)

# Advisory-lock key1. Others in use: 8420607, 8421161, 8421358, 8421570
# (``rg -n pg_advisory orchestrator``). Key2 is always 1.
SETUP_LOCK_KEY1: int = 8421981
_LOCK_SQL = "SELECT pg_advisory_xact_lock(%s::integer, %s::integer)"

REMINDER_JOB_ID = "setup_claim_reminder"


class SetupNotOpen(Exception):
    """The claim was refused: setup is closed, or another claim won."""


class BootState(str, enum.Enum):
    NOT_APPLICABLE = "not_applicable"  # sign-in off, or users exist
    OPEN = "open"
    BLOCKED_BOOTSTRAP_FAILED = "blocked_bootstrap_failed"
    BLOCKED_SETUP_DONE_NO_USERS = "blocked_setup_done_no_users"
    BLOCKED_NO_MIGRATION = "blocked_no_migration"


_state: BootState = BootState.NOT_APPLICABLE
_claimed: bool = False

# Test seam: called inside the claim transaction after ``create_user`` and
# before the ``instance_setup`` INSERT. ``None`` in production.
_before_insert_hook: Callable[[], None] | None = None


# ---------------------------------------------------------------------------
# SQL helpers (the only place this module talks to the database)
# ---------------------------------------------------------------------------


def _table_exists() -> bool:
    row = config.get_metadata_store().fetch_one("SELECT to_regclass('public.instance_setup') AS t")
    return bool(row and row.get("t") is not None)


def _row_exists() -> bool:
    row = config.get_metadata_store().fetch_one("SELECT 1 AS one FROM instance_setup WHERE id = 1")
    return row is not None


def _set_lock_timeout() -> None:
    config.get_metadata_store().execute("SET LOCAL lock_timeout = '5s'")


def _lock() -> None:
    config.get_metadata_store().fetch_one(_LOCK_SQL, (SETUP_LOCK_KEY1, 1))


def _insert_row(user_id: str) -> bool:
    """Insert the singleton row; ``False`` when it already exists."""
    row = config.get_metadata_store().fetch_one(
        "INSERT INTO instance_setup (id, admin_user_id) VALUES (1, %s) "
        "ON CONFLICT (id) DO NOTHING RETURNING id",
        (user_id,),
    )
    return row is not None


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def evaluate_boot_state() -> BootState:
    """Decide the boot state once; the gate calls this after the bootstrap step."""
    global _state, _claimed
    from auth import auth_enabled

    state = _compute_boot_state(auth_enabled())
    _state = state
    _claimed = False
    return state


def _compute_boot_state(enabled: bool) -> BootState:
    if not enabled or users_svc.count_users() > 0:
        return BootState.NOT_APPLICABLE
    # No users and sign-in is on. ``bootstrap_if_empty`` ran before the gate, so
    # any bootstrap setting that still left no user means it failed (an email
    # alone, or a password without an email). Setup must not open then: the
    # operator meant to create the admin from the environment.
    if users_svc.bootstrap_env_intended():
        return BootState.BLOCKED_BOOTSTRAP_FAILED
    if not _table_exists():
        return BootState.BLOCKED_NO_MIGRATION
    if _row_exists():
        return BootState.BLOCKED_SETUP_DONE_NO_USERS
    # Legacy ``default`` rows do not close setup: the claim's remap moves them
    # to the new admin, and counting them would brick a restart before the claim.
    return BootState.OPEN


def is_open() -> bool:
    return _state is BootState.OPEN and not _claimed


def _reset_for_tests(state: BootState = BootState.NOT_APPLICABLE, *, claimed: bool = False) -> None:
    global _state, _claimed, _before_insert_hook
    _state = state
    _claimed = claimed
    _before_insert_hook = None


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------


def validate_claim(email: str, password: str) -> None:
    """Raise ``ValueError`` / ``PasswordPolicyViolationError`` for bad input.

    Separate from :func:`claim` so a caller can map exactly these two errors to
    422 and let anything raised inside the transaction propagate.
    """
    users_svc.validate_user_email(email)
    users_svc.validate_password_policy(password)


def claim(email: str, password: str) -> InternalUser:
    """Create the first admin.

    The route calls :func:`validate_claim` first so it can map exactly those
    two errors to 422; it runs again here, before any transaction, so a direct
    caller cannot reach ``create_user`` with weak input.
    """
    global _claimed
    if not is_open():
        raise SetupNotOpen("setup is not open")
    validate_claim(email, password)

    ms = config.get_metadata_store()
    with ms.transaction():
        _set_lock_timeout()
        _lock()
        if _row_exists() or users_svc.count_users() > 0:
            # The database is the authority: setup is closed from now on.
            _claimed = True
            raise SetupNotOpen("setup was completed by another request")
        user = users_svc.create_user(email, password, role="admin", allows_shared=True)
        if _before_insert_hook is not None:
            _before_insert_hook()
        if not _insert_row(user.id):
            # The database says setup is closed: close the flag too, so /status
            # stops answering needs_setup (the transaction still rolls back).
            _claimed = True
            raise SetupNotOpen("another claim won the singleton row")

    _claimed = True
    _log.info("first admin created by setup (user_id=%s)", user.id)
    return user


# ---------------------------------------------------------------------------
# Reminder (log banner + scheduler job)
# ---------------------------------------------------------------------------


def log_claim_banner() -> None:
    _log.warning(
        "\n"
        "[setup] ==================================================================\n"
        "[setup] Lumogis is waiting for its admin account.\n"
        "[setup] Open Lumogis in your browser now and create it.\n"
        "[setup] Until you do, anyone who can reach this computer can create it.\n"
        "[setup] =================================================================="
    )


def remind_if_open() -> None:
    """Scheduler job: repeat the banner while open; remove the job once closed."""
    if is_open():
        log_claim_banner()
        return
    remove_reminder_job()


def remove_reminder_job() -> None:
    """Remove the reminder job; a missing job is not an error."""
    try:
        config.get_scheduler().remove_job(REMINDER_JOB_ID)
    except KeyError:  # apscheduler's JobLookupError is a KeyError
        pass
