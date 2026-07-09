# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Multi-store purge orchestration for ingested documents (LUM-160 / LUM-500 / LUM-501)."""

from __future__ import annotations

import json
import logging

from services.memory_purge import PurgeResult
from services.memory_purge import _retry_store_arm
from services.point_ids import document_chunk_point_id

import config

_log = logging.getLogger(__name__)


class DocumentNotFoundError(Exception):
    """Raised when the caller has no matching personal document row."""


# ---------------------------------------------------------------------------
# Tombstone helpers (LUM-500 / LUM-501)
# ---------------------------------------------------------------------------


def _tombstone_insert(
    ms,
    user_id: str,
    document_id: int,
    file_path: str,
    chunk_count: int,
    orphan_entity_ids: list[str],
) -> None:
    ms.execute(
        "INSERT INTO purged_documents "
        "(user_id, document_id, file_path, chunk_count, orphan_entity_ids) "
        "VALUES (%s, %s, %s, %s, %s::jsonb) "
        "ON CONFLICT (user_id, document_id) DO NOTHING",
        (user_id, document_id, file_path, chunk_count, json.dumps(orphan_entity_ids)),
    )


def _tombstone_update(
    ms,
    user_id: str,
    document_id: int,
    result: PurgeResult,
) -> None:
    # SCOPE-EXEMPT: purged_documents tombstone updates are owner-keyed only.
    if not result.partial:
        ms.execute(
            # SCOPE-EXEMPT: purged_documents resolved tombstone update (owner-keyed).
            "UPDATE purged_documents "
            "SET qdrant_deleted = %s, graph_deleted = %s, "
            "qdrant_entities_deleted = %s, errors = %s::jsonb, "
            "resolved_at = NOW() "
            "WHERE user_id = %s AND document_id = %s",
            (
                result.qdrant_deleted,
                result.graph_deleted,
                result.qdrant_entities_deleted,
                json.dumps(result.errors),
                user_id,
                document_id,
            ),
        )
    else:
        ms.execute(
            # SCOPE-EXEMPT: purged_documents partial tombstone update (owner-keyed).
            "UPDATE purged_documents "
            "SET qdrant_deleted = %s, graph_deleted = %s, "
            "qdrant_entities_deleted = %s, errors = %s::jsonb "
            "WHERE user_id = %s AND document_id = %s",
            (
                result.qdrant_deleted,
                result.graph_deleted,
                result.qdrant_entities_deleted,
                json.dumps(result.errors),
                user_id,
                document_id,
            ),
        )


def _tombstone_fetch(ms, user_id: str, document_id: int) -> dict | None:
    # SCOPE-EXEMPT: purged_documents lookup by owner + document_id for purge retry.
    return ms.fetch_one(
        "SELECT file_path, chunk_count, qdrant_deleted, graph_deleted, "
        "qdrant_entities_deleted, orphan_entity_ids "
        "FROM purged_documents WHERE user_id = %s AND document_id = %s",
        (user_id, document_id),
    )


# ---------------------------------------------------------------------------
# Store arms
# ---------------------------------------------------------------------------


def _run_qdrant_arm(
    user_id: str, file_path: str, chunk_count: int, document_id: int
) -> tuple[bool, list[str]]:
    def _arm() -> None:
        vs = config.get_vector_store()
        # Primary: delete all chunks for this path (handles sparse indices when
        # block_ingest skips early chunks but writes later ones). Note the
        # (user_id, file_path) sweep already catches this source's shared
        # projection chunks (LUM-157 projections keep the owner's user_id +
        # file_path), so deleting a shared source does not orphan them.
        vs.delete_where(
            collection="documents",
            filter={
                "must": [
                    {"key": "user_id", "match": {"value": user_id}},
                    {"key": "file_path", "match": {"value": file_path}},
                ]
            },
        )
        # LUM-157 belt-and-suspenders: also delete the shared chunk projections
        # keyed by published_from (exact + explicit; covers any future divergence
        # between the source file_path and a projection's file_path).
        vs.delete_where(
            collection="documents",
            filter={
                "must": [
                    {"key": "published_from", "match": {"value": int(document_id)}},
                    {"key": "user_id", "match": {"value": user_id}},
                    {"key": "scope", "match": {"value": "shared"}},
                ]
            },
        )
        # Legacy fallback: deterministic IDs for points ingested before file_path
        # was indexed in the payload (pre-LUM-505).
        for i in range(chunk_count):
            pid = document_chunk_point_id(user_id, file_path, i)
            vs.delete(collection="documents", id=pid)

    return _retry_store_arm("qdrant", _arm)


