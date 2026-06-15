# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Multi-store purge orchestration for conversation/session memory (LUM-162)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from dataclasses import field

from services.point_ids import session_conversation_point_id
from services.projection import projection_point_id

import config

_log = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_S = 0.1


@dataclass
class PurgeResult:
    postgres_deleted: bool = False
    qdrant_deleted: bool = False
    graph_deleted: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        if not self.postgres_deleted:
            return False
        return not (self.qdrant_deleted and self.graph_deleted)


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
    result = PurgeResult()

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
                    "DELETE FROM web_conversations WHERE conversation_id = %s::uuid AND user_id = %s",
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
        from plugins.graph.writer import delete_session as graph_delete_session

        graph_delete_session(gs, session_id=session_id, user_id=user_id)

    if gs is None:
        result.graph_deleted = True
    else:
        ok, errs = _retry_store_arm("graph", _graph_arm)
        result.graph_deleted = ok
        result.errors.extend(errs)

    return result
