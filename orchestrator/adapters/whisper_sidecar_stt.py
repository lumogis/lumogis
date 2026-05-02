# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""HTTP speech-to-text client for a Whisper-class sidecar (STT‑2A).

Does not ship a sidecar image — operators point ``STT_SIDECAR_URL`` at a compatible
HTTP service. Implements :class:`~ports.speech_to_text.SpeechToText`.
"""

from __future__ import annotations

from typing import Any

import httpx
from models.api_v1 import TranscriptionResult
from models.api_v1 import TranscriptionSegment


class WhisperSidecarSpeechToTextAdapter:
    """``httpx``-backed REST client — no audio/transcript/logging of secrets."""

    _FILENAME_BY_MIME = {
        "audio/webm": "audio.webm",
        "audio/mp4": "audio.mp4",
        "audio/mpeg": "audio.mpeg",
        "audio/wav": "audio.wav",
        "audio/x-wav": "audio.wav",
    }

    def __init__(
        self,
        *,
        base_url: str,
        health_path: str,
        transcribe_path: str,
        http_timeout_sec: int,
        ping_timeout_sec: int,
        api_key: str | None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._health_path = health_path
        self._transcribe_path = transcribe_path
        self._timeout = float(http_timeout_sec)
        self._ping_timeout = float(ping_timeout_sec)
        self._api_key = api_key

    def _client_opts(self) -> dict[str, Any]:
        return {"follow_redirects": False}

    def ping(self) -> bool:
        url = f"{self._base}{self._health_path}"
        try:
            with httpx.Client(**self._client_opts()) as client:
                r = client.get(url, timeout=httpx.Timeout(self._ping_timeout))
        except (httpx.HTTPError, OSError):
            return False
        return r.status_code == 200

    def transcribe(
        self,
        audio_bytes: bytes,
        mime_type: str,
        *,
        language: str | None,
        user_id: str,
    ) -> TranscriptionResult:
        _ = user_id

        import config as cfg

        model = cfg.get_stt_model()
        env_lang = cfg.get_stt_language()
        hint = (language or "").strip() or (env_lang or "").strip() or ""

        basename = self._FILENAME_BY_MIME.get(
            mime_type.split(";", maxsplit=1)[0].strip().lower(),
            "audio.blob",
        )
        data: dict[str, str] = {"model": model}
        if hint:
            data["language"] = hint

        hdrs: dict[str, str] = {}
        if self._api_key:
            hdrs["Authorization"] = f"Bearer {self._api_key}"

        transcribe_url = f"{self._base}{self._transcribe_path}"
        tout = httpx.Timeout(self._timeout)

        mime_plain = mime_type.split(";", maxsplit=1)[0].strip()
        try:
            with httpx.Client(**self._client_opts()) as client:
                resp = client.post(
                    transcribe_url,
                    timeout=tout,
                    headers=hdrs,
                    files={"file": (basename, audio_bytes, mime_plain)},
                    data=data,
                )
        except (httpx.HTTPError, OSError):
            raise

        if resp.status_code >= 400:
            raise ValueError(f"sidecar transcription failed: HTTP {resp.status_code}")

        try:
            payload = resp.json()
        except Exception as exc:
            raise ValueError("sidecar transcription: invalid JSON response") from exc

        try:
            return _transcription_result_from_json(payload, default_model=model)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"sidecar transcription: malformed response: {exc!s}") from exc


def _num(x: Any) -> float:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise ValueError("segment timestamps must be numbers")
    return float(x)


def _segments_from_payload(raw: Any) -> list[TranscriptionSegment]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("segments must be a list")

    items: list[TranscriptionSegment] = []
    for obj in raw:
        if not isinstance(obj, dict):
            raise ValueError("segment entries must be objects")
        txt = obj.get("text")
        if not isinstance(txt, str):
            raise ValueError("segment.text must be a string")
        start = _num(obj.get("start"))
        end = _num(obj.get("end"))
        if end < start:
            raise ValueError("segment end must be >= start")
        items.append(TranscriptionSegment(start=start, end=end, text=txt))
    return items


def _transcription_result_from_json(payload: Any, *, default_model: str) -> TranscriptionResult:
    if isinstance(payload, str):
        try:
            import json as _json

            payload = _json.loads(payload)
        except Exception:
            raise ValueError("transcription payload must be a JSON object")
    if not isinstance(payload, dict):
        raise ValueError("transcription payload must be a JSON object")

    txt = payload.get("text")
    if not isinstance(txt, str):
        raise ValueError('transcription response missing string "text"')

    lang = payload.get("language")
    lang_out = lang if isinstance(lang, str) else None

    dur_val = payload.get("duration_seconds")
    if dur_val is None:
        dur_val = payload.get("duration")
    dur: float | None
    if dur_val is None:
        dur = None
    elif isinstance(dur_val, bool) or not isinstance(dur_val, (int, float)):
        raise ValueError("duration must be a number")
    else:
        dur = float(dur_val)

    mdl = payload.get("model")
    model_out = mdl if isinstance(mdl, str) else default_model

    segs_raw = payload.get("segments")
    segments = _segments_from_payload(segs_raw)

    return TranscriptionResult(
        text=txt,
        language=lang_out,
        duration_seconds=dur,
        provider="whisper_sidecar",
        model=model_out,
        segments=segments,
    )
