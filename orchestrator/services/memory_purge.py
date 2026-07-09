# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Multi-store purge orchestration for conversation/session memory (LUM-162)."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from dataclasses import field

from services.point_ids import session_conversation_point_id
from services.projection import projection_point_id

import config

_log = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_S = 0.1

# Sweeper configuration (LUM-416). Env vars allow per-deployment tuning.
PURGE_SWEEPER_INTERVAL_SECONDS: int = int(
    os.getenv("LUMOGIS_PURGE_SWEEPER_INTERVAL_SECONDS", "300")
)
_SWEEPER_MIN_AGE_MINUTES: int = int(os.getenv("LUMOGIS_PURGE_SWEEPER_MIN_AGE_MINUTES", "5"))
_SWEEPER_MAX_ATTEMPTS: int = int(os.getenv("LUMOGIS_PURGE_SWEEPER_MAX_ATTEMPTS", "20"))


@dataclass
class PurgeResult:
    postgres_deleted: bool = False
    qdrant_deleted: bool = False
    graph_deleted: bool = False
    qdrant_entities_deleted: bool = False
    orphan_entity_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        if not self.postgres_deleted:
            return False
        return not (self.qdrant_deleted and self.graph_deleted and self.qdrant_entities_deleted)


# ---------------------------------------------------------------------------
# Tombstone helpers (LUM-416)
# ---------------------------------------------------------------------------


def _tombstone_update(ms, user_id: str, session_id: str, result: PurgeResult) -> None:
    """Persist store-arm outcomes to the tombstone; set resolved_at when fully done."""
    # SCOPE-EXEMPT: purged_conversations tombstone updates are owner-keyed only.
    if not result.partial:
        ms.execute(
            # SCOPE-EXEMPT: purged_conversations resolved tombstone update (owner-keyed).
            "UPDATE purged_conversations "
            "SET qdrant_deleted = %s, graph_deleted = %s, errors = %s::jsonb, "
            "resolved_at = NOW() "
            "WHERE user_id = %s AND conversation_id = %s::uuid",
            (
                result.qdrant_deleted,
                result.graph_deleted,
                json.dumps(result.errors),
                user_id,
                session_id,
            ),
        )
    else:
        ms.execute(
            # SCOPE-EXEMPT: purged_conversations partial tombstone update (owner-keyed).
            "UPDATE purged_conversations "
            "SET qdrant_deleted = %s, graph_deleted = %s, errors = %s::jsonb "
            "WHERE user_id = %s AND conversation_id = %s::uuid",
            (
                result.qdrant_deleted,
                result.graph_deleted,
                json.dumps(result.errors),
                user_id,
                session_id,
            ),
        )


def purge_conversation_point(user_id: str, point_id: str) -> bool:
    """Delete a single deterministic conversations-collection point (LUM-91 primitive)."""
    vs = config.get_vector_store()
    try:
        vs.delete(collection="conversations", id=point_id)
        return True
    except Exception as exc:
        _log.warning("purge_conversation_point failed point_id=%s: %s", point_id, exc)
        return False


def _retry_store_arm(label: str, fn) -> tuple[bool, list[str]]:
    errors: list[str] = []
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            fn()
            return True, errors
        except Exception as exc:
            msg = f"{label}: {exc}"
            errors.append(msg)
            _log.warning("purge retry %d/%d — %s", attempt, _RETRY_ATTEMPTS, msg)
            if attempt < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_S)
    return False, errors


def is_conversation_purged(*, user_id: str, session_id: str) -> bool:
    """Return True when the conversation was hard-deleted (tombstone present)."""
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: purged_conversations has no scope column — tombstone rows
    # are owner-keyed (user_id, conversation_id) for purge coordination only.
    row = ms.fetch_one(
        "SELECT 1 FROM purged_conversations WHERE user_id = %s AND conversation_id = %s::uuid",
        (user_id, session_id),
    )
    return row is not None


def conversation_purge_target_exists(*, user_id: str, session_id: str) -> bool:
    """True when delete/retry should proceed (live session row or partial purge tombstone)."""
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT session_id FROM sessions "
        "WHERE session_id = %s AND user_id = %s AND scope = 'personal'",
        (session_id, user_id),
    )
    if row:
        return True
    wc = ms.fetch_one(
        "SELECT conversation_id FROM web_conversations "
        "WHERE conversation_id = %s::uuid AND user_id = %s",
        (session_id, user_id),
    )
    if wc:
        return True
    return is_conversation_purged(user_id=user_id, session_id=session_id)


