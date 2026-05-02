# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Validation + orchestration for speech-to-text (no web framework imports).

``routes/api_v1/voice.py`` maps domain failures to HTTP. Provider wiring stays
in :mod:`config` only; this module does not load provider implementation code.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time

from models.api_v1 import TranscriptionResult

import config as app_config

_log = logging.getLogger(__name__)

_stt_gate = threading.BoundedSemaphore(1)

MIME_DECLARE_LOGGED_ONCE: bool = False

_ALLOWED_MIME = frozenset(
    {
        "audio/webm",
        "audio/mp4",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
    }
)

_FFPROBE_TIMEOUT_SEC = 15
_FFPROBE_STDOUT_CAP = 1_048_576


class SttDisabled(Exception):
    """STT_BACKEND=none — route maps to HTTP 503 ``stt_disabled``."""

    def __init__(
        self, message: str = "Speech-to-text is not enabled on this Lumogis server"
    ) -> None:
        self.code = "stt_disabled"
        self.message = message
        super().__init__(message)


class SttValidationError(Exception):
    """Request-level validation failures (mapped to HTTP in route layer)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class SttProcessingError(Exception):
    """Adapter/ping/transcribe failure — maps to HTTP 503 ``stt_processing_error``."""

    def __init__(self, message: str, code: str = "stt_processing_error") -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def _normalize_mime(mime_type: str) -> str:
    return mime_type.split(";", maxsplit=1)[0].strip().lower()


def _log_declared_mime_once(mime_normalized: str) -> None:
    """Optional INFO note that MIME is declarative-only in STT-1."""

    global MIME_DECLARE_LOGGED_ONCE
    if not MIME_DECLARE_LOGGED_ONCE:
        MIME_DECLARE_LOGGED_ONCE = True
        _log.info(
            "stt.mime: declared-only allowlist (%s); magic-byte sniff is optional follow-up.",
            mime_normalized[:80],
        )


def _probe_duration_sec(audio_bytes: bytes) -> float | None:
    """Best-effort duration seconds via ``ffprobe``; failures log and return ``None``."""

    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        _log.info("event=stt_duration_check_skipped reason=ffprobe_unavailable")
        return None

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="lumogis_stt_", delete=False, suffix=".bin"
        ) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        argv = [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            tmp_path,
        ]
        proc = subprocess.run(
            argv,
            capture_output=True,
            timeout=_FFPROBE_TIMEOUT_SEC,
            check=False,
        )
        if proc.returncode != 0:
            _log.warning(
                "event=stt_duration_probe_failed returncode=%s stderr=%s",
                proc.returncode,
                proc.stderr.decode("utf-8", errors="replace")[:512],
            )
            return None
        stdout = proc.stdout.decode("utf-8", errors="replace")
        if len(stdout) > _FFPROBE_STDOUT_CAP:
            _log.warning("event=stt_duration_probe_failed reason=stdout_overflow")
            return None
        data = json.loads(stdout)
        return float(data["format"]["duration"])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, KeyError, TypeError):
        _log.warning("event=stt_duration_probe_failed", exc_info=True)
        return None
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def transcribe_blob(
    audio_bytes: bytes,
    *,
    mime_type: str,
    language: str | None,
    user_id: str,
) -> TranscriptionResult:
    """Validate request bytes and invoke the wired STT adapter (serialised).

    Raises :class:`SttDisabled` / :class:`SttValidationError` /
    :class:`SttProcessingError`.
    """

    if app_config.get_stt_backend() == "none":
        raise SttDisabled()

    adapter = app_config.get_speech_to_text()
    if adapter is None:
        raise SttDisabled()

    max_bytes = app_config.get_stt_max_audio_bytes()
    max_dur = app_config.get_stt_max_duration_sec()

    mime_n = _normalize_mime(mime_type)
    if mime_n not in _ALLOWED_MIME:
        raise SttValidationError(
            "stt_bad_mime", f"MIME type not allowed: {mime_type!r}"
        )
    _log_declared_mime_once(mime_n)

    if len(audio_bytes) > max_bytes:
        raise SttValidationError(
            "stt_audio_too_large",
            f"Audio exceeds STT_MAX_AUDIO_BYTES ({max_bytes})",
        )

    dur = _probe_duration_sec(audio_bytes)
    if dur is not None and dur > max_dur:
        raise SttValidationError(
            "stt_duration_exceeded",
            f"Reported duration {dur}s exceeds STT_MAX_DURATION_SEC ({max_dur})",
        )

    wait_start = time.monotonic()
    _log.debug("stt queue: waiting for slot")
    _stt_gate.acquire()
    wait_elapsed = time.monotonic() - wait_start
    if wait_elapsed > 5.0:
        _log.warning(
            "event=stt_queue_wait_slow waited_sec=%.2f",
            wait_elapsed,
        )
    elif wait_elapsed > 0:
        _log.debug(
            "event=stt_queue_wait waited_sec=%.2f",
            wait_elapsed,
        )
    try:
        try:
            if not adapter.ping():
                raise SttProcessingError(
                    "Speech-to-text adapter did not respond to ping",
                    "stt_processing_error",
                )
        except SttProcessingError:
            raise
        except Exception as exc:
            _log.exception("stt_ping_failed")
            raise SttProcessingError(
                "Speech-to-text adapter is unavailable", "stt_processing_error"
            ) from exc

        try:
            result = adapter.transcribe(
                audio_bytes,
                mime_n,
                language=language,
                user_id=user_id,
            )
        except Exception as exc:
            _log.exception("stt_transcribe_failed")
            raise SttProcessingError(
                "Speech-to-text transcription failed", "stt_processing_error"
            ) from exc
    finally:
        _stt_gate.release()

    if app_config.parse_stt_debug_log_transcript():
        preview = (result.text[:80] + "…") if len(result.text) > 80 else result.text
        _log.debug("stt_transcript_preview_redacted(len=%s)=%s", len(result.text), preview)

    return result
