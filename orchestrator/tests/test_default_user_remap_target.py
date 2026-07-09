# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for the default-user remap target resolution (LUM-473 Chunk A).

The native single→multi (auth-on) flip relies on ``db_default_user_remap`` to
move the existing ``user_id='default'`` rows onto the new admin. The supervisor
must therefore NOT leave ``INBOX_OWNER_USER_ID=default`` in the Core env when
auth is on: that value is the **highest-precedence** resolver and would make the
remap a silent no-op, permanently stranding the admin's pre-sharing library.

These tests pin that precedence contract so the fix in
``apps/lumogis-server/.../env.rs`` (omit the ``default`` owner vars when
``auth_on``) cannot regress unnoticed.
"""

from __future__ import annotations

import db_default_user_remap as remap


class _FakeCursor:
    def __init__(self, results: list):
        self._results = results

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):  # noqa: D401 — queries are irrelevant here
        return None

    def fetchone(self):
        return self._results.pop(0) if self._results else None


class _FakeConn:
    """Returns queued ``fetchone`` rows across successive cursor contexts."""

    def __init__(self, results: list):
        self._results = results

    def cursor(self):
        return _FakeCursor(self._results)


def test_inbox_owner_default_ignored_when_auth_enabled(monkeypatch):
    """INBOX_OWNER_USER_ID=default must not no-op the remap when auth is on.

    Previously resolution returned ``'default'`` immediately, so the remap ran
    ``UPDATE ... SET user_id='default' WHERE user_id='default'`` (no-op) and
    stranded the legacy library. With AUTH_ENABLED=true the sentinel is ignored
    and resolution falls through to the bootstrap admin email.
    """
    monkeypatch.setenv("INBOX_OWNER_USER_ID", "default")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "admin@home.lan")
    conn = _FakeConn([(1,), ("admin-uuid-123",)])
    assert remap._resolve_target_user_id(conn) == "admin-uuid-123"


def test_falls_through_to_admin_email_when_inbox_unset(monkeypatch):
    """The auth-on env build: INBOX_OWNER_USER_ID unset → resolve via the admin email.

    Returns the admin's real UUID (not the ``default`` sentinel), so the remap
    moves the legacy library onto the admin.
    """
    monkeypatch.delenv("INBOX_OWNER_USER_ID", raising=False)
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "admin@home.lan")
    # 1st fetchone → users table exists; 2nd fetchone → the admin row id.
    conn = _FakeConn([(1,), ("admin-uuid-123",)])
    assert remap._resolve_target_user_id(conn) == "admin-uuid-123"


def test_empty_inbox_owner_is_ignored(monkeypatch):
    """An empty INBOX_OWNER_USER_ID must not short-circuit (whitespace/blank guard)."""
    monkeypatch.setenv("INBOX_OWNER_USER_ID", "   ")
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "admin@home.lan")
    conn = _FakeConn([(1,), ("admin-uuid-123",)])
    assert remap._resolve_target_user_id(conn) == "admin-uuid-123"


def test_first_auth_boot_remap_fails_until_admin_exists(monkeypatch):
    """Pre-uvicorn remap races bootstrap; post-bootstrap pass can resolve the admin.

    LUM-473 household-sharing enable: launcher remap runs before the admin row
    exists, so ``_resolve_target_user_id`` returns ``None``. After
    ``bootstrap_if_empty`` creates the admin, the same resolver succeeds.
    """
    monkeypatch.delenv("INBOX_OWNER_USER_ID", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "admin@home.lan")
    # Pre-bootstrap: users table exists but admin row not seeded yet.
    pre_boot = _FakeConn([(1,), None])
    assert remap._resolve_target_user_id(pre_boot) is None
    # Post-bootstrap: admin row present → remap can target it.
    post_boot = _FakeConn([(1,), ("admin-uuid-123",)])
    assert remap._resolve_target_user_id(post_boot) == "admin-uuid-123"


def test_post_bootstrap_remap_helper_invokes_script(monkeypatch):
    """``main._run_post_bootstrap_default_user_remap`` delegates to db_default_user_remap."""
    import db_default_user_remap

    calls: list[int] = []

    def fake_main():
        calls.append(1)
        return 0

    monkeypatch.setattr(db_default_user_remap, "main", fake_main)
    import main

    main._run_post_bootstrap_default_user_remap()
    assert calls == [1]
