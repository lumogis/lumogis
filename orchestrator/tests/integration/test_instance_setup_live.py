# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""First-admin setup against real Postgres (LUM-981, L1-L8).

Each test gets a scratch database (``tests/_fresh_db.fresh_db``), runs the real
migration runner against it, and points ``config``'s metadata store at it. Both
claim threads share that store; ``transaction()`` binds a connection per thread.

The single-admin guarantee is proven here, not in the browser (vacuous-test
principle): L1/L2 race real transactions, L3 removes the advisory lock and shows
the singleton primary key alone still gives exactly one admin, L6 shows the
rollback of the user when the singleton row turns out to exist.

``LUMOGIS_REQUIRE_INTEGRATION_DB=1`` turns a skip into a failure.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import db_default_user_remap as remap
import db_migrations
import psycopg2
import pytest
import services.instance_setup as setup
from services.instance_setup import BootState
from services.instance_setup import SetupNotOpen
from tests._fresh_db import fresh_db  # noqa: F401 — fixture
from tests._fresh_db import superuser  # noqa: F401 — fixture

import config

# conftest's autouse fixture stubs ``remap.main`` for every test; bound at import,
# before any fixture runs, this is the real one.
_REAL_REMAP_MAIN = remap.main

_MIGRATIONS = Path(__file__).resolve().parents[3] / "postgres" / "migrations"
PASSWORD = "correct-horse-battery"


@pytest.fixture
def live(fresh_db, monkeypatch):  # noqa: F811
    """A scratch database with every migration applied, wired in as the store."""
    from adapters.postgres_store import PostgresStore

    name = fresh_db()
    monkeypatch.setenv("POSTGRES_DB", name)
    monkeypatch.setenv("LUMOGIS_MIGRATIONS_DIR", str(_MIGRATIONS))
    monkeypatch.delenv("LUMOGIS_BASELINE_SQL", raising=False)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LUMOGIS_BOOTSTRAP_ADMIN_EMAIL", raising=False)
    assert db_migrations.main([]) == 0

    store = PostgresStore(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "lumogis"),
        password=os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        dbname=name,
    )
    monkeypatch.setitem(config._instances, "metadata_store", store)
    setup._reset_for_tests(BootState.OPEN)
    yield store
    setup._reset_for_tests()
    store.close()


def _admins(ms) -> int:
    return ms.fetch_one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'")["n"]


def _setup_rows(ms) -> int:
    return ms.fetch_one("SELECT COUNT(*) AS n FROM instance_setup")["n"]


def _race_two_claims(monkeypatch):
    """A holds its transaction open (after create_user, before the INSERT); B starts then.

    Returns ``(outcome_a, outcome_b)``: a user, or the exception raised.
    """
    a_in_txn = threading.Event()
    state = {"first": True}
    guard = threading.Lock()

    def hook():
        with guard:
            mine, state["first"] = state["first"], False
        if mine:
            a_in_txn.set()
            time.sleep(0.5)

    monkeypatch.setattr(setup, "_before_insert_hook", hook)
    out: dict[str, object] = {}

    def go(key: str, email: str) -> None:
        try:
            out[key] = setup.claim(email, PASSWORD)
        except Exception as exc:  # noqa: BLE001 — the outcome is what the test asserts
            out[key] = exc

    ta = threading.Thread(target=go, args=("a", "first@example.com"))
    tb = threading.Thread(target=go, args=("b", "second@example.com"))
    ta.start()
    assert a_in_txn.wait(10)
    tb.start()
    ta.join(30)
    tb.join(30)
    return out["a"], out["b"]


def test_l1_two_concurrent_claims_create_one_admin(live, monkeypatch):
    a, b = _race_two_claims(monkeypatch)
    assert not isinstance(a, Exception), a
    assert isinstance(b, SetupNotOpen), b
    assert _admins(live) == 1
    assert _setup_rows(live) == 1
    row = live.fetch_one("SELECT admin_user_id FROM instance_setup WHERE id = 1")
    assert row["admin_user_id"] == a.id


def test_l2_ten_concurrent_claims_create_one_admin(live):
    barrier = threading.Barrier(10)
    out: list[object] = []
    guard = threading.Lock()

    def go(i: int) -> None:
        barrier.wait(10)
        try:
            res: object = setup.claim(f"admin{i}@example.com", PASSWORD)
        except Exception as exc:  # noqa: BLE001
            res = exc
        with guard:
            out.append(res)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    winners = [r for r in out if not isinstance(r, Exception)]
    losers = [r for r in out if isinstance(r, Exception)]
    assert len(out) == 10
    assert len(winners) == 1
    assert all(isinstance(r, SetupNotOpen) for r in losers), losers
    assert _admins(live) == 1
    assert _setup_rows(live) == 1


