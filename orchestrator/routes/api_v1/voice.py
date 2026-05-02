# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Push-to-talk / upload transcription — ``POST /api/v1/voice/transcribe``.

STT‑1 ships ``fake_stt`` scaffolding only — see ``speech_to_text`` exploration + ADR.
"""

from __future__ import annotations

import logging

from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi import UploadFile
from fastapi import status
from models.api_v1 import VoiceTranscribeResponse
from services.speech_to_text import SttDisabled
from services.speech_to_text import SttProcessingError
from services.speech_to_text import SttValidationError
from services.speech_to_text import transcribe_blob
from starlette.concurrency import run_in_threadpool

import config

_log = logging.getLogger(__name__)

# Starlette 0.38+ introduced new constant names; use ints so importing legacy
# names (which warn) is never required for the fallback path.
_STT_413 = getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413)
_STT_422 = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)

router = APIRouter(
    prefix="/api/v1/voice",
    tags=["v1-voice"],
    dependencies=[Depends(require_user)],
)


@router.post(
    "/transcribe",
    response_model=VoiceTranscribeResponse,
    summary="Upload short audio for speech-to-text transcription",
)
async def transcribe_voice(
    request: Request,
    file: UploadFile | None = File(default=None),
    language: str | None = Form(default=None),
):
    """Return a transcript payload when ``STT_BACKEND`` is wired; ``503`` when disabled."""

    ctx = get_user(request)
    user_id = ctx.user_id

    if file is None:
        raise HTTPException(
            status_code=_STT_422,
            detail={
                "code": "stt_multipart_invalid",
                "message": "Multipart field 'file' is required.",
            },
        )

    ctype = file.content_type or "application/octet-stream"

    max_bytes = config.get_stt_max_audio_bytes()
    chunks: list[bytes] = []
    total = 0
    while True:
        part = await file.read(min(65536, max_bytes + 1 - total))
        if not part:
            break
        total += len(part)
        chunks.append(part)
        if total > max_bytes:
            raise HTTPException(
                status_code=_STT_413,
                detail={
                    "code": "stt_audio_too_large",
                    "message": f"Audio exceeds STT_MAX_AUDIO_BYTES ({max_bytes})",
                },
            )

    payload = b"".join(chunks)

    try:
        result = await run_in_threadpool(
            transcribe_blob,
            payload,
            mime_type=ctype,
            language=language,
            user_id=user_id,
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
                    status.HTTP_400_BAD_REQUEST
                    if exc.code == "stt_duration_exceeded"
                    else _STT_422
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

    return VoiceTranscribeResponse(
        text=result.text,
        language=result.language,
        duration_seconds=result.duration_seconds,
        provider=result.provider,
        model=result.model,
        segments=list(result.segments),
    )
