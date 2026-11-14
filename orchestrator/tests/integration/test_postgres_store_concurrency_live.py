# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-816 — the shared-connection hazard is gone, proved against real Postgres.

**This file used to assert the opposite.** Written for LUM-65, it proved that
`PostgresStore` shared one connection process-wide and that `transaction()` was
therefore unsafe from any thread: a second concurrent `transaction()` raised,
and — worse — an ordinary `execute()` on another thread was silently *enrolled*
in the open transaction and discarded when it rolled back. Those assertions were
the evidence LUM-816 was filed on.

Inverting them is this chunk's acceptance criterion. Same scenarios, same
threads, opposite expectations: concurrent transactions now succeed
independently, and a bystander's write is never captured by someone else's
transaction.

The tests kept from the old file are the ones about **affinity**, because a pool
introduces a failure the single connection could not have: statements inside
`with ms.transaction():` must reach the transaction's connection and no other.
The port yields nothing from `transaction()` and all 14 callers rely on that, so
a pool without thread-local affinity would send each statement to a different
connection and lose atomicity everywhere, silently, with every fake-backed test
still green.

A fake cannot stand in for any of this — it cannot tell one connection from
another. Requires a reachable Postgres; skips otherwise (fatal in CI under
`LUMOGIS_REQUIRE_INTEGRATION_DB`, LUM-718).
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest
from adapters.postgres_store import PostgresStore
from tests.integration._pgguard import require_postgres

psycopg2 = pytest.importorskip("psycopg2")

_LATCH_TIMEOUT = 10.0