def test_l3_without_the_lock_the_primary_key_still_gives_one_admin(live, monkeypatch):
    """Negative control: the advisory lock is a no-op. The singleton PK is the guarantee;
    the lock only turns the loser's failure into a clean 409."""
    monkeypatch.setattr(setup, "_LOCK_SQL", "SELECT %s::integer, %s::integer")
    a, b = _race_two_claims(monkeypatch)
    # Without the lock B does not wait for A: B can commit first and A then loses at
    # the singleton INSERT. Either order is fine; exactly one claim wins.
    winners = [r for r in (a, b) if not isinstance(r, Exception)]
    losers = [r for r in (a, b) if isinstance(r, Exception)]
    assert len(winners) == 1, (a, b)
    assert isinstance(losers[0], (SetupNotOpen, psycopg2.IntegrityError)), losers[0]
    assert _admins(live) == 1
    assert _setup_rows(live) == 1
    assert (
        live.fetch_one("SELECT COUNT(*) AS n FROM users")["n"] == 1
    )  # the loser's user rolled back
    row = live.fetch_one("SELECT admin_user_id FROM instance_setup WHERE id = 1")
    assert row["admin_user_id"] == winners[0].id


def test_l4_the_row_survives_deleting_the_admin_and_setup_stays_closed(live):
    user = setup.claim("admin@example.com", PASSWORD)
    live.execute("DELETE FROM users WHERE id = %s", (user.id,))
    row = live.fetch_one("SELECT admin_user_id FROM instance_setup WHERE id = 1")
    assert row is not None and row["admin_user_id"] is None
    assert setup.evaluate_boot_state() is BootState.BLOCKED_SETUP_DONE_NO_USERS
    assert setup.is_open() is False


def test_l5_migration_085_applies_once_and_is_idempotent(live):
    assert db_migrations.main([]) == 0  # the fixture ran it once already
    rows = live.fetch_all(
        "SELECT filename FROM schema_migrations WHERE filename = %s",
        ("085-instance-setup.sql",),
    )
    assert len(rows) == 1
    assert live.fetch_one("SELECT to_regclass('public.instance_setup') AS t")["t"] is not None


def test_l6_a_lost_insert_rolls_back_the_new_user(live, monkeypatch):
    """Another claim committed between the re-check and the INSERT: the user this
    claim created must not survive. (The unit store's transaction() is a no-op, so
    only a real database can show this.)"""
    live.execute("INSERT INTO instance_setup (id, admin_user_id) VALUES (1, NULL)")
    monkeypatch.setattr(setup, "_row_exists", lambda: False)  # the re-check misses it
    with pytest.raises(SetupNotOpen):
        setup.claim("late@example.com", PASSWORD)
    assert live.fetch_one("SELECT COUNT(*) AS n FROM users")["n"] == 0
    assert _setup_rows(live) == 1


def _insert_default_rows(ms) -> None:
    ms.execute(
        "INSERT INTO audit_log (user_id, action_name, connector, mode) "
        "VALUES ('default', 'x', 'y', 'DO')"
    )
    ms.execute(
        "INSERT INTO action_log (connector, action_type, mode, user_id) "
        "VALUES ('y', 'x', 'DO', 'default')"
    )
    ms.execute(
        "INSERT INTO auth_sessions (id, user_id, family_id, refresh_token_hash, expires_at, "
        "device_label, ip_hash, ua_hash) VALUES (%s, 'default', 'f', %s, now() + interval '1 day', "
        "'d', %s, %s)",
        ("s" * 32, "a" * 64, "b" * 64, "c" * 64),
    )


def test_l7_the_claim_moves_default_rows_to_the_new_admin(live, monkeypatch):
    live.execute(
        "INSERT INTO audit_log (user_id, action_name, connector, mode) "
        "VALUES ('default', 'x', 'y', 'DO')"
    )
    assert setup.evaluate_boot_state() is BootState.OPEN  # default rows do not close setup

    user = setup.claim("admin@example.com", PASSWORD)

    results: list[int] = []

    def real_remap(**kwargs):
        rc = _REAL_REMAP_MAIN(**kwargs)
        results.append(rc)
        return rc

    monkeypatch.setattr(remap, "main", real_remap)
    import routes.setup as setup_routes

    setup_routes._remap_after_claim()

    assert results == [0]
    row = live.fetch_one("SELECT user_id FROM audit_log")
    assert row["user_id"] == user.id
    assert live.fetch_one("SELECT COUNT(*) AS n FROM audit_log WHERE user_id = 'default'")["n"] == 0


def test_l8_default_rows_do_not_close_setup_across_a_restart(live):
    """The restart-before-claim case: background work wrote audit, action and session
    rows under ``default`` while setup waited. A "holds data" refusal would brick it."""
    _insert_default_rows(live)
    assert setup.evaluate_boot_state() is BootState.OPEN
    assert setup.is_open() is True
    # and the remap, run before the claim, waits instead of warning (exit 0)
    assert _REAL_REMAP_MAIN(post_bootstrap=True) == 0
