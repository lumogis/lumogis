# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Per-document advisory lock serialising share vs re-ingest vs purge (LUM-157).

A share/unshare projection job, a re-ingest re-projection, or a document purge
for the **same** document must not interleave (the re-ingest chunk wipe has no
scope filter, and purge deletes Qdrant chunks while a share job may be
upserting from a cached scroll). All three paths acquire this blocking
``pg_advisory_lock`` keyed on the document id on a dedicated checkout
connection (mirrors ``services/consolidation_lock.py`` / ADR 022), so the
process-wide connection is never held for the duration of the projection I/O.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

import psycopg2

import config

_log = logging.getLogger(__name__)

# Stable salt; grep repo for ``pg_advisory`` before changing.
SHARE_DOCUMENT_ADVISORY_KEY1 = 8421570


def _advisory_key2(document_id: int) -> int:
    """Positive int32 advisory key2 for a document id."""
    return int(document_id) & 0x7FFFFFFF


@contextmanager
def share_document_lock(document_id: int):
    """Block until the per-document share lock is held, then release on exit.

    Falls back to a no-op (yields without a lock) if a dedicated connection
    cannot be opened — the projection is still idempotent, we only lose the
    serialisation guarantee, which is logged.
    """
    key2 = _advisory_key2(document_id)
    conn = None
    try:
        store = config.get_metadata_store()
        conn = psycopg2.connect(**store._dsn)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_lock(%s::integer, %s::integer)",
                (SHARE_DOCUMENT_ADVISORY_KEY1, key2),
            )
    except Exception as exc:
        _log.warning(
            "share_lock: could not acquire advisory lock for document_id=%s "
            "(continuing without serialisation): %s",
            document_id,
            exc,
        )
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        yield  # degrade to no lock — projection is idempotent regardless
        return

    try:
        yield
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(%s::integer, %s::integer)",
                    (SHARE_DOCUMENT_ADVISORY_KEY1, key2),
                )
        except Exception as exc:
            _log.warning("share_lock: advisory unlock failed document_id=%s: %s", document_id, exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass
