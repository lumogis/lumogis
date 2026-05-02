# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Projection engine for the personal/shared/system scope model.

Owns the cross-store mechanics of "publish a personal row to the
household": one ``project_<resource>(...)`` per publishable resource,
plus the inverse ``unproject_<resource>(...)`` and the merge-driven
``remap_published_from(...)`` sweep.

Six v1 publishable resources (per plan §2.4 and §6):

    * notes        (UUID PK)
    * audio_memos  (UUID PK)
    * sessions     (UUID PK)
    * file_index   (INTEGER PK — the only INTEGER case in v1)
    * entities     (UUID PK)
    * signals      (UUID PK)

Idempotency: every UUID-PK resource uses ``uuid5(NAMESPACE_URL,
f"{table}::{src_pk}::{target_scope}")`` as the projection PK so a
concurrent re-publish lands on the same row via the partial unique
index ``<table>_published_from_scope_uniq`` (plan §2.5). The
INTEGER-PK case (``file_index``) relies on the partial unique index
alone — concurrent inserts collapse via ``ON CONFLICT``.

Cross-store commit ordering (plan §7 step 4a):

    1. Mirror to Qdrant first (idempotent on deterministic uuid5 id).
    2. (Entities only) MERGE the FalkorDB shared node + sweep edges.
    3. Only after both succeed, COMMIT the Postgres projection row.

Postgres in this codebase is autocommit (``adapters/postgres_store.py``
sets ``conn.autocommit = True``), so "transaction" semantics here are
practical rather than strict: every write is itself idempotent
(``ON CONFLICT`` upserts; deterministic uuid5 ids), so retries on
partial failure converge.

``user_id`` on a shared/system projection row is **attribution-only**
(who published it), NOT ownership in the personal sense. Visibility
is gated on ``scope IN ('shared','system')``, not on ``user_id``
matching the requester. Analytics that ``GROUP BY user_id`` over
shared rows must read this as "publisher", not "owner".
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from typing import Optional

from auth import UserContext

import config

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deterministic projection IDs
# ---------------------------------------------------------------------------

_NS = uuid.NAMESPACE_URL


def projection_pk(table: str, src_pk: str, target_scope: str) -> str:
    """Deterministic UUID for a projection row of ``src_pk`` in ``table``.

    Concurrent re-publishes of the same source-row land on the same
    projection PK and collapse via the partial unique index on
    ``(published_from, scope)``. See plan §7 step 2.
    """
    return str(uuid.uuid5(_NS, f"{table}::{src_pk}::{target_scope}"))


def projection_point_id(collection: str, src_pk: str, target_scope: str) -> str:
    """Deterministic Qdrant point id for a projection mirror.

    Mirrors the SQL ``projection_pk`` rule so unpublish can always
    locate the projection point without a SELECT round-trip.
    """
    return str(uuid.uuid5(_NS, f"{collection}::{src_pk}::{target_scope}"))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_target_scope(target_scope: str) -> None:
    if target_scope not in ("shared", "system"):
        # v1 publish surface only ever uses 'shared'; 'system' is reserved
        # for system-owned writers (signal monitor, dedup promotion, etc.)
        raise ValueError(
            f"projection target_scope must be 'shared' or 'system'; got {target_scope!r}"
        )


def _embed_for_projection(text: str) -> Optional[list[float]]:
    """Re-embed projection text. Returns None if embedder fails.

    Projections re-embed (rather than reuse the source point's vector)
    because the ``VectorStore`` port has no ``retrieve`` method. The
    embedder is deterministic enough that repeated calls over the same
    text produce semantically identical vectors.
    """
    try:
        embedder = config.get_embedder()
        return embedder.embed(text or "")
    except Exception as exc:
        _log.warning("projection: embedder failed — %s", exc)
        return None


def _qdrant_upsert_safe(
    collection: str,
    point_id: str,
    vector: Optional[list[float]],
    payload: dict,
) -> None:
    """Upsert a Qdrant projection point; raise on hard backend failure.

    Per plan §7 step 4a, projection-backend failures must surface to
    the caller (route layer translates to HTTP 502). Returns silently
    only on transient/embedder issues that left ``vector=None``.
    """
    if vector is None:
        # No vector available — skip the Qdrant mirror; Postgres-side
        # projection is still valid, the row will just not surface in
        # semantic search until next re-embed.
        _log.warning(
            "projection: skipping Qdrant upsert collection=%s id=%s (no vector)",
            collection,
            point_id,
        )
        return
    vs = config.get_vector_store()
    vs.upsert(collection=collection, id=point_id, vector=vector, payload=payload)


