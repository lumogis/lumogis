# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""MetadataStore adapter for PostgreSQL, backed by a connection pool (LUM-816).

Before this, one psycopg2 connection served the whole process and
``transaction()`` worked by toggling that connection's ``autocommit`` — shared
mutable state across ~40 AnyIO threadpool workers and 12 scheduler jobs. Two
threads reaching ``transaction()`` together made the loser raise; worse, an
ordinary ``execute()`` on another thread was silently *enrolled* in the open
transaction and discarded when it rolled back. That cost four P0 defects on
LUM-65 and had been rediscovered three times before.

Three things make the pool correct, and each exists because leaving it out is
silent rather than loud:

**1. Thread-local affinity.** The port declares ``transaction()`` yielding
*nothing*, and all 15 callers are written ``with ms.transaction(): ms.execute(...)``
— they pass no handle and rely on ``execute`` reaching the transaction's
connection. A plain pool would hand each statement a *different* connection, so
every one would run outside its transaction and autocommit. ``_local.conn``
carries the current transaction's connection for the duration of the block, and
``execute``/``fetch_*`` prefer it.

**2. An explicit checkout protocol.** psycopg2 does the opposite of what is
convenient in three places, all measured rather than assumed: pooled connections
are born ``autocommit=False``; ``putconn`` *rolls back* uncommitted work; and
``putconn`` *preserves* session-scoped advisory locks where ``close()`` releases
them. So every checkout normalises ``autocommit`` and every return rolls back a
non-idle transaction first.

**3. Advisory-lock connections stay OUTSIDE the pool.** ``checkout_connection``
still opens a fresh connection and closes it. Both reasons are load-bearing:
``document_purge`` holds a lock and then opens a transaction *inside* it, so one
logical unit needs two connections and would self-deadlock at ``pool_max``; and
closing (rather than returning) is what releases a leaked advisory lock, which
is the backstop four call sites already rely on.

**4. A dead connection heals on use, not on checkout.** The old store ran
``SELECT 1`` before every query and reconnected when it failed. A pool cannot
pay that — it is a round-trip per statement, and the round-trip is most of what
pooling saves — and it cannot skip it cheaply either: measured, ``conn.closed``
is still 0 on a connection the server has already dropped. So the *failure* is
the probe: see :meth:`PostgresStore._run`.
"""

import logging
import os
import threading
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.extensions
import psycopg2.extras
import psycopg2.pool
from ports.metadata_store import PoolExhaustedError

_log = logging.getLogger(__name__)

#: Pool bounds. ``min == max`` deliberately: ``_putconn`` only returns a
#: connection to the free list while ``len(_pool) < minconn`` and closes it
#: otherwise, so a low minconn silently degrades the pool into connect-per-use.
_DEFAULT_POOL_MIN = 10
_DEFAULT_POOL_MAX = 10

#: How long a caller waits for a free connection before giving up. psycopg2
#: never waits — ``getconn`` raises ``PoolError`` the instant every connection is
#: checked out — so without this a burst wider than the pool is a 500 rather
#: than a queue.
_CHECKOUT_TIMEOUT_SECONDS = 10.0
_CHECKOUT_POLL_SECONDS = 0.01

#: One-shot so a hot path cannot spam the log with the alias deprecation.
_ALIAS_WARNED = False


#: Re-exported so this module stays the one place a reader looks for pool
#: behaviour. It is **defined on the port** (``ports.metadata_store``) because
#: `main.py` maps it to 503 and must not import an adapter to do so.
PoolExhaustedError = PoolExhaustedError


def _pool_bound(name: str, default: int) -> int:
    """Read a pool bound from env, clamping rather than raising.

    Same posture as ``password_reset.ttl_minutes()``: a typo in an operator's
    env must not make the database unreachable.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, min(int(raw), 100))
    except ValueError:
        _log.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default


