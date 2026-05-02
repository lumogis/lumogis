# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Phase-5 capture API — **5B** CRUD + **5C** attachments + **5D** transcribe + **5G** index (in-memory store).

Legacy ``POST /upload`` remains **501**. ``POST …/index`` is **live** (**5G**).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timezone

import pytest
from fastapi.testclient import TestClient
from services.point_ids import note_conversation_point_id
from tests.captures_memory_store import CapturesMemoryMetadataStore


def _inject_audio_attachment(
    captures_ms: CapturesMemoryMetadataStore,
    cap_id: str,
    user_id: str = "default",
) -> str:
    aid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    captures_ms.attachments[aid] = {
        "id": uuid.UUID(aid),
        "capture_id": uuid.UUID(cap_id),
        "user_id": user_id,
        "attachment_type": "audio",
        "storage_key": f"{user_id}/{cap_id}/{aid}/blob.webm",
        "original_filename": "x.webm",
        "mime_type": "audio/webm",
        "size_bytes": 10,
        "sha256": None,
        "processing_status": "stored",
        "client_attachment_id": None,
        "created_at": now,
    }
    return aid


def _inject_transcript(
    captures_ms: CapturesMemoryMetadataStore,
    *,
    cap_id: str,
    att_id: str,
    user_id: str = "default",
    text: str = "spoken text",
) -> None:
    tid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    captures_ms.transcripts[tid] = {
        "id": uuid.UUID(tid),
        "capture_id": uuid.UUID(cap_id),
        "attachment_id": uuid.UUID(att_id),
        "user_id": user_id,
        "provider": "p",
        "model": "m",
        "transcript_text": text,
        "transcript_status": "complete",
        "transcript_provenance": "server_stt",
        "language": None,
        "confidence": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }


