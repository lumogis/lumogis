# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase-5 capture persistence — CRUD (**5B**), attachments (**5C**), transcripts (**5D**), index (**5G**).

All SQL uses parameterised placeholders only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import Literal
from typing import Optional
from uuid import UUID
from uuid import uuid4

import hooks
from actions.audit import write_audit
from events import Event
from fastapi import HTTPException
from fastapi import status
from models.actions import AuditEntry
from models.api_v1 import CaptureAttachmentSummary
from models.api_v1 import CaptureCreated
from models.api_v1 import CaptureCreateRequest
from models.api_v1 import CaptureDetail
from models.api_v1 import CaptureListItem
from models.api_v1 import CapturePatchRequest
from models.api_v1 import CaptureTranscriptSummary
from models.api_v1 import TranscriptionResult
from ports.metadata_store import MetadataStore
from services.point_ids import note_conversation_point_id

import config
from services import media_storage

_log = logging.getLogger(__name__)

_ALLOWED_URL_SCHEMES = frozenset(("http", "https"))


def _normalize_tag_list(tags: Optional[list[str]]) -> Optional[tuple[str, ...]]:
    if tags is None:
        return None
    return tuple(sorted(tags))


def _normalize_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _payload_identity(
    *,
    text: Optional[str],
    title: Optional[str],
    url: Optional[str],
    tags: Optional[list[str]],
    capture_type: str,
) -> str:
    """Stable string for idempotent replay comparison."""
    payload = {
        "text": text,
        "title": title,
        "url": url,
        "tags": list(_normalize_tag_list(tags)) if tags else None,
        "capture_type": capture_type,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _validate_url(url: str) -> None:
    if not url:
        return
    m = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", url)
    if not m:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_url_scheme"},
        )
    scheme = m.group(1).lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_url_scheme"},
        )


def derive_capture_type(
    *, text: Optional[str], url: Optional[str]
) -> Literal["text", "url", "mixed"]:
    has_text = bool(text)
    has_url = bool(url)
    if has_text and has_url:
        return "mixed"
    if has_url:
        return "url"
    return "text"


def create_capture(
    ms: MetadataStore,
    *,
    user_id: str,
    body: CaptureCreateRequest,
) -> tuple[CaptureCreated, int]:
    """Insert capture or return idempotent match. Returns (body, http_status)."""
    text = _normalize_text(body.text)
    url = _normalize_text(body.url)
    title = _normalize_text(body.title)
    if text is None and url is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "capture_requires_text_or_url"},
        )
    if url:
        _validate_url(url)

    capture_type = derive_capture_type(text=text, url=url)
    tags = body.tags
    client_id = body.client_id

    if client_id is not None:
        try:
            UUID(client_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "invalid_client_id"},
            ) from exc

    if client_id:
        row = ms.fetch_one(
            # SCOPE-EXEMPT: captures rows are per-user staging; table has no scope column (Phase-5).
            "SELECT id, text, title, url, tags, capture_type, status "
            "FROM captures WHERE user_id = %s AND local_client_id = %s",
            (user_id, client_id),
        )
        if row is not None:
            new_type = derive_capture_type(text=text, url=url)
            new_pid = _payload_identity(
                text=text, title=title, url=url, tags=tags, capture_type=new_type
            )
            old_pid = _payload_identity(
                text=row["text"],
                title=row["title"],
                url=row["url"],
                tags=list(row["tags"]) if row["tags"] is not None else None,
                capture_type=row["capture_type"],
            )
            if new_pid != old_pid:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"error": "idempotency_key_conflict"},
                )
            return (
                CaptureCreated(capture_id=str(row["id"]), status="pending"),
                status.HTTP_200_OK,
            )

    row = ms.fetch_one(
        "INSERT INTO captures (user_id, status, capture_type, title, text, url, "
        "local_client_id, source_channel, tags, last_error) "
        "VALUES (%s, 'pending', %s, %s, %s, %s, %s, 'lumogis_web', %s, NULL) "
        "RETURNING id, status",
        (user_id, capture_type, title, text, url, client_id, tags),
    )
    if row is None:
        _log.error("capture insert returned no row user_id=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"error": "insert_failed"}
        )
    return (
        CaptureCreated(capture_id=str(row["id"]), status="pending"),
        status.HTTP_201_CREATED,
    )