def _qdrant_delete_safe(collection: str, point_id: str) -> None:
    """Best-effort delete; logs on failure (plan §7 unpublish step 6)."""
    try:
        vs = config.get_vector_store()
        vs.delete(collection=collection, id=point_id)
    except Exception as exc:
        _log.warning(
            "projection: Qdrant delete failed collection=%s id=%s — %s",
            collection,
            point_id,
            exc,
        )


# ---------------------------------------------------------------------------
# Per-resource publish helpers
# ---------------------------------------------------------------------------


def project_note(src: dict, *, target_scope: str, actor: UserContext) -> dict:
    """Project a personal `notes` row to ``target_scope``.

    ``user_id`` on the projection row is the ``actor.user_id``
    (publisher attribution). ``graph_projected_at`` is reset to NULL
    so the existing graph-projection scheduler picks up the new
    shared/system row on its next pass (plan §7 step 5).
    """
    _validate_target_scope(target_scope)
    src_pk = str(src["note_id"])
    new_pk = projection_pk("notes", src_pk, target_scope)
    point_id = projection_point_id("conversations", src_pk, target_scope)

    payload = {
        "note_id": new_pk,
        "text": src.get("text") or "",
        "user_id": actor.user_id,
        "scope": target_scope,
        "published_from": src_pk,
    }
    vector = _embed_for_projection(payload["text"])
    _qdrant_upsert_safe("conversations", point_id, vector, payload)

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        """
        INSERT INTO notes (note_id, text, user_id, source, scope, published_from,
                           graph_projected_at)
        VALUES (%s, %s, %s, %s, %s, %s, NULL)
        ON CONFLICT (published_from, scope) WHERE published_from IS NOT NULL DO UPDATE SET
            text = EXCLUDED.text,
            updated_at = NOW(),
            graph_projected_at = NULL
        RETURNING *
        """,
        (
            new_pk,
            src.get("text") or "",
            actor.user_id,
            src.get("source") or "quick_capture",
            target_scope,
            src_pk,
        ),
    )
    _log.info(
        "projection: note src=%s new=%s scope=%s actor=%s",
        src_pk,
        new_pk,
        target_scope,
        actor.user_id,
    )
    return row or {"note_id": new_pk, "scope": target_scope, "published_from": src_pk}


def project_audio_memo(src: dict, *, target_scope: str, actor: UserContext) -> dict:
    _validate_target_scope(target_scope)
    src_pk = str(src["audio_id"])
    new_pk = projection_pk("audio_memos", src_pk, target_scope)
    point_id = projection_point_id("conversations", src_pk, target_scope)

    payload = {
        "audio_id": new_pk,
        "transcript": src.get("transcript") or "",
        "user_id": actor.user_id,
        "scope": target_scope,
        "published_from": src_pk,
    }
    vector = _embed_for_projection(payload["transcript"])
    _qdrant_upsert_safe("conversations", point_id, vector, payload)

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        """
        INSERT INTO audio_memos (audio_id, file_path, transcript, duration_seconds,
                                 whisper_model, user_id, scope, published_from,
                                 transcribed_at, graph_projected_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
        ON CONFLICT (published_from, scope) WHERE published_from IS NOT NULL DO UPDATE SET
            transcript = EXCLUDED.transcript,
            duration_seconds = EXCLUDED.duration_seconds,
            whisper_model = EXCLUDED.whisper_model,
            updated_at = NOW(),
            graph_projected_at = NULL
        RETURNING *
        """,
        (
            new_pk,
            src.get("file_path") or "",
            src.get("transcript"),
            src.get("duration_seconds"),
            src.get("whisper_model"),
            actor.user_id,
            target_scope,
            src_pk,
            src.get("transcribed_at"),
        ),
    )
    _log.info(
        "projection: audio src=%s new=%s scope=%s actor=%s",
        src_pk,
        new_pk,
        target_scope,
        actor.user_id,
    )
    return row or {"audio_id": new_pk, "scope": target_scope, "published_from": src_pk}


