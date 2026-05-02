# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: local speech-to-text (push-to-talk / uploads).

Adapters resolve from :mod:`config` only — services never import adapters.
"""

from __future__ import annotations

from typing import Protocol

from models.api_v1 import TranscriptionResult


class SpeechToText(Protocol):
    """Provider-agnostic transcription; implementations live under ``adapters/``."""

    def ping(self) -> bool:
        """Return True when the backend is reachable and ready."""

    def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str,
        *,
        language: str | None,
        user_id: str,
    ) -> TranscriptionResult:
        """Transcribe ``audio_bytes`` declared as ``mime_type``."""