def get_capture(
    ms: MetadataStore,
    *,
    user_id: str,
    capture_id: str,
) -> CaptureDetail:
    try:
        UUID(capture_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"error": "capture_not_found"}
        ) from exc

    row = ms.fetch_one(
        "SELECT * FROM captures WHERE id = %s::uuid AND user_id = %s",
        (capture_id, user_id),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"error": "capture_not_found"}
        )

    att_rows = ms.fetch_all(
        "SELECT id, attachment_type, mime_type, size_bytes, original_filename, "
        "processing_status, created_at FROM capture_attachments "
        "WHERE capture_id = %s::uuid AND user_id = %s ORDER BY created_at",
        (capture_id, user_id),
    )
    tr_rows = ms.fetch_all(
        "SELECT id, attachment_id, transcript_status, transcript_text, "
        "transcript_provenance, language, confidence, created_at, updated_at "
        "FROM capture_transcripts WHERE capture_id = %s::uuid AND user_id = %s "
        "ORDER BY created_at",
        (capture_id, user_id),
    )
    attachments = [
        CaptureAttachmentSummary(
            id=str(r["id"]),
            attachment_type=r["attachment_type"],
            mime_type=r["mime_type"],
            size_bytes=int(r["size_bytes"]),
            original_filename=r.get("original_filename"),
            processing_status=r["processing_status"],
            created_at=r["created_at"],
        )
        for r in att_rows
    ]
    transcripts = [_transcript_row_to_summary(r) for r in tr_rows]
    return CaptureDetail(
        id=str(row["id"]),
        status=row["status"],
        capture_type=row["capture_type"],
        title=row.get("title"),
        text=row.get("text"),
        url=row.get("url"),
        tags=sorted(row["tags"]) if row.get("tags") is not None else None,
        note_id=str(row["note_id"]) if row.get("note_id") is not None else None,
        source_channel=row["source_channel"],
        last_error=row.get("last_error"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        captured_at=row.get("captured_at"),
        indexed_at=row.get("indexed_at"),
        attachments=attachments,
        transcripts=transcripts,
    )


def list_captures(
    ms: MetadataStore,
    *,
    user_id: str,
    scope: Optional[Literal["personal", "shared", "system"]],
    limit: int,
    offset: int,
) -> tuple[list[CaptureListItem], int]:
    """List captures for user. MVP: no ``scope`` column — only *personal* rows exist."""
    if scope is None:
        scope = "personal"
    if scope == "personal":
        where = "user_id = %s"
        params: tuple[Any, ...] = (user_id,)
    elif scope in ("shared", "system"):
        # No shared/system capture rows in MVP (no scope column) — empty list.
        return [], 0
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_scope"},
        )

    total_row = ms.fetch_one(
        f"SELECT COUNT(*) AS c FROM captures WHERE {where}",
        params,
    )
    total = int(total_row["c"]) if total_row else 0

    rows = ms.fetch_all(
        "SELECT c.*, "
        "(SELECT COUNT(*) FROM capture_attachments a WHERE a.capture_id = c.id) AS attachment_count, "
        "(SELECT COUNT(*) FROM capture_transcripts t WHERE t.capture_id = c.id) AS transcript_count "
        f"FROM captures c WHERE {where} "
        "ORDER BY c.updated_at DESC LIMIT %s OFFSET %s",
        (*params, limit, offset),
    )
    items = [
        CaptureListItem(
            id=str(r["id"]),
            status=r["status"],
            capture_type=r["capture_type"],
            title=r.get("title"),
            text=r.get("text"),
            url=r.get("url"),
            attachment_count=int(r["attachment_count"]),
            transcript_count=int(r["transcript_count"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]
    return items, total


def patch_capture(
    ms: MetadataStore,
    *,
    user_id: str,
    capture_id: str,
    body: CapturePatchRequest,
) -> CaptureDetail:
    try:
        UUID(capture_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"error": "capture_not_found"}
        ) from exc

    row = ms.fetch_one(
        "SELECT * FROM captures WHERE id = %s::uuid AND user_id = %s",
        (capture_id, user_id),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"error": "capture_not_found"}
        )
    if row["status"] == "indexed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "capture_indexed_not_editable"},
        )

    fields_set = body.model_fields_set
    if "text" in fields_set:
        text = _normalize_text(body.text)
    else:
        text = row.get("text")
    if "url" in fields_set:
        url = _normalize_text(body.url)
    else:
        url = row.get("url")
    if "title" in fields_set:
        title = _normalize_text(body.title)
    else:
        title = row.get("title")
    if "tags" in fields_set:
        tags = list(body.tags) if body.tags is not None else row.get("tags")
    else:
        tags = row.get("tags")
    if tags is not None:
        tags = list(tags)

    if url:
        _validate_url(str(url))

    t_norm = _normalize_text(text) if isinstance(text, str) else text
    u_norm = _normalize_text(url) if isinstance(url, str) else url
    if t_norm is None and u_norm is None:
        att = ms.fetch_one(
            "SELECT 1 AS x FROM capture_attachments "
            "WHERE capture_id = %s::uuid AND user_id = %s LIMIT 1",
            (capture_id, user_id),
        )
        if att is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "capture_requires_text_or_url"},
            )

    capture_type = derive_capture_type(
        text=t_norm,
        url=u_norm,
    )

    ms.execute(
        "UPDATE captures SET text = %s, title = %s, url = %s, tags = %s, capture_type = %s "
        "WHERE id = %s::uuid AND user_id = %s",
        (t_norm, title, u_norm, tags, capture_type, capture_id, user_id),
    )
    return get_capture(ms, user_id=user_id, capture_id=capture_id)


