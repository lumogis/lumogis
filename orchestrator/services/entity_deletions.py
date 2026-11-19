# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""After a member deletes an entry: make the delete true in Qdrant and the graph (LUM-966).

``entity_edit.delete_entry`` removes the entry from Postgres in one transaction
and writes an **ids-only tombstone** (``entity_deletions``) in that same
transaction. Everything outside Postgres happens after the commit, off the
request path, and is retried until it is true:

* the delete fires :data:`events.Event.ENTITY_DELETED` with ``fire_background``;
* a Core listener (:func:`on_entity_deleted_points`, registered at start-up in
  **every** graph mode) deletes the entry's Qdrant points and its household
  copies' points, then verifies with ``count_where`` and stamps
  ``points_cleared_at``;
* the graph writer (inprocess) or the webhook dispatcher (service) removes the
  node and stamps ``graph_cleared_at`` — with the graph disabled the tombstone is
  stamped at insert;
* :func:`sweep_entity_deletions` re-fires whatever is still unstamped every few
  minutes, and the orphan GC (``graph.reconcile``) is the final backstop.

A tombstone is also what lets the graph writer refuse a late ``ENTITY_CREATED``
for a deleted entry (``writer.project_entity``). Tombstones hold ids only, never
names, and are removed seven days after both arms clear and Qdrant is re-counted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from dataclasses import field
from typing import Any

import hooks
from events import Event

import config

_log = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 300

#: A fresh tombstone is left alone this long: the background listener is still working.
_SWEEP_MIN_AGE = "2 minutes"
#: How long a fully-cleared tombstone is kept (and re-verified) before it is deleted.
_RETAIN = "7 days"
#: The graph arm is re-fired this many times; after that the orphan GC owns the node.
MAX_GRAPH_ATTEMPTS = 10


@dataclass
class Tombstone:
    entity_id: str
    user_id: str
    projection_ids: list[str] = field(default_factory=list)


def insert_tombstone(ms: Any, t: Tombstone, *, graph_enabled: bool) -> None:
    """Record the delete **inside the delete transaction**.

    With the graph disabled there is no graph arm: ``graph_cleared_at`` is
    stamped at insert so the row can age out.
    """
    ms.execute(
        "INSERT INTO entity_deletions (entity_id, user_id, projection_ids, graph_cleared_at) "
        "VALUES (%s, %s, %s::uuid[], CASE WHEN %s THEN NULL ELSE NOW() END) "
        "ON CONFLICT (entity_id) DO NOTHING",
        (t.entity_id, t.user_id, list(t.projection_ids), bool(graph_enabled)),
    )


def _qdrant_filters(t: Tombstone) -> list[dict]:
    ids = [t.entity_id]
    return [
        {
            "must": [
                {"key": "user_id", "match": {"value": t.user_id}},
                {"key": "entity_id", "match": {"any": ids}},
            ]
        },
        {
            "must": [
                {"key": "user_id", "match": {"value": t.user_id}},
                {"key": "published_from", "match": {"any": ids}},
            ]
        },
    ]


def clear_points(t: Tombstone) -> bool:
    """Delete the entry's personal and household points; stamp when verified. Never raises."""
    try:
        from services.document_purge import _run_qdrant_entity_arm

        ok, _errors = _run_qdrant_entity_arm(t.user_id, [t.entity_id])
        vs = config.get_vector_store()
        remaining = sum(vs.count_where("entities", f) for f in _qdrant_filters(t))
        if not ok or remaining:
            _log.error(
                "entity delete: points not cleared, sweep will retry entity_id=%s remaining=%d",
                t.entity_id,
                remaining,
            )
            return False
        config.get_metadata_store().execute(
            "UPDATE entity_deletions SET points_cleared_at = NOW() "
            # SCOPE-EXEMPT: entity_deletions is an ids-only per-member tombstone table.
            "WHERE entity_id = %s::uuid AND user_id = %s",
            (t.entity_id, t.user_id),
        )
        return True
    except Exception:  # noqa: BLE001 - a retry sweep follows
        _log.exception("entity delete: clearing points failed entity_id=%s", t.entity_id)
        return False


def fire_deleted(t: Tombstone) -> None:
    hooks.fire_background(
        Event.ENTITY_DELETED,
        entity_id=t.entity_id,
        user_id=t.user_id,
        projection_ids=list(t.projection_ids),
    )


def on_entity_deleted_points(
    *, entity_id: str, user_id: str, projection_ids: Any = (), **_kw: Any
) -> None:
    """Core-side listener for :data:`Event.ENTITY_DELETED` (every graph mode)."""
    clear_points(
        Tombstone(entity_id=str(entity_id), user_id=user_id, projection_ids=list(projection_ids))
    )


def after_delete_commit(t: Tombstone) -> None:
    """Qdrant and the graph both clear on the hooks executor; the response never waits."""
    fire_deleted(t)


