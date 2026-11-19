# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""First-admin setup: boot state and claim ordering (LUM-981, U1-U12).

Unit tests with the module's five SQL helpers and ``users_svc.count_users`` /
``create_user`` replaced. The store's ``transaction()`` is a no-op here, so
**rollback is proven only live** (``integration/test_instance_setup_live.py``,
L6). Route behaviour lives in ``test_setup_routes.py``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
import services.instance_setup as setup
import services.users as users_svc
from services.instance_setup import BootState
from services.instance_setup import SetupNotOpen
from services.users import PasswordPolicyViolationError

import config


class JobLookupError(KeyError):
    """Stands in for ``apscheduler.jobstores.base.JobLookupError`` (a ``KeyError``).

    apscheduler is not installed in the unit-test venv (conftest stubs it).
    """


GOOD_EMAIL = "admin@example.com"
GOOD_PASSWORD = "correct-horse-battery"


@pytest.fixture(autouse=True)
def _isolated_state():
    setup._reset_for_tests()
    yield
    setup._reset_for_tests()


class _NoopTransactionStore:
    """Only ``transaction()``: a no-op context, counted."""

    def __init__(self) -> None:
        self.entered = 0

    @contextmanager
    def transaction(self):
        self.entered += 1
        yield


@pytest.fixture
def store():
    s = _NoopTransactionStore()
    config._instances["metadata_store"] = s
    yield s
    config._instances.pop("metadata_store", None)


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", raising=False)


def _boot(monkeypatch, *, users=0, table=True, row=False) -> BootState:
    monkeypatch.setattr(users_svc, "count_users", lambda: users)
    monkeypatch.setattr(setup, "_table_exists", lambda: table)
    monkeypatch.setattr(setup, "_row_exists", lambda: row)
    return setup.evaluate_boot_state()


# --- boot state ------------------------------------------------------------


def test_u1_boot_state_open_on_empty_install(auth_on, monkeypatch):
    assert _boot(monkeypatch) is BootState.OPEN
    assert setup.is_open() is True


def test_u2_boot_state_no_migration(auth_on, monkeypatch):
    assert _boot(monkeypatch, table=False) is BootState.BLOCKED_NO_MIGRATION
    assert setup.is_open() is False


@pytest.mark.parametrize(
    "name", ["LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD", "LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD_FILE"]
)
def test_u3b_a_bootstrap_password_without_email_keeps_setup_closed(auth_on, monkeypatch, name):
    """Code review: a scripted install that forgot the email must not open setup."""
    monkeypatch.delenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", raising=False)
    monkeypatch.setenv(name, "/run/secrets/x" if name.endswith("FILE") else "correct-horse-battery")
    assert _boot(monkeypatch) is BootState.BLOCKED_BOOTSTRAP_FAILED
    assert setup.is_open() is False


def test_u3_boot_state_bootstrap_failed(auth_on, monkeypatch):
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "a@example.com")
    assert _boot(monkeypatch) is BootState.BLOCKED_BOOTSTRAP_FAILED
    assert setup.is_open() is False