def _run_graph_arm(file_path: str, user_id: str) -> tuple[bool, list[str]]:
    gs = config.get_graph_store()
    if gs is None:
        return True, []

    def _arm() -> None:
        from plugins.graph.writer import delete_document as graph_delete_document

        graph_delete_document(gs, file_path=file_path, user_id=user_id)

    return _retry_store_arm("graph", _arm)


def _run_qdrant_entity_arm(user_id: str, entity_ids: list[str]) -> tuple[bool, list[str]]:
    """Delete orphaned entity Qdrant points (LUM-501).

    Two filters, both scoped to the owning ``user_id``:

    * personal points — payload ``entity_id`` equals the orphaned id;
    * shared/system projection points — payload ``published_from`` equals the
      orphaned id (the projection's own ``entity_id`` is a distinct uuid5, see
      ``services.projection.project_entity``). Postgres cascades these rows via
      the ``published_from`` FK (``ON DELETE CASCADE``); we mirror that in Qdrant
      so a deleted personal entity cannot linger in shared-scope search.
    """
    if not entity_ids:
        return True, []

    def _arm() -> None:
        vs = config.get_vector_store()
        vs.delete_where(
            collection="entities",
            filter={
                "must": [
                    {"key": "user_id", "match": {"value": user_id}},
                    {"key": "entity_id", "match": {"any": entity_ids}},
                ]
            },
        )
        vs.delete_where(
            collection="entities",
            filter={
                "must": [
                    {"key": "user_id", "match": {"value": user_id}},
                    {"key": "published_from", "match": {"any": entity_ids}},
                ]
            },
        )

    return _retry_store_arm("qdrant_entities", _arm)


