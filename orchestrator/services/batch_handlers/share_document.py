# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Batch handlers: household document share / unshare projection (LUM-157).

``share_document`` mirrors a personal document's Qdrant content chunks (and the
``file_index`` metadata row) into shared scope so other household members can
search + document-chat it; ``unshare_document`` removes both. Both:

* re-check ownership at run time (a replayed/stale job, or an ownership change
  between enqueue and run, is an explicit terminal **no-op** — never
  retry-to-dead);
* serialise against a concurrent re-ingest of the same document via the
  per-document advisory lock (``services/share_lock.py``);
* report progress via ``ingest_progress`` and set their own terminal stage
  (``done`` / ``partial``); ``batch_queue._run_one_tick`` does NOT force these
  kinds to ``done``, so a handler-set ``partial`` survives.
"""

from __future__ import annotations

import logging

from auth import UserContext
from pydantic import BaseModel
from pydantic import Field
from services.batch_queue import register_batch_handler

import config

_log = logging.getLogger(__name__)


class ShareDocumentPayload(BaseModel):
    document_id: int = Field(..., gt=0)


def _fetch_personal_source(user_id: str, document_id: int) -> dict | None:
    ms = config.get_metadata_store()
    return ms.fetch_one(
        "SELECT * FROM file_index WHERE id = %s AND user_id = %s AND scope = 'personal'",
        (document_id, user_id),
    )


@register_batch_handler("share_document", ShareDocumentPayload)
def handle_share(*, user_id: str, payload: ShareDocumentPayload, job_id: int) -> None:
    from services import ingest_progress as ip
    from services import projection as proj
    from services.share_lock import share_document_lock

    doc_id = payload.document_id
    src = _fetch_personal_source(user_id, doc_id)
    if src is None:
        # Stale/replayed job or ownership changed — terminal no-op, no retry.
        _log.info("share_document: source gone/foreign document_id=%s — no-op", doc_id)
        ip.update_ingest_job_progress(
            job_id=job_id,
            user_id=user_id,
            stage="done",
            status_message="No longer shareable",
        )
        return

    ip.update_ingest_job_progress(job_id=job_id, user_id=user_id, stage="projecting")
    actor = UserContext(user_id=user_id, is_authenticated=True)
    with share_document_lock(doc_id):
        _row, projected, failed = proj.project_file_with_status(
            src, target_scope="shared", actor=actor
        )
        # LUM-586: cascade the document's extracted entities into shared scope
        # (Postgres+Qdrant here; KG shared-graph projection via DOCUMENT_SHARED).
        # No-op unless the graph feature is enabled. Fold its partial counts in
        # so an entity that fails to project is reported honestly, same as a
        # chunk that fails to mirror.
        from services import document_entity_cascade as cascade

        ent_ok, ent_failed = cascade.cascade_share_document_entities(
            src_file=src, actor=actor
        )
        projected += ent_ok
        failed += ent_failed

    if failed > 0:
        ip.update_ingest_job_progress(
            job_id=job_id,
            user_id=user_id,
            stage="partial",
            status_message=f"Shared {projected} of {projected + failed} sections",
        )
    else:
        ip.update_ingest_job_progress(job_id=job_id, user_id=user_id, stage="done")


@register_batch_handler("unshare_document", ShareDocumentPayload)
def handle_unshare(*, user_id: str, payload: ShareDocumentPayload, job_id: int) -> None:
    from services import ingest_progress as ip
    from services import projection as proj
    from services.share_lock import share_document_lock

    doc_id = payload.document_id
    src = _fetch_personal_source(user_id, doc_id)
    if src is None:
        _log.info("unshare_document: source gone/foreign document_id=%s — no-op", doc_id)
        ip.update_ingest_job_progress(
            job_id=job_id,
            user_id=user_id,
            stage="done",
            status_message="No longer shared",
        )
        return

    ip.update_ingest_job_progress(job_id=job_id, user_id=user_id, stage="projecting")
    with share_document_lock(doc_id):
        proj.unproject_file(doc_id, target_scope="shared")
    ip.update_ingest_job_progress(job_id=job_id, user_id=user_id, stage="done")