@pytest.fixture(autouse=True)
def _single_user_dev_auth(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture
def captures_ms(monkeypatch: pytest.MonkeyPatch) -> CapturesMemoryMetadataStore:
    """Swap the autouse mock for a store that understands capture SQL."""

    import config as cfg

    store = CapturesMemoryMetadataStore()
    cfg._instances["metadata_store"] = store
    return store


@pytest.fixture
def capture_media_root(tmp_path, monkeypatch: pytest.MonkeyPatch):
    import services.media_storage as mst

    root = tmp_path.resolve()
    monkeypatch.setattr(mst, "_CAPTURE_MEDIA_ROOT", root)
    return root


@pytest.fixture
def client(captures_ms: CapturesMemoryMetadataStore, capture_media_root):
    import main

    with TestClient(main.app) as c:
        yield c


def _assert_501(resp):
    assert resp.status_code == 501
    body = resp.json()
    assert body["detail"]["error"] == "not_implemented"
    assert body["detail"]["since_phase"] == 5


# ── Create ───────────────────────────────────────────────────────────


def test_create_capture_rejects_blank_text_without_url(client: TestClient):
    resp = client.post("/api/v1/captures", json={"text": "  \t  "})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "capture_requires_text_or_url"


def test_create_capture_201(client: TestClient):
    resp = client.post("/api/v1/captures", json={"text": "quick note"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert uuid.UUID(body["capture_id"])


def test_create_capture_with_url(client: TestClient):
    resp = client.post(
        "/api/v1/captures",
        json={"url": "https://example.com", "title": "A link"},
    )
    assert resp.status_code == 201


def test_create_capture_missing_content_returns_422(client: TestClient):
    resp = client.post("/api/v1/captures", json={})
    assert resp.status_code == 422


def test_create_capture_invalid_url_scheme_422(client: TestClient):
    resp = client.post("/api/v1/captures", json={"url": "ftp://example.com/x"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_url_scheme"


def test_create_idempotent_replay_200(client: TestClient):
    cid = "00000000-0000-4000-8000-0000000000ab"
    payload = {"text": "idempotent note", "client_id": cid}
    r1 = client.post("/api/v1/captures", json=payload)
    assert r1.status_code == 201
    cap_id = r1.json()["capture_id"]
    r2 = client.post("/api/v1/captures", json=payload)
    assert r2.status_code == 200
    assert r2.json()["capture_id"] == cap_id


def test_create_idempotency_key_conflict_409(client: TestClient):
    cid = "00000000-0000-4000-8000-0000000000cd"
    r1 = client.post("/api/v1/captures", json={"text": "first", "client_id": cid})
    assert r1.status_code == 201
    r2 = client.post("/api/v1/captures", json={"text": "second", "client_id": cid})
    assert r2.status_code == 409
    assert r2.json()["detail"]["error"] == "idempotency_key_conflict"


def test_create_invalid_client_id_422(client: TestClient):
    resp = client.post(
        "/api/v1/captures",
        json={"text": "x", "client_id": "not-a-uuid"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_client_id"


# ── Text alias ────────────────────────────────────────────────────────


def test_text_capture_alias_201(client: TestClient):
    resp = client.post(
        "/api/v1/captures/text",
        json={"text": "hello", "scope": "personal"},
    )
    assert resp.status_code == 201


def test_text_capture_scope_not_personal_422(client: TestClient):
    resp = client.post(
        "/api/v1/captures/text",
        json={"text": "hello", "scope": "shared"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "capture_scope_not_supported"


# ── List ──────────────────────────────────────────────────────────────


def test_list_captures_pagination(client: TestClient):
    for i in range(3):
        assert client.post("/api/v1/captures", json={"text": f"n{i}"}).status_code == 201
    r = client.get("/api/v1/captures?limit=2&offset=0")
    assert r.status_code == 200
    data = r.json()
    assert data["total"] == 3
    assert len(data["captures"]) == 2
    assert data["limit"] == 2
    assert data["offset"] == 0


def test_list_captures_scope_shared_empty(client: TestClient):
    assert client.post("/api/v1/captures", json={"text": "solo"}).status_code == 201
    r = client.get("/api/v1/captures?scope=shared")
    assert r.status_code == 200
    assert r.json()["total"] == 0
    assert r.json()["captures"] == []


def test_list_captures_invalid_scope_422(client: TestClient):
    r = client.get("/api/v1/captures?scope=not_a_scope")
    assert r.status_code == 422


# ── Per-capture CRUD ────────────────────────────────────────────────────


def test_get_patch_delete_roundtrip(client: TestClient, captures_ms: CapturesMemoryMetadataStore):
    r = client.post("/api/v1/captures", json={"text": "orig", "tags": ["b", "a"]})
    cap_id = r.json()["capture_id"]
    g = client.get(f"/api/v1/captures/{cap_id}")
    assert g.status_code == 200
    assert g.json()["text"] == "orig"
    assert g.json()["tags"] == ["a", "b"]

    p = client.patch(
        f"/api/v1/captures/{cap_id}",
        json={"text": "updated", "tags": []},
    )
    assert p.status_code == 200
    assert p.json()["text"] == "updated"
    assert p.json()["tags"] == []

    d = client.delete(f"/api/v1/captures/{cap_id}")
    assert d.status_code == 204
    assert client.get(f"/api/v1/captures/{cap_id}").status_code == 404


def test_get_capture_not_found_404(client: TestClient):
    missing = "00000000-0000-4000-8000-00000000beef"
    assert client.get(f"/api/v1/captures/{missing}").status_code == 404


def test_get_capture_malformed_id_404(client: TestClient):
    assert client.get("/api/v1/captures/not-uuid").status_code == 404


def test_patch_requires_text_or_url_when_clearing_both(client: TestClient):
    r = client.post("/api/v1/captures", json={"text": "only text"})
    cap_id = r.json()["capture_id"]
    resp = client.patch(
        f"/api/v1/captures/{cap_id}",
        json={"text": "", "url": ""},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "capture_requires_text_or_url"


def test_patch_and_delete_blocked_when_indexed(
    client: TestClient, captures_ms: CapturesMemoryMetadataStore
):
    r = client.post("/api/v1/captures", json={"text": "to index later"})
    cap_id = r.json()["capture_id"]
    captures_ms.captures[cap_id]["status"] = "indexed"

    pr = client.patch(f"/api/v1/captures/{cap_id}", json={"text": "x"})
    assert pr.status_code == 409
    assert pr.json()["detail"]["error"] == "capture_indexed_not_editable"

    dr = client.delete(f"/api/v1/captures/{cap_id}")
    assert dr.status_code == 409
    assert dr.json()["detail"]["error"] == "indexed_capture_requires_memory_delete"


def test_patch_and_delete_allowed_when_failed(
    client: TestClient, captures_ms: CapturesMemoryMetadataStore
):
    r = client.post("/api/v1/captures", json={"text": "failed row"})
    cap_id = r.json()["capture_id"]
    captures_ms.captures[cap_id]["status"] = "failed"

    pr = client.patch(f"/api/v1/captures/{cap_id}", json={"title": "t"})
    assert pr.status_code == 200
    dr = client.delete(f"/api/v1/captures/{cap_id}")
    assert dr.status_code == 204


def test_cross_user_isolation_404(
    client: TestClient, captures_ms: CapturesMemoryMetadataStore, monkeypatch
):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "capture-route-test-secret-do-not-use")
    from auth import mint_access_token

    alice_h = {"Authorization": f"Bearer {mint_access_token('alice-cap', 'user')}"}
    bob_h = {"Authorization": f"Bearer {mint_access_token('bob-cap', 'user')}"}
    r = client.post("/api/v1/captures", json={"text": "alice only"}, headers=alice_h)
    assert r.status_code == 201
    cap_id = r.json()["capture_id"]
    assert client.get(f"/api/v1/captures/{cap_id}", headers=bob_h).status_code == 404


def test_require_user_401_when_auth_enabled(client: TestClient, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "capture-route-test-secret-do-not-use")
    assert client.post("/api/v1/captures", json={"text": "x"}).status_code == 401


# ── Attachments (5C) ────────────────────────────────────────────────────


def test_attachment_upload_detail_download_delete(client: TestClient, capture_media_root):
    cap = client.post("/api/v1/captures", json={"text": "has file"})
    assert cap.status_code == 201
    cap_id = cap.json()["capture_id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    up = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        files={"file": ("shot.png", png, "image/png")},
    )
    assert up.status_code == 201
    body = up.json()
    att_id = body["id"]
    assert body["mime_type"] == "image/png"
    assert body["attachment_type"] == "image"
    detail = client.get(f"/api/v1/captures/{cap_id}")
    assert detail.status_code == 200
    assert len(detail.json()["attachments"]) == 1
    assert detail.json()["attachments"][0]["id"] == att_id
    assert detail.json()["capture_type"] == "mixed"

    dl = client.get(f"/api/v1/captures/{cap_id}/attachments/{att_id}")
    assert dl.status_code == 200
    assert dl.content.startswith(b"\x89PNG")
    assert "attachment" in (dl.headers.get("content-disposition") or "").lower()

    rm = client.delete(f"/api/v1/captures/{cap_id}/attachments/{att_id}")
    assert rm.status_code == 204
    assert not any(capture_media_root.rglob("blob.png"))


def test_attachment_upload_missing_capture_404(client: TestClient):
    missing = "00000000-0000-4000-8000-00000000cafe"
    r = client.post(
        f"/api/v1/captures/{missing}/attachments",
        files={"file": ("x.jpg", b"x", "image/jpeg")},
    )
    assert r.status_code == 404


def test_attachment_upload_mime_rejected_415(client: TestClient):
    cap = client.post("/api/v1/captures", json={"text": "x"})
    cap_id = cap.json()["capture_id"]
    r = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        files={"file": ("x.bin", b"zzz", "application/octet-stream")},
    )
    assert r.status_code == 415


def test_attachment_upload_too_large_413(client: TestClient, monkeypatch):
    import services.media_storage as mst

    monkeypatch.setattr(mst, "_IMAGE_MAX_BYTES", 10)
    cap = client.post("/api/v1/captures", json={"text": "x"})
    cap_id = cap.json()["capture_id"]
    r = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        files={"file": ("big.jpg", b"x" * 20, "image/jpeg")},
    )
    assert r.status_code == 413
    assert r.json()["detail"]["error"] == "file_too_large"


def test_attachment_idempotent_replay_200(client: TestClient):
    cap = client.post("/api/v1/captures", json={"text": "x"})
    cap_id = cap.json()["capture_id"]
    cid = "00000000-0000-4000-8000-00000000ab01"
    f = {"file": ("a.jpg", b"bytes", "image/jpeg")}
    r1 = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        data={"client_attachment_id": cid},
        files=f,
    )
    assert r1.status_code == 201
    aid = r1.json()["id"]
    r2 = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        data={"client_attachment_id": cid},
        files=f,
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == aid


def test_attachment_invalid_client_id_422(client: TestClient):
    cap = client.post("/api/v1/captures", json={"text": "x"})
    cap_id = cap.json()["capture_id"]
    r = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        data={"client_attachment_id": "not-a-uuid"},
        files={"file": ("a.jpg", b"1", "image/jpeg")},
    )
    assert r.status_code == 422


def test_attachment_cross_user_404(client: TestClient, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "capture-route-test-secret-do-not-use")
    from auth import mint_access_token

    alice_h = {"Authorization": f"Bearer {mint_access_token('alice-att', 'user')}"}
    bob_h = {"Authorization": f"Bearer {mint_access_token('bob-att', 'user')}"}
    cap = client.post("/api/v1/captures", json={"text": "alice"}, headers=alice_h)
    cap_id = cap.json()["capture_id"]
    r = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        headers=bob_h,
        files={"file": ("x.jpg", b"x", "image/jpeg")},
    )
    assert r.status_code == 404


def test_attachment_blocked_when_capture_indexed_409(
    client: TestClient, captures_ms: CapturesMemoryMetadataStore
):
    cap = client.post("/api/v1/captures", json={"text": "x"})
    cap_id = cap.json()["capture_id"]
    captures_ms.captures[cap_id]["status"] = "indexed"
    r = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        files={"file": ("x.jpg", b"x", "image/jpeg")},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "capture_indexed"


def test_attachment_delete_blocked_when_indexed_409(
    client: TestClient,
    captures_ms: CapturesMemoryMetadataStore,
):
    cap = client.post("/api/v1/captures", json={"text": "x"})
    cap_id = cap.json()["capture_id"]
    up = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        files={"file": ("x.jpg", b"x", "image/jpeg")},
    )
    assert up.status_code == 201
    att_id = up.json()["id"]
    captures_ms.captures[cap_id]["status"] = "indexed"
    r = client.delete(f"/api/v1/captures/{cap_id}/attachments/{att_id}")
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "capture_indexed"


def test_attachment_download_missing_blob_404(client: TestClient, capture_media_root):
    cap = client.post("/api/v1/captures", json={"text": "x"})
    cap_id = cap.json()["capture_id"]
    up = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        files={"file": ("x.jpg", b"hi", "image/jpeg")},
    )
    att_id = up.json()["id"]
    for p in capture_media_root.rglob("blob.jpg"):
        p.unlink()
    r = client.get(f"/api/v1/captures/{cap_id}/attachments/{att_id}")
    assert r.status_code == 404


def test_attachment_download_malformed_ids_404(client: TestClient):
    cap = client.post("/api/v1/captures", json={"text": "x"})
    cap_id = cap.json()["capture_id"]
    r = client.get(f"/api/v1/captures/{cap_id}/attachments/not-a-uuid")
    assert r.status_code == 404


# ── Transcribe (5D) ───────────────────────────────────────────────────────


def _pop_stt_adapter() -> None:
    import config as cfg

    for key in list(cfg._instances):
        if str(key).startswith("speech_to_text"):
            cfg._instances.pop(key, None)


def _auth_headers(monkeypatch: pytest.MonkeyPatch, user_sub: str) -> dict[str, str]:
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "capture-transcribe-test-secret-do-not-use")
    from auth import mint_access_token

    return {"Authorization": f"Bearer {mint_access_token(user_sub, 'user')}"}


def _upload_audio_attachment(
    client: TestClient,
    cap_id: str,
    *,
    body: bytes = b"fake-webm-audio",
    headers: dict[str, str] | None = None,
    **form,
) -> str:
    up = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        files={"file": ("x.webm", body, "audio/webm")},
        data=dict(form),
        headers=headers or {},
    )
    assert up.status_code == 201, up.text
    return up.json()["id"]


