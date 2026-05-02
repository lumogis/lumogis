# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase-5 capture endpoints — 5B CRUD + **5C attachments** + **5D transcribe** + **5G index**.

Core create/list/detail/patch/delete (**5B**); multipart attachment
upload/download/delete (**5C**); **POST …/transcribe** via the verified STT
facade (**5D**); **POST …/index** promotes to notes + Qdrant **conversations** (**5G**).

Route ordering: FastAPI evaluates fixed-segment routes before
parameterised ones; the `/{id}` group is declared after the literal
paths (/text, /upload).
"""

from __future__ import annotations

from typing import Literal
from typing import Optional

from auth import UserContext
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Query
from fastapi import Response
from fastapi import UploadFile
from fastapi import status
from fastapi.responses import FileResponse
from models.api_v1 import CaptureAttachmentSummary
from models.api_v1 import CaptureCreated
from models.api_v1 import CaptureCreateRequest
from models.api_v1 import CaptureDetail
from models.api_v1 import CaptureListResponse
from models.api_v1 import CapturePatchRequest
from models.api_v1 import CaptureTextRequest
from models.api_v1 import CaptureTranscribeRequest
from models.api_v1 import CaptureTranscriptSummary
from services.speech_to_text import SttDisabled
from services.speech_to_text import SttProcessingError
from services.speech_to_text import SttValidationError
from services.speech_to_text import transcribe_blob
from starlette.concurrency import run_in_threadpool

import config
from services import captures as capture_svc

router = APIRouter(
    prefix="/api/v1/captures",
    tags=["v1-captures"],
    dependencies=[Depends(require_user)],
)

_COMMON_LIVE = {401: {"description": "Unauthenticated"}}
_COMMON_INDEX = {
    401: {"description": "Unauthenticated"},
    404: {"description": "Capture not found"},
    409: {"description": "Already indexed or invalid state"},
    422: {"description": "Transcript required or no indexable content"},
    503: {"description": "Vector / embedder unavailable"},
}
_STT_413 = getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413)
_STT_422 = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)
_ATTACHMENT_LIVE = {
    **_COMMON_LIVE,
    404: {"description": "Not found"},
    409: {"description": "Conflict — capture is indexed"},
    413: {"description": "File too large"},
    415: {"description": "MIME type not in allowlist"},
}


def _not_implemented():
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail={"error": "not_implemented", "since_phase": 5},
    )


# ── Create / alias routes (fixed paths first) ─────────────────────────


@router.post(
    "",
    response_model=CaptureCreated,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_COMMON_LIVE,
        200: {"description": "Idempotent replay — same client_id + same payload"},
        201: {"description": "Created"},
        409: {"description": "Idempotency key conflict — same client_id, different payload"},
        422: {"description": "Validation error"},
    },
    summary="Create a capture (canonical)",
)
def create_capture(
    body: CaptureCreateRequest,
    response: Response,
    user: UserContext = Depends(require_user),
):
    """Create a personal staging capture.

    ``client_id`` in the body is the wire name for the client-generated
    UUID (``local_capture_id``). Replay with the same ``(user_id,
    client_id)`` → 200 with the original ``capture_id``; same
    ``client_id`` + different payload → 409 (plan §7 idempotency freeze).
    """
    ms = config.get_metadata_store()
    out, code = capture_svc.create_capture(ms, user_id=user.user_id, body=body)
    response.status_code = code
    return out


@router.post(
    "/text",
    response_model=CaptureCreated,
    status_code=status.HTTP_201_CREATED,
    responses={
        **_COMMON_LIVE,
        200: {"description": "Idempotent replay"},
        201: {"description": "Created"},
        409: {"description": "Idempotency key conflict"},
        422: {"description": "Validation error"},
    },
    summary="Create a capture — text alias (backward-compat)",
)
def create_capture_text(
    body: CaptureTextRequest,
    response: Response,
    user: UserContext = Depends(require_user),
):
    """Thin alias for ``POST /api/v1/captures`` — same handler, same response.

    Exists for backward-compatibility with Phase-0 stubs. Do not diverge
    the behaviour from the canonical create route (plan §12.4).
    """
    if body.scope != "personal":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "capture_scope_not_supported"},
        )
    ms = config.get_metadata_store()
    canonical = CaptureCreateRequest(
        text=body.text,
        title=body.title,
        url=None,
        client_id=None,
        tags=body.tags,
    )
    out, code = capture_svc.create_capture(ms, user_id=user.user_id, body=canonical)
    response.status_code = code
    return out


@router.post(
    "/upload",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    responses={
        501: {"description": "Deprecated upload surface — use POST /captures/{id}/attachments"}
    },
    summary="Legacy upload stub (501)",
    include_in_schema=True,
)
def capture_upload_stub():
    """Permanently 501.

    Clients must use ``POST /api/v1/captures/{id}/attachments`` (multipart,
    after capture create). This stub is kept so existing OpenAPI snapshots
    do not drift (plan §12.4 "POST /upload" note).
    """
    _not_implemented()


# ── List ──────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=CaptureListResponse,
    responses={
        **_COMMON_LIVE,
        422: {"description": "Invalid scope query"},
    },
    summary="List my captures (paginated)",
)
def list_captures(
    user: UserContext = Depends(require_user),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    scope: Optional[Literal["personal", "shared", "system"]] = Query(
        default=None,
        description="MVP: only personal captures exist; shared/system return an empty page.",
    ),
):
    """Return summary rows for the authenticated user's captures.

    Ordered by ``updated_at DESC``. Use ``limit`` / ``offset`` for
    pagination (plan §12.4).
    """
    ms = config.get_metadata_store()
    items, total = capture_svc.list_captures(
        ms,
        user_id=user.user_id,
        scope=scope,
        limit=limit,
        offset=offset,
    )
    return CaptureListResponse(captures=items, total=total, limit=limit, offset=offset)


# ── Per-capture CRUD ──────────────────────────────────────────────────


@router.get(
    "/{capture_id}",
    response_model=CaptureDetail,
    responses={**_COMMON_LIVE, 404: {"description": "Capture not found"}},
    summary="Get capture detail",
)
def get_capture(capture_id: str, user: UserContext = Depends(require_user)):
    """Return full capture with nested attachment and transcript summaries."""
    ms = config.get_metadata_store()
    return capture_svc.get_capture(ms, user_id=user.user_id, capture_id=capture_id)


@router.patch(
    "/{capture_id}",
    response_model=CaptureDetail,
    responses={
        **_COMMON_LIVE,
        404: {"description": "Capture not found"},
        409: {"description": "Capture is indexed — not editable"},
        422: {"description": "Validation error"},
    },
    summary="Update a pending capture",
)
def patch_capture(
    capture_id: str, body: CapturePatchRequest, user: UserContext = Depends(require_user)
):
    """Edit ``text``, ``title``, ``url``, or ``tags`` while status is ``pending``.

    Returns 409 when capture is ``indexed`` (plan §10).
    """
    ms = config.get_metadata_store()
    return capture_svc.patch_capture(ms, user_id=user.user_id, capture_id=capture_id, body=body)


@router.delete(
    "/{capture_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **_COMMON_LIVE,
        404: {"description": "Capture not found"},
        409: {"description": "Capture is indexed — delete via memory API"},
    },
    summary="Delete a pending or failed capture",
)
def delete_capture(capture_id: str, user: UserContext = Depends(require_user)):
    """Delete the capture row, its attachments metadata, transcript rows, and
    on-disk media files.

    Returns 409 with ``indexed_capture_requires_memory_delete`` when
    ``status = indexed`` (plan §10).
    """
    ms = config.get_metadata_store()
    capture_svc.delete_capture(ms, user_id=user.user_id, capture_id=capture_id)


# ── Attachments ───────────────────────────────────────────────────────


@router.post(
    "/{capture_id}/attachments",
    status_code=status.HTTP_201_CREATED,
    response_model=CaptureAttachmentSummary,
    responses={
        **_ATTACHMENT_LIVE,
        200: {"description": "Idempotent replay — same client_attachment_id"},
        201: {"description": "Created"},
        422: {"description": "Validation error"},
    },
    summary="Upload an attachment (multipart)",
)
async def upload_attachment(
    capture_id: str,
    response: Response,
    file: UploadFile = File(...),
    client_attachment_id: Optional[str] = Form(default=None),
    user: UserContext = Depends(require_user),
):
    """Store an image or audio file for an existing capture.

    Multipart form fields (plan §12.4):
    - ``file`` — binary upload.
    - ``client_attachment_id`` — optional; client-side ``local_attachment_id``
      for idempotent replay (same triple ``(user_id, capture_id,
      client_attachment_id)`` → 200 with existing attachment).

    MIME allowlist: ``image/jpeg``, ``image/png``, ``image/webp``,
    ``audio/webm``, ``audio/mp4``, ``audio/mpeg``, ``audio/wav``.
    Size limits: images 10 MiB, audio 25 MiB (plan §12.2).
    """
    ms = config.get_metadata_store()
    body = await file.read()
    out, code = capture_svc.add_capture_attachment(
        ms,
        user_id=user.user_id,
        capture_id=capture_id,
        content=body,
        mime_type=file.content_type,
        original_filename=file.filename,
        client_attachment_id=client_attachment_id,
    )
    response.status_code = code
    return out


@router.get(
    "/{capture_id}/attachments/{attachment_id}",
    response_class=FileResponse,
    responses={
        **_COMMON_LIVE,
        404: {"description": "Attachment not found"},
    },
    summary="Download an attachment (authenticated)",
)
def download_attachment(
    capture_id: str,
    attachment_id: str,
    user: UserContext = Depends(require_user),
):
    """Stream the attachment binary with ``require_user`` auth.

    No anonymous static URLs (plan §12.2).
    """
    ms = config.get_metadata_store()
    path, mime, dl_name = capture_svc.get_attachment_download(
        ms,
        user_id=user.user_id,
        capture_id=capture_id,
        attachment_id=attachment_id,
    )
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "attachment_not_found"},
        )
    return FileResponse(
        path,
        media_type=mime,
        filename=dl_name,
        content_disposition_type="attachment",
    )


@router.delete(
    "/{capture_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **_COMMON_LIVE,
        404: {"description": "Attachment or capture not found"},
        409: {"description": "Capture is indexed"},
    },
    summary="Delete an attachment",
)
def delete_attachment(
    capture_id: str,
    attachment_id: str,
    user: UserContext = Depends(require_user),
):
    """Remove attachment metadata, transcript rows, and on-disk file.

    Allowed only while capture ``status ∈ {pending, failed}`` (plan §10).
    """
    ms = config.get_metadata_store()
    capture_svc.delete_capture_attachment(
        ms,
        user_id=user.user_id,
        capture_id=capture_id,
        attachment_id=attachment_id,
    )


# ── Transcription + Index ─────────────────────────────────────────────


@router.post(
    "/{capture_id}/transcribe",
    response_model=CaptureTranscriptSummary,
    responses={
        **_COMMON_LIVE,
        404: {"description": "Capture, attachment, or blob missing"},
        409: {"description": "Capture is indexed"},
        422: {"description": "No pending audio or attachment is not audio"},
        503: {"description": "STT disabled or processing error"},
    },
    summary="Transcribe an audio attachment",
)
async def transcribe_capture(
    capture_id: str,
    body: CaptureTranscribeRequest,
    user: UserContext = Depends(require_user),
):
    """Call the SpeechToText foundation for an audio attachment.

    ``attachment_id`` in the body targets one attachment; omit to pick the
    first audio attachment (by ``created_at``) that lacks a **complete**
    transcript with non-empty text. Idempotent: repeats return the stored row
    without re-invoking STT. On STT **503**, nothing is persisted.
    """
    ms = config.get_metadata_store()
    aid = body.attachment_id.strip() if body.attachment_id else None
    target = capture_svc.prepare_capture_transcribe(
        ms, user_id=user.user_id, capture_id=capture_id, attachment_id=aid
    )
    if target.idempotent_summary is not None:
        return target.idempotent_summary

    att_id = str(target.attachment["id"])
    audio_bytes, mime = capture_svc.read_transcribe_audio_bytes(target.attachment)

    try:
        result = await run_in_threadpool(
            transcribe_blob,
            audio_bytes,
            mime_type=mime,
            language=None,
            user_id=user.user_id,
        )
    except SttDisabled as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except SttValidationError as exc:
        sc = (
            _STT_413
            if exc.code == "stt_audio_too_large"
            else (
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
                if exc.code == "stt_bad_mime"
                else (
                    status.HTTP_400_BAD_REQUEST if exc.code == "stt_duration_exceeded" else _STT_422
                )
            )
        )
        raise HTTPException(
            status_code=sc,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except SttProcessingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    return capture_svc.finish_capture_transcribe(
        ms,
        user_id=user.user_id,
        capture_id=capture_id,
        attachment_id=att_id,
        transcript_row_id_to_update=target.transcript_row_id_to_update,
        result=result,
        failed=False,
    )


@router.post(
    "/{capture_id}/index",
    response_model=CaptureDetail,
    responses={
        **_COMMON_LIVE,
        **_COMMON_INDEX,
    },
    summary="Promote capture to memory (notes + conversations)",
)
def post_capture_index(
    capture_id: str,
    user: UserContext = Depends(require_user),
):
    """Build a personal ``notes`` row from reviewed capture content and
    upsert the Qdrant ``conversations`` vector.

    Sets ``status = indexed`` and links ``note_id``. Fires
    ``Event.NOTE_CAPTURED`` for graph hook. Returns 409 if already
    indexed (plan §8 + §9).
    """
    ms = config.get_metadata_store()
    return capture_svc.index_capture(ms, user_id=user.user_id, capture_id=capture_id)
