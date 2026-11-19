# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Regression tests for the default-user remap target resolution (LUM-473 Chunk A).

The native single→multi (auth-on) flip relies on ``db_default_user_remap`` to
move the existing ``user_id='default'`` rows onto the new admin. The supervisor
must therefore NOT leave ``INBOX_OWNER_USER_ID=default`` in the Core env when
auth is on: that value is the **highest-precedence** resolver and would make the
remap a silent no-op, permanently stranding the admin's pre-sharing library.

These tests pin that precedence contract so the fix in
the appliance's ``env.rs`` (omit the ``default`` owner vars when
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

    calls: list[dict] = []

    def fake_main(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(db_default_user_remap, "main", fake_main)
    import main

    main._run_post_bootstrap_default_user_remap()
    # LUM-916: the second pass identifies itself so it, not the pre-uvicorn
    # pass, is the one that warns when the bootstrap admin never appeared.
    assert calls == [{"post_bootstrap": True}]


# --- LUM-981 (M1): the admin created at first-visitor setup is a target tier ----


class _RecordingCursor(_FakeCursor):
    def __init__(self, results: list, log: list[str]):
        super().__init__(results)
        self._log = log

    def execute(self, sql, *args, **kwargs):  # noqa: D401
        self._log.append(" ".join(str(sql).split()))


class _RecordingConn(_FakeConn):
    def __init__(self, results: list):
        super().__init__(results)
        self.sql: list[str] = []

    def cursor(self):
        return _RecordingCursor(self._results, self.sql)


def _setup_env(monkeypatch):
    monkeypatch.delenv("INBOX_OWNER_USER_ID", raising=False)
    monkeypatch.delenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "true")


def test_m1_instance_setup_admin_is_the_target_with_no_env_tiers(monkeypatch):
    _setup_env(monkeypatch)
    conn = _RecordingConn([("instance_setup",), ("setup-admin-id",)])
    assert remap._resolve_target_user_id(conn) == "setup-admin-id"
    assert "to_regclass('public.instance_setup')" in conn.sql[0]
    assert "NOT u.disabled" in conn.sql[1]


def test_m1_a_disabled_setup_admin_is_not_a_target(monkeypatch):
    _setup_env(monkeypatch)
    # the guard finds the table; the join (which excludes disabled users) finds nobody
    conn = _RecordingConn([("instance_setup",), None])
    assert remap._resolve_target_user_id(conn) is None


def test_m1_inbox_owner_still_wins(monkeypatch):
    _setup_env(monkeypatch)
    monkeypatch.setenv("INBOX_OWNER_USER_ID", "explicit-owner")
    conn = _RecordingConn([("instance_setup",), ("setup-admin-id",)])
    assert remap._resolve_target_user_id(conn) == "explicit-owner"
    assert conn.sql == []


def test_m1_the_bootstrap_email_tier_wins_over_the_setup_admin(monkeypatch):
    _setup_env(monkeypatch)
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "admin@home.lan")
    conn = _RecordingConn([(1,), ("env-admin-id",), ("instance_setup",), ("setup-admin-id",)])
    assert remap._resolve_target_user_id(conn) == "env-admin-id"


def test_m1_a_missing_table_is_not_an_error(monkeypatch):
    """The remap also runs on databases where migration 085 has not applied yet."""
    _setup_env(monkeypatch)
    conn = _RecordingConn([(None,)])
    assert remap._resolve_target_user_id(conn) is None
    assert len(conn.sql) == 1  # only the to_regclass guard ran; no query on the missing table


def test_m1_not_used_when_sign_in_is_off(monkeypatch):
    _setup_env(monkeypatch)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    conn = _RecordingConn([(1,), None])  # users table exists, users is empty -> dev no-op
    assert remap._resolve_target_user_id(conn) == "__DEV_NOOP__"
    assert not any("instance_setup" in q for q in conn.sql)


def test_post_bootstrap_remap_helper_survives_sys_exit(monkeypatch):
    """``_wait_for_postgres()`` ends in ``sys.exit(1)``; the lifespan must not die of it."""
    import db_default_user_remap

    def exits(**_kwargs):
        raise SystemExit(1)

    monkeypatch.setattr(db_default_user_remap, "main", exits)
    import main

    main._run_post_bootstrap_default_user_remap()  # no raise


def test_run_post_bootstrap_safely_warns_on_rc2_and_never_raises(monkeypatch, caplog):
    """Code review (LUM-981): the post-claim remap used to drop rc 2 silently."""
    import logging

    import db_default_user_remap as r

    caplog.set_level(logging.WARNING, logger="db_default_user_remap")
    monkeypatch.setattr(r, "main", lambda post_bootstrap=False: 2)
    assert r.run_post_bootstrap_safely("after setup claim") == 2
    assert any("after setup claim" in m and "exited 2" in m for m in caplog.messages)

    def _boom(post_bootstrap=False):
        raise SystemExit(1)

    monkeypatch.setattr(r, "main", _boom)
    assert r.run_post_bootstrap_safely("after setup claim") == 1