def delete_capture(ms: MetadataStore, *, user_id: str, capture_id: str) -> None:
    try:
        UUID(capture_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"error": "capture_not_found"}
        ) from exc

    row = ms.fetch_one(
        "SELECT status FROM captures WHERE id = %s::uuid AND user_id = %s",
        (capture_id, user_id),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"error": "capture_not_found"}
        )
    if row["status"] == "indexed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "indexed_capture_requires_memory_delete"},
        )
    key_rows = ms.fetch_all(
        "SELECT storage_key FROM capture_attachments WHERE capture_id = %s::uuid AND user_id = %s",
        (capture_id, user_id),
    )
    for kr in key_rows:
        # No user input in storage_key — still guard against bad metadata.
        sk = kr.get("storage_key")
        if not sk:
            continue
        try:
            media_storage.unlink_storage_file(str(sk))
        except (OSError, ValueError) as exc:
            _log.warning("capture delete: could not unlink %r: %s", sk, exc)
    ms.execute(
        "DELETE FROM captures WHERE id = %s::uuid AND user_id = %s",
        (capture_id, user_id),
    )


def _attachment_row_to_summary(row: dict) -> CaptureAttachmentSummary:
    return CaptureAttachmentSummary(
        id=str(row["id"]),
        attachment_type=row["attachment_type"],
        mime_type=row["mime_type"],
        size_bytes=int(row["size_bytes"]),
        original_filename=row.get("original_filename"),
        processing_status=row["processing_status"],
        created_at=row["created_at"],
    )


