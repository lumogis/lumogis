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

User-scoped reads must additionally go through one of the household visibility
helpers (``visible_filter``, ``visible_qdrant_filter``, ``visible_cypher_fragment``)
or, for admin god-mode / scope-exempt tables, carry the comment tag
``# ADMIN-BYPASS:`` or ``# SCOPE-EXEMPT:`` (acceptance criterion #4 grep-gate).

Correct (user-scoped read uses visibility helper):
    clause, params = visible_filter(user)
    store.fetch_all(f"SELECT * FROM signals WHERE {clause}", params)

Incorrect (SQL-injectable):
    store.fetch_all(f"SELECT * FROM signals WHERE user_id = '{user_id}'")

Return format
-------------
fetch_one returns a dict of {column: value} or None if no row matched.
fetch_all returns a (possibly empty) list of such dicts.
"""

from typing import Protocol


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
