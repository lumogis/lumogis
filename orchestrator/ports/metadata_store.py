# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: metadata store protocol.

Implemented by adapters/postgres_store.py (default).
PostgreSQL is the recommended backend; any relational store with parameterized
query support can implement this port.

Schema ownership
----------------
All schema definitions live in postgres/init.sql. The protocol is intentionally
thin: it provides parameterized query execution and does not expose schema
management — migrations are handled outside the adapter.

Parameterized queries
---------------------
All user-supplied values must be passed as params, never interpolated into the
query string. This is enforced by convention in every service.

Correct:
    store.fetch_all("SELECT * FROM signals WHERE user_id = %s", (user_id,))

Incorrect (SQL-injectable):
    store.fetch_all(f"SELECT * FROM signals WHERE user_id = '{user_id}'")

Return format
-------------
fetch_one returns a dict of {column: value} or None if no row matched.
fetch_all returns a (possibly empty) list of such dicts.
"""

from contextlib import AbstractContextManager
from typing import Protocol


class PoolExhaustedError(RuntimeError):
    """No connection became free within an implementation's checkout deadline.

    Lives on the **port**, not the adapter, because it is a failure mode of the
    contract rather than of psycopg2: any pooled implementation can run out of
    connections, and the caller that maps it to an HTTP status has no business
    importing an adapter to name it. `main.py` answers it with **503** — the
    database is healthy and the request will succeed shortly, which is the
    opposite of what a 500 tells a client.

    Distinct from psycopg2's `PoolError`, which is also raised for a *closed*
    pool; retrying that for a full deadline during shutdown would stall teardown
    for no reason, so the adapter re-raises the closed case immediately.
    """


class MetadataStore(Protocol):
    def ping(self) -> bool:
        """Return True if the database is reachable. Does not raise."""
        ...

    def execute(self, query: str, params: tuple | None = None) -> None:
        """Execute a write query (INSERT / UPDATE / DELETE). Commits on success."""
        ...

    def fetch_one(self, query: str, params: tuple | None = None) -> dict | None:
        """Execute a read query and return the first row as a dict, or None."""
        ...

    def fetch_all(self, query: str, params: tuple | None = None) -> list[dict]:
        """Execute a read query and return all rows as a list of dicts."""
        ...

    def isolated_transaction(self) -> AbstractContextManager["MetadataStore"]:
        """**Deprecated (LUM-816) — use :meth:`transaction`.**

        This existed because the store held one connection process-wide, so
        :meth:`transaction` could not be opened safely from a request or
        scheduler thread (LUM-65). The Postgres adapter is now pooled and
        :meth:`transaction` checks out its own connection, so the distinction
        has no meaning left.

        Kept as an alias for one release so existing call sites keep working.
        The yielded object still supports ``execute`` / ``fetch_one`` /
        ``fetch_all`` — and ``savepoint``.

        One difference from :meth:`transaction` survives the aliasing: this one
        **refuses to nest**. Nesting :meth:`transaction` is a savepoint, which is
        what a caller nesting two atomic units usually means — but the callers
        who chose *this* method chose it for independence, and a savepoint
        commits nothing of its own. `system_mail.drain_outbox` claims each outbox
        row in one because the attempt bump has to survive a crash mid-send;
        demoted to a savepoint, an outer rollback would take the bump and the
        post-send DELETE with it and re-mail a link that was already delivered.
        """
        ...

    def transaction(self) -> AbstractContextManager[None]:
        """Open an explicit transaction. Safe from any thread (LUM-816).

        Within the ``with`` block every ``execute`` / ``fetch_*`` on **this
        store** runs in one transaction; it commits on clean exit and rolls back
        on any exception. Note that the block yields **nothing** — callers use
        the store itself inside it, and the implementation is responsible for
        routing those statements to the transaction's connection.

        This used to be unsafe off a single-threaded path: the adapter held one
        connection for the whole process and ``transaction()`` toggled its
        ``autocommit``, so two concurrent callers collided and an unrelated
        ``execute`` on another thread was silently enrolled and lost. That cost
        four P0 defects (LUM-65) and was rediscovered three times before being
        fixed. It is no longer a hazard — but **anything that needs a session to
        outlive a statement** (``pg_advisory_lock``, ``LISTEN``, ``SET LOCAL``)
        still needs a dedicated connection, not this.

        **Nesting is a savepoint**, not an error: an inner block rolls back
        alone and the outer keeps commit ownership.

        See ``per_user_backup_export`` plan Pass 0 step 6.
        """
        ...