def _pool_sizes() -> tuple[int, int]:
    """Resolve ``(minconn, maxconn)`` from env. **Max wins when they conflict.**

    Max is a hard resource ceiling — on a shared Postgres it is the number of
    slots the operator has actually been given — so min yields to it rather than
    the other way round. Clamping in the other direction is what
    ``max(minconn, ...)`` did, and it meant an operator who set
    ``LUMOGIS_PG_POOL_MAX=4`` and left min unset silently got ten connections:
    2.5x the cap they asked for, with a startup ``FATAL: remaining connection
    slots are reserved`` and nothing in the log to attribute it to.

    Both mismatches are logged, because each is an operator intent the process
    is declining to honour exactly.
    """
    minconn = _pool_bound("LUMOGIS_PG_POOL_MIN", _DEFAULT_POOL_MIN)
    maxconn = _pool_bound("LUMOGIS_PG_POOL_MAX", _DEFAULT_POOL_MAX)
    if minconn > maxconn:
        _log.warning(
            "LUMOGIS_PG_POOL_MIN=%d exceeds LUMOGIS_PG_POOL_MAX=%d; using %d for both. "
            "Max is the ceiling the database has slots for, so min yields to it.",
            minconn,
            maxconn,
            maxconn,
        )
        minconn = maxconn
    elif minconn < maxconn:
        _log.warning(
            "LUMOGIS_PG_POOL_MIN=%d is below LUMOGIS_PG_POOL_MAX=%d. Above %d "
            "concurrent callers psycopg2 closes returned connections instead of "
            "keeping them, so the pool degrades into connect-per-use (~12ms per "
            "statement versus ~0.15ms). Set them equal unless you mean this.",
            minconn,
            maxconn,
            minconn,
        )
    return minconn, maxconn