def sweep_entity_deletions(limit: int = 100) -> dict:
    """Retry what is still unstamped; age out what is done. Never raises."""
    counts = {"points": 0, "graph": 0, "gave_up": 0, "removed": 0, "recheck": 0}
    try:
        ms = config.get_metadata_store()
        _sweep_pending(ms, limit, counts)
        _sweep_done(ms, limit, counts)
    except Exception:  # noqa: BLE001
        _log.exception("entity_deletion_sweep failed")
    return counts


def _tombstone(row: dict) -> Tombstone:
    return Tombstone(
        entity_id=str(row["entity_id"]),
        user_id=row["user_id"],
        projection_ids=[str(p) for p in (row.get("projection_ids") or [])],
    )


def _sweep_pending(ms: Any, limit: int, counts: dict) -> None:
    rows = ms.fetch_all(
        # ``projection_ids::text[]``: psycopg2 returns a ``uuid[]`` as one string, and
        # iterating that would walk its characters.
        "SELECT entity_id, user_id, projection_ids::text[] AS projection_ids, "
        "points_cleared_at, graph_cleared_at, attempts "
        # SCOPE-EXEMPT: entity_deletions is an ids-only per-member tombstone table.
        "FROM entity_deletions "
        "WHERE (points_cleared_at IS NULL OR graph_cleared_at IS NULL) "
        f"AND deleted_at < now() - interval '{_SWEEP_MIN_AGE}' "
        "ORDER BY deleted_at LIMIT %s",
        (limit,),
    )
    # NOTE (LUM-966 verify): the Qdrant arm has no attempt cap. A row whose points
    # never clear is retried (and logged) every tick and keeps its place at the front
    # of this oldest-first batch, so more than ``limit`` such rows would starve newer
    # tombstones. Only a lasting Qdrant fault produces that; once Qdrant answers, the
    # backlog drains. Cap it or order by attempts if that is ever seen.
    for row in rows:
        t = _tombstone(row)
        if row.get("points_cleared_at") is None:
            if clear_points(t):
                counts["points"] += 1
        if row.get("graph_cleared_at") is None:
            attempts = int(row.get("attempts") or 0)
            if attempts >= MAX_GRAPH_ATTEMPTS:
                continue
            ms.execute(
                "UPDATE entity_deletions SET attempts = attempts + 1 "
                # SCOPE-EXEMPT: entity_deletions is an ids-only per-member tombstone table.
                "WHERE entity_id = %s::uuid AND user_id = %s",
                (t.entity_id, t.user_id),
            )
            fire_deleted(t)
            counts["graph"] += 1
            if attempts + 1 >= MAX_GRAPH_ATTEMPTS:
                # The 10th attempt: the orphan GC owns the node from here on, and
                # stamping lets the tombstone age out instead of living forever.
                _log.error(
                    "entity delete: graph not cleared after %d attempts, left to the orphan GC "
                    "entity_id=%s",
                    MAX_GRAPH_ATTEMPTS,
                    t.entity_id,
                )
                ms.execute(
                    "UPDATE entity_deletions SET graph_cleared_at = NOW() "
                    # SCOPE-EXEMPT: entity_deletions is an ids-only per-member tombstone table.
                    "WHERE entity_id = %s::uuid AND user_id = %s AND graph_cleared_at IS NULL",
                    (t.entity_id, t.user_id),
                )
                counts["gave_up"] += 1


def _sweep_done(ms: Any, limit: int, counts: dict) -> None:
    rows = ms.fetch_all(
        "SELECT entity_id, user_id, projection_ids::text[] AS projection_ids "
        # SCOPE-EXEMPT: entity_deletions is an ids-only per-member tombstone table.
        "FROM entity_deletions "
        "WHERE points_cleared_at IS NOT NULL AND graph_cleared_at IS NOT NULL "
        f"AND deleted_at < now() - interval '{_RETAIN}' "
        "ORDER BY deleted_at LIMIT %s",
        (limit,),
    )
    for row in rows:
        t = _tombstone(row)
        try:
            vs = config.get_vector_store()
            remaining = sum(vs.count_where("entities", f) for f in _qdrant_filters(t))
        except Exception:  # noqa: BLE001 - keep the row; look again next tick
            _log.warning("entity delete: re-count failed entity_id=%s", t.entity_id)
            continue
        if remaining == 0:
            ms.execute(
                # SCOPE-EXEMPT: entity_deletions is an ids-only per-member tombstone table.
                "DELETE FROM entity_deletions WHERE entity_id = %s::uuid AND user_id = %s",
                (t.entity_id, t.user_id),
            )
            counts["removed"] += 1
        else:
            # A publish whose Qdrant upsert landed after the first clear.
            ms.execute(
                "UPDATE entity_deletions SET points_cleared_at = NULL "
                # SCOPE-EXEMPT: entity_deletions is an ids-only per-member tombstone table.
                "WHERE entity_id = %s::uuid AND user_id = %s",
                (t.entity_id, t.user_id),
            )
            counts["recheck"] += 1