def _transcript_row_to_summary(row: dict) -> CaptureTranscriptSummary:
    return CaptureTranscriptSummary(
        id=str(row["id"]),
        attachment_id=str(row["attachment_id"]),
        transcript_status=row["transcript_status"],
        transcript_text=row.get("transcript_text"),
        transcript_provenance=row["transcript_provenance"],
        language=row.get("language"),
        confidence=float(row["confidence"]) if row.get("confidence") is not None else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _next_capture_type_for_new_attachment(
    current: str,
    *,
    has_text: bool,
    has_url: bool,
    attachment_kind: Literal["image", "audio"],
) -> str:
    has_ur = has_text or has_url
    if attachment_kind == "image":
        if current in ("voice", "mixed"):
            return "mixed"
        if current == "photo":
            return "photo"
        if has_ur or current in ("text", "url"):
            return "mixed"
        return "photo"
    if current in ("photo", "mixed"):
        return "mixed"
    if current == "voice":
        return "voice"
    if has_ur or current in ("text", "url"):
        return "mixed"
    return "voice"


def add_capture_attachment(
    ms: MetadataStore,
    *,
    user_id: str,
    capture_id: str,
    content: bytes,
    mime_type: str | None,
    original_filename: str | None,
    client_attachment_id: str | None,
) -> tuple[CaptureAttachmentSummary, int]:
    """Persist attachment metadata + bytes. Returns (summary, http status).

    Idempotent replay (same ``client_attachment_id`` for this capture and
    user) returns **200** with the existing row (plan §12.4).
    """
    try:
        UUID(capture_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "capture_not_found"},
        ) from exc

    cap = ms.fetch_one(
        "SELECT * FROM captures WHERE id = %s::uuid AND user_id = %s",
        (capture_id, user_id),
    )
    if cap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"error": "capture_not_found"}
        )
    if cap["status"] == "indexed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"error": "capture_indexed"}
        )

    lc = client_attachment_id.strip() if client_attachment_id else None
    if lc:
        try:
            UUID(lc)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "invalid_client_attachment_id"},
            ) from exc
        existing = ms.fetch_one(
            # SCOPE-EXEMPT: capture_attachments rows are per-user; table has no scope column.
            "SELECT id, attachment_type, mime_type, size_bytes, original_filename, "
            "processing_status, created_at FROM capture_attachments "
            "WHERE user_id = %s AND capture_id = %s::uuid AND client_attachment_id = %s",
            (user_id, capture_id, lc),
        )
        if existing is not None:
            return _attachment_row_to_summary(existing), status.HTTP_200_OK

    if not mime_type or not str(mime_type).strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "missing_content_type"},
        )

    try:
        kind, mime_norm = media_storage.classify_inbound_mime(mime_type)
        media_storage.validate_attachment(mime_norm, len(content), kind)
        leaf = media_storage.leaf_filename_for_mime(mime_norm)
    except media_storage.MimeNotAllowedError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={"error": "mime_type_not_allowed", "mime_type": exc.mime_type},
        ) from exc
    except media_storage.FileTooLargeError as exc:
        raise HTTPException(
            status_code=getattr(status, "HTTP_413_CONTENT_TOO_LARGE", 413),
            detail={"error": "file_too_large", "limit_bytes": exc.limit_bytes},
        ) from exc

    attachment_id = str(uuid4())
    storage_key = f"{user_id}/{capture_id}/{attachment_id}/{leaf}"
    disk_path = media_storage.safe_attachment_path(user_id, capture_id, attachment_id, leaf)

    disk_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        disk_path.write_bytes(content)
    except OSError as exc:
        _log.exception("attachment write failed capture_id=%s", capture_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "attachment_write_failed"},
        ) from exc

    sha256_hex = hashlib.sha256(content).hexdigest()

    try:
        ins = ms.fetch_one(
            "INSERT INTO capture_attachments (id, capture_id, user_id, attachment_type, "
            "storage_key, original_filename, mime_type, size_bytes, sha256, processing_status, "
            "client_attachment_id) "
            "VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, 'stored', %s) "
            "RETURNING id, attachment_type, mime_type, size_bytes, original_filename, "
            "processing_status, created_at",
            (
                attachment_id,
                capture_id,
                user_id,
                kind,
                storage_key,
                original_filename,
                mime_norm,
                len(content),
                sha256_hex,
                lc,
            ),
        )
    except Exception:
        try:
            disk_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    if ins is None:
        try:
            disk_path.unlink(missing_ok=True)
        except OSError:
            pass
        _log.error("attachment insert returned no row capture_id=%s", capture_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "attachment_insert_failed"},
        )

    has_text = bool(_normalize_text(cap.get("text")))
    has_url = bool(_normalize_text(cap.get("url")))
    next_type = _next_capture_type_for_new_attachment(
        cap["capture_type"],
        has_text=has_text,
        has_url=has_url,
        attachment_kind=kind,
    )
    if next_type != cap["capture_type"]:
        ms.execute(
            "UPDATE captures SET capture_type = %s WHERE id = %s::uuid AND user_id = %s",
            (next_type, capture_id, user_id),
        )

    return _attachment_row_to_summary(ins), status.HTTP_201_CREATED


def get_attachment_download(
    ms: MetadataStore,
    *,
    user_id: str,
    capture_id: str,
    attachment_id: str,
) -> tuple[Path, str, str]:
    """Resolve attachment on disk. Raises ``HTTPException`` for 404 paths."""
    try:
        UUID(capture_id)
        UUID(attachment_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "attachment_not_found"},
        ) from exc

    row = ms.fetch_one(
        "SELECT storage_key, mime_type, original_filename FROM capture_attachments "
        "WHERE id = %s::uuid AND capture_id = %s::uuid AND user_id = %s",
        (attachment_id, capture_id, user_id),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"error": "attachment_not_found"}
        )

    try:
        path = media_storage.resolve_storage_key_file(str(row["storage_key"]))
    except ValueError as exc:
        _log.warning("attachment download: bad storage_key %r: %s", row["storage_key"], exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "attachment_not_found"},
        ) from exc

    dl_name = row.get("original_filename") or path.name
    return path, str(row["mime_type"]), dl_name


