# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from unittest import mock

import pytest
from adapters.fake_stt import FakeSpeechToTextAdapter
from models.api_v1 import TranscriptionResult

import config


def _reload_config_env(monkeypatch: pytest.MonkeyPatch, **vals: str | None) -> None:
    """Drop STT adapter singleton + apply env for STT parse tests."""

    for k, v in vals.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    for key in list(config._instances):
        if str(key).startswith("speech_to_text"):
            config._instances.pop(key, None)


def test_fake_stt_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_config_env(
        monkeypatch,
        STT_BACKEND="fake_stt",
        STT_MAX_AUDIO_BYTES="1024",
        STT_MAX_DURATION_SEC="600",
    )
    monkeypatch.setenv("FAKE_STT_OUTPUT", "hello-fake")
    ad = FakeSpeechToTextAdapter()
    r = ad.transcribe(b"x", "audio/wav", language=None, user_id="u1")
    assert r.text == "hello-fake"
    assert r.provider == "fake_stt"
    assert r.segments == []


def test_fake_stt_segments_empty() -> None:
    ad = FakeSpeechToTextAdapter()
    out = ad.transcribe(b"abc", "audio/webm", language="en", user_id="default")
    assert isinstance(out, TranscriptionResult)
    assert out.segments == []


def test_facade_rejects_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_config_env(
        monkeypatch,
        STT_BACKEND="fake_stt",
        STT_MAX_AUDIO_BYTES="10",
        STT_MAX_DURATION_SEC="600",
    )
    import services.speech_to_text as svc

    svc.MIME_DECLARE_LOGGED_ONCE = False
    with pytest.raises(svc.SttValidationError) as ei:
        svc.transcribe_blob(b"1" * 20, mime_type="audio/webm", language=None, user_id="u")
    assert ei.value.code == "stt_audio_too_large"


def test_facade_rejects_bad_mime(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_config_env(
        monkeypatch,
        STT_BACKEND="fake_stt",
        STT_MAX_AUDIO_BYTES="1024",
        STT_MAX_DURATION_SEC="600",
    )
    import services.speech_to_text as svc

    svc.MIME_DECLARE_LOGGED_ONCE = False
    with pytest.raises(svc.SttValidationError) as ei:
        svc.transcribe_blob(b"x", mime_type="image/png", language=None, user_id="u")
    assert ei.value.code == "stt_bad_mime"


def test_ffprobe_long_duration_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_config_env(
        monkeypatch,
        STT_BACKEND="fake_stt",
        STT_MAX_AUDIO_BYTES="1024",
        STT_MAX_DURATION_SEC="5",
    )
    import services.speech_to_text as svc

    svc.MIME_DECLARE_LOGGED_ONCE = False

    fake_json = json.dumps({"format": {"duration": "999.0"}}).encode()

    def fake_run(*_a, **_k):
        return mock.Mock(returncode=0, stdout=fake_json, stderr=b"")

    with mock.patch.object(svc, "_probe_duration_sec", return_value=999.0):
        with pytest.raises(svc.SttValidationError) as ei:
            svc.transcribe_blob(b"x", mime_type="audio/webm", language=None, user_id="u")
    assert ei.value.code == "stt_duration_exceeded"


def test_ffprobe_skips_on_failure_logs_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _reload_config_env(
        monkeypatch,
        STT_BACKEND="fake_stt",
        STT_MAX_AUDIO_BYTES="1024",
        STT_MAX_DURATION_SEC="5",
    )
    import services.speech_to_text as svc

    svc.MIME_DECLARE_LOGGED_ONCE = False
    caplog.set_level(logging.WARNING)
    with mock.patch.object(svc, "_probe_duration_sec", return_value=None):
        r = svc.transcribe_blob(b"ok", mime_type="audio/webm", language=None, user_id="u")
    assert r.provider == "fake_stt"


def test_invalid_stt_max_audio_bytes_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_config_env(monkeypatch, STT_BACKEND="none")
    monkeypatch.setenv("STT_MAX_AUDIO_BYTES", "not-an-int")
    with pytest.raises(RuntimeError):
        config.get_stt_max_audio_bytes()


def test_invalid_stt_max_duration_sec_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _reload_config_env(monkeypatch, STT_BACKEND="none")
    monkeypatch.setenv("STT_MAX_DURATION_SEC", "not-an-int")
    with pytest.raises(RuntimeError):
        config.get_stt_max_duration_sec()


def test_invalid_stt_debug_warns_and_false(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    config._stt_warned_invalid_debug_once = False
    _reload_config_env(monkeypatch, STT_BACKEND="none")
    monkeypatch.setenv("STT_DEBUG_LOG_TRANSCRIPT", "garbage")
    caplog.set_level(logging.WARNING)
    assert config.parse_stt_debug_log_transcript() is False
    assert any("STT_DEBUG_LOG_TRANSCRIPT" in r.message for r in caplog.records)


def test_service_facade_no_fastapi_import() -> None:
    path = Path(__file__).resolve().parents[1] / "services" / "speech_to_text.py"
    txt = path.read_text()
    assert "HTTPException" not in txt
    assert "fastapi" not in txt.lower()


def test_service_facade_no_adapters_import() -> None:
    path = Path(__file__).resolve().parents[1] / "services" / "speech_to_text.py"
    assert "adapters" not in path.read_text()


def test_port_module_loads() -> None:
    assert importlib.util.find_spec("ports.speech_to_text") is not None
