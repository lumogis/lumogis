# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Consolidation advisory-lock singleton per (scope_owner, entity_type) — LUM-358.

Cross-process coalescing via ``pg_try_advisory_lock`` on a **dedicated** checkout
connection (mirrors ``signals/digest.py`` / ADR 022). In-process coalescing is
APScheduler ``max_instances=1, coalesce=True`` (LUM-109 owns scheduler config).

**Hold the lock handle only for the fast claim/stage window (milliseconds).**
Never wrap LLM inference inside ``ConsolidationLockHandle``.
"""

from __future__ import annotations

import logging
import zlib
from typing import Literal

import config
import psycopg2
import psycopg2.extras

_log = logging.getLogger(__name__)

# Stable salt; grep repo for ``pg_advisory`` before changing.
CONSOLIDATION_ADVISORY_KEY1 = 8421358

_Scope = Literal["personal", "shared", "system"]


def advisory_key2(scope_owner: str, entity_type: str) -> int:
    """Deterministic positive int32 advisory key2 from scope owner + entity type."""
    if not scope_owner or not entity_type:
        raise ValueError("scope_owner and entity_type must be non-empty")
    payload = f"{scope_owner}:{entity_type}".encode()
    return zlib.crc32(payload) & 0x7FFFFFFF


def resolve_scope_owner(*, user_id: str, scope: str) -> str:
    """Map entity scope to consolidation lock bucket owner."""
    if scope == "personal":
        return user_id
    if scope in ("shared", "system"):
        return "household"
    raise ValueError(f"invalid scope: {scope!r}")


class ConsolidationLockHandle:
    """Context manager over a dedicated Postgres session holding an advisory lock.

    Unlock on ``__exit__`` then close the connection. Slow consolidation work
    must run **outside** this handle.
    """

    def __init__(self, conn, key1: int, key2: int) -> None:
        self._conn = conn
        self._key1 = key1
        self._key2 = key2

    def __enter__(self) -> ConsolidationLockHandle:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(%s::integer, %s::integer)",
                    (self._key1, self._key2),
                )
        except Exception as exc:
            _log.warning(
                "consolidation_lock: advisory unlock failed key1=%s key2=%s: %s",
                self._key1,
                self._key2,
                exc,
            )
        finally:
            try:
                self._conn.close()
            except Exception:
                pass


def try_acquire_consolidation_lock(
    scope_owner: str,
    entity_type: str,
    *,
    dsn: str | None = None,
) -> ConsolidationLockHandle | None:
    """Non-blocking try; returns ``None`` when another session holds the lock."""
    key2 = advisory_key2(scope_owner, entity_type)
    conn = _open_checkout_connection(dsn)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock(%s::integer, %s::integer) AS ok",
                (CONSOLIDATION_ADVISORY_KEY1, key2),
            )
            row = cur.fetchone()
        if not row or not row.get("ok"):
            _log.debug(
                "consolidation_lock: coalesce scope_owner=%s entity_type=%s",
                scope_owner,
                entity_type,
            )
            conn.close()
            return None
        return ConsolidationLockHandle(conn, CONSOLIDATION_ADVISORY_KEY1, key2)
    except Exception as exc:
        _log.warning("consolidation_lock: advisory try failed: %s", exc)
        try:
            conn.close()
        except Exception:
            pass
        return None


def _open_checkout_connection(dsn: str | None):
    if dsn is not None:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        return conn
    store = config.get_metadata_store()
    conn = psycopg2.connect(**store._dsn)
    conn.autocommit = True
    return conn
