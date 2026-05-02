# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis

from __future__ import annotations

import concurrent.futures
import logging

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def orch_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App with sane STT numeric defaults for route tests."""

    monkeypatch.setenv("STT_MAX_AUDIO_BYTES", "26214400")
    monkeypatch.setenv("STT_MAX_DURATION_SEC", "600")

    import main

    with TestClient(main.app) as c:
        yield c


def _pop_stt_adapter() -> None:
    import config as cfg

    for key in list(cfg._instances):
        if str(key).startswith("speech_to_text"):
            cfg._instances.pop(key, None)


def _auth(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("AUTH_SECRET", "test-voice-route-secret-do-not-use")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token('u-voice', 'user')}"}


def test_stt_disabled_503(orch_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "none")
    hdr = _auth(monkeypatch)
    r = orch_client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("x.webm", b"abc", "audio/webm")},
        headers=hdr,
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "stt_disabled"


def test_auth_401(orch_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "test-voice-401-xx")
    r = orch_client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("x.webm", b"abc", "audio/webm")},
    )
    assert r.status_code == 401


def test_fake_stt_200(orch_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    hdr = _auth(monkeypatch)
    r = orch_client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("x.webm", b"abc", "audio/webm")},
        headers=hdr,
    )
    assert r.status_code == 200
    js = r.json()
    assert js["provider"] == "fake_stt"
    assert js["segments"] == []


def test_oversize_413(orch_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    monkeypatch.setenv("STT_MAX_AUDIO_BYTES", "10")
    hdr = _auth(monkeypatch)
    r = orch_client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("x.webm", b"x" * 20, "audio/webm")},
        headers=hdr,
    )
    assert r.status_code == 413
    assert r.json()["detail"]["code"] == "stt_audio_too_large"


def test_bad_mime_415(orch_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    hdr = _auth(monkeypatch)
    r = orch_client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("x.png", b"abc", "image/png")},
        headers=hdr,
    )
    assert r.status_code == 415
    assert r.json()["detail"]["code"] == "stt_bad_mime"


def test_missing_file_422(orch_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    hdr = _auth(monkeypatch)
    r = orch_client.post("/api/v1/voice/transcribe", data={"language": "en"}, headers=hdr)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "stt_multipart_invalid"


def test_openapi_has_transcribe_route() -> None:
    """Structured spec from the app — avoids auth middleware on ``GET /openapi.json``."""
    from scripts.dump_openapi import _build_openapi
    from scripts.dump_openapi import _normalise

    spec = _normalise(_build_openapi())
    assert "/api/v1/voice/transcribe" in (spec.get("paths") or {})


def test_admin_diagnostics_speech_to_text(
    orch_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pop_stt_adapter()
    monkeypatch.setenv("AUTH_SECRET", "test-voice-admin-diagnostics")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    from auth import mint_access_token

    hdr = {"Authorization": f"Bearer {mint_access_token('voice-admin', 'admin')}"}
    r = orch_client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    st = r.json()["speech_to_text"]
    assert st["backend"] == "fake_stt"
    assert isinstance(st["transcribe_available"], bool)
    assert st["endpoint"] == "/api/v1/voice/transcribe"
    assert isinstance(st["max_audio_bytes"], int)


def test_admin_diagnostics_non_admin_403(
    orch_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_SECRET", "test-voice-admin-diagnostics")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    from auth import mint_access_token

    hdr = {"Authorization": f"Bearer {mint_access_token('bob-nonadmin', 'user')}"}
    r = orch_client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 403


def test_processing_error_503(orch_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    hdr = _auth(monkeypatch)

    import config as cfg

    class Bad:
        def ping(self) -> bool:
            return True

        def transcribe(self, *_a, **_k):
            raise RuntimeError("boom")

    _pop_stt_adapter()
    cfg._instances["speech_to_text:fake_stt"] = Bad()
    r = orch_client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("x.webm", b"abc", "audio/webm")},
        headers=hdr,
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "stt_processing_error"


def test_concurrent_two_posts(orch_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    hdr = _auth(monkeypatch)

    def one() -> int:
        rr = orch_client.post(
            "/api/v1/voice/transcribe",
            files={"file": ("x.webm", b"z", "audio/webm")},
            headers=hdr,
        )
        return rr.status_code

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(lambda _: one(), range(2)))
    assert codes == [200, 200]


def test_no_full_transcript_in_default_logs(
    orch_client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    hdr = _auth(monkeypatch)
    monkeypatch.setenv("FAKE_STT_OUTPUT", "SECRET_LONG_TRANSCRIPT_PAYLOAD_XXXX")
    caplog.set_level(logging.INFO)
    r = orch_client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("x.webm", b"abc", "audio/webm")},
        headers=hdr,
    )
    assert r.status_code == 200
    joined = " ".join(rec.getMessage() for rec in caplog.records)
    assert "SECRET_LONG_TRANSCRIPT_PAYLOAD_XXXX" not in joined


async def test_transcribe_calls_run_in_threadpool(
    orch_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    hits: dict[str, int] = {}

    async def _rtp(fn, *a, **kw):
        hits["calls"] = hits.get("calls", 0) + 1
        return fn(*a, **kw)

    import routes.api_v1.voice as vmod

    monkeypatch.setattr(vmod, "run_in_threadpool", _rtp)
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    hdr = _auth(monkeypatch)
    r = orch_client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("x.webm", b"z", "audio/webm")},
        headers=hdr,
    )
    assert r.status_code == 200
    assert hits.get("calls") == 1


def test_whisper_sidecar_200_via_injected_adapter(
    orch_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "whisper_sidecar")
    monkeypatch.setenv("STT_SIDECAR_URL", "http://lumogis-stt:8080")
    monkeypatch.delenv("STT_SIDECAR_ALLOW_REMOTE", raising=False)

    from models.api_v1 import TranscriptionResult

    import config as cfg

    class Good:
        def ping(self) -> bool:
            return True

        def transcribe(self, *_a, **_k) -> TranscriptionResult:
            return TranscriptionResult(text="svc", provider="whisper_sidecar", model="m")

    cfg._instances["speech_to_text:whisper_sidecar"] = Good()
    hdr = _auth(monkeypatch)
    r = orch_client.post(
        "/api/v1/voice/transcribe",
        files={"file": ("x.webm", b"z", "audio/webm")},
        headers=hdr,
    )
    assert r.status_code == 200
    js = r.json()
    assert js["provider"] == "whisper_sidecar"
    assert js["text"] == "svc"


def test_whisper_diag_backend(
    monkeypatch: pytest.MonkeyPatch, orch_client: TestClient
) -> None:
    _pop_stt_adapter()
    monkeypatch.setenv("AUTH_SECRET", "test-whisper-admin")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("STT_BACKEND", "whisper_sidecar")
    monkeypatch.setenv("STT_SIDECAR_URL", "http://lumogis-stt:8080")

    import config as cfg

    class Stub:
        def ping(self) -> bool:
            return True

        def transcribe(self, *_a, **_k):  # pragma: no cover - never called
            raise AssertionError()

    cfg._instances["speech_to_text:whisper_sidecar"] = Stub()
    from auth import mint_access_token

    hdr = {"Authorization": f"Bearer {mint_access_token('admin-w', 'admin')}"}
    r = orch_client.get("/api/v1/admin/diagnostics", headers=hdr)
    assert r.status_code == 200
    st = r.json()["speech_to_text"]
    assert st["backend"] == "whisper_sidecar"
    assert st["transcribe_available"] is True
