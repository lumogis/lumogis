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


def _insert_projection_or_rollback_qdrant(
    *,
    collection: str,
    point_id: str,
    insert_sql: str,
    params: tuple,
) -> Optional[dict]:
    """Run a single-point projection's Postgres INSERT after its Qdrant upsert,
    rolling back the orphan point if the INSERT fails (LUM-581 / LUM-44).

    Every single-point ``project_*`` helper upserts the Qdrant projection point
    *before* writing the Postgres projection row. If the Postgres write then
    fails, the shared/system Qdrant point is left orphaned: it is searchable by
    the whole household, but the owner has **no** Postgres projection row, so
    ``is_shared`` reads ``false`` and they never see (or can) unshare it — a
    silent content leak that ``unproject_*`` is never asked to clean up.

    This wrapper compensates: on a Postgres failure it best-effort deletes the
    just-upserted Qdrant point and re-raises, so a failed publish leaves no
    orphaned shared vector. (The benign inverse — a Postgres row without its
    vector, e.g. an idempotent re-publish whose transient Qdrant failure was
    compensated — is visible as ``is_shared`` and self-heals on the next
    publish/unpublish, and never leaks searchable content.)
    """
    ms = config.get_metadata_store()
    try:
        return ms.fetch_one(insert_sql, params)
    except Exception:
        _qdrant_delete_safe(collection, point_id)
        _log.error(
            "projection: Postgres projection insert failed after Qdrant upsert; "
            "rolled back orphan point collection=%s id=%s",
            collection,
            point_id,
        )
        raise


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

    row = _insert_projection_or_rollback_qdrant(
        collection="conversations",
        point_id=point_id,
        insert_sql="""
        INSERT INTO notes (note_id, text, user_id, source, scope, published_from,
                           graph_projected_at)
        VALUES (%s, %s, %s, %s, %s, %s, NULL)
        ON CONFLICT (published_from, scope) WHERE published_from IS NOT NULL DO UPDATE SET
            text = EXCLUDED.text,
            updated_at = NOW(),
            graph_projected_at = NULL
        RETURNING *
        """,
        params=(
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

    row = _insert_projection_or_rollback_qdrant(
        collection="conversations",
        point_id=point_id,
        insert_sql="""
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
        params=(
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


def project_session(
    src: dict,
    *,
    target_scope: str,
    actor: UserContext,
    shared_summary: str | None = None,
) -> dict:
    _validate_target_scope(target_scope)
    src_pk = str(src["session_id"])
    new_pk = projection_pk("sessions", src_pk, target_scope)
    point_id = projection_point_id("conversations", src_pk, target_scope)

    # LUM-582 Rung 1 — the household-facing summary is an editable artifact on the
    # projected row, never the owner's canonical source summary. Precedence:
    #   1. an explicit ``shared_summary`` override (the sharer edited it);
    #   2. else the EXISTING projected summary (a bare re-publish must not revert
    #      a prior edit back to the raw AI summary);
    #   3. else the source AI summary (first share).
    override = (shared_summary or "").strip()
    if override:
        summary_text = override
    else:
        _existing = config.get_metadata_store().fetch_one(
            "SELECT summary FROM sessions WHERE published_from = %s AND scope = %s",
            (src_pk, target_scope),
        )
        summary_text = (_existing.get("summary") if _existing else None) or (
            src.get("summary") or ""
        )
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

    row = _insert_projection_or_rollback_qdrant(
        collection="conversations",
        point_id=point_id,
        insert_sql="""
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
        params=(
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


def _project_file_chunks(src: dict, *, target_scope: str) -> tuple[int, int]:
    """Mirror a personal document's Qdrant chunks into ``target_scope`` (LUM-157).

    Reuses the source chunks' stored vectors via the Qdrant adapter's
    ``scroll_collection(..., with_vectors=True)`` — the ``VectorStore`` port has
    no ``retrieve``, but the adapter exposes vectors — so **no re-embed**. Falls
    back to re-embedding a chunk's text only if its vector is unavailable.
    Idempotent: deterministic projection point ids overwrite on re-share.

    Returns ``(projected, failed)``. Resilient per-chunk: a transient upsert
    (or a chunk with no usable vector) increments ``failed`` and the loop
    continues, so the share job can report an honest ``partial`` rather than
    aborting the whole projection on the first bad chunk.
    """
    # LUM-157 finding-5: only shared documents mirror content chunks. Historically
    # project_file did nothing in Qdrant for non-'shared' scopes; the share UI only
    # ever sends 'shared', and system-scoped rows are produced by system-owned
    # writers that manage their own content. Gate defensively so a future caller
    # can't silently duplicate personal document content into 'system' scope.
    if target_scope != "shared":
        return 0, 0
    src_pk = int(src["id"])
    owner = src.get("user_id") or ""
    file_path = src.get("file_path") or ""
    if not owner or not file_path:
        return 0, 0
    vs = config.get_vector_store()
    scroll = getattr(vs, "scroll_collection", None)
    if scroll is None:  # non-Qdrant backend: chunk projection unavailable
        _log.warning(
            "projection: vector store has no scroll_collection; file chunks not mirrored src=%s",
            src_pk,
        )
        return 0, 0
    chunks = scroll("documents", user_id=owner, file_path=file_path, with_vectors=True)
    projected = 0
    failed = 0
    for pt in chunks:
        payload = pt.get("payload") or {}
        if payload.get("scope") != "personal":
            continue  # only project the personal source chunks, never a projection
        vector = pt.get("vector")
        if vector is None:
            vector = _embed_for_projection(payload.get("text") or "")
        if vector is None:
            failed += 1  # no usable vector — honest partial, not a silent skip
            continue
        # LUM-157 finding-4: chunk_index must be an int. A missing/non-int value
        # would collapse multiple chunks onto the same deterministic point id
        # (f"{src_pk}:None") → silent overwrite / lost content. Skip such chunks
        # (counted as failed → honest partial) so per-chunk ids stay unique.
        chunk_ix = payload.get("chunk_index")
        if not isinstance(chunk_ix, int) or isinstance(chunk_ix, bool):
            failed += 1
            _log.warning(
                "projection: skipping chunk with non-int chunk_index src=%s value=%r",
                src_pk,
                chunk_ix,
            )
            continue
        point_id = projection_point_id("documents", f"{src_pk}:{chunk_ix}", target_scope)
        new_payload = {
            "file_path": file_path,
            "chunk_index": chunk_ix,
            "text": payload.get("text"),
            "user_id": owner,
            "scope": target_scope,
            "published_from": src_pk,
        }
        if payload.get("section_header"):
            new_payload["section_header"] = payload["section_header"]
        try:
            _qdrant_upsert_safe("documents", point_id, vector, new_payload)
            projected += 1
        except Exception as exc:
            failed += 1
            _log.warning(
                "projection: file chunk upsert failed src=%s chunk=%s scope=%s — %s",
                src_pk,
                chunk_ix,
                target_scope,
                exc,
            )
    _log.info(
        "projection: file chunks src=%s scope=%s projected=%s failed=%s",
        src_pk,
        target_scope,
        projected,
        failed,
    )
    return projected, failed


def _unproject_file_chunks(src_pk: int, owner: str, target_scope: str) -> None:
    """Remove the shared/system Qdrant chunk projections for a source (LUM-157).

    Scoped by ``published_from`` (+ ``user_id`` + ``scope``) — a **non-empty**
    ``must`` clause, never the empty-``must`` match-all that would wipe the
    whole ``documents`` collection.
    """
    if not owner:
        return
    vs = config.get_vector_store()
    vs.delete_where(
        "documents",
        {
            "must": [
                {"key": "published_from", "match": {"value": int(src_pk)}},
                {"key": "user_id", "match": {"value": owner}},
                {"key": "scope", "match": {"value": target_scope}},
            ]
        },
    )


def _project_file_row(src: dict, *, target_scope: str, actor: UserContext) -> dict:
    """Insert (or ON CONFLICT update) the ``file_index`` shared projection row."""
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
    return row or {"id": None, "scope": target_scope, "published_from": src_pk}


def project_file(src: dict, *, target_scope: str, actor: UserContext) -> dict:
    """Project a personal `file_index` row + mirror its Qdrant chunks (LUM-157).

    ``file_index`` is the only INTEGER-PK resource in v1 — the
    projection PK is server-assigned by SERIAL; idempotency is
    enforced solely by the partial unique index
    ``file_index_published_from_scope_uniq``. The Postgres projection row
    (metadata) plus the reuse-vectors chunk projection (content) together make
    a shared document retrievable by other household members.
    """
    _validate_target_scope(target_scope)
    row, _projected, _failed = project_file_with_status(
        src, target_scope=target_scope, actor=actor
    )
    return row


def project_file_with_status(
    src: dict, *, target_scope: str, actor: UserContext
) -> tuple[dict, int, int]:
    """Like :func:`project_file` but returns ``(row, projected, failed)``.

    Used by the ``share_document`` background job so it can report an honest
    ``partial`` share status when some content chunks could not be mirrored.
    """
    _validate_target_scope(target_scope)
    src_pk = int(src["id"])
    owner = str(src.get("user_id") or "")
    # LUM-157 finding-6: the chunk projection derives the owner from the source
    # row. That is only sound because the publish path fetches the caller's OWN
    # personal row (WHERE user_id = caller AND scope = 'personal'). Make the
    # invariant explicit so a future caller can never project another member's
    # content by passing a foreign source row.
    if owner != str(actor.user_id):
        raise ValueError(
            "project_file: source owner does not match actor "
            f"({owner!r} != {actor.user_id!r})"
        )
    row = _project_file_row(src, target_scope=target_scope, actor=actor)
    # LUM-157 finding-3: unproject-then-project so the shared set EXACTLY mirrors
    # the current source chunks. Without the pre-delete, re-sharing after a
    # re-ingest with FEWER chunks would leave the removed chunk indices as
    # retrievable shared orphans (deterministic ids only overwrite matching
    # indices, never delete stale ones). Scoped delete (published_from + user_id
    # + scope) — never a match-all.
    if target_scope == "shared":
        _unproject_file_chunks(src_pk, owner, target_scope)
    # Mirror the document's content chunks to Qdrant at target_scope so other
    # household members can actually search / document-chat over it (LUM-157).
    projected, failed = _project_file_chunks(src, target_scope=target_scope)
    _log.info(
        "projection: file src=%d new=%s scope=%s actor=%s projected=%s failed=%s",
        src_pk,
        (row or {}).get("id"),
        target_scope,
        actor.user_id,
        projected,
        failed,
    )
    return row, projected, failed


def reproject_shared_on_reingest(
    user_id: str,
    file_path: str,
    *,
    removed_entity_ids: list[str] | None = None,
) -> None:
    """Re-mirror shared chunks after a re-ingest of a currently-shared doc (LUM-157).

    A re-ingest wipes+rewrites the personal chunks (``_delete_document_vectors_for_path``
    has no scope filter, so the old shared chunks are gone too). If the source
    still has an active shared projection, re-run the projection so members see
    the **new** content, never stale/missing. Serialised against a concurrent
    share via the per-document advisory lock. Best-effort: failures are logged,
    not raised (the ingest job itself already succeeded).

    LUM-604: when ``removed_entity_ids`` is supplied (personal entities dropped
    by the latest extraction), diff-retract unjustified doc-origin shared rows
    before re-running the entity cascade for the surviving set.
    """
    if not user_id or not file_path:
        return
    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: personal source row owned by the ingest caller.
    src = ms.fetch_one(
        "SELECT * FROM file_index WHERE user_id = %s AND file_path = %s AND scope = 'personal'",
        (user_id, file_path),
    )
    if not src:
        return
    src_pk = int(src["id"])
    shared = ms.fetch_one(
        "SELECT 1 FROM file_index WHERE published_from = %s AND scope = 'shared' "
        "AND user_id = %s LIMIT 1",
        (src_pk, user_id),
    )
    if not shared:
        return
    from services.share_lock import share_document_lock

    try:
        with share_document_lock(src_pk):
            from services import document_entity_cascade

            # LUM-604: retract shared rows for entities dropped by this re-ingest
            # before re-projecting chunks and (re-)cascading survivors.
            document_entity_cascade.retract_removed_entities_on_reingest(
                file_path=file_path,
                owner_user_id=user_id,
                removed_entity_ids=list(removed_entity_ids or ()),
            )
            # finding-3: clear any stale shared chunks before re-projecting so a
            # re-ingest with fewer chunks can't leave orphaned shared points.
            _unproject_file_chunks(src_pk, user_id, "shared")
            _projected, failed = _project_file_chunks(src, target_scope="shared")
            # LUM-586: a re-ingest can add new extracted entities; re-run the
            # entity cascade so members' shared graph reflects them. New/existing
            # entities are (re-)projected idempotently. No-op unless the graph
            # feature is enabled.
            document_entity_cascade.cascade_share_document_entities(
                src_file=src,
                actor=UserContext(user_id=user_id, is_authenticated=True),
            )
        if failed:
            _log.warning(
                "projection: reingest re-projection partial src=%s failed=%s "
                "(shared content may be incomplete until next share retry)",
                src_pk,
                failed,
            )
    except Exception as exc:
        _log.warning(
            "projection: reingest re-projection failed src=%s — %s", src_pk, exc
        )


def project_entity(
    src: dict,
    *,
    target_scope: str,
    actor: UserContext,
    share_origin: str = "user",
) -> dict:
    """Project a personal `entities` row.

    Entities also drive the FalkorDB shared-graph projection — the
    backward sweep over incident edges happens via
    :mod:`services.lumogis-graph.projection` (Pass 8 KG-side helper).
    The orchestrator-side keeps the Postgres + Qdrant mirror; the
    graph reconciler picks up the new shared row on its next pass.

    ``share_origin`` records why this shared projection exists (LUM-586):

    * ``"user"`` (default) — a direct LUM-581 entity share. Callers that
      publish an entity on its own (e.g. ``routes/scope.py``) omit the kwarg
      and get ``"user"`` so a later document cascade cannot silently
      reclassify a hand-shared entity as document-only.
    * ``"document"`` — a LUM-586 document cascade
      (``document_entity_cascade.cascade_share_document_entities``).

    When both paths touch the same row the ``ON CONFLICT`` rule promotes
    ``share_origin`` to ``"multiple"``; refcounted retraction reads it to
    decide whether a document unshare may drop the projection.
    """
    _validate_target_scope(target_scope)
    if share_origin not in ("document", "user"):
        raise ValueError(
            f"share_origin must be 'document' or 'user'; got {share_origin!r}"
        )
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

    row = _insert_projection_or_rollback_qdrant(
        collection="entities",
        point_id=point_id,
        insert_sql="""
        INSERT INTO entities (entity_id, name, entity_type, aliases, context_tags,
                              mention_count, user_id, scope, published_from,
                              extraction_quality, share_origin, is_staged)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE)
        ON CONFLICT (published_from, scope) WHERE published_from IS NOT NULL DO UPDATE SET
            name = EXCLUDED.name,
            entity_type = EXCLUDED.entity_type,
            aliases = EXCLUDED.aliases,
            context_tags = EXCLUDED.context_tags,
            mention_count = EXCLUDED.mention_count,
            -- LUM-586 provenance promotion: a NULL (pre-migration) row adopts the
            -- incoming origin; identical origins are idempotent; a genuinely
            -- different origin (user vs document, or already 'multiple') promotes
            -- to 'multiple' so a doc unshare downgrades rather than deletes.
            share_origin = CASE
                WHEN entities.share_origin IS NULL THEN EXCLUDED.share_origin
                WHEN entities.share_origin = EXCLUDED.share_origin THEN entities.share_origin
                ELSE 'multiple'
              END,
            updated_at = NOW()
        RETURNING *
        """,
        params=(
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
            share_origin,
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

    row = _insert_projection_or_rollback_qdrant(
        collection="signals",
        point_id=point_id,
        insert_sql="""
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
        params=(
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
    """INTEGER-PK unpublish for ``file_index`` (LUM-157).

    Deletes the Postgres projection row **and** the reuse-vectors Qdrant chunk
    projections (scoped by ``published_from`` — never a match-all).

    LUM-586: after the chunk teardown, retract the document's cascaded shared
    **entity** projections (refcounted — only those this document was the last
    justification for). ``file_path`` is resolved from the deleted shared row so
    the retraction planner can enumerate the document's extracted entities. This
    single choke point covers both owner unshare (route + share_document job)
    and admin unshare (``admin_unshare`` routes ``files`` through here).
    """
    ms = config.get_metadata_store()
    deleted = ms.fetch_one(
        "DELETE FROM file_index WHERE published_from = %s AND scope = %s "
        "RETURNING id, user_id, file_path",
        (int(src_pk), target_scope),
    )
    if not deleted:
        return 0
    owner = deleted.get("user_id") or ""
    file_path = deleted.get("file_path") or ""
    _unproject_file_chunks(int(src_pk), owner, target_scope)
    if target_scope == "shared" and file_path and owner:
        from services import document_entity_cascade

        document_entity_cascade.retract_document_entities(
            file_path=file_path, owner_user_id=owner
        )
    return 1


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
