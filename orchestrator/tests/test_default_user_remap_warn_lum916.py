# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-916: the remap WARNs only for real leftovers, never on the documented switch.

On the switch to sign-in (``AUTH_ENABLED=true`` plus the bootstrap admin env),
the entrypoint runs ``db_default_user_remap`` before uvicorn, while ``users``
is still empty: the orchestrator creates the admin later and runs the remap a
second time. The first pass used to log "rows remain unattributed" although
the second pass then remapped everything.
"""

from __future__ import annotations

import logging
import re

import db_default_user_remap as remap
import pytest

# conftest's autouse fixture stubs ``remap.main`` for every test (the lifespan
# calls it). Bound at import, before any fixture runs, this is the real one.
_main = remap.main


class _FakeDb:
    """Just enough SQL for the remap: users, information_schema, COUNT, UPDATE."""

    def __init__(self, *, users: list[tuple[str, str]], default_rows: dict[str, int]):
        self.users = users  # (id, email)
        self.default_rows = dict(default_rows)
        self.updates: list[tuple[str, str]] = []

    def cursor(self):
        return _FakeCursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeCursor:
    def __init__(self, db: _FakeDb):
        self.db = db
        self._row = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params: tuple = ()):
        sql = " ".join(sql.split())
        self._row = None
        if "information_schema.tables" in sql:
            name = params[0] if params else "users"
            if name == "users" or name in self.db.default_rows:
                self._row = (1,)
        elif "information_schema.columns" in sql:
            if params[0] in self.db.default_rows:
                self._row = (1,)
        elif sql.startswith("SELECT 1 FROM users"):
            self._row = (1,) if self.db.users else None
        elif sql.startswith("SELECT id FROM users"):
            for uid, email in self.db.users:
                if email.lower() == params[0].lower():
                    self._row = (uid,)
        elif sql.startswith("SELECT COUNT(*)"):
            table = re.search(r"FROM (\w+)", sql).group(1)
            self._row = (self.db.default_rows.get(table, 0),)
        elif sql.startswith("UPDATE"):
            table = re.search(r"UPDATE (\w+)", sql).group(1)
            self.rowcount = self.db.default_rows.get(table, 0)
            self.db.default_rows[table] = 0
            self.db.updates.append((table, params[0]))
        else:  # pragma: no cover — a new query must be taught to the fake
            raise AssertionError(f"unexpected SQL: {sql}")

    def fetchone(self):
        return self._row


@pytest.fixture
def fake_db(monkeypatch):
    holder: dict = {}

    def install(db: _FakeDb) -> _FakeDb:
        holder["db"] = db
        return db

    monkeypatch.setattr(remap, "_wait_for_postgres", lambda: None)
    monkeypatch.setattr(remap, "_conn", lambda: holder["db"])
    monkeypatch.delenv("INBOX_OWNER_USER_ID", raising=False)
    monkeypatch.delenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    return install


def _warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "db_default_user_remap" and r.levelno >= logging.WARNING
    ]


def test_documented_switch_logs_no_warning(fake_db, monkeypatch, caplog):
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "admin@home.lan")
    db = fake_db(_FakeDb(users=[], default_rows={"notes": 4, "sessions": 2}))
    caplog.set_level(logging.INFO, logger="db_default_user_remap")

    # Pass 1 — entrypoint, before the orchestrator has created the admin.
    assert _main() == 0
    assert db.updates == []

    # The orchestrator creates the bootstrap admin, then runs pass 2.
    db.users.append(("admin-uuid", "admin@home.lan"))
    assert _main(post_bootstrap=True) == 0
    assert db.default_rows == {"notes": 0, "sessions": 0}
    assert ("notes", "admin-uuid") in db.updates

    assert _warnings(caplog) == []


def test_later_boot_without_bootstrap_env_logs_no_warning(fake_db, caplog):
    """After the switch the docs let the operator drop the bootstrap env."""
    fake_db(_FakeDb(users=[("admin-uuid", "admin@home.lan")], default_rows={"notes": 0}))
    caplog.set_level(logging.INFO, logger="db_default_user_remap")
    assert _main() == 0
    assert _main(post_bootstrap=True) == 0
    assert _warnings(caplog) == []


def test_real_leftover_still_warns(fake_db, caplog):
    fake_db(_FakeDb(users=[("admin-uuid", "admin@home.lan")], default_rows={"notes": 3}))
    assert _main() == 2
    warns = _warnings(caplog)
    assert any("3 legacy 'default'-user rows remain unattributed" in w for w in warns)


def test_bootstrap_admin_never_created_warns_on_second_pass(fake_db, monkeypatch, caplog):
    """A too-short bootstrap password leaves users empty: pass 2 must still warn."""
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "admin@home.lan")
    fake_db(_FakeDb(users=[], default_rows={"notes": 1}))
    assert _main() == 0
    assert _warnings(caplog) == []
    assert _main(post_bootstrap=True) == 2
    assert any("remain unattributed" in w for w in _warnings(caplog))


def test_bootstrap_email_matching_nobody_still_warns(fake_db, monkeypatch, caplog):
    """A typo in the email on an install that already has users is a real problem."""
    monkeypatch.setenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", "typo@home.lan")
    fake_db(_FakeDb(users=[("admin-uuid", "admin@home.lan")], default_rows={"notes": 1}))
    assert _main() == 2
    warns = _warnings(caplog)
    assert any("no enabled user with that email" in w for w in warns)
    assert any("remain unattributed" in w for w in warns)
