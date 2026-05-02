# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis

"""STT-2A sidecar adapter + strict STT_SIDECAR_URL parsing (mocked HTTP)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import adapters.whisper_sidecar_stt as sidecar_adapter
import pytest
from adapters.whisper_sidecar_stt import WhisperSidecarSpeechToTextAdapter

import config


def _purge_stt() -> None:
    for k in list(config._instances):
        if str(k).startswith("speech_to_text"):
            config._instances.pop(k, None)


def _httpx_client_mock(post_return: object, get_return: object | None = None) -> MagicMock:
    inst = MagicMock()
    inst.post.return_value = post_return
    if get_return is not None:
        inst.get.return_value = get_return
    ctx = MagicMock()
    ctx.__enter__.return_value = inst
    ctx.__exit__.return_value = None
    ctor = MagicMock(return_value=ctx)
    return ctor


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, ctor: MagicMock) -> MagicMock:
    monkeypatch.setattr(sidecar_adapter.httpx, "Client", ctor)
    return ctor.return_value.__enter__.return_value


def test_config_unknown_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _purge_stt()
    monkeypatch.setenv("STT_BACKEND", "bogus")
    with pytest.raises(RuntimeError):
        config.get_stt_backend()


def test_config_case_sensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    _purge_stt()
    monkeypatch.setenv("STT_BACKEND", "FAKE_STT")
    with pytest.raises(RuntimeError):
        config.get_stt_backend()


def test_faster_whisper_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _purge_stt()
    monkeypatch.setenv("STT_BACKEND", "faster_whisper")
    with pytest.raises(RuntimeError, match="faster_whisper backend is not implemented"):
        config.get_stt_backend()


def test_whisper_missing_url_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _purge_stt()
    monkeypatch.setenv("STT_BACKEND", "whisper_sidecar")
    monkeypatch.delenv("STT_SIDECAR_URL", raising=False)
    with pytest.raises(RuntimeError):
        config.normalize_stt_sidecar_base_url("")


@pytest.mark.parametrize(
    "bad",
    [
        "ftp://x",
        "http://u:p@localhost/",
        "http://evil.com/path",
        "http://evil.com?q=1",
        "http://evil.com#h",
    ],
)
def test_sidecar_url_rejects_bad_shapes(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.delenv("STT_SIDECAR_ALLOW_REMOTE", raising=False)
    with pytest.raises(RuntimeError):
        config.normalize_stt_sidecar_base_url(bad)


def test_sidecar_empty_string_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STT_SIDECAR_ALLOW_REMOTE", raising=False)
    with pytest.raises(RuntimeError):
        config.normalize_stt_sidecar_base_url("")


@pytest.mark.parametrize(
    "ok",
    [
        "http://localhost:8080/",
        "http://127.0.0.1:9911",
        "http://192.168.4.3:8080/",
        "http://169.254.9.1:8080/",
        "http://lumogis-stt:8080/",
        "http://[::1]:8123/",
    ],
)
def test_sidecar_url_allows_local_shapes(monkeypatch: pytest.MonkeyPatch, ok: str) -> None:
    monkeypatch.delenv("STT_SIDECAR_ALLOW_REMOTE", raising=False)
    assert config.normalize_stt_sidecar_base_url(ok).startswith("http")


def test_public_dns_blocked_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STT_SIDECAR_ALLOW_REMOTE", raising=False)
    with pytest.raises(RuntimeError):
        config.normalize_stt_sidecar_base_url("http://speech.example.com:8080")


def test_ipv4_mapped_public_ip_blocked_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """::ffff:x.x.x.x must not bypass STT_SIDECAR_ALLOW_REMOTE (SSRF hardening)."""
    monkeypatch.delenv("STT_SIDECAR_ALLOW_REMOTE", raising=False)
    with pytest.raises(RuntimeError):
        config.normalize_stt_sidecar_base_url("http://[::ffff:8.8.8.8]:8080/")


def test_ipv4_mapped_private_ip_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STT_SIDECAR_ALLOW_REMOTE", raising=False)
    assert config.normalize_stt_sidecar_base_url(
        "http://[::ffff:192.168.4.3]:8080/"
    ).startswith("http")


def test_public_dns_ok_when_allow_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STT_SIDECAR_ALLOW_REMOTE", "true")
    assert "speech.example.com" in config.normalize_stt_sidecar_base_url(
        "https://speech.example.com:443/"
    )


def test_invalid_bool_allow_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STT_SIDECAR_ALLOW_REMOTE", "maybe")
    with pytest.raises(RuntimeError):
        config.get_stt_sidecar_allow_remote()


def test_invalid_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STT_TIMEOUT_SEC", "-1")
    with pytest.raises(RuntimeError):
        config.get_stt_timeout_sec()


def test_invalid_health_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STT_SIDECAR_HEALTH_PATH", "no_leading_slash")
    with pytest.raises(RuntimeError):
        config.get_stt_sidecar_health_path()


def test_adapter_ping_200(monkeypatch: pytest.MonkeyPatch) -> None:
    ok = MagicMock(status_code=200)
    ctor = _httpx_client_mock(post_return=None, get_return=ok)
    _patch_httpx_client(monkeypatch, ctor)

    ad = WhisperSidecarSpeechToTextAdapter(
        base_url="http://127.0.0.1:7",
        health_path="/health",
        transcribe_path="/v1/audio/transcriptions",
        http_timeout_sec=30,
        ping_timeout_sec=2,
        api_key=None,
    )
    assert ad.ping() is True
    inst = ctor.return_value.__enter__.return_value
    inst.get.assert_called_once()


def test_adapter_ping_non_200_false(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = MagicMock(status_code=503)
    ctor = _httpx_client_mock(post_return=None, get_return=bad)
    _patch_httpx_client(monkeypatch, ctor)

    ad = WhisperSidecarSpeechToTextAdapter(
        base_url="http://127.0.0.1:7",
        health_path="/health",
        transcribe_path="/v1/audio/transcriptions",
        http_timeout_sec=30,
        ping_timeout_sec=2,
        api_key=None,
    )
    assert ad.ping() is False


def test_adapter_transcribe_happy_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _purge_stt()
    monkeypatch.setenv("STT_MODEL", "base")

    body = {
        "text": "hello",
        "language": "en",
        "duration": 3.5,
        "model": "upstream",
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = body
    mock_response.content = json.dumps(body).encode()
    ctor = _httpx_client_mock(post_return=mock_response)
    inst = _patch_httpx_client(monkeypatch, ctor)

    ad = WhisperSidecarSpeechToTextAdapter(
        base_url="http://127.0.0.1:7",
        health_path="/health",
        transcribe_path="/v1/audio/transcriptions",
        http_timeout_sec=30,
        ping_timeout_sec=2,
        api_key="supersecret",
    )
    res = ad.transcribe(b"\xff", "audio/webm", language=None, user_id="u1")
    assert res.text == "hello"
    assert res.provider == "whisper_sidecar"
    assert res.model == "upstream"
    assert res.duration_seconds == 3.5
    assert len(res.segments) == 1
    call_kw = inst.post.call_args[1]
    hdrs = call_kw["headers"]
    assert hdrs["Authorization"] == "Bearer supersecret"
    files = call_kw["files"]
    assert files["file"][0].endswith(".webm")
    data = call_kw["data"]
    assert data["model"] == "base"
    assert "language" not in data


def test_adapter_transcribe_language_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _purge_stt()
    monkeypatch.setenv("STT_MODEL", "base")
    monkeypatch.setenv("STT_LANGUAGE", "fr")

    body = {"text": "bonjour", "segments": []}
    mock_response = MagicMock(status_code=200, content=json.dumps(body).encode())
    mock_response.json.return_value = body
    ctor = _httpx_client_mock(post_return=mock_response)
    inst = _patch_httpx_client(monkeypatch, ctor)

    ad = WhisperSidecarSpeechToTextAdapter(
        base_url="http://127.0.0.1:7",
        health_path="/health",
        transcribe_path="/v1/audio/transcriptions",
        http_timeout_sec=30,
        ping_timeout_sec=2,
        api_key=None,
    )
    ad.transcribe(b"x", "audio/webm", language=None, user_id="u")
    data = inst.post.call_args[1]["data"]
    assert data["language"] == "fr"


def test_adapter_errors_on_http_500(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_response = MagicMock(status_code=500, content=b"err")
    ctor = _httpx_client_mock(post_return=mock_response)
    _patch_httpx_client(monkeypatch, ctor)

    ad = WhisperSidecarSpeechToTextAdapter(
        base_url="http://127.0.0.1:7",
        health_path="/health",
        transcribe_path="/v1/audio/transcriptions",
        http_timeout_sec=30,
        ping_timeout_sec=2,
        api_key=None,
    )
    with pytest.raises(ValueError):
        ad.transcribe(b"x", "audio/webm", language=None, user_id="u")


def test_adapter_transcribe_no_api_key_in_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _purge_stt()
    monkeypatch.setenv("STT_MODEL", "base")
    body = {"text": "x", "segments": []}
    mock_response = MagicMock(status_code=200, content=json.dumps(body).encode())
    mock_response.json.return_value = body
    ctor = _httpx_client_mock(post_return=mock_response)
    _patch_httpx_client(monkeypatch, ctor)

    ad = WhisperSidecarSpeechToTextAdapter(
        base_url="http://127.0.0.1:7",
        health_path="/health",
        transcribe_path="/v1/audio/transcriptions",
        http_timeout_sec=30,
        ping_timeout_sec=2,
        api_key="SECRETKEY",
    )
    import logging

    caplog.set_level(logging.INFO)
    ad.transcribe(b"x", "audio/webm", language=None, user_id="u")
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "SECRETKEY" not in joined