def test_u4_boot_state_not_applicable_when_auth_off(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")

    def _no_db(*_a, **_k):
        raise AssertionError("auth off must not touch the database")

    monkeypatch.setattr(users_svc, "count_users", _no_db)
    monkeypatch.setattr(setup, "_table_exists", _no_db)
    assert setup.evaluate_boot_state() is BootState.NOT_APPLICABLE
    assert setup.is_open() is False


def test_u5_boot_state_not_applicable_when_users_exist(auth_on, monkeypatch):
    assert _boot(monkeypatch, users=1) is BootState.NOT_APPLICABLE
    assert setup.is_open() is False


def test_u6_boot_state_setup_done_no_users_stays_closed(auth_on, monkeypatch):
    assert _boot(monkeypatch, row=True) is BootState.BLOCKED_SETUP_DONE_NO_USERS
    assert setup.is_open() is False


# --- claim -----------------------------------------------------------------


def _wire_claim(monkeypatch, *, users=0, row=False, inserted=True):
    """Patch the SQL helpers and user service onto one parent so call order is visible."""
    parent = MagicMock()
    parent.set_lock_timeout.side_effect = lambda: None
    parent.lock.side_effect = lambda: None
    parent.row_exists.side_effect = lambda: row
    parent.count_users.side_effect = lambda: users
    parent.create_user.side_effect = lambda *a, **k: MagicMock(id="admin-1")
    parent.insert_row.side_effect = lambda uid: inserted
    monkeypatch.setattr(setup, "_set_lock_timeout", parent.set_lock_timeout)
    monkeypatch.setattr(setup, "_lock", parent.lock)
    monkeypatch.setattr(setup, "_row_exists", parent.row_exists)
    monkeypatch.setattr(setup, "_insert_row", parent.insert_row)
    monkeypatch.setattr(users_svc, "count_users", parent.count_users)
    monkeypatch.setattr(users_svc, "create_user", parent.create_user)
    return parent


def _names(parent) -> list[str]:
    return [c[0] for c in parent.mock_calls]


def test_u7_claim_creates_admin_and_row_in_order(store, monkeypatch):
    setup._reset_for_tests(BootState.OPEN)
    parent = _wire_claim(monkeypatch)

    user = setup.claim(GOOD_EMAIL, GOOD_PASSWORD)

    assert user.id == "admin-1"
    parent.create_user.assert_called_once_with(
        GOOD_EMAIL, GOOD_PASSWORD, role="admin", allows_shared=True
    )
    parent.insert_row.assert_called_once_with("admin-1")
    assert _names(parent) == [
        "set_lock_timeout",
        "lock",
        "row_exists",
        "count_users",
        "create_user",
        "insert_row",
    ]
    assert store.entered == 1
    assert setup.is_open() is False


def test_u8_claim_refuses_when_not_open_and_runs_no_sql(store, monkeypatch):
    setup._reset_for_tests(BootState.NOT_APPLICABLE)
    parent = _wire_claim(monkeypatch)
    with pytest.raises(SetupNotOpen):
        setup.claim(GOOD_EMAIL, GOOD_PASSWORD)
    assert parent.mock_calls == []
    assert store.entered == 0


def test_u9_claim_validates_before_the_transaction(store, monkeypatch):
    setup._reset_for_tests(BootState.OPEN)
    parent = _wire_claim(monkeypatch)
    with pytest.raises(PasswordPolicyViolationError):
        setup.claim(GOOD_EMAIL, "short")
    assert store.entered == 0
    assert parent.mock_calls == []
    assert setup.is_open() is True


def test_u10_claim_conflict_raises_and_closes_the_flag(store, monkeypatch):
    """The INSERT found the singleton row: SetupNotOpen leaves the ``with`` block.

    Rollback itself is proven live (L6): this store's ``transaction()`` is a no-op.
    """
    setup._reset_for_tests(BootState.OPEN)
    _wire_claim(monkeypatch, inserted=False)
    with pytest.raises(SetupNotOpen):
        setup.claim(GOOD_EMAIL, GOOD_PASSWORD)
    assert store.entered == 1
    assert setup.is_open() is False  # the database says closed (code review)


def test_u12_recheck_closes_the_flag(store, monkeypatch):
    setup._reset_for_tests(BootState.OPEN)
    parent = _wire_claim(monkeypatch, users=1)
    with pytest.raises(SetupNotOpen):
        setup.claim(GOOD_EMAIL, GOOD_PASSWORD)
    assert setup.is_open() is False
    assert "create_user" not in _names(parent)
    assert "insert_row" not in _names(parent)


def test_u12_recheck_on_existing_row_closes_the_flag(store, monkeypatch):
    setup._reset_for_tests(BootState.OPEN)
    parent = _wire_claim(monkeypatch, row=True)
    with pytest.raises(SetupNotOpen):
        setup.claim(GOOD_EMAIL, GOOD_PASSWORD)
    assert setup.is_open() is False
    assert "create_user" not in _names(parent)


# --- reminder --------------------------------------------------------------


def test_u11_remind_if_open_logs_the_banner_while_open(monkeypatch, caplog):
    setup._reset_for_tests(BootState.OPEN)
    scheduler = MagicMock()
    monkeypatch.setattr(config, "get_scheduler", lambda: scheduler)
    with caplog.at_level(logging.WARNING, logger=setup.__name__):
        setup.remind_if_open()
    assert "Lumogis is waiting for its admin account" in caplog.text
    scheduler.remove_job.assert_not_called()


def test_u11_remind_if_open_removes_its_job_once_closed(monkeypatch, caplog):
    setup._reset_for_tests(BootState.OPEN, claimed=True)
    scheduler = MagicMock()
    monkeypatch.setattr(config, "get_scheduler", lambda: scheduler)
    with caplog.at_level(logging.WARNING, logger=setup.__name__):
        setup.remind_if_open()
    scheduler.remove_job.assert_called_once_with("setup_claim_reminder")
    assert "waiting for its admin" not in caplog.text


def test_u11_a_missing_job_is_swallowed(monkeypatch):
    setup._reset_for_tests(BootState.NOT_APPLICABLE)
    scheduler = MagicMock()
    scheduler.remove_job.side_effect = JobLookupError("setup_claim_reminder")
    monkeypatch.setattr(config, "get_scheduler", lambda: scheduler)
    setup.remind_if_open()  # no raise