def project_session(src: dict, *, target_scope: str, actor: UserContext) -> dict:
    _validate_target_scope(target_scope)
    src_pk = str(src["session_id"])
    new_pk = projection_pk("sessions", src_pk, target_scope)
    point_id = projection_point_id("conversations", src_pk, target_scope)

    summary_text = src.get("summary") or ""
    topics = src.get("topics") or []
    payload = {
        "session_id": new_pk,
        "summary": summary_text,
        "topics": topics,
        "entities": src.get("entities") or [],
        "user_id": actor.user_id,
        "scope": target_scope,
        "published_from": src_pk,
    }
    vector = _embed_for_projection(f"{summary_text} Topics: {', '.join(topics)}")
    _qdrant_upsert_safe("conversations", point_id, vector, payload)

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        """
        INSERT INTO sessions (session_id, summary, topics, entities, entity_ids,
                              user_id, scope, published_from, graph_projected_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL)
        ON CONFLICT (published_from, scope) WHERE published_from IS NOT NULL DO UPDATE SET
            summary = EXCLUDED.summary,
            topics = EXCLUDED.topics,
            entities = EXCLUDED.entities,
            entity_ids = EXCLUDED.entity_ids,
            updated_at = NOW(),
            graph_projected_at = NULL
        RETURNING *
        """,
        (
            new_pk,
            summary_text,
            topics,
            src.get("entities") or [],
            src.get("entity_ids") or [],
            actor.user_id,
            target_scope,
            src_pk,
        ),
    )
    _log.info(
        "projection: session src=%s new=%s scope=%s actor=%s",
        src_pk,
        new_pk,
        target_scope,
        actor.user_id,
    )
    return row or {"session_id": new_pk, "scope": target_scope, "published_from": src_pk}


def project_file(src: dict, *, target_scope: str, actor: UserContext) -> dict:
    """Project a personal `file_index` row.

    ``file_index`` is the only INTEGER-PK resource in v1 — the
    projection PK is server-assigned by SERIAL; idempotency is
    enforced solely by the partial unique index
    ``file_index_published_from_scope_uniq``.
    """
    _validate_target_scope(target_scope)
    src_pk = int(src["id"])

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        """
        INSERT INTO file_index (file_path, file_hash, file_type, chunk_count,
                                ocr_used, user_id, scope, published_from)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (published_from, scope) WHERE published_from IS NOT NULL DO UPDATE SET
            file_hash = EXCLUDED.file_hash,
            chunk_count = EXCLUDED.chunk_count,
            updated_at = NOW()
        RETURNING *
        """,
        (
            src.get("file_path") or "",
            src.get("file_hash") or "",
            src.get("file_type") or "",
            int(src.get("chunk_count") or 0),
            bool(src.get("ocr_used") or False),
            actor.user_id,
            target_scope,
            src_pk,
        ),
    )
    _log.info(
        "projection: file src=%d new=%s scope=%s actor=%s",
        src_pk,
        (row or {}).get("id"),
        target_scope,
        actor.user_id,
    )
    return row or {"id": None, "scope": target_scope, "published_from": src_pk}


def project_entity(src: dict, *, target_scope: str, actor: UserContext) -> dict:
    """Project a personal `entities` row.

    Entities also drive the FalkorDB shared-graph projection — the
    backward sweep over incident edges happens via
    :mod:`services.lumogis-graph.projection` (Pass 8 KG-side helper).
    The orchestrator-side keeps the Postgres + Qdrant mirror; the
    graph reconciler picks up the new shared row on its next pass.
    """
    _validate_target_scope(target_scope)
    src_pk = str(src["entity_id"])
    new_pk = projection_pk("entities", src_pk, target_scope)
    point_id = projection_point_id("entities", src_pk, target_scope)

    payload = {
        "entity_id": new_pk,
        "name": src.get("name") or "",
        "entity_type": src.get("entity_type") or "",
        "user_id": actor.user_id,
        "scope": target_scope,
        "published_from": src_pk,
    }
    vector = _embed_for_projection(f"{payload['name']} {payload['entity_type']}")
    _qdrant_upsert_safe("entities", point_id, vector, payload)

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        """
        INSERT INTO entities (entity_id, name, entity_type, aliases, context_tags,
                              mention_count, user_id, scope, published_from,
                              extraction_quality, is_staged)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE)
        ON CONFLICT (published_from, scope) WHERE published_from IS NOT NULL DO UPDATE SET
            name = EXCLUDED.name,
            entity_type = EXCLUDED.entity_type,
            aliases = EXCLUDED.aliases,
            context_tags = EXCLUDED.context_tags,
            mention_count = EXCLUDED.mention_count,
            updated_at = NOW()
        RETURNING *
        """,
        (
            new_pk,
            src.get("name") or "",
            src.get("entity_type") or "",
            src.get("aliases") or [],
            src.get("context_tags") or [],
            int(src.get("mention_count") or 1),
            actor.user_id,
            target_scope,
            src_pk,
            src.get("extraction_quality"),
        ),
    )
    _log.info(
        "projection: entity src=%s new=%s scope=%s actor=%s",
        src_pk,
        new_pk,
        target_scope,
        actor.user_id,
    )
    return row or {"entity_id": new_pk, "scope": target_scope, "published_from": src_pk}