def purge_session_memory(*, user_id: str, session_id: str) -> PurgeResult:
    """Hard-delete a personal conversation across Postgres, Qdrant, and optional graph."""
    ms = config.get_metadata_store()
    # qdrant_entities_deleted is N/A for session purge (sessions have no orphan
    # entity Qdrant points — that concept belongs to document purge). Mark it
    # True upfront so PurgeResult.partial reflects only the two relevant arms.
    result = PurgeResult(qdrant_entities_deleted=True)

    session_row = ms.fetch_one(
        "SELECT session_id FROM sessions "
        "WHERE session_id = %s AND user_id = %s AND scope = 'personal'",
        (session_id, user_id),
    )
    tombstone = is_conversation_purged(user_id=user_id, session_id=session_id)
    if not session_row and not tombstone:
        return result

    def _postgres_arm() -> None:
        with ms.transaction():
            # Tombstone inserted atomically: a crash cannot leave rows deleted
            # without a retryable record (mirrors document_purge / LUM-500).
            ms.execute(
                "INSERT INTO purged_conversations (user_id, conversation_id) "
                "VALUES (%s, %s::uuid) "
                "ON CONFLICT (user_id, conversation_id) DO NOTHING",
                (user_id, session_id),
            )
            ms.execute(
                "DELETE FROM web_messages WHERE conversation_id = %s::uuid AND user_id = %s",
                (session_id, user_id),
            )
            ms.execute(
                "DELETE FROM web_conversations WHERE conversation_id = %s::uuid AND user_id = %s",
                (session_id, user_id),
            )
            ms.execute(
                "DELETE FROM sessions WHERE published_from = %s::uuid",
                (session_id,),
            )
            ms.execute(
                "DELETE FROM sessions WHERE session_id = %s AND user_id = %s "
                "AND scope = 'personal'",
                (session_id, user_id),
            )

    if session_row:
        try:
            _postgres_arm()
            result.postgres_deleted = True
        except Exception as exc:
            result.errors.append(f"postgres: {exc}")
            _log.warning("purge_session_memory: Postgres arm failed session_id=%s", session_id)
            return result
    elif tombstone:
        try:
            with ms.transaction():
                ms.execute(
                    "DELETE FROM web_messages WHERE conversation_id = %s::uuid AND user_id = %s",
                    (session_id, user_id),
                )
                ms.execute(
                    "DELETE FROM web_conversations "
                    "WHERE conversation_id = %s::uuid AND user_id = %s",
                    (session_id, user_id),
                )
            result.postgres_deleted = True
        except Exception as exc:
            result.errors.append(f"postgres: {exc}")
            _log.warning(
                "purge_session_memory: tombstone web cleanup failed session_id=%s",
                session_id,
            )
            return result
    else:
        result.postgres_deleted = True

    from services.batch_queue import cancel_pending_session_end_jobs

    cancel_pending_session_end_jobs(user_id=user_id, session_id=session_id)

    point_ids = [
        session_conversation_point_id(user_id, session_id),
        projection_point_id("conversations", session_id, "shared"),
        projection_point_id("conversations", session_id, "system"),
    ]

    def _qdrant_arm() -> None:
        vs = config.get_vector_store()
        for pid in point_ids:
            vs.delete(collection="conversations", id=pid)

    ok, errs = _retry_store_arm("qdrant", _qdrant_arm)
    result.qdrant_deleted = ok
    result.errors.extend(errs)

    gs = config.get_graph_store()

    def _graph_arm() -> None:
        from plugins.graph.writer import delete_session
        from plugins.graph.writer import delete_session_projections

        delete_session(gs, session_id=session_id, user_id=user_id)
        delete_session_projections(gs, source_session_id=session_id)

    if gs is None:
        result.graph_deleted = True
    else:
        ok, errs = _retry_store_arm("graph", _graph_arm)
        result.graph_deleted = ok
        result.errors.extend(errs)

    # Persist arm outcomes so the sweeper knows which arms still need retrying.
    # Only callable when a tombstone exists (Postgres arm succeeded above or was
    # already present from a previous attempt).
    if result.postgres_deleted:
        try:
            _tombstone_update(ms, user_id, session_id, result)
        except Exception:
            _log.warning("purge_session_memory: tombstone update failed session_id=%s", session_id)

    return result


# ---------------------------------------------------------------------------
# Reconciliation sweeper (LUM-416)
# ---------------------------------------------------------------------------