def delete_capture_attachment(
    ms: MetadataStore,
    *,
    user_id: str,
    capture_id: str,
    attachment_id: str,
) -> None:
    try:
        UUID(capture_id)
        UUID(attachment_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "attachment_not_found"},
        ) from exc

    cap = ms.fetch_one(
        "SELECT status FROM captures WHERE id = %s::uuid AND user_id = %s",
        (capture_id, user_id),
    )
    if cap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"error": "capture_not_found"}
        )
    if cap["status"] == "indexed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"error": "capture_indexed"}
        )

    row = ms.fetch_one(
        "SELECT storage_key FROM capture_attachments "
        "WHERE id = %s::uuid AND capture_id = %s::uuid AND user_id = %s",
        (attachment_id, capture_id, user_id),
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"error": "attachment_not_found"}
        )

    try:
        media_storage.unlink_storage_file(str(row["storage_key"]))
    except (OSError, ValueError) as exc:
        _log.warning("attachment delete: unlink failed: %s", exc)

    ms.execute(
        "DELETE FROM capture_attachments WHERE id = %s::uuid AND capture_id = %s::uuid AND user_id = %s",
        (attachment_id, capture_id, user_id),
    )


def _is_transcribable_audio_attachment(row: dict) -> bool:
    if row.get("attachment_type") != "audio":
        return False
    mime = str(row.get("mime_type") or "").split(";", maxsplit=1)[0].strip().lower()
    try:
        kind, _ = media_storage.classify_inbound_mime(mime)
    except media_storage.MimeNotAllowedError:
        return False
    return kind == "audio"


def _latest_transcript_for_attachment(
    ms: MetadataStore,
    *,
    user_id: str,
    capture_id: str,
    attachment_id: str,
) -> dict | None:
    return ms.fetch_one(
        "SELECT id, attachment_id, transcript_status, transcript_text, transcript_provenance, "
        "language, confidence, error, provider, model, created_at, updated_at, capture_id, user_id "
        "FROM capture_transcripts WHERE attachment_id = %s::uuid AND capture_id = %s::uuid "
        "AND user_id = %s ORDER BY created_at DESC LIMIT 1",
        (attachment_id, capture_id, user_id),
    )


def _transcript_complete_nonempty(row: dict | None) -> bool:
    if row is None:
        return False
    if row.get("transcript_status") != "complete":
        return False
    return bool(_normalize_text(row.get("transcript_text")))


@dataclass(frozen=True)
class TranscribeTarget:
    attachment: dict
    transcript_row_id_to_update: str | None
    idempotent_summary: CaptureTranscriptSummary | None


def prepare_capture_transcribe(
    ms: MetadataStore,
    *,
    user_id: str,
    capture_id: str,
    attachment_id: str | None,
) -> TranscribeTarget:
    try:
        UUID(capture_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "capture_not_found"},
        ) from exc

    cap = ms.fetch_one(
        "SELECT status FROM captures WHERE id = %s::uuid AND user_id = %s",
        (capture_id, user_id),
    )
    if cap is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail={"error": "capture_not_found"}
        )
    if cap["status"] == "indexed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail={"error": "capture_indexed"}
        )

    if attachment_id:
        att_id_s = attachment_id.strip()
        try:
            UUID(att_id_s)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "attachment_not_found"},
            ) from exc
        att = ms.fetch_one(
            "SELECT id, attachment_type, mime_type, storage_key, original_filename, "
            "processing_status, size_bytes, created_at FROM capture_attachments "
            "WHERE id = %s::uuid AND capture_id = %s::uuid AND user_id = %s",
            (att_id_s, capture_id, user_id),
        )
        if att is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "attachment_not_found"},
            )
        if not _is_transcribable_audio_attachment(att):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "attachment_not_audio"},
            )
        latest = _latest_transcript_for_attachment(
            ms, user_id=user_id, capture_id=capture_id, attachment_id=att_id_s
        )
        if _transcript_complete_nonempty(latest):
            assert latest is not None
            return TranscribeTarget(att, None, _transcript_row_to_summary(latest))
        up_id = str(latest["id"]) if latest is not None else None
        return TranscribeTarget(att, up_id, None)

    audios = ms.fetch_all(
        "SELECT id, attachment_type, mime_type, storage_key, original_filename, "
        "processing_status, size_bytes, created_at FROM capture_attachments "
        "WHERE capture_id = %s::uuid AND user_id = %s AND attachment_type = 'audio' "
        "ORDER BY created_at ASC",
        (capture_id, user_id),
    )
    audios = [a for a in audios if _is_transcribable_audio_attachment(a)]
    if not audios:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "capture_no_pending_audio"},
        )
    for att in audios:
        aid = str(att["id"])
        latest = _latest_transcript_for_attachment(
            ms, user_id=user_id, capture_id=capture_id, attachment_id=aid
        )
        if _transcript_complete_nonempty(latest):
            continue
        up_id = str(latest["id"]) if latest is not None else None
        return TranscribeTarget(att, up_id, None)

    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"error": "capture_no_pending_audio"},
    )


