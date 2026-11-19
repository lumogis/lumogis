# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Port: local speech-to-text (push-to-talk / uploads).

Adapters resolve from :mod:`config` only — services never import adapters.
"""

from __future__ import annotations

from typing import Protocol
from typing import runtime_checkable

from models.api_v1 import TranscriptionResult


class SttUnreachable(Exception):
    """The engine cannot be reached (connect error, or its ping did not answer 200)."""


class SttModelMissing(Exception):
    """The engine answered that the configured speech model is not installed."""


class SttModelUnknown(Exception):
    """The engine does not know this model id (its install call answered 404)."""


class SpeechToText(Protocol):
    """Provider-agnostic transcription; implementations live under ``adapters/``."""

    def ping(self) -> bool:
        """Return True when the backend is reachable (not that a model is installed)."""

    def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str,
        *,
        language: str | None,
        user_id: str,
    ) -> TranscriptionResult:
        """Transcribe ``audio_bytes`` declared as ``mime_type``."""


@runtime_checkable
class SpeechModelInstaller(Protocol):
    """Optional: an engine whose model Core can check and install (LUM-995).

    Separate from :class:`SpeechToText` so adapters that manage no model (the fake, a
    third-party engine) need not implement it.
    """

    def model_installed(self, model: str) -> bool:
        """True when ``model`` is installed in the engine.

        Raises :class:`SttUnreachable` when the engine cannot be reached."""

    def install_model(self, model: str) -> None:
        """Download ``model`` into the engine; blocks until done.

        Raises :class:`SttUnreachable` or :class:`SttModelUnknown`."""
