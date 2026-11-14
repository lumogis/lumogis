# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Email attachment routes — read on demand, stage for send (LUM-695).

Read side (``/signals/{signal_id}/attachments``): metadata comes from the
``email_attachment`` rows written at ingest; **bytes are fetched on demand from
IMAP** and nothing is stored. That is a product-visible trade, not just an
implementation choice — opening an attachment needs the mailbox reachable, and
a message deleted upstream takes its attachment with it. The error contract
below says so explicitly rather than collapsing into a generic failure:

* **503** the mailbox is unreachable — retry later
* **410** the message is genuinely gone — retrying will never help
* **413** too large (per-attachment from recorded metadata, or whole-message
  from an ``RFC822.SIZE`` precheck; neither streams)
* **415** the extension is on the download blocklist
* **409** several messages claim this ``Message-ID`` — we refuse to guess which
  one the recorded metadata describes rather than serve a plausible wrong file
* **404** not the caller's signal — never 403, which would confirm it exists

A metadata-mode mailbox reports ``available: false`` rather than an empty list.
An empty list is indistinguishable from "this message has no attachments",
which would be an outright lie about a message that has three.

Write side (``/attachments/stage``): the composer uploads here and the send
payload carries **references**. Attachment bytes never enter the action payload.

Downloads are **always** ``Content-Disposition: attachment`` plus ``nosniff``.
Serving attacker-supplied ``text/html`` inline would be stored XSS on our own
origin.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections import deque
from typing import Deque
from urllib.parse import quote as urlquote

from adapters.email_imap_source import AttachmentFetchError
from auth import get_user
from authz import require_user
from fastapi import APIRouter
from fastapi import Depends
from fastapi import File
from fastapi import HTTPException
from fastapi import Request
from fastapi import UploadFile
from fastapi import status
from fastapi.responses import Response
from pydantic import BaseModel
from routes.api_v1.email_send import _assert_owned_signal
from services.email_message_locate import message_id_for
from services.email_message_locate import organize_folder_for

import config
from services import email_attachment_staging as staging
from services import email_attachments

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/email", tags=["email-attachments"])

# Its OWN token bucket, not the LLM ones. Sharing would let upload traffic
# starve the user's draft-gen or their create/approve/execute send flow —
# following the per-route pattern email_send.py already uses.
_STAGE_WINDOW_SEC = 60.0
_STAGE_LIMIT = 30
_stage_calls: dict[str, Deque[float]] = defaultdict(deque)

# The DOWNLOAD side needs its own bound too, and arguably more than the upload
# side does: one request pulls a whole message (up to
# EMAIL_ATTACHMENT_MAX_MESSAGE_FETCH_MB, 50 MB by default) into memory, and the
# work is an outbound IMAP round trip we do not control the latency of. Without
# a bucket a single authenticated session can hold an unbounded number of those
# in flight. Separate from the upload bucket so neither starves the other.
_DOWNLOAD_WINDOW_SEC = 60.0
_DOWNLOAD_LIMIT = 30
_download_calls: dict[str, Deque[float]] = defaultdict(deque)

_READ_CHUNK_BYTES = 1024 * 1024


def _bucket_check(bucket: Deque[float], *, limit: int, window: float, detail: str) -> None:
    now = time.monotonic()
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail,
            headers={"Retry-After": "60"},
        )
    bucket.append(now)


def _stage_rate_check(request: Request) -> None:
    _bucket_check(
        _stage_calls[get_user(request).user_id],
        limit=_STAGE_LIMIT,
        window=_STAGE_WINDOW_SEC,
        detail="too many uploads; try again shortly",
    )


def _download_rate_check(request: Request) -> None:
    _bucket_check(
        _download_calls[get_user(request).user_id],
        limit=_DOWNLOAD_LIMIT,
        window=_DOWNLOAD_WINDOW_SEC,
        detail="too many attachment downloads; try again shortly",
    )


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


class AttachmentRow(BaseModel):
    part_index: int
    filename: str
    mime_type: str
    size_bytes: int


class AttachmentListResponse(BaseModel):
    rows: list[AttachmentRow]
    # False for a metadata-mode mailbox: we cannot know whether this message has
    # attachments, and saying "none" would be a lie. The UI must say so.
    available: bool


class StagedAttachmentOut(BaseModel):
    id: str
    filename: str
    mime_type: str
    size_bytes: int


class DiscardResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Read — list
# ---------------------------------------------------------------------------


@router.get(
    "/signals/{signal_id}/attachments",
    response_model=AttachmentListResponse,
    dependencies=[Depends(require_user)],
)
def list_attachments(signal_id: str, request: Request) -> AttachmentListResponse:
    """Attachment metadata for the caller's own signal (404 for anyone else's)."""
    caller = get_user(request)
    owned = _assert_owned_signal(signal_id, caller.user_id)

    if getattr(owned.source, "extraction_method", "") != "imap_full_ingest":
        # Metadata-mode mailboxes never fetch bodies, so no attachment data was
        # ever extracted. Distinguishable from "no attachments" by construction.
        return AttachmentListResponse(rows=[], available=False)

    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: email_attachment has no scope column; ownership was already
    # established by _assert_owned_signal above, and the user_id filter here is
    # defence in depth on rows that are per-user by construction.
    rows = (
        ms.fetch_all(
            "SELECT part_index, filename, mime_type, size_bytes FROM email_attachment "
            "WHERE user_id = %s AND signal_id = %s ORDER BY part_index",
            (caller.user_id, signal_id),
        )
        or []
    )
    return AttachmentListResponse(
        rows=[
            AttachmentRow(
                part_index=int(r["part_index"]),
                filename=str(r["filename"]),
                mime_type=str(r["mime_type"] or ""),
                size_bytes=int(r["size_bytes"] or 0),
            )
            for r in rows
        ],
        available=True,
    )