def test_transcribe_success_persists_and_lists_on_detail(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    monkeypatch.setenv("STT_MAX_AUDIO_BYTES", "26214400")
    monkeypatch.setenv("STT_MAX_DURATION_SEC", "600")
    monkeypatch.setenv("FAKE_STT_OUTPUT", "hello capture")

    cap_id = client.post("/api/v1/captures", json={"text": "note"}).json()["capture_id"]
    att_id = _upload_audio_attachment(client, cap_id)

    r = client.post(
        f"/api/v1/captures/{cap_id}/transcribe",
        json={"attachment_id": att_id},
    )
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["transcript_status"] == "complete"
    assert js["transcript_text"] == "hello capture"
    assert js["attachment_id"] == att_id

    det = client.get(f"/api/v1/captures/{cap_id}")
    assert det.status_code == 200
    ts = det.json()["transcripts"]
    assert len(ts) == 1
    assert ts[0]["id"] == js["id"]
    assert ts[0]["transcript_text"] == "hello capture"


def test_transcribe_idempotent_returns_existing_without_second_row(
    client: TestClient,
    captures_ms: CapturesMemoryMetadataStore,
    monkeypatch: pytest.MonkeyPatch,
):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    monkeypatch.setenv("STT_MAX_AUDIO_BYTES", "26214400")
    monkeypatch.setenv("FAKE_STT_OUTPUT", "stable")

    cap_id = client.post("/api/v1/captures", json={"text": "n"}).json()["capture_id"]
    att_id = _upload_audio_attachment(client, cap_id, body=b"bytes-one")

    r1 = client.post(
        f"/api/v1/captures/{cap_id}/transcribe",
        json={"attachment_id": att_id},
    )
    assert r1.status_code == 200
    r2 = client.post(
        f"/api/v1/captures/{cap_id}/transcribe",
        json={"attachment_id": att_id},
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"]
    assert len(captures_ms.transcripts) == 1


def test_transcribe_omitted_attachment_picks_first_pending_audio(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    monkeypatch.setenv("STT_MAX_AUDIO_BYTES", "26214400")
    monkeypatch.setenv("FAKE_STT_OUTPUT", "second only")

    cap_id = client.post("/api/v1/captures", json={"text": "n"}).json()["capture_id"]
    first_id = _upload_audio_attachment(client, cap_id, body=b"a1")
    second_id = _upload_audio_attachment(client, cap_id, body=b"a2")

    assert (
        client.post(
            f"/api/v1/captures/{cap_id}/transcribe",
            json={"attachment_id": first_id},
        ).status_code
        == 200
    )

    r = client.post(f"/api/v1/captures/{cap_id}/transcribe", json={})
    assert r.status_code == 200
    assert r.json()["attachment_id"] == second_id


def test_transcribe_all_audio_complete_returns_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    monkeypatch.setenv("STT_MAX_AUDIO_BYTES", "26214400")
    monkeypatch.setenv("FAKE_STT_OUTPUT", "done")

    cap_id = client.post("/api/v1/captures", json={"text": "n"}).json()["capture_id"]
    att_id = _upload_audio_attachment(client, cap_id)
    assert (
        client.post(
            f"/api/v1/captures/{cap_id}/transcribe",
            json={"attachment_id": att_id},
        ).status_code
        == 200
    )

    r = client.post(f"/api/v1/captures/{cap_id}/transcribe", json={})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "capture_no_pending_audio"


def test_transcribe_no_audio_returns_422(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    cap_id = client.post("/api/v1/captures", json={"text": "solo"}).json()["capture_id"]
    r = client.post(f"/api/v1/captures/{cap_id}/transcribe", json={})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "capture_no_pending_audio"


def test_transcribe_image_attachment_id_returns_422(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    cap_id = client.post("/api/v1/captures", json={"text": "n"}).json()["capture_id"]
    up = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        files={"file": ("x.jpg", b"\xff\xd8\xff", "image/jpeg")},
    )
    assert up.status_code == 201
    att_id = up.json()["id"]
    r = client.post(
        f"/api/v1/captures/{cap_id}/transcribe",
        json={"attachment_id": att_id},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "attachment_not_audio"


def test_transcribe_missing_capture_404(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    missing = "00000000-0000-4000-8000-00000000beef"
    r = client.post(f"/api/v1/captures/{missing}/transcribe", json={})
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "capture_not_found"


def test_transcribe_malformed_capture_id_404(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    r = client.post("/api/v1/captures/not-uuid/transcribe", json={})
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "capture_not_found"


def test_transcribe_missing_attachment_404(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    cap_id = client.post("/api/v1/captures", json={"text": "n"}).json()["capture_id"]
    ghost = "00000000-0000-4000-8000-000000000099"
    r = client.post(
        f"/api/v1/captures/{cap_id}/transcribe",
        json={"attachment_id": ghost},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "attachment_not_found"


def test_transcribe_malformed_attachment_id_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    cap_id = client.post("/api/v1/captures", json={"text": "n"}).json()["capture_id"]
    r = client.post(
        f"/api/v1/captures/{cap_id}/transcribe",
        json={"attachment_id": "not-a-uuid"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "attachment_not_found"


def test_transcribe_blob_missing_404(
    client: TestClient, capture_media_root, monkeypatch: pytest.MonkeyPatch
):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    cap_id = client.post("/api/v1/captures", json={"text": "n"}).json()["capture_id"]
    att_id = _upload_audio_attachment(client, cap_id)
    for p in capture_media_root.rglob("*"):
        if p.is_file():
            p.unlink()
    r = client.post(
        f"/api/v1/captures/{cap_id}/transcribe",
        json={"attachment_id": att_id},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "attachment_blob_missing"


def test_transcribe_indexed_capture_409(
    client: TestClient,
    captures_ms: CapturesMemoryMetadataStore,
    monkeypatch: pytest.MonkeyPatch,
):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    cap_id = client.post("/api/v1/captures", json={"text": "n"}).json()["capture_id"]
    att_id = _upload_audio_attachment(client, cap_id)
    captures_ms.captures[cap_id]["status"] = "indexed"
    r = client.post(
        f"/api/v1/captures/{cap_id}/transcribe",
        json={"attachment_id": att_id},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "capture_indexed"


def test_transcribe_cross_user_404(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    hdr_alice = _auth_headers(monkeypatch, "alice-tr")
    hdr_bob = _auth_headers(monkeypatch, "bob-tr")
    cap_id = client.post(
        "/api/v1/captures",
        json={"text": "alice"},
        headers=hdr_alice,
    ).json()["capture_id"]
    att_id = _upload_audio_attachment(client, cap_id, body=b"x", headers=hdr_alice)

    r = client.post(
        f"/api/v1/captures/{cap_id}/transcribe",
        json={"attachment_id": att_id},
        headers=hdr_bob,
    )
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "capture_not_found"


def test_transcribe_stt_disabled_503(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "none")
    cap_id = client.post("/api/v1/captures", json={"text": "n"}).json()["capture_id"]
    att_id = _upload_audio_attachment(client, cap_id)
    r = client.post(
        f"/api/v1/captures/{cap_id}/transcribe",
        json={"attachment_id": att_id},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "stt_disabled"


def test_transcribe_stt_processing_error_503(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    monkeypatch.setenv("STT_MAX_AUDIO_BYTES", "26214400")

    import config as cfg

    class Bad:
        def ping(self) -> bool:
            return True

        def transcribe(self, *a, **k):
            from services.speech_to_text import SttProcessingError

            raise SttProcessingError("boom")

    cfg._instances["speech_to_text:fake_stt"] = Bad()

    cap_id = client.post("/api/v1/captures", json={"text": "n"}).json()["capture_id"]
    att_id = _upload_audio_attachment(client, cap_id)
    r = client.post(
        f"/api/v1/captures/{cap_id}/transcribe",
        json={"attachment_id": att_id},
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "stt_processing_error"


def test_transcribe_empty_stt_text_persists_failed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    _pop_stt_adapter()
    monkeypatch.setenv("STT_BACKEND", "fake_stt")
    monkeypatch.setenv("STT_MAX_AUDIO_BYTES", "26214400")

    import routes.api_v1.captures as cap_routes
    from models.api_v1 import TranscriptionResult

    def _tb(*_a, **_k):
        return TranscriptionResult(
            text="",
            language=None,
            duration_seconds=None,
            provider="fake_stt",
            model="fake",
            segments=[],
        )

    monkeypatch.setattr(cap_routes, "transcribe_blob", _tb)

    cap_id = client.post("/api/v1/captures", json={"text": "n"}).json()["capture_id"]
    att_id = _upload_audio_attachment(client, cap_id)
    r = client.post(
        f"/api/v1/captures/{cap_id}/transcribe",
        json={"attachment_id": att_id},
    )
    assert r.status_code == 200
    assert r.json()["transcript_status"] == "failed"
    assert r.json()["transcript_text"] is None


# ── Index (5G) ──────────────────────────────────────────────────────────


def test_index_text_capture_success(
    client: TestClient, captures_ms: CapturesMemoryMetadataStore, mock_vector_store
):
    r = client.post("/api/v1/captures", json={"text": "hello", "title": "My title"})
    assert r.status_code == 201
    cap_id = r.json()["capture_id"]
    ir = client.post(f"/api/v1/captures/{cap_id}/index")
    assert ir.status_code == 200
    body = ir.json()
    assert body["status"] == "indexed"
    assert body["note_id"]
    assert captures_ms.captures[cap_id]["status"] == "indexed"
    conv = mock_vector_store._collections.get("conversations", [])
    assert conv
    payload = conv[-1]["payload"]
    assert payload["source"] == "lumogis_web_capture"
    assert payload["scope"] == "personal"
    assert payload["user_id"] == "default"
    assert payload["session_id"] == payload["note_id"]
    assert "hello" in payload["summary"] and "My title" in payload["summary"]
    expected_pid = note_conversation_point_id("default", body["note_id"])
    assert conv[-1]["id"] == expected_pid


def test_index_includes_url_in_combined_text(client: TestClient, mock_vector_store):
    r = client.post(
        "/api/v1/captures",
        json={"url": "https://example.com/x", "title": "Link"},
    )
    cap_id = r.json()["capture_id"]
    ir = client.post(f"/api/v1/captures/{cap_id}/index")
    assert ir.status_code == 200
    summary = mock_vector_store._collections["conversations"][-1]["payload"]["summary"]
    assert "URL: https://example.com/x" in summary


def test_index_audio_requires_complete_transcript(
    client: TestClient, captures_ms: CapturesMemoryMetadataStore
):
    r = client.post("/api/v1/captures", json={"text": "voice"})
    cap_id = r.json()["capture_id"]
    _inject_audio_attachment(captures_ms, cap_id)
    ir = client.post(f"/api/v1/captures/{cap_id}/index")
    assert ir.status_code == 422
    assert ir.json()["detail"]["error"] == "capture_transcript_required"


def test_index_audio_with_transcript_ok(
    client: TestClient, captures_ms: CapturesMemoryMetadataStore, mock_vector_store
):
    r = client.post("/api/v1/captures", json={"text": "voice"})
    cap_id = r.json()["capture_id"]
    aid = _inject_audio_attachment(captures_ms, cap_id)
    _inject_transcript(captures_ms, cap_id=cap_id, att_id=aid, text="said hello")
    ir = client.post(f"/api/v1/captures/{cap_id}/index")
    assert ir.status_code == 200
    summary = mock_vector_store._collections["conversations"][-1]["payload"]["summary"]
    assert "voice" in summary and "said hello" in summary


def test_index_photo_without_caption_fails(client: TestClient, capture_media_root):
    cap = client.post("/api/v1/captures", json={"text": "placeholder"})
    cap_id = cap.json()["capture_id"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    up = client.post(
        f"/api/v1/captures/{cap_id}/attachments",
        files={"file": ("shot.png", png, "image/png")},
    )
    assert up.status_code == 201
    pr = client.patch(
        f"/api/v1/captures/{cap_id}",
        json={"text": None, "title": None, "url": None},
    )
    assert pr.status_code == 200
    ir = client.post(f"/api/v1/captures/{cap_id}/index")
    assert ir.status_code == 422
    assert ir.json()["detail"]["error"] == "capture_no_indexable_content"


def test_index_already_indexed_409(client: TestClient):
    cap_id = client.post("/api/v1/captures", json={"text": "x"}).json()["capture_id"]
    assert client.post(f"/api/v1/captures/{cap_id}/index").status_code == 200
    r2 = client.post(f"/api/v1/captures/{cap_id}/index")
    assert r2.status_code == 409
    assert r2.json()["detail"]["error"] == "capture_indexed"


def test_index_cross_user_404(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_SECRET", "capture-route-test-secret-do-not-use")
    from auth import mint_access_token

    alice_h = {"Authorization": f"Bearer {mint_access_token('alice-ix', 'user')}"}
    bob_h = {"Authorization": f"Bearer {mint_access_token('bob-ix', 'user')}"}
    cap_id = client.post("/api/v1/captures", json={"text": "a"}, headers=alice_h).json()[
        "capture_id"
    ]
    r = client.post(f"/api/v1/captures/{cap_id}/index", headers=bob_h)
    assert r.status_code == 404


def test_index_malformed_capture_id_404(client: TestClient):
    assert client.post("/api/v1/captures/not-a-uuid/index").status_code == 404


def test_index_qdrant_failure_then_retry(
    client: TestClient,
    captures_ms: CapturesMemoryMetadataStore,
    mock_vector_store,
    monkeypatch: pytest.MonkeyPatch,
):
    attempts = {"n": 0}
    orig_upsert = mock_vector_store.upsert

    def flaky_upsert(**kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("qdrant down")
        return orig_upsert(**kwargs)

    monkeypatch.setattr(mock_vector_store, "upsert", flaky_upsert)

    cap_id = client.post("/api/v1/captures", json={"text": "ix"}).json()["capture_id"]
    r = client.post(f"/api/v1/captures/{cap_id}/index")
    assert r.status_code == 503
    assert captures_ms.captures[cap_id]["status"] == "failed"
    assert len(captures_ms.notes) == 0

    r2 = client.post(f"/api/v1/captures/{cap_id}/index")
    assert r2.status_code == 200
    assert r2.json()["status"] == "indexed"


def test_index_does_not_upsert_documents_collection(client: TestClient, mock_vector_store):
    cap_id = client.post("/api/v1/captures", json={"text": "d"}).json()["capture_id"]
    client.post(f"/api/v1/captures/{cap_id}/index")
    assert not mock_vector_store._collections.get("documents")


# ── Stub routes (501) ───────────────────────────────────────────────────


def test_legacy_post_upload_returns_501(client: TestClient):
    resp = client.post("/api/v1/captures/upload")
    _assert_501(resp)
