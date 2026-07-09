# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Graph-aware entity sharing: document → extracted-entity cascade (LUM-586).

When a household member shares a document, the document's **extracted
entities** (and, KG-side, the ``RELATES_TO`` edges between them) are projected
into shared scope so the household knowledge graph reflects what the shared
document is *about* — not just its raw chunks (LUM-157 already mirrors chunks).

Two directions:

* :func:`cascade_share_document_entities` — on share, project every personal
  entity extracted from the document into ``scope='shared'`` (Postgres + Qdrant
  via :mod:`services.projection`) with ``share_origin='document'`` provenance,
  then fire :attr:`events.Event.DOCUMENT_SHARED` so the KG service MERGEs the
  shared nodes and sweeps incident edges (``GRAPH_MODE=service``).
* :func:`retract_document_entities` — on unshare/purge, drop the shared entity
  projections that the document was the *last* justification for
  (refcounted; see :func:`plan_document_entity_retraction`), and downgrade
  ``share_origin='multiple'`` rows to ``'user'`` when the document was their
  last *document* justification.

Mode gating
-----------
The whole cascade is a **no-op** unless the knowledge-graph feature is enabled
(``config.get_graph_mode() != 'disabled'``). Note the gate is **graph mode**,
not ``config.get_graph_store()``: in ``GRAPH_MODE=service`` the orchestrator
process has *no* in-process FalkorDB store (``GRAPH_BACKEND`` is set only on the
``lumogis-graph`` container), so a store-presence gate would wrongly disable the
primary target. The Postgres+Qdrant shared rows are created in any non-disabled
mode so "my shared items" and unshare work regardless of graph mode; the graph
projection itself is ``service``-mode-only in v1 (in-process parity deferred).