def project_signal(src: dict, *, target_scope: str, actor: UserContext) -> dict:
    _validate_target_scope(target_scope)
    src_pk = str(src["signal_id"])
    new_pk = projection_pk("signals", src_pk, target_scope)
    point_id = projection_point_id("signals", src_pk, target_scope)

    title = src.get("title") or ""
    summary = src.get("content_summary") or ""
    payload = {
        "signal_id": new_pk,
        "title": title,
        "url": src.get("url") or "",
        "user_id": actor.user_id,
        "scope": target_scope,
        "published_from": src_pk,
    }
    vector = _embed_for_projection(f"{title} {summary}")
    _qdrant_upsert_safe("signals", point_id, vector, payload)

    ms = config.get_metadata_store()
    row = ms.fetch_one(
        """
        INSERT INTO signals (signal_id, user_id, source_id, title, url, published_at,
                             content_summary, entities, topics, importance_score,
                             relevance_score, notified, scope, published_from,
                             source_url, source_label)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s,
                %s, %s, %s, %s)
        ON CONFLICT (published_from, scope) WHERE published_from IS NOT NULL DO UPDATE SET
            title = EXCLUDED.title,
            url = EXCLUDED.url,
            content_summary = EXCLUDED.content_summary,
            entities = EXCLUDED.entities,
            topics = EXCLUDED.topics,
            importance_score = EXCLUDED.importance_score,
            relevance_score = EXCLUDED.relevance_score,
            source_url = EXCLUDED.source_url,
            source_label = EXCLUDED.source_label
        RETURNING *
        """,
        (
            new_pk,
            actor.user_id,
            src.get("source_id") or "",
            title,
            src.get("url") or "",
            src.get("published_at"),
            summary,
            _json_or_default(src.get("entities"), "[]"),
            _json_or_default(src.get("topics"), "[]"),
            float(src.get("importance_score") or 0.0),
            float(src.get("relevance_score") or 0.0),
            bool(src.get("notified") or False),
            target_scope,
            src_pk,
            src.get("source_url"),
            src.get("source_label"),
        ),
    )
    _log.info(
        "projection: signal src=%s new=%s scope=%s actor=%s",
        src_pk,
        new_pk,
        target_scope,
        actor.user_id,
    )
    return row or {"signal_id": new_pk, "scope": target_scope, "published_from": src_pk}


# ---------------------------------------------------------------------------
# Per-resource unpublish helpers
# ---------------------------------------------------------------------------


def _unproject_uuid_pk(
    *,
    table: str,
    pk_col: str,
    src_pk: str,
    target_scope: str,
    qdrant_collection: Optional[str],
) -> int:
    """Delete a UUID-PK projection row + its Qdrant mirror.

    Returns the number of Postgres rows deleted (0 or 1).
    """
    ms = config.get_metadata_store()
    deleted = ms.fetch_one(
        f"DELETE FROM {table} WHERE published_from = %s AND scope = %s RETURNING {pk_col}",
        (src_pk, target_scope),
    )
    if qdrant_collection is not None:
        _qdrant_delete_safe(
            qdrant_collection,
            projection_point_id(qdrant_collection, src_pk, target_scope),
        )
    return 1 if deleted else 0


def unproject_note(src_pk: str, target_scope: str = "shared") -> int:
    return _unproject_uuid_pk(
        table="notes",
        pk_col="note_id",
        src_pk=src_pk,
        target_scope=target_scope,
        qdrant_collection="conversations",
    )


def unproject_audio_memo(src_pk: str, target_scope: str = "shared") -> int:
    return _unproject_uuid_pk(
        table="audio_memos",
        pk_col="audio_id",
        src_pk=src_pk,
        target_scope=target_scope,
        qdrant_collection="conversations",
    )


