# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Push document ingest — ``POST /api/v1/ingest/upload``."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from auth import UserContext
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Header
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi import status
from models.api_v1 import IngestBatchSummaryResponse
from models.api_v1 import IngestJobProgressResponse
from models.api_v1 import IngestUploadQueuedResponse
from services.media_storage import _reject_unsafe_path_component

import config

_log = logging.getLogger(__name__)

_READ_CHUNK_BYTES = 1024 * 1024

router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["v1-ingest"],
    dependencies=[Depends(require_user)],
)


def _sanitize_upload_basename(filename: str | None) -> str:
    """Single safe leaf name for ``{file_id}_{basename}`` storage."""
    leaf = Path(filename or "upload").name.strip()
    if not leaf or leaf in {".", ".."}:
        leaf = "upload"
    _reject_unsafe_path_component(leaf, field="filename")
    return leaf


def _stored_upload_path(*, user_id: str, file_id: str, basename: str) -> Path:
    _reject_unsafe_path_component(user_id, field="user_id")
    dest_dir = config.get_uploads_path() / user_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{file_id}_{basename}"
    candidate = (dest_dir / stored_name).resolve(strict=False)
    root = config.get_uploads_path().resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise ValueError(f"path traversal detected for upload destination {candidate!r}")
    return candidate


@router.post(
    "/upload",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=IngestUploadQueuedResponse,
    responses={
        401: {"description": "Unauthenticated"},
        413: {"description": "Payload too large"},
        415: {"description": "Unsupported extension"},
        422: {"description": "Missing file"},
        500: {"description": "Enqueue failure"},
    },
    summary="Upload a document for queued ingest",
)
async def upload_ingest_file(
    file: UploadFile = File(...),
    user: UserContext = Depends(require_user),
    batch_id_header: str | None = Header(None, alias="X-Lumogis-Batch-Id"),
) -> IngestUploadQueuedResponse:
    """Stream upload to persistent storage and enqueue ``ingest_upload`` batch work."""
    if file.filename is None and file.size in (None, 0):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="file is required",
        )

    ext = Path(file.filename or "").suffix.lower()
    extractors = config.get_extractors()
    if ext not in extractors:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"unsupported file extension: {ext or '(none)'}",
        )

    file_id = uuid.uuid4().hex
    basename = _sanitize_upload_basename(file.filename)
    dest = _stored_upload_path(user_id=user.user_id, file_id=file_id, basename=basename)
    max_bytes = config.get_inbox_max_file_bytes()
    total = 0

    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(_READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="file exceeds maximum upload size",
                    )
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        _log.exception("upload write failed for user=%s", user.user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to store upload",
        ) from exc

    if total == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="file is required",
        )

    try:
        from services.batch_queue import enqueue

        from services import batch_handlers as _batch_handlers_registered  # noqa: F401
        from services import ingest_progress as ip

        batch_id: str | None = None
        if batch_id_header is not None:
            try:
                batch_id = ip.validate_batch_id(batch_id_header)
            except ValueError as exc:
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="invalid X-Lumogis-Batch-Id",
                ) from exc

        payload: dict[str, object] = {
            "stored_path": str(dest),
            "file_id": file_id,
            "original_filename": file.filename,
        }
        if batch_id is not None:
            payload["batch_id"] = batch_id

        job_id = enqueue(
            user_id=user.user_id,
            kind="ingest_upload",
            payload=payload,
        )
        ip.update_ingest_job_progress(
            job_id=job_id,
            user_id=user.user_id,
            stage="queued",
        )
    except HTTPException:
        raise
    except Exception as exc:
        dest.unlink(missing_ok=True)
        _log.exception("ingest_upload enqueue failed for user=%s", user.user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to queue ingest",
        ) from exc

    return IngestUploadQueuedResponse(file_id=file_id, job_id=job_id)


@router.get(
    "/jobs/{job_id}",
    response_model=IngestJobProgressResponse,
    responses={
        401: {"description": "Unauthenticated"},
        404: {"description": "Job not found"},
    },
    summary="Poll ingest job progress",
)
def get_ingest_job_progress(
    job_id: int,
    user: UserContext = Depends(require_user),
) -> IngestJobProgressResponse:
    from services import ingest_progress as ip

    row = ip.get_ingest_job_row(job_id=job_id, user_id=user.user_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return IngestJobProgressResponse.model_validate(ip.job_row_to_progress_response(row))


@router.get(
    "/batches/{batch_id}",
    response_model=IngestBatchSummaryResponse,
    responses={
        401: {"description": "Unauthenticated"},
        400: {"description": "Invalid batch id"},
    },
    summary="Poll multi-file upload batch counters",
)
def get_ingest_batch_summary(
    batch_id: str,
    user: UserContext = Depends(require_user),
) -> IngestBatchSummaryResponse:
    from services import ingest_progress as ip

    try:
        summary = ip.get_ingest_batch_summary(batch_id=batch_id, user_id=user.user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid batch_id",
        ) from exc
    return IngestBatchSummaryResponse.model_validate(summary)