def _conn_kwargs() -> dict:
    return {
        "host": os.environ.get("POSTGRES_HOST", "postgres"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "user": os.environ.get("POSTGRES_USER", "lumogis"),
        "password": os.environ.get("POSTGRES_PASSWORD", "lumogis-dev"),
        "dbname": os.environ.get("POSTGRES_DB", "lumogis"),
    }


@pytest.fixture
def store():
    """A real `PostgresStore` plus a scratch table.

    Teardown goes through the public `close()`. The old fixture reached for
    `st._conn.autocommit` and `st._conn.close()`, which no longer exist — a
    private-attribute grab that would have broken on this chunk regardless of
    whether the behaviour under test still held.
    """
    try:
        st = PostgresStore(**_conn_kwargs())
    except Exception as exc:  # pragma: no cover - environment-dependent
        require_postgres(exc)

    table = f"lum816_txn_probe_{uuid.uuid4().hex[:12]}"
    st.execute(f"CREATE TABLE {table} (tag TEXT PRIMARY KEY)")
    try:
        yield st, table
    finally:
        try:
            st.execute(f"DROP TABLE IF EXISTS {table}")
            st.close()
        except Exception:  # pragma: no cover - best-effort cleanup
            pass


def _committed(table: str) -> set[str]:
    """Read committed state on a connection outside the store entirely."""
    conn = psycopg2.connect(**_conn_kwargs())
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT tag FROM {table}")
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The inversion — what this file used to prove, now disproved
# ---------------------------------------------------------------------------


def test_concurrent_transactions_no_longer_collide(store):
    """Was: the second concurrent `transaction()` raises `RuntimeError`.

    It raised because `transaction()` read `self._conn.autocommit` on the one
    shared connection and refused when it was already False. With a pool each
    thread holds its own connection, so both commit independently.
    """
    st, table = store
    holder_inside = threading.Event()
    release_holder = threading.Event()
    errors: dict[str, BaseException | None] = {"contender": None}

    def holder():
        with st.transaction():
            st.execute(f"INSERT INTO {table} VALUES (%s)", ("holder",))
            holder_inside.set()
            release_holder.wait(_LATCH_TIMEOUT)

    def contender():
        assert holder_inside.wait(_LATCH_TIMEOUT), "holder never opened its transaction"
        try:
            with st.transaction():
                st.execute(f"INSERT INTO {table} VALUES (%s)", ("contender",))
        except BaseException as exc:  # noqa: BLE001 - recorded and asserted below
            errors["contender"] = exc
        finally:
            release_holder.set()

    threads = [threading.Thread(target=holder), threading.Thread(target=contender)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(_LATCH_TIMEOUT * 2)

    assert errors["contender"] is None, (
        f"concurrent transactions must not collide any more; got {errors['contender']!r}"
    )
    assert _committed(table) == {"holder", "contender"}


def test_a_bystanders_write_is_no_longer_swallowed_by_someone_elses_rollback(store):
    """Was: the bystander's write is enrolled in the holder's transaction and lost.

    This was the quiet mode, and the dangerous one — nothing raised, and a
    committed-looking write simply vanished. The bystander now has its own
    connection, so the holder's rollback cannot reach it.
    """
    st, table = store
    holder_inside = threading.Event()
    bystander_wrote = threading.Event()
    observed: dict[str, object] = {"bystander_error": None}

    def holder():
        try:
            with st.transaction():
                st.execute(f"INSERT INTO {table} VALUES (%s)", ("holder",))
                holder_inside.set()
                if not bystander_wrote.wait(_LATCH_TIMEOUT):
                    raise RuntimeError("bystander never wrote")
                raise RuntimeError("request failed after the bystander's write")
        except RuntimeError:
            pass  # the rollback is the point

    def bystander():
        if not holder_inside.wait(_LATCH_TIMEOUT):
            observed["bystander_error"] = RuntimeError("holder never opened its transaction")
            bystander_wrote.set()
            return
        try:
            st.execute(f"INSERT INTO {table} VALUES (%s)", ("bystander",))
        except BaseException as exc:  # noqa: BLE001 - recorded and asserted below
            observed["bystander_error"] = exc
        finally:
            bystander_wrote.set()

    threads = [threading.Thread(target=holder), threading.Thread(target=bystander)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(_LATCH_TIMEOUT * 2)

    assert observed["bystander_error"] is None, (
        f"the bystander's INSERT failed outright: {observed['bystander_error']!r}"
    )
    committed = _committed(table)
    assert "holder" not in committed, "the holder's transaction should have rolled back"
    assert "bystander" in committed, (
        "the bystander's write was lost — it is still being enrolled in another "
        "thread's transaction, which is the exact defect LUM-816 was filed to fix"
    )


# ---------------------------------------------------------------------------
# Affinity — the failure a pool introduces that one connection could not
# ---------------------------------------------------------------------------


def test_statements_inside_a_transaction_use_the_transaction_connection(store):
    """The load-bearing test for the whole chunk.

    `transaction()` yields nothing and every caller writes
    `with ms.transaction(): ms.execute(...)`. Without thread-local affinity
    those statements check out a *different* connection, run outside the
    transaction and autocommit — so a rollback would leave them behind. All 14
    call sites, silently, with fakes still green.
    """
    st, table = store
    try:
        with st.transaction():
            st.execute(f"INSERT INTO {table} VALUES (%s)", ("inside",))
            # Not yet visible elsewhere: it is inside an open transaction.
            assert "inside" not in _committed(table)
            raise RuntimeError("roll it back")
    except RuntimeError:
        pass

    assert _committed(table) == set(), (
        "a statement issued inside the block escaped its transaction — the plain "
        "`execute` did not land on the transaction's connection"
    )


def test_affinity_is_cleared_after_the_block(store):
    """A leaked binding would route later work onto a connection someone else owns.

    The AnyIO threadpool reuses threads, so a request inheriting the previous
    request's affinity is not hypothetical.
    """
    st, table = store
    with st.transaction():
        st.execute(f"INSERT INTO {table} VALUES (%s)", ("in-tx",))
    assert getattr(st._local, "conn", None) is None, "transaction() leaked its binding"

    st.execute(f"INSERT INTO {table} VALUES (%s)", ("after-tx",))
    assert _committed(table) == {"in-tx", "after-tx"}, (
        "a statement after the block did not commit — affinity was not released"
    )


def test_each_thread_gets_its_own_transaction_connection(store):
    """Concurrent transactions must not share a binding."""
    st, table = store
    seen: dict[str, int] = {}
    barrier = threading.Barrier(4, timeout=_LATCH_TIMEOUT)

    def worker(tag: str):
        with st.transaction():
            st.execute(f"INSERT INTO {table} VALUES (%s)", (tag,))
            seen[tag] = id(getattr(st._local, "conn", None))
            barrier.wait()  # hold all four open at once

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(_LATCH_TIMEOUT * 2)

    assert len(set(seen.values())) == 4, f"threads shared a connection: {seen}"
    assert _committed(table) == {"t0", "t1", "t2", "t3"}


def test_nested_transaction_is_a_savepoint_and_the_outer_still_commits(store):
    """Nesting used to raise. It is now a savepoint, and the outer keeps ownership.

    The inner branch must not clear the thread binding: if it did, the outer
    block's remaining statements would land on a different connection — this
    ticket's own bug, inside its own fix.
    """
    st, table = store
    with st.transaction():
        st.execute(f"INSERT INTO {table} VALUES (%s)", ("outer",))
        try:
            with st.transaction():
                st.execute(f"INSERT INTO {table} VALUES (%s)", ("inner",))
                raise RuntimeError("inner fails")
        except RuntimeError:
            pass
        st.execute(f"INSERT INTO {table} VALUES (%s)", ("after-inner",))

    assert _committed(table) == {"outer", "after-inner"}, (
        "the inner savepoint should have rolled back alone, and the statement "
        "after it should still have landed on the outer transaction"
    )


def test_savepoint_is_reachable_on_the_store(store):
    """`services/password_reset.py` probes `getattr(ms, "savepoint", None)`.

    It degrades **silently** to no savepoint when that returns None. `savepoint`
    lived only on the bound-connection handle, so exposing `isolated_transaction` as an
    alias yielding the store would have handed back both LUM-65 P0s — the
    migration-073 guard breaking CLI lockout recovery, and `mint()`'s inert
    retry — without a single test failing.
    """
    st, table = store
    assert getattr(st, "savepoint", None) is not None, "the guards would degrade silently"

    with st.transaction():
        st.execute(f"INSERT INTO {table} VALUES (%s)", ("before",))
        try:
            with st.savepoint():
                st.fetch_all("SELECT 1 FROM a_table_that_does_not_exist")
        except Exception:
            pass  # genuinely optional now
        st.execute(f"INSERT INTO {table} VALUES (%s)", ("after",))

    assert _committed(table) == {"before", "after"}


def test_isolated_transaction_alias_still_works_for_lum65_call_sites(store):
    """`with ms.isolated_transaction() as tx: tx.execute(...)` must keep working.

    The alias yields the store, which is only correct because affinity sends
    `store.execute` to the transaction's connection.
    """
    st, table = store
    with st.isolated_transaction() as tx:
        tx.execute(f"INSERT INTO {table} VALUES (%s)", ("via-alias",))
    assert _committed(table) == {"via-alias"}


# ---------------------------------------------------------------------------
# Advisory locks — outside the pool, deliberately
# ---------------------------------------------------------------------------


def test_advisory_lock_connections_are_not_pooled(store):
    """A lock must not ride a returned connection back into the pool.

    `pg_advisory_lock` is session-scoped: closing a connection releases it,
    returning it to a pool does not. If lock connections were pooled, a leaked
    lock would idle in the free list and every later borrower would inherit it.
    `checkout_connection` therefore opens and closes its own connection.

    Also the reason `document_purge` (hold a lock, then open a transaction
    inside it) does not self-deadlock: its two connections come from different
    places.
    """
    st, _ = store
    key1, key2 = 6816, 1

    with st.checkout_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s::integer, %s::integer)", (key1, key2))
            assert cur.fetchone()[0] is True
        # A transaction opened while the lock is held must still get a connection.
        with st.transaction():
            st.fetch_one("SELECT 1")

    # The lock connection was closed on exit, so the lock is gone — checked from
    # a session that has nothing to do with the store.
    probe = psycopg2.connect(**_conn_kwargs())
    try:
        with probe.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s::integer, %s::integer)", (key1, key2))
            assert cur.fetchone()[0] is True, (
                "the advisory lock outlived its connection — it was returned to a "
                "pool instead of closed, and that connection is now poisoned"
            )
            cur.execute("SELECT pg_advisory_unlock(%s::integer, %s::integer)", (key1, key2))
    finally:
        probe.close()


# ---------------------------------------------------------------------------
# Recovery — what `_ensure_conn` used to do, and what replaced it
# ---------------------------------------------------------------------------


@pytest.fixture
def tiny_store(monkeypatch):
    """A store with exactly one pooled connection, so it can be targeted.

    The default pool is ten; a test that kills "the" connection needs to know
    which one the next checkout returns.
    """
    monkeypatch.setenv("LUMOGIS_PG_POOL_MIN", "1")
    monkeypatch.setenv("LUMOGIS_PG_POOL_MAX", "1")
    try:
        st = PostgresStore(**_conn_kwargs())
    except Exception as exc:  # pragma: no cover - environment-dependent
        require_postgres(exc)
    try:
        yield st
    finally:
        st.close()


def _kill(pid: int) -> None:
    conn = psycopg2.connect(**_conn_kwargs())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
    finally:
        conn.close()


def test_a_dead_pooled_connection_is_healed_rather_than_returned_as_a_500(tiny_store):
    """`_ensure_conn` ran `SELECT 1` before every query and reconnected on failure.

    Deleting it with nothing in its place meant that after a
    `docker compose restart postgres` every pooled connection was dead and every
    request 500ed until each one had been consumed by a failure. The pool cannot
    afford the old probe — it is a round-trip per statement, and the round-trip
    is most of what pooling saves — and it cannot skip it cheaply either:
    `conn.closed` is still 0 on a connection the server has already dropped.

    So the failure *is* the probe. This kills the one pooled backend and asserts
    the next ordinary read succeeds anyway, on a replacement.
    """
    st = tiny_store
    pid = st.fetch_one("SELECT pg_backend_pid() AS pid")["pid"]
    _kill(pid)

    healed = st.fetch_one("SELECT pg_backend_pid() AS pid")
    assert healed is not None, "a dead pooled connection surfaced as an error"
    assert healed["pid"] != pid, "the query somehow ran on the terminated backend"

    # And the pool is not one connection poorer for it.
    assert st.fetch_one("SELECT 1 AS ok")["ok"] == 1


def test_ping_goes_through_the_pool_it_vouches_for(tiny_store):
    """A green health check has to mean *requests* will work.

    `ping()` opened its own connection for a while, which reports healthy
    through exactly the outage it exists to catch: after a restart every pooled
    connection is dead while a brand-new one answers fine, so `/admin/health`
    stayed green while every request 500ed.

    Killing a backend cannot tell the two implementations apart — an ordinary
    read heals itself either way, so both report True. What separates them is
    *which* connection the ping runs on, so the off-pool route is sabotaged
    instead: `_dsn` is the DSN `_connect()` uses, while the pool holds the
    connect arguments it was built with. After this line an off-pool ping cannot
    reach Postgres at all and a pooled one is unaffected.
    """
    st = tiny_store
    assert st.fetch_one("SELECT 1 AS ok")["ok"] == 1  # pool warm, one connection
    st._dsn["port"] = 1

    assert st.ping() is True, "ping opened its own connection instead of using the pool"


# ---------------------------------------------------------------------------
# Savepoint nesting
# ---------------------------------------------------------------------------


def test_the_outer_rollback_reaches_the_outer_savepoint(store):
    """Both savepoints were named `lumogis_sp`, and Postgres resolves the name
    to the **most recent** one — and `ROLLBACK TO` does not destroy it.

    So the outer rollback landed on the inner mark and left everything written
    between them applied. `transaction()` routes every nested block through
    `savepoint()`, so this is one nested `with ms.transaction():` away.

    Measured before the fix: `{'base', 'between'}` survived a rollback that
    should have left `{'base'}`.
    """
    st, table = store

    with st.transaction():
        st.execute(f"INSERT INTO {table} VALUES (%s)", ("base",))
        try:
            with st.transaction():  # outer savepoint
                st.execute(f"INSERT INTO {table} VALUES (%s)", ("between",))
                try:
                    with st.transaction():  # inner savepoint, same depth+1
                        st.execute(f"INSERT INTO {table} VALUES (%s)", ("inner",))
                        raise RuntimeError("inner fails")
                except RuntimeError:
                    pass
                raise RuntimeError("outer fails")
        except RuntimeError:
            pass

    assert _committed(table) == {"base"}, (
        "the outer rollback did not undo the middle block — the savepoint names "
        "collided and it rolled back to the inner mark instead"
    )


def test_isolated_transaction_still_refuses_to_nest(store):
    """Its callers chose it *for* independence; a savepoint is not that.

    `system_mail.drain_outbox` claims each outbox row in one of these because the
    attempt bump has to survive a crash mid-send. Demoted to a savepoint it would
    commit nothing of its own, and an outer rollback would take the bump and the
    post-send DELETE with it — re-mailing an already-delivered reset link. Nothing
    nests today; this keeps it loud if anything ever does.
    """
    st, table = store

    with pytest.raises(RuntimeError, match="cannot nest"):
        with st.transaction():
            with st.isolated_transaction():
                st.execute(f"INSERT INTO {table} VALUES (%s)", ("never",))

    assert _committed(table) == set()


def test_a_forty_thread_burst_does_not_open_forty_connections(monkeypatch):
    """The DoD's `pg_stat_activity` bound, asserted rather than asserted-about.

    ~40 AnyIO threadpool workers serve sync routes, and before the pool each
    statement rode one shared connection. The failure a pool must not introduce
    is the opposite one: a connection per concurrent caller, which is what
    connection-per-request would have cost and what `max_connections` (100)
    bounds globally.

    Counted from a probe session, restricted to backends **this store opened**,
    which carry a per-run ``application_name``. It used to count every backend
    on the database that started after the store did, which also caught any
    connection this test never opened that happened to start inside the window
    — another client of a shared database, a fresh autovacuum worker (it has
    ``datname`` set) — and failed the bound intermittently (LUM-902).

    The tag is applied through libpq's ``PGAPPNAME``, read at connect time, and
    stays set for the store's whole life, so every connection it opens is
    counted: the pool's, any replacement the pool makes mid-burst, and an
    off-pool ``_connect()``. A tag that failed to reach the store would leave the
    count at zero, which the ``peak > 0`` assertion turns into a failure rather
    than a vacuous pass.
    """
    monkeypatch.setenv("LUMOGIS_PG_POOL_MIN", "4")
    monkeypatch.setenv("LUMOGIS_PG_POOL_MAX", "4")

    # Guarded, because this is the first connection the test opens: the
    # hermeticity gate runs this directory with every backend on a dead port and
    # requires a *skip*, not an error. An unguarded `psycopg2.connect` here read
    # as a failure and took `make test` to exit 2 (LUM-753 / LUM-718).
    try:
        probe = psycopg2.connect(**_conn_kwargs())
    except Exception as exc:  # pragma: no cover - environment-dependent
        require_postgres(exc)
    probe.autocommit = True
    try:
        # Set only now, so the probe itself (already connected) is not tagged.
        app_name = f"lum902-burst-{uuid.uuid4().hex[:12]}"
        monkeypatch.setenv("PGAPPNAME", app_name)
        st = PostgresStore(**_conn_kwargs())

        peak = 0
        stop = threading.Event()

        def sample():
            nonlocal peak
            while not stop.is_set():
                with probe.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM pg_stat_activity WHERE application_name = %s",
                        (app_name,),
                    )
                    peak = max(peak, int(cur.fetchone()[0]))

        sampler = threading.Thread(target=sample)
        sampler.start()
        try:
            workers = [
                threading.Thread(target=lambda: st.fetch_one("SELECT pg_sleep(0.05), 1 AS ok"))
                for _ in range(40)
            ]
            for w in workers:
                w.start()
            for w in workers:
                w.join(_LATCH_TIMEOUT)
        finally:
            stop.set()
            sampler.join(_LATCH_TIMEOUT)
            st.close()

        assert peak <= 4, (
            f"40 concurrent callers opened {peak} connections against a pool of 4 — "
            "the ceiling is not holding"
        )
        assert peak > 0, "the sampler never observed the pool; the bound is unproven"
    finally:
        probe.close()
