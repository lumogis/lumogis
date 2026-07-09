# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Document library CRUD — list, detail, delete, re-ingest (LUM-160)."""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from auth import UserContext
from models.api_v1 import DocumentDetail
from models.api_v1 import DocumentEntityLink
from models.api_v1 import DocumentStatus
from models.api_v1 import DocumentSummary
from models.api_v1 import ReingestQueuedResponse
from services import users as users_service
from services.document_purge import DocumentNotFoundError
from services.document_purge import purge_document
from services.entities import resolve_relation_source_id
from services.memory_purge import PurgeResult
from visibility import visible_filter

import config

_log = logging.getLogger(__name__)

_PATH_PREFIX_RE = re.compile(r"^(/home/|/data/|/app/)")


class SourceUnavailableError(Exception):
    """Raised when re-ingest cannot find the on-disk source file."""


def _sanitize_error_message(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw[:500]
    if _PATH_PREFIX_RE.search(text):
        parts = text.replace("\\", "/").split("/")
        text = parts[-1] if parts else text
    if "/" in text and len(text) > 80:
        return "Ingest failed"
    return text


def _relations_owner_user_id(row: dict[str, Any], ms) -> str:
    if row.get("scope") == "personal" or not row.get("published_from"):
        return row["user_id"]
    src = ms.fetch_one(
        "SELECT user_id FROM file_index WHERE id = %s AND scope = 'personal'",
        (row["published_from"],),
    )
    return src["user_id"] if src else row["user_id"]


def _job_path(payload: dict[str, Any], kind: str) -> str | None:
    if kind == "ingest_upload":
        return payload.get("stored_path")
    if kind == "ingest_watch_file":
        return payload.get("path")
    return None


def _fetch_inflight_jobs(user_id: str) -> list[dict[str, Any]]:
    ms = config.get_metadata_store()
    try:
        rows = ms.fetch_all(
            """
            SELECT id, kind, payload, status
            FROM user_batch_jobs
            WHERE user_id = %s
              AND kind IN ('ingest_upload', 'ingest_watch_file')
              AND status IN ('pending', 'running')
            ORDER BY id DESC
            """,
            (user_id,),
        )
    except Exception as exc:
        _log.warning("list_documents: in-flight job query failed — %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        path = _job_path(payload, row["kind"])
        if not path:
            continue
        out.append(
            {
                "job_id": row["id"],
                "kind": row["kind"],
                "path": path,
                "display_name": payload.get("original_filename") or Path(path).name,
            }
        )
    return out


def _fetch_shared_source_ids(user_id: str) -> set[int]:
    """Source document ids the caller currently has a shared projection for (LUM-157)."""
    ms = config.get_metadata_store()
    try:
        rows = ms.fetch_all(
            # SCOPE-EXEMPT: caller-owned shared projection rows (published_from lookup).
            "SELECT DISTINCT published_from FROM file_index "
            "WHERE user_id = %s AND scope = 'shared' AND published_from IS NOT NULL",
            (user_id,),
        )
    except Exception as exc:
        _log.warning("list_documents: shared-source query failed — %s", exc)
        return set()
    return {int(r["published_from"]) for r in rows if r.get("published_from") is not None}


def _fetch_inflight_share_jobs(user_id: str) -> dict[int, dict[str, Any]]:
    """Map ``document_id`` → in-flight share/unshare job for the caller (LUM-157).

    A single in-flight share/unshare job per document is expected (the enqueue
    path is idempotent — see :func:`share_document`); if more than one is found
    the most recent wins.
    """
    ms = config.get_metadata_store()
    try:
        rows = ms.fetch_all(
            """
            SELECT id, kind, payload
            FROM user_batch_jobs
            WHERE user_id = %s
              AND kind IN ('share_document', 'unshare_document')
              AND status IN ('pending', 'running')
            ORDER BY id DESC
            """,
            (user_id,),
        )
    except Exception as exc:
        _log.warning("list_documents: in-flight share job query failed — %s", exc)
        return {}
    by_doc: dict[int, dict[str, Any]] = {}
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        doc_id = payload.get("document_id")
        if doc_id is None:
            continue
        doc_id = int(doc_id)
        if doc_id in by_doc:
            continue  # keep the most recent (rows ordered id DESC)
        by_doc[doc_id] = {
            "job_id": int(row["id"]),
            "kind": row["kind"],
            "direction": "sharing" if row["kind"] == "share_document" else "unsharing",
        }
    return by_doc


def _derive_share_fields(
    row: dict[str, Any],
    *,
    caller_user_id: str,
    shared_source_ids: set[int],
    inflight_share: dict[int, dict[str, Any]],
) -> tuple[str, int | None, bool]:
    """Return ``(share_status, in_flight_share_job_id, is_owner)`` for a row.

    ``is_owner`` — a shared projection carries the *publisher's* ``user_id`` and
    the owner's own projection is collapsed out of their list, so any row whose
    ``user_id`` equals the caller is owned by the caller (personal source or a
    projection the collapse would have hidden). See ``list_documents``.
    """
    is_owner = row.get("user_id") == caller_user_id
    scope = row.get("scope") or "personal"
    doc_id = row.get("id")

    # Member's view of another owner's shared projection.
    if scope == "shared" and not is_owner:
        return "shared", None, False

    # Owner's source row: in-flight job > shared projection > personal.
    job = inflight_share.get(int(doc_id)) if doc_id is not None else None
    if job is not None:
        return job["direction"], job["job_id"], is_owner
    if doc_id is not None and int(doc_id) in shared_source_ids:
        return "shared", None, is_owner
    return "personal", None, is_owner


def _fetch_dead_jobs_by_path(user_id: str) -> dict[str, dict[str, Any]]:
    ms = config.get_metadata_store()
    try:
        rows = ms.fetch_all(
            """
            SELECT kind, payload, error
            FROM user_batch_jobs
            WHERE user_id = %s
              AND kind IN ('ingest_upload', 'ingest_watch_file')
              AND status = 'dead'
            ORDER BY finished_at DESC NULLS LAST, id DESC
            """,
            (user_id,),
        )
    except Exception as exc:
        _log.warning("list_documents: dead job query failed — %s", exc)
        return {}
    by_path: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        path = _job_path(payload, row["kind"])
        if not path or path in by_path:
            continue
        err = payload.get("error") or row.get("error")
        by_path[path] = {"error": err}
    return by_path


def _derive_status(
    *,
    file_path: str,
    chunk_count: int,
    inflight_paths: set[str],
    dead_by_path: dict[str, dict[str, Any]],
) -> tuple[DocumentStatus, str | None]:
    if file_path in inflight_paths:
        return DocumentStatus.indexing, None
    if chunk_count > 0:
        return DocumentStatus.indexed, None
    dead = dead_by_path.get(file_path)
    if dead:
        msg = _sanitize_error_message(dead.get("error")) or "Ingest failed"
        return DocumentStatus.failed, msg
    if chunk_count == 0:
        return DocumentStatus.failed, None
    return DocumentStatus.indexed, None


def _row_to_summary(
    row: dict[str, Any],
    *,
    inflight_paths: set[str],
    dead_by_path: dict[str, dict[str, Any]],
    caller_user_id: str,
    shared_source_ids: set[int],
    inflight_share: dict[int, dict[str, Any]],
) -> DocumentSummary:
    status, error_message = _derive_status(
        file_path=row["file_path"],
        chunk_count=int(row.get("chunk_count") or 0),
        inflight_paths=inflight_paths,
        dead_by_path=dead_by_path,
    )
    share_status, in_flight_share_job_id, is_owner = _derive_share_fields(
        row,
        caller_user_id=caller_user_id,
        shared_source_ids=shared_source_ids,
        inflight_share=inflight_share,
    )
    return DocumentSummary(
        document_id=row["id"],
        display_name=Path(row["file_path"]).name,
        file_path=row["file_path"],
        file_type=row.get("file_type") or "",
        chunk_count=int(row.get("chunk_count") or 0),
        entity_count=int(row.get("entity_count") or 0),
        scope=row.get("scope") or "personal",
        status=status,
        indexed_at=row.get("updated_at"),
        error_message=error_message,
        share_status=share_status,  # type: ignore[arg-type]
        in_flight_share_job_id=in_flight_share_job_id,
        is_owner=is_owner,
    )


def list_documents(user: UserContext, *, limit: int = 50) -> list[DocumentSummary]:
    """List ingested documents visible to the caller (household union default)."""
    ms = config.get_metadata_store()
    vis_clause, vis_params = visible_filter(user)

    try:
        rows = ms.fetch_all(
            f"""
            SELECT fi.id, fi.file_path, fi.file_type, fi.chunk_count, fi.user_id,
                   fi.scope, fi.updated_at, fi.published_from,
                   (
                     SELECT COUNT(*)::int
                     FROM entity_relations er
                     WHERE er.evidence_type = 'DOCUMENT'
                       AND er.evidence_id = fi.file_path
                       AND er.user_id = CASE
                         WHEN fi.scope = 'personal' OR fi.published_from IS NULL THEN fi.user_id
                         ELSE COALESCE(
                           (SELECT src.user_id FROM file_index src
                            WHERE src.id = fi.published_from AND src.scope = 'personal'
                            LIMIT 1),
                           fi.user_id
                         )
                       END
                   ) AS entity_count
            FROM file_index fi
            WHERE {vis_clause}
              -- LUM-157 collapse: hide the caller's OWN shared projection rows
              -- (they already see the personal source, marked share_status
              -- 'shared'). A projection carries the publisher's user_id, so
              -- other members' shared rows (user_id != caller) are kept.
              AND NOT (fi.published_from IS NOT NULL AND fi.user_id = %s)
            ORDER BY fi.updated_at DESC
            LIMIT %s
            """,
            (*vis_params, user.user_id, limit),
        )
    except Exception as exc:
        _log.warning("list_documents: DB query failed — %s", exc)
        return []

    inflight = _fetch_inflight_jobs(user.user_id)
    inflight_paths = {j["path"] for j in inflight}
    indexed_paths = {r["file_path"] for r in rows}
    dead_by_path = _fetch_dead_jobs_by_path(user.user_id)
    shared_source_ids = _fetch_shared_source_ids(user.user_id)
    inflight_share = _fetch_inflight_share_jobs(user.user_id)

    summaries = [
        _row_to_summary(
            r,
            inflight_paths=inflight_paths,
            dead_by_path=dead_by_path,
            caller_user_id=user.user_id,
            shared_source_ids=shared_source_ids,
            inflight_share=inflight_share,
        )
        for r in rows
    ]

    for job in inflight:
        if job["path"] in indexed_paths:
            continue
        summaries.append(
            DocumentSummary(
                document_id=None,
                in_flight_job_id=job["job_id"],
                display_name=job["display_name"],
                file_path=job["path"],
                file_type=Path(job["path"]).suffix.lstrip(".").lower(),
                chunk_count=0,
                entity_count=0,
                scope="personal",
                status=DocumentStatus.indexing,
                indexed_at=None,
                error_message=None,
            )
        )

    summaries.sort(
        key=lambda s: (s.indexed_at is None, s.indexed_at),
        reverse=True,
    )
    return summaries[:limit]


def get_document(user: UserContext, document_id: int) -> DocumentDetail:
    """Return document detail for a persisted ``file_index`` row."""
    ms = config.get_metadata_store()
    vis_clause, vis_params = visible_filter(user)
    row = ms.fetch_one(
        f"""
        SELECT fi.id, fi.file_path, fi.file_type, fi.file_hash, fi.chunk_count,
               fi.user_id, fi.scope, fi.updated_at, fi.published_from,
               (
                 SELECT COUNT(*)::int
                 FROM entity_relations er
                 WHERE er.evidence_type = 'DOCUMENT'
                   AND er.evidence_id = fi.file_path
                   AND er.user_id = CASE
                     WHEN fi.scope = 'personal' OR fi.published_from IS NULL THEN fi.user_id
                     ELSE COALESCE(
                       (SELECT src.user_id FROM file_index src
                        WHERE src.id = fi.published_from AND src.scope = 'personal'
                        LIMIT 1),
                       fi.user_id
                     )
                   END
               ) AS entity_count
        FROM file_index fi
        WHERE fi.id = %s AND {vis_clause}
        """,
        (document_id, *vis_params),
    )
    if not row:
        raise DocumentNotFoundError(document_id)

    owner_uid = _relations_owner_user_id(row, ms)
    entity_rows = ms.fetch_all(
        """
        SELECT e.entity_id, e.name, e.entity_type, e.published_from
        FROM entity_relations er
        JOIN entities e ON e.entity_id = er.source_id AND e.user_id = er.user_id
        WHERE er.evidence_type = 'DOCUMENT'
          AND er.evidence_id = %s
          AND er.user_id = %s
        ORDER BY e.name ASC
        """,
        (row["file_path"], owner_uid),
    )
    entities = [
        DocumentEntityLink(
            entity_id=resolve_relation_source_id(er),
            name=er.get("name") or "",
            entity_type=er.get("entity_type") or "",
        )
        for er in entity_rows
    ]

    inflight = _fetch_inflight_jobs(user.user_id)
    inflight_paths = {j["path"] for j in inflight}
    dead_by_path = _fetch_dead_jobs_by_path(user.user_id)
    status, error_message = _derive_status(
        file_path=row["file_path"],
        chunk_count=int(row.get("chunk_count") or 0),
        inflight_paths=inflight_paths,
        dead_by_path=dead_by_path,
    )
    share_status, in_flight_share_job_id, is_owner = _derive_share_fields(
        row,
        caller_user_id=user.user_id,
        shared_source_ids=_fetch_shared_source_ids(user.user_id),
        inflight_share=_fetch_inflight_share_jobs(user.user_id),
    )

    # LUM-585 — "Shared by {member}" attribution: only for a NON-owner viewing a
    # SHARED document. ``owner_uid`` is already resolved above, so this is one
    # extra user lookup on the single-row detail endpoint (no N+1; the list
    # endpoint deliberately leaves ``shared_by`` unset). Degrades to None.
    shared_by = None
    if not is_owner and share_status in ("shared", "partial"):
        shared_by = users_service.display_label_for(owner_uid)

    file_path = row["file_path"]
    return DocumentDetail(
        document_id=row["id"],
        display_name=Path(file_path).name,
        file_path=file_path,
        file_type=row.get("file_type") or "",
        chunk_count=int(row.get("chunk_count") or 0),
        entity_count=int(row.get("entity_count") or 0),
        scope=row.get("scope") or "personal",
        status=status,
        indexed_at=row.get("updated_at"),
        error_message=error_message,
        file_hash=row.get("file_hash"),
        entities=entities,
        source_available=Path(file_path).is_file(),
        share_status=share_status,  # type: ignore[arg-type]
        in_flight_share_job_id=in_flight_share_job_id,
        is_owner=is_owner,
        shared_by=shared_by,
    )


def delete_document(user_id: str, document_id: int) -> PurgeResult:
    return purge_document(user_id=user_id, document_id=document_id)


def reingest_document(
    user_id: str,
    document_id: int,
    *,
    force: bool = False,
) -> ReingestQueuedResponse:
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT id, file_path FROM file_index "
        "WHERE id = %s AND user_id = %s AND scope = 'personal'",
        (document_id, user_id),
    )
    if not row:
        raise DocumentNotFoundError(document_id)

    file_path = row["file_path"]
    if not Path(file_path).is_file():
        raise SourceUnavailableError(file_path)

    from services.batch_queue import enqueue

    upload_root = (config.get_uploads_path() / user_id).resolve(strict=False)
    resolved = Path(file_path).resolve(strict=False)
    try:
        under_uploads = resolved.is_relative_to(upload_root)
    except AttributeError:
        under_uploads = str(resolved).startswith(str(upload_root))

    if under_uploads:
        job_id = enqueue(
            user_id=user_id,
            kind="ingest_upload",
            payload={
                "stored_path": file_path,
                "file_id": uuid.uuid4().hex,
                "original_filename": Path(file_path).name,
                "force": force,
            },
        )
    else:
        job_id = enqueue(
            user_id=user_id,
            kind="ingest_watch_file",
            payload={"path": file_path, "force": force},
        )

    from services import ingest_progress as ip

    ip.update_ingest_job_progress(job_id=job_id, user_id=user_id, stage="queued")

    return ReingestQueuedResponse(document_id=document_id, job_id=job_id, queued=True)


# ---------------------------------------------------------------------------
# Household document sharing (LUM-157)
# ---------------------------------------------------------------------------


class DocumentNotSharedError(Exception):
    """Raised when unshare targets a document with no active shared projection."""


def _fetch_personal_source(user_id: str, document_id: int) -> dict[str, Any] | None:
    """Owner-only, personal-source-only fetch (mirrors routes/scope.py guard)."""
    ms = config.get_metadata_store()
    return ms.fetch_one(
        "SELECT id FROM file_index "
        "WHERE id = %s AND user_id = %s AND scope = 'personal'",
        (document_id, user_id),
    )


def _document_is_shared(user_id: str, document_id: int) -> bool:
    """True if a shared projection exists (or a share job is in flight)."""
    if document_id in _fetch_inflight_share_jobs(user_id):
        return True
    ms = config.get_metadata_store()
    row = ms.fetch_one(
        "SELECT 1 FROM file_index "
        "WHERE published_from = %s AND scope = 'shared' AND user_id = %s LIMIT 1",
        (document_id, user_id),
    )
    return bool(row)


def _enqueue_share_job(user_id: str, document_id: int, *, kind: str, share_status: str):
    """Enqueue a share/unshare job, coalescing on an existing in-flight job."""
    from models.api_v1 import ShareQueuedResponse

    # Single in-flight share/unshare job per document (concurrency guard): if a
    # share or unshare job for this document is already pending/running, reuse it
    # rather than racing a second projection against it.
    existing = _fetch_inflight_share_jobs(user_id).get(document_id)
    if existing is not None and existing.get("kind") == kind:
        return ShareQueuedResponse(
            document_id=document_id,
            job_id=existing["job_id"],
            share_status=existing["direction"],
        )

    from services import batch_handlers as _batch_handlers_registered  # noqa: F401
    from services.batch_queue import enqueue

    job_id = enqueue(user_id=user_id, kind=kind, payload={"document_id": document_id})

    from services import ingest_progress as ip

    ip.update_ingest_job_progress(job_id=job_id, user_id=user_id, stage="queued")
    return ShareQueuedResponse(
        document_id=document_id, job_id=job_id, share_status=share_status
    )


def share_document(user_id: str, document_id: int):
    """Queue a share job for the caller's own personal document (LUM-157).

    Synchronous owner + ``scope='personal'`` guard (fail-fast 404 before
    enqueue); the projection itself runs in the ``share_document`` background
    job. Idempotent — a duplicate share coalesces onto the in-flight job.
    """
    if _fetch_personal_source(user_id, document_id) is None:
        raise DocumentNotFoundError(document_id)
    return _enqueue_share_job(
        user_id, document_id, kind="share_document", share_status="sharing"
    )


def unshare_document(user_id: str, document_id: int):
    """Queue an unshare job for the caller's own shared document (LUM-157)."""
    if _fetch_personal_source(user_id, document_id) is None:
        raise DocumentNotFoundError(document_id)
    if not _document_is_shared(user_id, document_id):
        raise DocumentNotSharedError(document_id)
    return _enqueue_share_job(
        user_id, document_id, kind="unshare_document", share_status="unsharing"
    )