def _delete_shared_entity_points(user_id: str, src_entity_ids: list[str]) -> None:
    """Best-effort delete of shared entity Qdrant points for retracted projections (LUM-586).

    Shared projection points carry ``published_from`` = the personal source
    ``entity_id`` and ``scope='shared'``; only those are removed (never the
    surviving personal points, which ``_run_qdrant_entity_arm`` would also
    match). The shared FalkorDB node is GC'd by the KG reconcile. Failures are
    logged, not raised — the authoritative Postgres shared row is already gone
    so the item cannot resurface in "my shared items".
    """
    if not src_entity_ids:
        return
    try:
        vs = config.get_vector_store()
        vs.delete_where(
            collection="entities",
            filter={
                "must": [
                    {"key": "user_id", "match": {"value": user_id}},
                    {"key": "published_from", "match": {"any": src_entity_ids}},
                    {"key": "scope", "match": {"value": "shared"}},
                ]
            },
        )
    except Exception:
        _log.warning(
            "purge_document: shared-entity Qdrant cleanup failed count=%d",
            len(src_entity_ids),
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def purge_document(*, user_id: str, document_id: int) -> PurgeResult:
    """Hard-delete a personal document across Postgres, Qdrant, and optional graph.

    On Postgres success a tombstone row is inserted in ``purged_documents``.
    Re-calling with the same ``document_id`` after a partial failure retries
    only the failed store arms and updates the tombstone (LUM-500).

    Orphaned entities (personal, zero remaining relations) are also removed
    from Postgres and the Qdrant ``entities`` collection (LUM-501).
    """
    ms = config.get_metadata_store()
    result = PurgeResult()

    row = ms.fetch_one(
        "SELECT id, file_path, chunk_count FROM file_index "
        "WHERE id = %s AND user_id = %s AND scope = 'personal'",
        (document_id, user_id),
    )

    if not row:
        return _handle_missing_row(ms, user_id, document_id, result)

    file_path = row["file_path"]
    chunk_count = int(row.get("chunk_count") or 0)

    # --- Postgres arm ---
    orphan_entity_ids: list[str] = []
    # LUM-586: shared entity projections this document was the last justification
    # for — deleted in-transaction (survivors) or cascaded (orphans); their
    # shared Qdrant points are cleaned post-commit. The shared FalkorDB nodes are
    # removed by the KG reconcile orphan-GC.
    shared_retract_ids: list[str] = []

    def _postgres_arm() -> None:
        nonlocal orphan_entity_ids, shared_retract_ids
        with ms.transaction():
            ms.execute(
                "DELETE FROM file_index WHERE published_from = %s",
                (document_id,),
            )
            # LUM-586: plan shared-entity retraction BEFORE the entity_relations
            # delete below removes the provenance rows the planner enumerates
            # over. Runs inside the txn so it reads the just-deleted shared
            # file_index row as gone (and other shared docs as still present).
            from services import document_entity_cascade

            shared_retract_ids, _shared_downgrade_ids = (
                document_entity_cascade.plan_document_entity_retraction(
                    ms, file_path, user_id
                )
            )
            # Detect orphans while their entity_relations rows still exist.
            # SCOPE-EXEMPT: inline entity GC targets personal entities for the purging user.
            orphan_rows = ms.fetch_all(
                "SELECT e.entity_id FROM entities e "
                "WHERE e.user_id = %s AND e.scope = 'personal' "
                "AND e.entity_id IN ("
                "  SELECT DISTINCT er.source_id FROM entity_relations er "
                "  WHERE er.evidence_id = %s AND er.evidence_type = 'DOCUMENT'"
                "  AND er.user_id = %s"
                ") "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM entity_relations er2 "
                "  WHERE er2.source_id = e.entity_id AND er2.user_id = %s"
                "  AND NOT (er2.evidence_id = %s AND er2.evidence_type = 'DOCUMENT')"
                ")",
                (user_id, file_path, user_id, user_id, file_path),
            )
            orphan_entity_ids = [r["entity_id"] for r in (orphan_rows or [])]
            ms.execute(
                "DELETE FROM entity_relations "
                "WHERE evidence_id = %s AND evidence_type = 'DOCUMENT' AND user_id = %s",
                (file_path, user_id),
            )
            if orphan_entity_ids:
                ms.execute(
                    "DELETE FROM entities "
                    "WHERE entity_id = ANY(%s::uuid[]) AND user_id = %s AND scope = 'personal'",
                    (orphan_entity_ids, user_id),
                )
            # LUM-586: drop the shared projections of SURVIVING entities this
            # document was the last shared-doc justification for. Orphaned
            # entities' shared rows already cascaded via the personal delete
            # above (published_from FK ON DELETE CASCADE); this DELETE is a
            # harmless no-op for those and removes the rest. Downgrade
            # 'multiple' rows (a direct share still holds) rather than delete.
            if shared_retract_ids:
                ms.execute(
                    "DELETE FROM entities "
                    "WHERE scope = 'shared' AND published_from = ANY(%s::uuid[])",
                    (shared_retract_ids,),
                )
            if _shared_downgrade_ids:
                ms.execute(
                    "UPDATE entities SET share_origin = 'user', updated_at = NOW() "
                    "WHERE scope = 'shared' AND published_from = ANY(%s::uuid[])",
                    (_shared_downgrade_ids,),
                )
            ms.execute(
                "DELETE FROM file_index WHERE id = %s AND user_id = %s AND scope = 'personal'",
                (document_id, user_id),
            )
            # Insert the tombstone inside the same transaction as the deletes so
            # the recovery record is atomic with them: a crash can never leave
            # rows deleted without a retryable tombstone (LUM-500 / LUM-501).
            _tombstone_insert(ms, user_id, document_id, file_path, chunk_count, orphan_entity_ids)

    try:
        _postgres_arm()
        result.postgres_deleted = True
        result.orphan_entity_ids = orphan_entity_ids
    except Exception as exc:
        result.errors.append(f"postgres: {exc}")
        _log.warning("purge_document: Postgres arm failed document_id=%s", document_id)
        return result

    # LUM-586: clean the shared entity Qdrant points for retracted projections
    # (post-commit, best-effort). Survivors' personal points are untouched.
    _delete_shared_entity_points(user_id, shared_retract_ids)

    if orphan_entity_ids:
        _log.info(
            "document_entity_gc",
            extra={
                "event": "document_entity_gc",
                "document_id": document_id,
                "file_path": file_path,
                "orphan_count": len(orphan_entity_ids),
                "user_id": user_id,
            },
        )

    return _run_store_arms(ms, user_id, document_id, file_path, chunk_count, result)


def _handle_missing_row(ms, user_id: str, document_id: int, result: PurgeResult) -> PurgeResult:
    """Called when no file_index row exists; checks for a partial tombstone."""
    tombstone = _tombstone_fetch(ms, user_id, document_id)
    if tombstone is None:
        raise DocumentNotFoundError(document_id)

    if (
        tombstone["qdrant_deleted"]
        and tombstone["graph_deleted"]
        and tombstone["qdrant_entities_deleted"]
    ):
        # Already fully resolved — idempotent success.
        result.postgres_deleted = True
        result.qdrant_deleted = True
        result.graph_deleted = True
        result.qdrant_entities_deleted = True
        return result

    # Partial tombstone — retry only the failed store arms.
    file_path = tombstone["file_path"]
    chunk_count = int(tombstone.get("chunk_count") or 0)
    result.postgres_deleted = True
    result.qdrant_deleted = bool(tombstone["qdrant_deleted"])
    result.graph_deleted = bool(tombstone["graph_deleted"])
    result.qdrant_entities_deleted = bool(tombstone["qdrant_entities_deleted"])
    entity_ids_raw = tombstone.get("orphan_entity_ids") or []
    result.orphan_entity_ids = (
        json.loads(entity_ids_raw) if isinstance(entity_ids_raw, str) else list(entity_ids_raw)
    )
    return _run_store_arms(ms, user_id, document_id, file_path, chunk_count, result)


def _run_store_arms(
    ms,
    user_id: str,
    document_id: int,
    file_path: str,
    chunk_count: int,
    result: PurgeResult,
) -> PurgeResult:
    """Execute whichever store arms have not yet succeeded, then update the tombstone."""
    if not result.qdrant_deleted:
        ok, errs = _run_qdrant_arm(user_id, file_path, chunk_count, document_id)
        result.qdrant_deleted = ok
        result.errors.extend(errs)

    if not result.graph_deleted:
        ok, errs = _run_graph_arm(file_path, user_id)
        result.graph_deleted = ok
        result.errors.extend(errs)

    if not result.qdrant_entities_deleted:
        ok, errs = _run_qdrant_entity_arm(user_id, result.orphan_entity_ids)
        result.qdrant_entities_deleted = ok
        result.errors.extend(errs)

    _tombstone_update(ms, user_id, document_id, result)

    if result.partial:
        _log.warning(
            "document_purge_partial",
            extra={
                "event": "document_purge_partial",
                "document_id": document_id,
                "file_path": file_path,
                "qdrant_deleted": result.qdrant_deleted,
                "graph_deleted": result.graph_deleted,
                "qdrant_entities_deleted": result.qdrant_entities_deleted,
                "errors": result.errors,
                "user_id": user_id,
            },
        )

    return result