Graph teardown
--------------
Retraction deletes the **authoritative** Postgres shared row + Qdrant point.
The shared FalkorDB node is removed by the KG service's reconcile orphan-GC
(``garbage_collect_orphan_nodes`` DETACH-DELETEs ``:Entity`` projection nodes
whose ``(user_id, lumogis_id)`` no longer exists in Postgres) — the same
best-effort, reconcile-backed teardown LUM-581 relies on (``unproject_entity``
has no direct graph call). Prompt service-mode graph teardown (a
``DOCUMENT_UNSHARED`` webhook) is a deferred follow-up.
"""

from __future__ import annotations

import logging

import hooks
from auth import UserContext
from events import Event
from models.webhook import DocumentSharedPayload
from models.webhook import SharedEntityRef

import config

_log = logging.getLogger(__name__)

# Cap the DOCUMENT_SHARED payload size so a large document (hundreds of
# extracted entities) is dispatched in bounded batches rather than one giant
# webhook body. The KG side processes each batch independently and idempotently.
_SHARE_BATCH = 200


def _graph_feature_enabled() -> bool:
    """True when the KG feature is on (either graph mode; see module docstring)."""
    try:
        return config.get_graph_mode() != "disabled"
    except Exception:  # pragma: no cover — defensive: unknown/unwired mode
        return False


def _extracted_personal_entities(ms, file_path: str, owner_user_id: str) -> list[dict]:
    """Return the owner's personal entities extracted from ``file_path``.

    Mirrors ``services.documents`` provenance: ``entity_relations`` rows with
    ``evidence_type='DOCUMENT'`` and ``evidence_id=<file_path>`` joined to the
    personal ``entities`` row. Full rows are returned so
    :func:`services.projection.project_entity` can mirror name/aliases/tags.
    """
    return ms.fetch_all(
        """
        SELECT DISTINCT e.*
        FROM entity_relations er
        JOIN entities e
          ON e.entity_id = er.source_id AND e.user_id = er.user_id
        WHERE er.evidence_type = 'DOCUMENT'
          AND er.evidence_id = %s
          AND er.user_id = %s
          AND e.scope = 'personal'
          AND (e.is_staged IS NOT TRUE)
        """,
        (file_path, owner_user_id),
    )


def _dispatch_document_shared(
    *, file_path: str, user_id: str, target_scope: str, refs: list[SharedEntityRef]
) -> None:
    """Fire ``DOCUMENT_SHARED`` in bounded batches (fire-and-forget).

    Consumed only when ``GRAPH_MODE=service`` (the outbound webhook callback is
    wired by :mod:`services.graph_webhook_dispatcher`); with no registered
    listener the fire is a harmless no-op.
    """
    for start in range(0, len(refs), _SHARE_BATCH):
        batch = refs[start : start + _SHARE_BATCH]
        payload = DocumentSharedPayload(
            file_path=file_path,
            user_id=user_id,
            target_scope=target_scope,  # type: ignore[arg-type]
            entities=batch,
        )
        # Fire the raw kwargs (not the model): graph_webhook_dispatcher's
        # kwargs-only callback rebuilds the payload and posts the envelope.
        hooks.fire_background(
            Event.DOCUMENT_SHARED,
            file_path=payload.file_path,
            user_id=payload.user_id,
            target_scope=payload.target_scope,
            entities=[r.model_dump() for r in batch],
        )


def cascade_share_document_entities(
    *, src_file: dict, actor: UserContext, target_scope: str = "shared"
) -> tuple[int, int]:
    """Project a shared document's extracted entities into ``target_scope``.

    Returns ``(projected, failed)`` so the share_document handler can fold the
    entity cascade into its honest ``partial`` status. A no-op ``(0, 0)`` when
    the graph feature is disabled or the document has no extracted entities.

    Owner invariant: ``src_file`` is the caller's OWN personal ``file_index``
    row (the publish path fetches ``WHERE user_id=caller AND scope='personal'``),
    so ``actor.user_id`` is the entity owner.
    """
    from services import projection

    if not _graph_feature_enabled():
        return 0, 0
    file_path = str(src_file.get("file_path") or "")
    if not file_path:
        return 0, 0
    owner = str(src_file.get("user_id") or actor.user_id)
    if owner != str(actor.user_id):
        raise ValueError(
            "cascade_share_document_entities: source owner does not match actor "
            f"({owner!r} != {actor.user_id!r})"
        )

    ms = config.get_metadata_store()
    rows = _extracted_personal_entities(ms, file_path, owner)
    if not rows:
        return 0, 0

    projected = 0
    failed = 0
    refs: list[SharedEntityRef] = []
    for e in rows:
        try:
            proj = projection.project_entity(
                e, target_scope=target_scope, actor=actor, share_origin="document"
            )
            refs.append(
                SharedEntityRef(
                    src_entity_id=str(e["entity_id"]),
                    proj_entity_id=str(proj.get("entity_id")),
                    name=str(e.get("name") or ""),
                    entity_type=str(e.get("entity_type") or ""),
                )
            )
            projected += 1
        except Exception:
            failed += 1
            _log.warning(
                "cascade_share: entity projection failed file=%s entity=%s",
                file_path[:80],
                e.get("entity_id"),
                exc_info=True,
            )

    if refs:
        _dispatch_document_shared(
            file_path=file_path,
            user_id=owner,
            target_scope=target_scope,
            refs=refs,
        )
    _log.info(
        "cascade_share: file=%s owner=%s projected=%d failed=%d",
        file_path[:80],
        owner,
        projected,
        failed,
    )
    return projected, failed


def _split_retract_and_downgrade(rows: list[dict]) -> tuple[list[str], list[str]]:
    """Classify planner rows into delete vs ``multiple`` → ``user`` downgrades."""
    retract: list[str] = []
    downgrade: list[str] = []
    for r in rows:
        if bool(r.get("other_justification")):
            continue
        origin = r.get("share_origin")
        if origin == "document":
            retract.append(str(r["src_entity_id"]))
        elif origin == "multiple":
            downgrade.append(str(r["src_entity_id"]))
        # 'user' / NULL → keep
    return retract, downgrade


def prune_stale_document_entity_relations(
    ms, file_path: str, owner_user_id: str, keep_entity_ids: list[str]
) -> list[str]:
    """Drop DOCUMENT relations for ``file_path`` not in the current extraction set.

    Re-ingest only INSERTs new ``entity_relations`` rows (``ON CONFLICT DO
    NOTHING``); without this prune, ``_extracted_personal_entities`` would keep
    returning entities dropped by the latest extraction. Returns the personal
    ``entity_id`` values whose relations were removed (the prior-minus-new diff).
    """
    if not file_path or not owner_user_id:
        return []
    rows = ms.fetch_all(
        """
        SELECT source_id
        FROM entity_relations
        WHERE evidence_type = 'DOCUMENT'
          AND evidence_id = %s
          AND user_id = %s
          AND NOT (source_id = ANY(%s::uuid[]))
        """,
        (file_path, owner_user_id, keep_entity_ids or []),
    )
    if not rows:
        return []
    removed = [str(r["source_id"]) for r in rows]
    ms.execute(
        """
        DELETE FROM entity_relations
        WHERE evidence_type = 'DOCUMENT'
          AND evidence_id = %s
          AND user_id = %s
          AND source_id = ANY(%s::uuid[])
        """,
        (file_path, owner_user_id, removed),
    )
    return removed


def plan_document_entity_retraction(
    ms, file_path: str, owner_user_id: str
) -> tuple[list[str], list[str]]:
    """Refcounted retraction plan for a document unshare/purge.

    Returns ``(retract_ids, downgrade_ids)`` — personal ``entity_id`` values
    whose shared projection should be **deleted** (``retract_ids``) or
    **downgraded** ``multiple`` → ``user`` (``downgrade_ids``).

    Decision per extracted entity with a live shared projection, given the
    projection's ``share_origin`` and whether the owner still has **another**
    currently-shared document that mentions it (``other_justification``):

    * ``'user'`` or ``NULL`` (pre-migration) → keep (never doc-retracted).
    * ``'document'`` → retract iff no other shared doc justifies it.
    * ``'multiple'`` → downgrade to ``'user'`` iff no other shared doc justifies
      it (the direct share still holds); otherwise keep as ``'multiple'``.
    """
    rows = ms.fetch_all(
        """
        SELECT DISTINCT pe.entity_id AS src_entity_id,
               sh.share_origin AS share_origin,
               EXISTS (
                 SELECT 1
                 FROM file_index sf
                 JOIN entity_relations er2
                   ON er2.evidence_id = sf.file_path AND er2.user_id = sf.user_id
                 WHERE sf.scope = 'shared'
                   AND sf.user_id = %s
                   AND sf.file_path <> %s
                   AND er2.evidence_type = 'DOCUMENT'
                   AND er2.source_id = pe.entity_id
               ) AS other_justification
        FROM entity_relations er
        JOIN entities pe
          ON pe.entity_id = er.source_id AND pe.user_id = er.user_id
         AND pe.scope = 'personal'
        JOIN entities sh
          ON sh.published_from = pe.entity_id AND sh.scope = 'shared'
        WHERE er.evidence_type = 'DOCUMENT'
          AND er.evidence_id = %s
          AND er.user_id = %s
        """,
        (owner_user_id, file_path, file_path, owner_user_id),
    )
    return _split_retract_and_downgrade(rows)


def plan_reingest_removed_entity_retraction(
    ms, file_path: str, owner_user_id: str, removed_entity_ids: list[str]
) -> tuple[list[str], list[str]]:
    """Refcounted retraction plan for entities dropped on a shared-doc re-ingest.

    Same ``share_origin`` / ``other_justification`` rules as
    :func:`plan_document_entity_retraction`, but scoped to an explicit removed-id
    list (relations already pruned — the unshare planner's join would miss them).
    """
    if not removed_entity_ids:
        return [], []
    rows = ms.fetch_all(
        """
        SELECT DISTINCT pe.entity_id AS src_entity_id,
               sh.share_origin AS share_origin,
               EXISTS (
                 SELECT 1
                 FROM file_index sf
                 JOIN entity_relations er2
                   ON er2.evidence_id = sf.file_path AND er2.user_id = sf.user_id
                 WHERE sf.scope = 'shared'
                   AND sf.user_id = %s
                   AND sf.file_path <> %s
                   AND er2.evidence_type = 'DOCUMENT'
                   AND er2.source_id = pe.entity_id
               ) AS other_justification
        FROM entities pe
        JOIN entities sh
          ON sh.published_from = pe.entity_id AND sh.scope = 'shared'
        WHERE pe.scope = 'personal'
          AND pe.user_id = %s
          AND pe.entity_id = ANY(%s::uuid[])
        """,
        (owner_user_id, file_path, owner_user_id, removed_entity_ids),
    )
    return _split_retract_and_downgrade(rows)


def _apply_entity_retraction_plan(
    *,
    file_path: str,
    owner_user_id: str,
    retract_ids: list[str],
    downgrade_ids: list[str],
    log_prefix: str,
) -> int:
    from services import projection

    ms = config.get_metadata_store()
    retracted = 0
    for src_entity_id in retract_ids:
        try:
            retracted += projection.unproject_entity(src_entity_id, "shared")
        except Exception:
            _log.warning(
                "%s: unproject failed file=%s entity=%s",
                log_prefix,
                file_path[:80],
                src_entity_id,
                exc_info=True,
            )
    if downgrade_ids:
        try:
            ms.execute(
                "UPDATE entities SET share_origin = 'user', updated_at = NOW() "
                "WHERE scope = 'shared' AND published_from = ANY(%s::uuid[])",
                (downgrade_ids,),
            )
        except Exception:
            _log.warning(
                "%s: downgrade failed file=%s count=%d",
                log_prefix,
                file_path[:80],
                len(downgrade_ids),
                exc_info=True,
            )
    _log.info(
        "%s: file=%s owner=%s retracted=%d downgraded=%d",
        log_prefix,
        file_path[:80],
        owner_user_id,
        retracted,
        len(downgrade_ids),
    )
    return retracted


def retract_document_entities(*, file_path: str, owner_user_id: str) -> int:
    """Retract/downgrade shared entity projections after a document unshare.

    Standalone path used by the owner unshare (``projection.unproject_file``)
    and admin unshare (``admin_unshare``) — NOT the purge path, which folds the
    Postgres deletes into its own transaction (see
    ``document_purge._postgres_arm``).

    Deletes each ``retract`` projection via ``projection.unproject_entity``
    (Postgres shared row + Qdrant point; the shared graph node is GC'd by the
    KG reconcile). Downgrades ``multiple`` → ``user`` in place. Returns the
    number of shared projections retracted (deleted).
    """
    if not _graph_feature_enabled() or not file_path or not owner_user_id:
        return 0

    ms = config.get_metadata_store()
    retract_ids, downgrade_ids = plan_document_entity_retraction(
        ms, file_path, owner_user_id
    )
    return _apply_entity_retraction_plan(
        file_path=file_path,
        owner_user_id=owner_user_id,
        retract_ids=retract_ids,
        downgrade_ids=downgrade_ids,
        log_prefix="retract_document_entities",
    )


def retract_removed_entities_on_reingest(
    *, file_path: str, owner_user_id: str, removed_entity_ids: list[str]
) -> int:
    """Diff-retract shared projections after a shared-doc re-ingest (LUM-604).

    Called from :func:`services.projection.reproject_shared_on_reingest` with
    the personal entity ids pruned from ``entity_relations`` during ingest.
    """
    if (
        not _graph_feature_enabled()
        or not file_path
        or not owner_user_id
        or not removed_entity_ids
    ):
        return 0

    ms = config.get_metadata_store()
    retract_ids, downgrade_ids = plan_reingest_removed_entity_retraction(
        ms, file_path, owner_user_id, removed_entity_ids
    )
    return _apply_entity_retraction_plan(
        file_path=file_path,
        owner_user_id=owner_user_id,
        retract_ids=retract_ids,
        downgrade_ids=downgrade_ids,
        log_prefix="retract_removed_entities_on_reingest",
    )
