# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Capture media storage helpers — path safety + MIME/size validation.

Binary blobs are never stored in Postgres. Each attachment lands on disk
under ``CAPTURE_MEDIA_ROOT/{user_id}/{capture_id}/{attachment_id}/``.

Security invariants (plan §12.2):
- ``safe_attachment_path()`` resolves the full candidate path and asserts
  it stays inside the root via ``Path.is_relative_to()``. Any ``..``
  traversal that would escape the root raises ``ValueError``.
- MIME types are validated against a frozen allowlist before the file is
  written; unknown types raise ``MimeNotAllowedError`` (→ HTTP 415).
- Size is checked against per-type byte limits; over-limit raises
  ``FileTooLargeError`` (→ HTTP 413).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Configuration (module-level; read once at import — operator may override
# via environment for the process lifetime).
# ---------------------------------------------------------------------------

_DATA_DIR = Path(os.environ.get("LUMOGIS_DATA_DIR", "/opt/lumogis/data"))
_CAPTURE_MEDIA_ROOT: Path = Path(os.environ.get("CAPTURE_MEDIA_ROOT", str(_DATA_DIR / "captures")))

_IMAGE_MAX_BYTES: int = int(os.environ.get("CAPTURE_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
_AUDIO_MAX_BYTES: int = int(os.environ.get("CAPTURE_MAX_AUDIO_BYTES", str(25 * 1024 * 1024)))

# Frozen MVP allowlists (plan §12.2).
_IMAGE_MIME_ALLOWLIST: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
_AUDIO_MIME_ALLOWLIST: frozenset[str] = frozenset(
    {
        "audio/webm",
        "audio/mp4",
        "audio/mpeg",
        "audio/wav",
    }
)


# ---------------------------------------------------------------------------
# Custom exceptions — callers map these to HTTP status codes.
# ---------------------------------------------------------------------------


class MimeNotAllowedError(ValueError):
    """MIME type is not in the attachment allowlist (→ HTTP 415)."""

    def __init__(self, mime_type: str) -> None:
        self.mime_type = mime_type
        super().__init__(f"MIME type not allowed: {mime_type!r}")


class FileTooLargeError(ValueError):
    """Upload exceeds the per-type size limit (→ HTTP 413)."""

    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = limit_bytes
        super().__init__(f"file exceeds {limit_bytes} byte limit")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_inbound_mime(mime_type: str) -> tuple[Literal["image", "audio"], str]:
    """Map a client ``Content-Type`` to attachment kind + normalised MIME.

    Raises ``MimeNotAllowedError`` when the type is not in either MVP
    allowlist.
    """
    m = mime_type.strip().lower()
    if m in _IMAGE_MIME_ALLOWLIST:
        return "image", m
    if m in _AUDIO_MIME_ALLOWLIST:
        return "audio", m
    raise MimeNotAllowedError(mime_type)


_MIME_LEAF: dict[str, str] = {
    "image/jpeg": "blob.jpg",
    "image/png": "blob.png",
    "image/webp": "blob.webp",
    "audio/webm": "blob.webm",
    "audio/mp4": "blob.m4a",
    "audio/mpeg": "blob.mp3",
    "audio/wav": "blob.wav",
}


def leaf_filename_for_mime(normalised_mime: str) -> str:
    """Server-controlled leaf name under ``…/attachment_id/`` (no client input)."""
    try:
        return _MIME_LEAF[normalised_mime]
    except KeyError as exc:
        raise MimeNotAllowedError(normalised_mime) from exc


def resolve_storage_key_file(storage_key: str, *, root: Path | None = None) -> Path:
    """Resolve ``storage_key`` (relative to capture media root) with traversal checks.

    ``storage_key`` must be a non-empty relative path without ``..`` segments.
    """
    _root = (root if root is not None else _CAPTURE_MEDIA_ROOT).resolve()
    sk = storage_key.strip().lstrip("/")
    if not sk or any(part == ".." for part in Path(sk).parts):
        raise ValueError(f"invalid storage_key: {storage_key!r}")
    candidate = (_root / sk).resolve()
    if not candidate.is_relative_to(_root):
        raise ValueError(
            f"path traversal in storage_key: {storage_key!r} resolves outside {_root!r}"
        )
    return candidate


def unlink_storage_file(storage_key: str, *, root: Path | None = None) -> None:
    """Delete the blob at ``storage_key``; ignores missing files."""
    path = resolve_storage_key_file(storage_key, root=root)
    path.unlink(missing_ok=True)


def safe_attachment_path(
    user_id: str,
    capture_id: str,
    attachment_id: str,
    filename: str,
    *,
    root: Path | None = None,
) -> Path:
    """Return the on-disk path for an attachment file, asserting no traversal.

    Parameters
    ----------
    user_id, capture_id, attachment_id:
        DB-sourced identifiers (UUIDs / JWT claim) — treated as path
        components.
    filename:
        Server-generated filename appended as the leaf. Must not cause the
        resolved path to exit the root.
    root:
        Override for the media root; defaults to ``_CAPTURE_MEDIA_ROOT``.
        Pass a ``tmp_path`` in tests to avoid touching the filesystem.

    Raises
    ------
    ValueError
        If the resolved candidate path escapes the root (path traversal).
    """
    _root = (root if root is not None else _CAPTURE_MEDIA_ROOT).resolve()
    candidate = (_root / user_id / capture_id / attachment_id / filename).resolve()
    if not candidate.is_relative_to(_root):
        raise ValueError(
            f"path traversal detected: resolved path {candidate!r} is outside root {_root!r}"
        )
    return candidate


def validate_attachment(
    mime_type: str,
    size_bytes: int,
    attachment_type: Literal["image", "audio"],
) -> None:
    """Validate MIME type and byte size for an inbound attachment.

    Parameters
    ----------
    mime_type:
        Content-Type declared by the client (lower-cased before call).
    size_bytes:
        Total byte count of the upload.
    attachment_type:
        ``"image"`` or ``"audio"`` — selects the allowlist and size cap.

    Raises
    ------
    MimeNotAllowedError
        MIME type is not in the frozen allowlist for ``attachment_type``.
    FileTooLargeError
        ``size_bytes`` exceeds the configured limit for ``attachment_type``.
    """
    allowlist = _IMAGE_MIME_ALLOWLIST if attachment_type == "image" else _AUDIO_MIME_ALLOWLIST
    if mime_type not in allowlist:
        raise MimeNotAllowedError(mime_type)

    limit = _IMAGE_MAX_BYTES if attachment_type == "image" else _AUDIO_MAX_BYTES
    if size_bytes > limit:
        raise FileTooLargeError(limit)