# ---------------------------------------------------------------------------
# Read — download
# ---------------------------------------------------------------------------


@router.get(
    "/signals/{signal_id}/attachments/{part_index}",
    dependencies=[Depends(require_user), Depends(_download_rate_check)],
)
def download_attachment(signal_id: str, part_index: int, request: Request) -> Response:
    """Fetch one attachment's bytes from IMAP and return them as a download.

    Every refusal happens **before** any fetch where it can: ownership, then the
    blocklist, then the recorded size. A non-owned signal never opens an IMAP
    connection at all.
    """
    caller = get_user(request)
    owned = _assert_owned_signal(signal_id, caller.user_id)

    ms = config.get_metadata_store()
    # SCOPE-EXEMPT: as above — no scope column, ownership already gated.
    row = ms.fetch_one(
        "SELECT part_index, filename, mime_type, size_bytes FROM email_attachment "
        "WHERE user_id = %s AND signal_id = %s AND part_index = %s LIMIT 1",
        (caller.user_id, signal_id, int(part_index)),
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "not_found"})

    filename = str(row["filename"])
    if email_attachments.is_blocked_extension(filename):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"error": "blocked_file_type"},
        )
    if int(row["size_bytes"] or 0) > email_attachments.max_attachment_bytes():
        # Refused from the RECORDED metadata — nothing is fetched or streamed.
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "attachment_too_large"},
        )

    from adapters.email_imap_source import EmailImapSource

    try:
        part = EmailImapSource(owned.source).fetch_attachment_part(
            uid=owned.uid,
            part_index=int(part_index),
            message_id=message_id_for(signal_id, caller.user_id),
            folder_hint=organize_folder_for(signal_id, caller.user_id),
            # The uid is only usable while this still matches — otherwise the
            # server has reassigned UIDs and that slot may hold someone else's
            # message. The adapter falls back to the Message-ID search.
            uidvalidity=owned.uidvalidity,
        )
    except AttachmentFetchError as exc:
        raise HTTPException(
            _STATUS_FOR_FETCH_ERROR.get(exc.code, 502), detail={"error": exc.code}
        ) from None

    _log.info(
        "email_attachment_download signal_id=%s part=%s filename=%s size=%s",
        signal_id,
        part_index,
        filename,
        len(part.data),  # size only — attachment BYTES are never logged
    )
    return Response(
        content=part.data,
        media_type="application/octet-stream",
        headers={
            # NEVER inline. Serving attacker-supplied text/html inline would be
            # stored XSS on our own origin.
            "Content-Disposition": (f"attachment; filename*=UTF-8''{urlquote(filename, safe='')}"),
            "X-Content-Type-Options": "nosniff",
        },
    )


_STATUS_FOR_FETCH_ERROR: dict[str, int] = {
    # Retryable: the mailbox is down. Distinct from message_gone on purpose.
    "imap_unreachable": status.HTTP_503_SERVICE_UNAVAILABLE,
    # Never retryable: the message is deleted, or UIDVALIDITY changed.
    "message_gone": status.HTTP_410_GONE,
    "message_too_large": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    "part_gone": status.HTTP_404_NOT_FOUND,
    # Several messages claim this Message-ID, so we cannot say which one the
    # recorded metadata describes. 409: the request is well-formed and the
    # caller owns the signal — the mailbox is in a state we refuse to guess at.
    "message_ambiguous": status.HTTP_409_CONFLICT,
}


# ---------------------------------------------------------------------------
# Write — staging
# ---------------------------------------------------------------------------


@router.post(
    "/attachments/stage",
    response_model=StagedAttachmentOut,
    dependencies=[Depends(require_user), Depends(_stage_rate_check)],
)
async def stage_attachment(request: Request, file: UploadFile = File(...)) -> StagedAttachmentOut:
    """Stage one file for a subsequent send. Owner-scoped; quota-checked.

    The response carries a **reference**, which is what the composer puts in the
    action payload. Bytes stay on disk under a server-controlled path until the
    handler resolves them at execute time.
    """
    caller = get_user(request)

    # Read with the per-attachment cap as a hard ceiling so a lying
    # Content-Length cannot make us buffer an unbounded upload.
    limit = email_attachments.max_attachment_bytes()
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={"error": "attachment_too_large"},
            )
        chunks.append(chunk)

    try:
        out = staging.stage(
            caller.user_id,
            file.filename or "",
            file.content_type or "application/octet-stream",
            b"".join(chunks),
        )
    except staging.AttachmentTooLargeError:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "attachment_too_large"},
        ) from None
    except staging.StagingQuotaExceededError:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"error": "staging_quota_exceeded"},
        ) from None
    except staging.StagingWriteError:
        raise HTTPException(
            status.HTTP_507_INSUFFICIENT_STORAGE,
            detail={"error": "staging_write_failed"},
        ) from None

    return StagedAttachmentOut(
        id=out.id,
        filename=out.filename,
        mime_type=out.mime_type,
        size_bytes=out.size_bytes,
    )


@router.delete(
    "/attachments/stage/{staged_id}",
    response_model=DiscardResponse,
    dependencies=[Depends(require_user)],
)
def discard_staged_attachment(staged_id: str, request: Request) -> DiscardResponse:
    """Drop a staged file the user removed from the composer before sending."""
    caller = get_user(request)
    if staging.resolve(caller.user_id, staged_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": "not_found"})
    staging.discard(caller.user_id, [staged_id])
    return DiscardResponse(status="discarded")