class PostgresStore:
    def __init__(self, host: str, port: int, user: str, password: str, dbname: str) -> None:
        self._dsn = dict(host=host, port=port, user=user, password=password, dbname=dbname)
        self._local = threading.local()
        minconn, maxconn = _pool_sizes()
        self._pool = psycopg2.pool.ThreadedConnectionPool(minconn, maxconn, **self._dsn)
        self._maxconn = maxconn
        _log.info(
            "PostgresStore pool ready (min=%d max=%d). The pool is PER PROCESS: "
            "running N uvicorn workers would open N x %d connections.",
            minconn,
            maxconn,
            maxconn,
        )

    def _connect(self):
        """A connection outside the pool — advisory locks and nothing else."""
        conn = psycopg2.connect(**self._dsn)
        conn.autocommit = True
        return conn

    def new_lock_connection(self):
        """A fresh connection for a session-scoped lock. **Caller must close it.**

        The public form of :meth:`_connect`, for the two lock helpers whose
        handle outlives any ``with`` block (`consolidation_lock`'s
        `ConsolidationLockHandle`, and `share_lock`'s degrade-to-no-lock path)
        and so cannot use :meth:`checkout_connection`.

        It exists because both of them previously did
        ``psycopg2.connect(**store._dsn)`` — reaching through the adapter into a
        private attribute, which put an unaudited copy of the credentials path
        in two services. Same behaviour, one owner.

        Deliberately **not** pooled, for the reasons on
        :meth:`checkout_connection`: closing is what releases an advisory lock,
        and lock holders must not compete for pooled connections with the
        transactions they wrap.
        """
        return self._connect()

    # ---- pool checkout -------------------------------------------------

    def _acquire(self):
        """Take a connection from the pool, waiting rather than failing at once.

        `getconn` raises `PoolError` immediately when every connection is out —
        it has no queue of its own. Polling is crude but correct and keeps the
        adapter free of a second synchronisation primitive; the wait only
        happens when the pool is genuinely saturated.
        """
        deadline = time.monotonic() + _CHECKOUT_TIMEOUT_SECONDS
        while True:
            try:
                conn = self._pool.getconn()
            except psycopg2.pool.PoolError as exc:
                if "closed" in str(exc).lower():
                    raise
                if time.monotonic() >= deadline:
                    raise PoolExhaustedError(
                        f"no Postgres connection free within "
                        f"{_CHECKOUT_TIMEOUT_SECONDS}s (pool max={self._maxconn}); "
                        f"raise LUMOGIS_PG_POOL_MAX if this recurs"
                    ) from exc
                time.sleep(_CHECKOUT_POLL_SECONDS)
                continue
            # Pooled connections are born autocommit=False. Outside a
            # transaction the port promises `execute` commits, and this is how
            # that is kept. The setter raises `InterfaceError` on a connection
            # the server has already dropped (measured), so it goes inside the
            # guard: escaping here would strand the checkout in the pool's
            # `_used` map for the life of the process.
            try:
                if not conn.autocommit:
                    conn.autocommit = True
            except Exception:  # noqa: BLE001 - a dead connection; take the next
                self._discard(conn)
                if time.monotonic() >= deadline:
                    raise
                continue
            return conn

    def _discard(self, conn) -> None:
        """Drop a connection instead of returning it, and free its slot.

        `putconn(close=True)` closes the connection and releases the pool slot,
        so the next `getconn` opens a replacement — measured: a fresh backend
        pid. That is how the pool heals after the server restarts, and it is
        why nothing here needs the old `_ensure_conn` probe.
        """
        try:
            self._pool.putconn(conn, close=True)
        except Exception:  # noqa: BLE001 - pool already closed during shutdown
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _release(self, conn) -> None:
        """Return a connection, leaving no transaction state for the next borrower.

        `putconn` rolls back a non-idle transaction itself, but only while the
        free list is below `minconn`; and it never normalises `autocommit`.
        Doing both here means the next borrower's assumptions hold regardless.

        **Session state is NOT reset, and that is a constraint on callers.**
        A pooled connection is reused, so anything session-scoped — ``SET``,
        ``SET ROLE``, ``search_path``, temporary tables, ``LISTEN``, prepared
        statements — outlives the checkout and is inherited by whoever borrows
        next. Verified: a ``SET application_name`` issued through the pooled
        surface was observed by twenty later borrowers.

        No code issues such a statement today, which is why this is a documented
        constraint rather than a ``DISCARD ALL`` on every return: that would add
        a server round-trip to every statement and spend most of the reuse
        benefit this pool exists for (~0.15ms checkout versus ~12ms connect).

        **If you need session-scoped state, use** :meth:`checkout_connection` —
        it is unpooled and closes on exit, so nothing survives it.
        """
        try:
            if not conn.closed:
                if conn.info.transaction_status != psycopg2.extensions.TRANSACTION_STATUS_IDLE:
                    conn.rollback()
                if not conn.autocommit:
                    conn.autocommit = True
        except Exception:  # noqa: BLE001 - a broken connection is discarded below
            try:
                self._pool.putconn(conn, close=True)
            except Exception:  # noqa: BLE001 - best effort
                pass
            return
        try:
            self._pool.putconn(conn)
        except Exception:  # noqa: BLE001 - pool already closed during shutdown
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _run(self, fn):
        """Run ``fn(conn)`` on the thread's transaction connection, or a checkout.

        Preferring `_local.conn` is the one rule that lets 405 call sites and 14
        `transaction()` blocks keep working untouched.

        **This also replaces `_ensure_conn`'s reconnect.** That ran `SELECT 1`
        before *every* query and reopened the connection when it failed, so a
        restarted Postgres healed invisibly. A pool cannot afford that probe: it
        is a server round-trip per statement, and the round-trip is most of what
        pooling saves (~0.15ms checkout versus ~12ms connect). And it could not
        be skipped cheaply — measured, `conn.closed` is still 0 on a connection
        the server dropped, right up until a statement is attempted.

        So the failure *is* the probe. A statement that breaks its connection
        leaves ``conn.closed == 2`` (measured; an ordinary SQL error leaves 0,
        so this never fires on a deadlock or a typo'd query). That connection is
        discarded, which frees its slot for a fresh backend, and the statement
        runs once more. After a `docker compose restart postgres` the first
        caller to touch each dead connection heals it instead of 500ing.

        Never retried inside a transaction: the block's earlier statements died
        with the connection, so re-running this one on a fresh connection would
        silently put it in a *different* transaction. There the error propagates
        and the caller's block rolls back, which is the honest outcome.

        The retry is also why session-scoped state has no business on this
        surface — a second reason on top of the reuse one in :meth:`_release`.
        A ``pg_advisory_lock`` taken through here could be re-issued on a
        *different* connection, which is the pooled successor to the
        reconnect-drops-the-lock gap LUM-358 documented. Lock holders use
        :meth:`checkout_connection` or :meth:`new_lock_connection`, which is
        what makes that structural rather than a caution.
        """
        bound = getattr(self._local, "conn", None)
        if bound is not None:
            return fn(bound)

        for attempt in (1, 2):
            conn = self._acquire()
            try:
                result = fn(conn)
            except Exception as exc:  # noqa: BLE001 - re-raised below
                if conn.closed and attempt == 1:
                    self._discard(conn)
                    _log.warning(
                        "Postgres connection was dead; discarded it and retrying "
                        "once on a fresh one: %s",
                        exc,
                    )
                    continue
                if conn.closed:
                    self._discard(conn)
                else:
                    self._release(conn)
                raise
            self._release(conn)
            return result

    # ---- MetadataStore surface -----------------------------------------

    def ping(self) -> bool:
        """True when the database answers **on a pooled connection**.

        It has to be a pooled one. Probing off-pool reports healthy through
        precisely the outage this check exists to catch: after a restart every
        pooled connection is dead while a brand-new connection answers fine, so
        `/admin/health` would stay green while every request 500s. Going through
        `_run` also means a ping heals one dead connection per call — the
        repair side-effect `_ensure_conn` used to provide, which the startup
        gate in `main.py` implicitly relied on.

        A saturated pool is *healthy*, not broken: every connection being busy
        serving traffic says nothing about the database, so that reports True.
        """
        try:
            self._run(lambda conn: self._select_one(conn))
            return True
        except PoolExhaustedError:
            return True
        except Exception:
            return False

    @staticmethod
    def _select_one(conn) -> None:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")

    def execute(self, query: str, params: tuple | None = None) -> None:
        def _go(conn):
            with conn.cursor() as cur:
                cur.execute(query, params)

        self._run(_go)

    def execute_isolated(self, query: str, params: tuple | None = None) -> None:
        """Deprecated (LUM-816) — every statement is isolated by default now.

        Kept so `services/users.py`'s `getattr(ms, "execute_isolated", ...)`
        lookup keeps resolving during the deprecation window. It deliberately
        bypasses thread affinity: its one caller is a fire-and-forget
        `last_seen_at` touch that must not join a caller's transaction.
        """
        conn = self._acquire()
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
        except Exception:
            if conn.closed:
                self._discard(conn)
                conn = None
            raise
        finally:
            if conn is not None:
                self._release(conn)

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        def _go(conn):
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return dict(row) if row else None

        return self._run(_go)

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        def _go(conn):
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query, params)
                return [dict(row) for row in cur.fetchall()]

        return self._run(_go)

    def close(self) -> None:
        """Close every pooled connection.

        Kept as `close()` on purpose: `config.shutdown()` dispatches on
        `hasattr(store, "close")`, so renaming it would make teardown silently
        skip the store and leak the whole pool on every lifespan, including
        per-test ones.
        """
        try:
            self._pool.closeall()
        except Exception:  # noqa: BLE001 - already closed
            pass

    @contextmanager
    def checkout_connection(self):
        """A dedicated connection for a session-scoped advisory lock (LUM-358).

        **Deliberately NOT from the pool** (LUM-816 decision 5), for two reasons
        that are each sufficient on their own:

        1. `document_purge` takes a lock and then opens `transaction()` *inside*
           it, so one logical unit needs two connections. Drawing both from the
           pool means `pool_max` concurrent units each hold one and wait for a
           second that does not exist — a self-deadlock the checkout timeout
           would turn into a synchronised stall broken only by a failed request.
        2. `pg_advisory_lock` is session-scoped, and **closing** a connection
           releases it while **returning** it to a pool does not. Four call
           sites rely on close-as-backstop when an unlock is swallowed. Pooling
           this path would leave a held lock idling in the free list, poisoning
           that connection for every later borrower.

        Anyone tempted to "optimise" this into a pooled checkout reintroduces
        both at once.
        """
        conn = self._connect()
        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - best-effort close
                pass

    @contextmanager
    def transaction(self):
        """Run a block atomically on one connection.

        Safe from any thread. It checks a connection out, binds it to this
        thread so plain `execute`/`fetch_*` inside the block land on it, and
        commits or rolls back on exit.

        **Nesting is a SAVEPOINT, not an error.** It used to raise, because on a
        shared connection there was nothing sane to do. Now the inner block is a
        savepoint on the outer transaction — which is what a caller nesting two
        atomic units almost always means — and the outer block keeps ownership
        of commit and rollback. The inner branch must not touch `_local.conn`:
        clearing it early would send the *outer* block's remaining statements to
        a different connection, which is precisely the bug this class exists to
        remove.
        """
        bound = getattr(self._local, "conn", None)
        if bound is not None:
            with self.savepoint():
                yield
            return

        conn = self._acquire()
        # Everything after the checkout is inside the try, including the two
        # lines that used to sit above it. `conn.autocommit = False` raises
        # `InterfaceError` on a connection the server has already dropped
        # (measured) — and an escape there returned nothing to the pool, so the
        # slot was gone for the life of the process. Repeated across the pool
        # that is a permanent, self-inflicted outage: every later request waits
        # out the checkout deadline and 503s, on a database that is fine.
        try:
            conn.autocommit = False
            self._local.conn = conn
            self._local.sp_depth = 0
            yield
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                _log.exception("rollback failed during transaction()")
            raise
        finally:
            # Clear affinity BEFORE returning the connection: a later statement
            # on this thread must not reach a connection someone else now owns.
            self._local.conn = None
            self._local.sp_depth = 0
            if conn.closed:
                self._discard(conn)
            else:
                try:
                    conn.autocommit = True
                except Exception:  # noqa: BLE001 - broken; _release discards it
                    pass
                self._release(conn)

    @contextmanager
    def isolated_transaction(self):
        """Deprecated alias for :meth:`transaction` (LUM-816).

        With a pool every transaction already runs on its own connection, so the
        distinction LUM-65 introduced no longer exists. Yields ``self`` because
        callers are written ``with ms.isolated_transaction() as tx: tx.execute(...)``
        — and with thread affinity ``self.execute`` lands on the transaction's
        connection, so those call sites keep working untouched.

        **Still refuses to nest**, where :meth:`transaction` now makes nesting a
        savepoint. The two are not interchangeable here: this method's callers
        chose it *for* independence, and a savepoint is the opposite of that.
        `system_mail.drain_outbox` claims each row in one of these because "the
        attempt bump must survive a crash mid-send, otherwise a restart re-sends
        immediately and forever" — as a savepoint it would commit nothing of its
        own, and an outer rollback would discard the bump and the post-send
        DELETE, mailing an already-delivered reset link a second time. No call
        site nests today; this keeps that a loud error rather than silent
        re-delivery if one ever does.

        Scheduled for removal after one release.
        """
        if getattr(self._local, "conn", None) is not None:
            raise RuntimeError(
                "isolated_transaction() cannot nest inside an open transaction: "
                "it promises an independently-committing unit of work and a "
                "savepoint is not one. Use transaction() if a savepoint is what "
                "you want, or pass the open transaction down instead."
            )
        global _ALIAS_WARNED
        if not _ALIAS_WARNED:
            _ALIAS_WARNED = True
            _log.warning(
                "isolated_transaction() is deprecated (LUM-816): transaction() is "
                "now isolated by default. This alias will be removed after one release."
            )
        with self.transaction():
            yield self

    @contextmanager
    def savepoint(self):
        """Make one fallible statement optional inside a transaction.

        Postgres aborts the **entire** transaction on any statement error, so a
        bare ``try/except`` around a statement leaves a transaction that can do
        nothing while looking handled — and ``COMMIT`` then degrades to
        ``ROLLBACK``. Only ``ROLLBACK TO SAVEPOINT`` genuinely undoes one
        statement.

        This exists because two LUM-65 call sites got that wrong. The
        migration-073 guard in `password_reset.invalidate_for_user` caught
        ``UndefinedTable`` and returned 0 — and thereby broke the CLI
        lockout-recovery path it was written to protect, since the next
        statement in the same transaction could no longer run. `mint()`'s
        collision-retry loop had the same shape. Neither could be caught by the
        unit suite: the fakes raise plain Python exceptions, which abort nothing.

        It must live **on the store**, because ``services/password_reset.py``
        probes ``getattr(ms, "savepoint", None)`` and **degrades silently to no
        savepoint** when it is absent. It used to live only on the bound-handle
        class the alias no longer returns, so without this method LUM-816 would
        have handed both P0s straight back with no test failing.

        Outside a transaction this is a no-op: there is nothing to save.

        **The name carries the nesting depth, and that is load-bearing.** It was
        a fixed ``lumogis_sp``, and `transaction()`'s nesting branch routes every
        nested block through here, so two levels were easy to reach. Postgres
        resolves an unqualified ``ROLLBACK TO`` to the *most recent* savepoint of
        that name and does not destroy it, so the outer rollback reached the
        inner savepoint and left everything the middle block wrote still applied.
        Measured, with both savepoints named ``lumogis_sp``: rolling back the
        outer one yielded ``['base', 'between']`` where unique names yield
        ``['base']``.

        The depth is an integer this class owns — a caller's string is never
        interpolated into SQL that cannot be parameterised, and a savepoint name
        cannot be.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            yield self
            return
        depth = getattr(self._local, "sp_depth", 0) + 1
        self._local.sp_depth = depth
        name = f"lumogis_sp_{depth}"
        try:
            with conn.cursor() as cur:
                cur.execute(f"SAVEPOINT {name}")
            try:
                yield self
            except Exception:
                with conn.cursor() as cur:
                    cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
                raise
            else:
                with conn.cursor() as cur:
                    cur.execute(f"RELEASE SAVEPOINT {name}")
        finally:
            self._local.sp_depth = depth - 1