def finish_capture_transcribe(
    ms: MetadataStore,
    *,
    user_id: str,
    capture_id: str,
    attachment_id: str,
    transcript_row_id_to_update: str | None,
    result: TranscriptionResult | None,
    failed: bool,
    error_message: str | None = None,
) -> CaptureTranscriptSummary:
    if failed or result is None:
        status_t: Literal["failed", "complete"] = "failed"
        text: str | None = None
        provider = result.provider if result else None
        model = result.model if result else None
        language = result.language if result else None
        conf = None
        err = error_message or "transcription_failed"
    else:
        body_text = _normalize_text(result.text)
        if not body_text:
            status_t = "failed"
            text = None
            provider = result.provider
            model = result.model
            language = result.language
            conf = None
            err = "stt_empty_transcript"
        else:
            status_t = "complete"
            text = body_text
            provider = result.provider
            model = result.model
            language = result.language
            conf = None
            err = None

    if transcript_row_id_to_update:
        ms.execute(
            "UPDATE capture_transcripts SET provider = %s, model = %s, transcript_text = %s, "
            "transcript_status = %s, language = %s, confidence = %s, error = %s "
            "WHERE id = %s::uuid AND user_id = %s",
            (
                provider,
                model,
                text,
                status_t,
                language,
                conf,
                err,
                transcript_row_id_to_update,
                user_id,
            ),
        )
        row = ms.fetch_one(
            "SELECT id, attachment_id, transcript_status, transcript_text, transcript_provenance, "
            "language, confidence, created_at, updated_at FROM capture_transcripts "
            "WHERE id = %s::uuid AND user_id = %s",
            (transcript_row_id_to_update, user_id),
        )
    else:
        row = ms.fetch_one(
            "INSERT INTO capture_transcripts (capture_id, attachment_id, user_id, provider, model, "
            "transcript_text, transcript_status, transcript_provenance, language, confidence, error) "
            "VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, 'server_stt', %s, %s, %s) "
            "RETURNING id, attachment_id, transcript_status, transcript_text, transcript_provenance, "
            "language, confidence, created_at, updated_at",
            (
                capture_id,
                attachment_id,
                user_id,
                provider,
                model,
                text,
                status_t,
                language,
                conf,
                err,
            ),
        )
    if row is None:
        _log.error("transcript persist returned no row attachment_id=%s", attachment_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "transcript_persist_failed"},
        )
    return _transcript_row_to_summary(row)


def read_transcribe_audio_bytes(attachment_row: dict) -> tuple[bytes, str]:
    """Load bytes and MIME from disk using ``storage_key``. Raises ``HTTPException``."""
    try:
        path = media_storage.resolve_storage_key_file(str(attachment_row["storage_key"]))
    except ValueError as exc:
        _log.warning("transcribe: bad storage_key %r: %s", attachment_row.get("storage_key"), exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "attachment_not_found"},
        ) from exc
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "attachment_blob_missing"},
        )
    return path.read_bytes(), str(attachment_row["mime_type"])


