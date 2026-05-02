# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for services/media_storage.py — path safety + MIME/size guards.

These tests do NOT hit the filesystem for writes; they exercise the
validation logic only. ``safe_attachment_path`` accepts an explicit
``root`` override so tests can use ``tmp_path`` without touching disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import services.media_storage as ms

# ── safe_attachment_path ──────────────────────────────────────────────


class TestSafeAttachmentPath:
    def test_valid_path_is_within_root(self, tmp_path: Path):
        result = ms.safe_attachment_path(
            "user1", "cap-uuid", "att-uuid", "audio.webm", root=tmp_path
        )
        assert result.is_relative_to(tmp_path)
        assert result == (tmp_path / "user1" / "cap-uuid" / "att-uuid" / "audio.webm").resolve()

    def test_traversal_in_filename_is_caught(self, tmp_path: Path):
        # Enough leading ".." to escape tmp_path regardless of its depth.
        evil = "../" * 20 + "etc/passwd"
        with pytest.raises(ValueError, match="path traversal"):
            ms.safe_attachment_path("user1", "cap-uuid", "att-uuid", evil, root=tmp_path)

    def test_traversal_in_user_id_is_caught(self, tmp_path: Path):
        with pytest.raises(ValueError, match="path traversal"):
            ms.safe_attachment_path("../" * 20, "cap-uuid", "att-uuid", "file.jpg", root=tmp_path)

    def test_normal_nested_path_does_not_raise(self, tmp_path: Path):
        # Nested sub-path still inside root — should not raise.
        result = ms.safe_attachment_path(
            "alice",
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            "photo.jpg",
            root=tmp_path,
        )
        assert str(result).startswith(str(tmp_path))


# ── validate_attachment — MIME ────────────────────────────────────────


class TestValidateAttachmentMime:
    @pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/webp"])
    def test_allowed_image_mimes(self, mime: str):
        ms.validate_attachment(mime, 1024, "image")  # must not raise

    @pytest.mark.parametrize("mime", ["audio/webm", "audio/mp4", "audio/mpeg", "audio/wav"])
    def test_allowed_audio_mimes(self, mime: str):
        ms.validate_attachment(mime, 1024, "audio")  # must not raise

    def test_image_mime_rejected_for_audio_type(self):
        with pytest.raises(ms.MimeNotAllowedError) as exc_info:
            ms.validate_attachment("image/jpeg", 1024, "audio")
        assert "image/jpeg" in str(exc_info.value)

    def test_audio_mime_rejected_for_image_type(self):
        with pytest.raises(ms.MimeNotAllowedError) as exc_info:
            ms.validate_attachment("audio/webm", 1024, "image")
        assert "audio/webm" in str(exc_info.value)

    def test_unknown_mime_rejected(self):
        with pytest.raises(ms.MimeNotAllowedError):
            ms.validate_attachment("application/octet-stream", 1024, "image")

    def test_video_mime_rejected(self):
        with pytest.raises(ms.MimeNotAllowedError):
            ms.validate_attachment("video/mp4", 1024, "audio")

    def test_mime_error_carries_mime_type(self):
        with pytest.raises(ms.MimeNotAllowedError) as exc_info:
            ms.validate_attachment("text/plain", 1024, "image")
        assert exc_info.value.mime_type == "text/plain"


# ── validate_attachment — size ────────────────────────────────────────


class TestValidateAttachmentSize:
    def test_image_at_limit_is_allowed(self):
        ms.validate_attachment("image/jpeg", ms._IMAGE_MAX_BYTES, "image")

    def test_image_over_limit_raises(self):
        with pytest.raises(ms.FileTooLargeError) as exc_info:
            ms.validate_attachment("image/jpeg", ms._IMAGE_MAX_BYTES + 1, "image")
        assert exc_info.value.limit_bytes == ms._IMAGE_MAX_BYTES

    def test_audio_at_limit_is_allowed(self):
        ms.validate_attachment("audio/webm", ms._AUDIO_MAX_BYTES, "audio")

    def test_audio_over_limit_raises(self):
        with pytest.raises(ms.FileTooLargeError) as exc_info:
            ms.validate_attachment("audio/webm", ms._AUDIO_MAX_BYTES + 1, "audio")
        assert exc_info.value.limit_bytes == ms._AUDIO_MAX_BYTES

    def test_zero_bytes_is_allowed(self):
        # Edge: empty file passes size check (MIME check still applies).
        ms.validate_attachment("image/png", 0, "image")

    def test_file_too_large_error_carries_limit(self):
        with pytest.raises(ms.FileTooLargeError) as exc_info:
            ms.validate_attachment("audio/mp4", ms._AUDIO_MAX_BYTES + 100, "audio")
        assert exc_info.value.limit_bytes > 0


# ── classify_inbound_mime + leaf + resolve ───────────────────────────────


class TestClassifyInboundMime:
    def test_classify_image(self):
        k, m = ms.classify_inbound_mime("  Image/JPEG  ")
        assert k == "image"
        assert m == "image/jpeg"

    def test_classify_audio(self):
        k, m = ms.classify_inbound_mime("audio/wav")
        assert k == "audio"
        assert m == "audio/wav"

    def test_reject_unknown(self):
        with pytest.raises(ms.MimeNotAllowedError):
            ms.classify_inbound_mime("application/pdf")


class TestLeafAndResolve:
    def test_leaf_for_each_allowed_mime(self):
        for mime in (
            "image/jpeg",
            "image/png",
            "image/webp",
            "audio/webm",
            "audio/mp4",
            "audio/mpeg",
            "audio/wav",
        ):
            leaf = ms.leaf_filename_for_mime(mime)
            assert leaf.startswith("blob.")

    def test_resolve_storage_key_stays_under_root(self, tmp_path: Path):
        sk = "u1/cap1/att1/blob.jpg"
        p = ms.resolve_storage_key_file(sk, root=tmp_path)
        assert p.is_relative_to(tmp_path.resolve())

    def test_resolve_rejects_parent_segments(self, tmp_path: Path):
        with pytest.raises(ValueError):
            ms.resolve_storage_key_file("u/../etc/passwd", root=tmp_path)
