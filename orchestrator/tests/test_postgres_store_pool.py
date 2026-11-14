# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""LUM-816 — pool wiring that does not need a database.

The behaviour that matters (affinity, commit semantics, advisory-lock isolation,
savepoint nesting, dead-connection recovery) is proved against a real Postgres in
`tests/integration/test_postgres_store_concurrency_live.py`, because a fake
cannot tell one connection from another. What is left for a unit test is the
wiring around the pool: sizing, the deprecation warning, the checkout deadline,
and the savepoint-name arithmetic — all of which are decisions rather than
database behaviour.
"""

from __future__ import annotations

import threading

import psycopg2.pool
import pytest
from adapters import postgres_store as ps


def _bare_store() -> ps.PostgresStore:
    """A store with thread-locals but no pool — no database needed.

    The methods under test here read `_local` and emit SQL strings; none of them
    reaches the pool. Constructing normally would open ten connections.
    """
    st = ps.PostgresStore.__new__(ps.PostgresStore)
    st._local = threading.local()
    return st


class _RecordingCursor:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._sink.append(sql)


class _RecordingConn:
    """Just enough connection to capture the SAVEPOINT statements."""

    def __init__(self) -> None:
        self.sql: list[str] = []

    def cursor(self, *a, **kw):
        return _RecordingCursor(self.sql)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", 10),  # unset → default
        ("4", 4),
        ("0", 1),  # clamped up, never zero
        ("9999", 100),  # clamped down, never unbounded
        ("banana", 10),  # unparseable → default, not a crash
        ("  6  ", 6),  # whitespace tolerated
    ],
)
def test_pool_bounds_clamp_rather_than_raise(monkeypatch, raw, expected):
    """A typo in an operator's env must not make the database unreachable.

    Same posture as `password_reset.ttl_minutes()`. Raising here would turn a
    stray character in `.env` into a stack that will not boot, which is a worse
    outcome than a slightly wrong pool size.
    """
    monkeypatch.setenv("LUMOGIS_PG_POOL_TEST", raw)
    assert ps._pool_bound("LUMOGIS_PG_POOL_TEST", 10) == expected


def test_an_explicit_max_below_the_default_min_wins(monkeypatch):
    """The operator's ceiling is honoured, not silently raised to the default.

    This is the whole point of `_pool_sizes`. `max(minconn, ...)` gave an
    operator who set only `LUMOGIS_PG_POOL_MAX=4` — the realistic case, a shared
    Postgres with few slots — ten connections instead of four, and nothing in
    the log said so. On a database sized for four, startup itself fails with
    `FATAL: remaining connection slots are reserved` and the config value the
    process ignored is the operator's only lead.
    """
    monkeypatch.delenv("LUMOGIS_PG_POOL_MIN", raising=False)
    monkeypatch.setenv("LUMOGIS_PG_POOL_MAX", "4")
    assert ps._pool_sizes() == (4, 4)


def test_min_above_max_yields_to_max(monkeypatch):
    """`ThreadedConnectionPool` would raise on min > max; clamp instead.

    Asserted through `_pool_sizes` — the function the constructor actually calls
    — rather than by re-running the constructor's arithmetic in the test, which
    is what the previous version of this test did and is why it could not have
    caught the bug above.
    """
    monkeypatch.setenv("LUMOGIS_PG_POOL_MIN", "20")
    monkeypatch.setenv("LUMOGIS_PG_POOL_MAX", "5")
    assert ps._pool_sizes() == (5, 5)


def test_defaults_keep_min_equal_to_max(monkeypatch):
    """`min == max` is load-bearing, not incidental.

    `_putconn` only returns a connection to the free list while
    `len(_pool) < minconn` and **closes** it otherwise. A default with min below
    max would silently degrade the pool into connect-per-use above `minconn`
    concurrent callers — measured at ~12ms per statement versus 0.15ms pooled,
    which is the entire reason this chunk chose a pool over connection-per-request.
    """
    monkeypatch.delenv("LUMOGIS_PG_POOL_MIN", raising=False)
    monkeypatch.delenv("LUMOGIS_PG_POOL_MAX", raising=False)
    assert ps._DEFAULT_POOL_MIN == ps._DEFAULT_POOL_MAX
    assert ps._pool_sizes() == (ps._DEFAULT_POOL_MIN, ps._DEFAULT_POOL_MAX)


def test_pool_exhausted_error_is_distinct_from_psycopg2_pool_error():
    """A closed pool and an exhausted one must not be handled alike.

    psycopg2 raises the same `PoolError` class for both. `_acquire` re-raises the
    *closed* case immediately — retrying it for the full deadline would stall
    shutdown for no reason — and only waits out the exhausted case. That split
    breaks the moment `PoolExhaustedError` becomes a `PoolError`: `_acquire`'s
    own `except psycopg2.pool.PoolError` would then catch the exception it just
    raised and re-enter the retry loop for a second full deadline.

    The previous assertion here was `not issubclass(..., Exception.__class__)`.
    `Exception.__class__` is `type`, so it held for every class ever written —
    a test that could not fail, guarding the one relationship that matters.
    """
    assert issubclass(ps.PoolExhaustedError, RuntimeError)
    assert not issubclass(ps.PoolExhaustedError, psycopg2.pool.PoolError)


def test_checkout_deadline_is_bounded_and_nonzero():
    """psycopg2 never queues — `getconn` raises the instant the pool is full.

    Without a deadline the adapter would 503 on the first request past
    `pool_max`; without a bound it would hang. Both numbers are decisions, so
    they are pinned here rather than left to drift.
    """
    assert 0 < ps._CHECKOUT_POLL_SECONDS < ps._CHECKOUT_TIMEOUT_SECONDS
    assert ps._CHECKOUT_TIMEOUT_SECONDS <= 30


def test_savepoint_exists_on_the_store_itself():
    """`services/password_reset.py` degrades **silently** when this is absent.

    It probes `getattr(ms, "savepoint", None)` and skips the savepoint when the
    result is None — no error, no log. `savepoint` used to live only on the
    bound-connection handle, so the LUM-816 alias yielding the store would have
    reinstated both LUM-65 P0s (the migration-073 guard breaking CLI lockout
    recovery, and `mint()`'s inert collision retry) with the suite still green.

    A one-line `hasattr` guard is a cheap price for a defect that expensive.
    """
    assert hasattr(ps.PostgresStore, "savepoint")


def test_nested_savepoints_get_distinct_names():
    """A shared savepoint name makes the outer rollback reach the inner mark.

    Postgres resolves an unqualified `ROLLBACK TO` to the most recent savepoint
    of that name and does not destroy it, so with both levels named
    `lumogis_sp` the outer rollback left every write the middle block made still
    applied. Measured against a real Postgres: `['base', 'between']` survived a
    rollback that should have left `['base']`.

    `transaction()` routes every nested block through `savepoint()`, so two
    levels are one nested `with ms.transaction():` away.
    """
    st = _bare_store()
    conn = _RecordingConn()
    st._local.conn = conn

    with st.savepoint():
        with st.savepoint():
            pass

    assert conn.sql == [
        "SAVEPOINT lumogis_sp_1",
        "SAVEPOINT lumogis_sp_2",
        "RELEASE SAVEPOINT lumogis_sp_2",
        "RELEASE SAVEPOINT lumogis_sp_1",
    ]


def test_savepoint_depth_unwinds_after_an_error():
    """Depth must not drift, or later savepoints collide with abandoned names."""
    st = _bare_store()
    conn = _RecordingConn()
    st._local.conn = conn

    with st.savepoint():
        with pytest.raises(ValueError):
            with st.savepoint():
                raise ValueError("inner blew up")
        assert st._local.sp_depth == 1

    assert st._local.sp_depth == 0
    assert "ROLLBACK TO SAVEPOINT lumogis_sp_2" in conn.sql


def test_isolated_transaction_refuses_to_nest():
    """Its callers chose it *for* independence; a savepoint is not that.

    `transaction()` now makes nesting a savepoint, which is almost always what a
    caller nesting two atomic units means. `isolated_transaction()` is the
    exception: `system_mail.drain_outbox` claims each row in one because the
    attempt bump must survive a crash mid-send. Demoted to a savepoint it would
    commit nothing of its own, and an outer rollback would discard the bump and
    the post-send DELETE — re-mailing an already-delivered reset link.

    No call site nests today. This keeps that loud if one ever does.
    """
    st = _bare_store()
    st._local.conn = object()

    with pytest.raises(RuntimeError, match="cannot nest"):
        with st.isolated_transaction():
            pass


class _FakePool:
    """Enough of `ThreadedConnectionPool` to see whether a checkout came back."""

    def __init__(self, conn) -> None:
        self._conn = conn
        self.out = 0
        self.returned = 0

    def getconn(self):
        self.out += 1
        return self._conn

    def putconn(self, conn, close=False):
        self.returned += 1


class _AutocommitRefusingConn:
    """A connection that rejects `autocommit = False`.

    Stands in for any failure between the checkout and the `try` — psycopg2
    raises `InterfaceError` from this setter on a connection the server has
    already dropped (measured), and it is the first thing `transaction()` does
    with a freshly checked-out connection.
    """

    closed = 0
    autocommit = True

    def __setattr__(self, name, value):
        if name == "autocommit" and value is False:
            raise RuntimeError("connection already closed")
        object.__setattr__(self, name, value)

    def rollback(self):
        pass


def test_a_transaction_that_fails_before_its_block_still_returns_the_connection():
    """Anything raising between `_acquire()` and the `try` strands the checkout.

    `ThreadedConnectionPool` keeps a checked-out connection in `_used` until it
    is handed back, so an escape there costs the slot for the life of the
    process — not for the request. Repeated across the pool that is a permanent
    self-inflicted outage: every later caller waits out the checkout deadline
    and gets a 503, on a database that is entirely healthy, while `/admin/health`
    reports fine.

    `conn.autocommit = False` and the affinity binding used to sit above the
    `try`. This pins them inside it.
    """
    st = _bare_store()
    conn = _AutocommitRefusingConn()
    st._pool = _FakePool(conn)

    with pytest.raises(RuntimeError):
        with st.transaction():
            pass  # pragma: no cover - never reached

    assert st._pool.returned == 1, "the connection was stranded in the pool's used map"
    assert getattr(st._local, "conn", None) is None, "affinity outlived the failure"


def test_closed_pool_is_not_retried_as_exhaustion():
    """Lifespan shutdown closes the singleton; live tests must not reuse it.

    ``getconn`` raises ``PoolError`` both when the pool is empty and when it is
    closed. Retrying the closed case until the checkout deadline would stall
    teardown, so ``_acquire`` re-raises when the message says closed. The
    conversation-share live test used to query its fixture store after
    ``TestClient`` exited and hit this path.
    """

    class _ClosedPool:
        closed = True

        def getconn(self):
            raise psycopg2.pool.PoolError("connection pool is closed")

    st = _bare_store()
    st._pool = _ClosedPool()
    st._maxconn = 10
    with pytest.raises(psycopg2.pool.PoolError, match="closed"):
        st._acquire()


def test_close_is_still_named_close():
    """`config.shutdown()` dispatches on `hasattr(store, "close")`.

    Renaming it to `closeall()` — which reads more naturally for a pool — would
    make teardown silently skip the store and leak every connection on each
    lifespan, including per-test ones. Pinned because the failure is invisible.
    """
    assert callable(getattr(ps.PostgresStore, "close", None))


def test_lock_connection_accessor_is_public():
    """`share_lock` and `consolidation_lock` used to read `store._dsn` directly.

    Both need a connection whose lifetime outlives any `with` block, so they
    cannot use `checkout_connection`. `new_lock_connection()` gives them one
    without a second copy of the credentials path living in a service.
    """
    assert callable(getattr(ps.PostgresStore, "new_lock_connection", None))