def unproject_session(src_pk: str, target_scope: str = "shared") -> int:
    return _unproject_uuid_pk(
        table="sessions",
        pk_col="session_id",
        src_pk=src_pk,
        target_scope=target_scope,
        qdrant_collection="conversations",
    )


def unproject_entity(src_pk: str, target_scope: str = "shared") -> int:
    return _unproject_uuid_pk(
        table="entities",
        pk_col="entity_id",
        src_pk=src_pk,
        target_scope=target_scope,
        qdrant_collection="entities",
    )


def unproject_signal(src_pk: str, target_scope: str = "shared") -> int:
    return _unproject_uuid_pk(
        table="signals",
        pk_col="signal_id",
        src_pk=src_pk,
        target_scope=target_scope,
        qdrant_collection="signals",
    )


def unproject_file(src_pk: int, target_scope: str = "shared") -> int:
    """INTEGER-PK unpublish for ``file_index``. No Qdrant mirror.

    File chunks are projected via re-ingest of the source row; the
    projection row carries metadata only, so there is no Qdrant point
    to delete here.
    """
    ms = config.get_metadata_store()
    deleted = ms.fetch_one(
        "DELETE FROM file_index WHERE published_from = %s AND scope = %s RETURNING id",
        (int(src_pk), target_scope),
    )
    return 1 if deleted else 0


# ---------------------------------------------------------------------------
# Merge-driven remap (called from entity_merge.merge_entities)
# ---------------------------------------------------------------------------


_PROJECTION_TABLES: tuple[tuple[str, str], ...] = (
    ("notes", "note_id"),
    ("audio_memos", "audio_id"),
    ("sessions", "session_id"),
    ("file_index", "id"),
    ("entities", "entity_id"),
    ("signals", "signal_id"),
)


def remap_published_from(loser_id: Any, winner_id: Any) -> None:
    """Repoint every projection row from ``loser_id`` to ``winner_id``.

    Called from ``services/entity_merge.merge_entities`` after the
    primary merge commits so dedup-driven merges keep household
    projections wired to the surviving canonical row (plan §2.11
    rule 31). Sweeps every projection-capable table because a single
    merge can touch any of them indirectly via attached projections.

    Two-step per table:

      1. ``DELETE`` colliding projections — rows whose ``(winner_id,
         scope)`` already has a projection (the partial unique index
         would otherwise reject the UPDATE).
      2. ``UPDATE`` survivors to point at ``winner_id``.

    Postcondition: ``SELECT 1 FROM <each table> WHERE published_from
    = $loser_id LIMIT 1`` returns nothing on every table.

    Failure mode: this function **does not** swallow exceptions. The
    primary caller (``entity_merge._run_phase_a``) wraps the merge in
    a single Postgres transaction; if a sweep statement aborts, the
    caller's ``conn.rollback()`` must restore the loser entity rather
    than leave the household graph half-remapped.
    """
    ms = config.get_metadata_store()
    for table, _pk in _PROJECTION_TABLES:
        ms.execute(
            f"DELETE FROM {table} t "
            f"WHERE t.published_from = %s "
            f"  AND EXISTS ("
            f"    SELECT 1 FROM {table} sib "
            f"    WHERE sib.published_from = %s AND sib.scope = t.scope"
            f"  )",
            (loser_id, winner_id),
        )
        ms.execute(
            f"UPDATE {table} SET published_from = %s WHERE published_from = %s",
            (winner_id, loser_id),
        )

    _log.info(
        "remap_published_from: swept %d tables loser=%s winner=%s",
        len(_PROJECTION_TABLES),
        loser_id,
        winner_id,
    )


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _json_or_default(value: Any, default: str) -> str:
    """Serialise ``value`` to JSON text for a JSONB column, else default.

    Mirrors the loose JSON handling in ``signal_processor._persist`` —
    accepts already-serialised text or list/dict and produces text
    suitable for the ``%s::jsonb`` cast.
    """
    if value is None:
        return default
    if isinstance(value, str):
        return value
    try:
        import json as _json

        return _json.dumps(value)
    except Exception:
        return default


__all__ = [
    "projection_pk",
    "projection_point_id",
    "project_note",
    "project_audio_memo",
    "project_session",
    "project_file",
    "project_entity",
    "project_signal",
    "unproject_note",
    "unproject_audio_memo",
    "unproject_session",
    "unproject_file",
    "unproject_entity",
    "unproject_signal",
    "remap_published_from",
]