def sweep_partial_purges() -> int:
    """Retry Qdrant/graph arms for unresolved partial conversation purges.

    Called by the APScheduler ``purge_partial_sweep`` job.  Returns the number
    of tombstones that reached ``resolved_at`` in this sweep pass.

    Design invariants:
    - Only tombstones older than ``_SWEEPER_MIN_AGE_MINUTES`` are touched (give
      the original sync retry its cooling-off window before the sweeper runs).
    - Capped at ``_SWEEPER_MAX_ATTEMPTS`` sweeper passes per tombstone — after
      that, we accept the orphan and log a WARNING rather than spin forever.
    - Qdrant deletes are idempotent (deterministic point IDs); the graph arm is
      also safe to replay (DETACH DELETE on a missing node is a no-op).
    """
    ms = config.get_metadata_store()
    rows = ms.fetch_all(
        "SELECT user_id, conversation_id::text AS session_id, "
        "qdrant_deleted, graph_deleted "
        "FROM purged_conversations "
        "WHERE resolved_at IS NULL "
        "AND sweep_attempts < %s "
        "AND purged_at < NOW() - INTERVAL '1 minute' * %s "
        "ORDER BY purged_at ASC "
        "LIMIT 100",
        (_SWEEPER_MAX_ATTEMPTS, _SWEEPER_MIN_AGE_MINUTES),
    )
    if not rows:
        return 0

    resolved = 0
    for row in rows:
        user_id: str = row["user_id"]
        session_id: str = row["session_id"]
        try:
            if _sweep_one(ms, user_id, session_id, row):
                resolved += 1
        except Exception:
            _log.exception(
                "purge_sweeper: unexpected error user_id=%s session_id=%s",
                user_id,
                session_id,
            )

    _log.info(
        "purge_sweep_pass",
        extra={
            "event": "purge_sweep_pass",
            "candidates": len(rows),
            "resolved": resolved,
        },
    )
    return resolved


def _sweep_one(ms, user_id: str, session_id: str, tombstone: dict) -> bool:
    """Retry failed store arms for one tombstone; update sweep_attempts.

    Returns True if resolved.
    """
    result = PurgeResult(
        postgres_deleted=True,
        qdrant_deleted=bool(tombstone["qdrant_deleted"]),
        graph_deleted=bool(tombstone["graph_deleted"]),
        qdrant_entities_deleted=True,  # N/A for session purge
    )

    if not result.qdrant_deleted:
        point_ids = [
            session_conversation_point_id(user_id, session_id),
            projection_point_id("conversations", session_id, "shared"),
            projection_point_id("conversations", session_id, "system"),
        ]

        def _qdrant_arm() -> None:
            vs = config.get_vector_store()
            for pid in point_ids:
                vs.delete(collection="conversations", id=pid)

        ok, errs = _retry_store_arm("qdrant[sweep]", _qdrant_arm)
        result.qdrant_deleted = ok
        result.errors.extend(errs)

    if not result.graph_deleted:
        gs = config.get_graph_store()
        if gs is None:
            result.graph_deleted = True
        else:

            def _graph_arm() -> None:
                from plugins.graph.writer import delete_session as _graph_delete_session
                from plugins.graph.writer import delete_session_projections

                _graph_delete_session(gs, session_id=session_id, user_id=user_id)
                delete_session_projections(gs, source_session_id=session_id)

            ok, errs = _retry_store_arm("graph[sweep]", _graph_arm)
            result.graph_deleted = ok
            result.errors.extend(errs)

    # Always increment sweep_attempts; set resolved_at only on full success.
    # SCOPE-EXEMPT: purged_conversations sweeper updates owner-keyed tombstones.
    if not result.partial:
        ms.execute(
            # SCOPE-EXEMPT: purged_conversations sweeper resolved tombstone (owner-keyed).
            "UPDATE purged_conversations "
            "SET qdrant_deleted = %s, graph_deleted = %s, errors = %s::jsonb, "
            "sweep_attempts = sweep_attempts + 1, resolved_at = NOW() "
            "WHERE user_id = %s AND conversation_id = %s::uuid",
            (
                result.qdrant_deleted,
                result.graph_deleted,
                json.dumps(result.errors),
                user_id,
                session_id,
            ),
        )
        _log.info(
            "purge_sweep_resolved",
            extra={"event": "purge_sweep_resolved", "session_id": session_id, "user_id": user_id},
        )
        return True

    ms.execute(
        # SCOPE-EXEMPT: purged_conversations sweeper partial retry (owner-keyed).
        "UPDATE purged_conversations "
        "SET qdrant_deleted = %s, graph_deleted = %s, errors = %s::jsonb, "
        "sweep_attempts = sweep_attempts + 1 "
        "WHERE user_id = %s AND conversation_id = %s::uuid",
        (
            result.qdrant_deleted,
            result.graph_deleted,
            json.dumps(result.errors),
            user_id,
            session_id,
        ),
    )
    _log.warning(
        "purge_sweep_still_partial",
        extra={
            "event": "purge_sweep_still_partial",
            "session_id": session_id,
            "user_id": user_id,
            "qdrant_deleted": result.qdrant_deleted,
            "graph_deleted": result.graph_deleted,
            "errors": result.errors,
        },
    )
    return False
