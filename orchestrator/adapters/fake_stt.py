# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Deterministic STT adapter for tests and dev (no real Whisper — STT‑2).

See draft ADR ``voice_input``: ``fake_stt`` labels responses clearly; production
heavyweight transcription uses ``faster-whisper`` in a later chunk.
"""

from __future__ import annotations

import hashlib
import os

from models.api_v1 import TranscriptionResult


class FakeSpeechToTextAdapter:
    """Returns stable text derived from inputs and optional ``FAKE_STT_OUTPUT``."""

    def ping(self) -> bool:
        return True

    def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str,
        *,
        language: str | None,
        user_id: str,
    ) -> TranscriptionResult:
        override = os.environ.get("FAKE_STT_OUTPUT", "").strip()
        if override:
            text = override
        else:
            h = hashlib.sha256(audio_bytes).hexdigest()[:16]
            text = f"[fake_stt:{len(audio_bytes)}b:{mime_type}:{user_id}:{h}]"
        model = os.environ.get("STT_MODEL", "base").strip() or None
        if model and model.lower() == "fake":
            model_used: str | None = "fake"
        else:
            model_used = "fake"
        return TranscriptionResult(
            text=text,
            language=language,
            duration_seconds=None,
            provider="fake_stt",
            model=model_used,
            segments=[],
        )