def _combined_capture_memory_text(detail: CaptureDetail) -> str:
    """Deterministic reviewed-only bundle for ``notes.text`` + Qdrant ``summary``."""
    parts: list[str] = []
    if detail.title:
        t = _normalize_text(detail.title)
        if t:
            parts.append(t)
    if detail.text:
        t = _normalize_text(detail.text)
        if t:
            parts.append(t)
    if detail.url:
        u = _normalize_text(detail.url)
        if u:
            parts.append(f"URL: {u}")
    for tr in sorted(detail.transcripts, key=lambda x: x.created_at):
        if tr.transcript_status != "complete":
            continue
        tx = _normalize_text(tr.transcript_text)
        if tx:
            parts.append(tx)
    return "\n\n".join(parts)


def _audio_transcripts_ready_for_index(detail: CaptureDetail) -> bool:
    """Each audio attachment must have at least one **complete** non-empty transcript."""
    for att in detail.attachments:
        if att.attachment_type != "audio":
            continue
        rows = [t for t in detail.transcripts if t.attachment_id == att.id]
        if not any(
            t.transcript_status == "complete" and _normalize_text(t.transcript_text) for t in rows
        ):
            return False
    return True


def _short_index_error(exc: BaseException) -> str:
    return str(exc).replace("\n", " ")[:2000]


def index_capture(ms: MetadataStore, *, user_id: str, capture_id: str) -> CaptureDetail:
    """Promote capture to a personal note + ``conversations`` Qdrant point (Phase 5G).

    Explicit user action only (route). No automatic indexing on save/STT.
    """
    detail = get_capture(ms, user_id=user_id, capture_id=capture_id)
    if detail.status == "indexed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "capture_indexed"},
        )
    if detail.status not in ("pending", "failed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "capture_invalid_state"},
        )

    has_audio = any(a.attachment_type == "audio" for a in detail.attachments)
    if has_audio and not _audio_transcripts_ready_for_index(detail):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "capture_transcript_required"},
        )

    combined = _combined_capture_memory_text(detail)
    if not combined.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "capture_no_indexable_content"},
        )

    note_row = ms.fetch_one(
        "INSERT INTO notes (text, user_id, source, scope) "
        "VALUES (%s, %s, %s, %s) RETURNING note_id",
        (combined, user_id, "lumogis_web_capture", "personal"),
    )
    if note_row is None or note_row.get("note_id") is None:
        _log.error("capture index: note insert returned no row capture_id=%s", capture_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "note_insert_failed"},
        )
    note_id_str = str(note_row["note_id"])

    try:
        embedder = config.get_embedder()
        vs = config.get_vector_store()
        vector = embedder.embed(combined)
        point_id = note_conversation_point_id(user_id, note_id_str)
        vs.upsert(
            collection="conversations",
            id=point_id,
            vector=vector,
            payload={
                "session_id": note_id_str,
                "summary": combined,
                "user_id": user_id,
                "scope": "personal",
                "note_id": note_id_str,
                "source": "lumogis_web_capture",
            },
        )
    except Exception as exc:
        _log.exception("capture index: Qdrant/embed failed capture_id=%s", capture_id)
        try:
            ms.execute(
                "DELETE FROM notes WHERE note_id = %s::uuid",
                (note_id_str,),
            )
        except Exception:
            _log.warning(
                "capture index: note cleanup failed note_id=%s", note_id_str, exc_info=True
            )
        ms.execute(
            "UPDATE captures SET status = 'failed', last_error = %s "
            "WHERE id = %s::uuid AND user_id = %s",
            (_short_index_error(exc), capture_id, user_id),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "index_memory_unavailable",
                "message": "Could not update the memory index. Try again later.",
            },
        ) from exc

    now = datetime.now(timezone.utc)
    ms.execute(
        "UPDATE captures SET status = 'indexed', note_id = %s::uuid, indexed_at = %s, "
        "last_error = NULL WHERE id = %s::uuid AND user_id = %s",
        (note_id_str, now, capture_id, user_id),
    )

    hooks.fire(Event.NOTE_CAPTURED, note_id=note_id_str, user_id=user_id)
    write_audit(
        AuditEntry(
            action_name="capture_index",
            connector="memory",
            mode="sync",
            input_summary=json.dumps({"capture_id": capture_id}),
            result_summary=json.dumps({"note_id": note_id_str, "status": "indexed"}),
            user_id=user_id,
        ),
    )

    return get_capture(ms, user_id=user_id, capture_id=capture_id)
