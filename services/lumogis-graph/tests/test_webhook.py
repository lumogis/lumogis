# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Unit tests for `routes/webhook.py`.

Contract under test (see plan §"Test cases" / "Unit tests in the KG service"):

  * Auth matrix (the four-cell matrix in `check_webhook_auth`):
      secret-set + bearer-correct           → 202
      secret-set + bearer-missing-or-wrong  → 401
      secret-unset + insecure-toggle-false  → 503  (default; safe-by-default)
      secret-unset + insecure-toggle-true   → 202  (explicit dev opt-in)

  * Envelope validation:
      valid envelope + valid payload (per event)  → 202 + writer enqueued
      invalid envelope (missing fields)            → 422
      unknown event                                → 422
      payload missing required field               → 422
      schema_version not in SUPPORTED_SCHEMA_VERSIONS
        → 422 + body {detail, received, supported}

  * Per-event dispatch:
      `webhook_queue.submit` is called with `graph.writer.on_<event_name>`
      and the deserialised payload kwargs.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client_with_webhook() -> TestClient:
    """Bare app with only the webhook router."""
    from routes.webhook import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _envelope(event: str, payload: dict, schema_version: int = 1) -> dict:
    return {
        "schema_version": schema_version,
        "event": event,
        "occurred_at": "2026-04-17T12:00:00+00:00",
        "payload": payload,
    }


_VALID_PAYLOADS: dict[str, dict] = {
    "on_document_ingested": {
        "file_path": "/data/foo.md",
        "chunk_count": 3,
        "user_id": "default",
    },
    "on_entity_created": {
        "entity_id": "ent-1",
        "name": "Ada Lovelace",
        "entity_type": "person",
        "evidence_id": "doc-1",
        "evidence_type": "document",
        "user_id": "default",
        "is_staged": False,
    },
    "on_session_ended": {
        "session_id": "s-1",
        "summary": "discussed graphs",
        "topics": ["graphs"],
        "entities": ["Ada Lovelace"],
        "entity_ids": ["ent-1"],
        "user_id": "default",
    },
    "on_entity_merged": {
        "winner_id": "ent-1",
        "loser_id": "ent-2",
        "user_id": "default",
    },
    "on_note_captured": {
        "note_id": "note-1",
        "user_id": "default",
    },
    "on_audio_transcribed": {
        "audio_id": "aud-1",
        "file_path": "/data/aud.wav",
        "duration_seconds": 12.5,
        "user_id": "default",
    },
}


@pytest.fixture
def captured_submits(monkeypatch):
    """Replace `webhook_queue.submit` with a recorder; return the list of calls."""
    captured: list[tuple[Any, dict]] = []

    def _fake_submit(fn, /, **kwargs):
        captured.append((fn, kwargs))
        return None

    import webhook_queue

    monkeypatch.setattr(webhook_queue, "submit", _fake_submit)
    return captured


# ---------------------------------------------------------------------------
# Auth matrix
# ---------------------------------------------------------------------------


def test_webhook_returns_503_when_secret_unset_and_insecure_false(monkeypatch, captured_submits):
    """Default safe-by-default contract: refuse traffic until configured."""
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "false")

    r = _client_with_webhook().post(
        "/webhook",
        json=_envelope("on_note_captured", _VALID_PAYLOADS["on_note_captured"]),
    )
    assert r.status_code == 503
    assert "webhook auth not configured" in r.json()["detail"]
    assert captured_submits == [], "no work should be enqueued when auth is misconfigured"


def test_webhook_returns_202_when_secret_unset_and_insecure_true(monkeypatch, captured_submits):
    """Explicit dev opt-in: KG_ALLOW_INSECURE_WEBHOOKS=true bypasses bearer check."""
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "true")

    r = _client_with_webhook().post(
        "/webhook",
        json=_envelope("on_note_captured", _VALID_PAYLOADS["on_note_captured"]),
    )
    assert r.status_code == 202
    assert len(captured_submits) == 1


def test_webhook_returns_401_when_bearer_missing_and_secret_set(monkeypatch, captured_submits):
    monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "false")

    r = _client_with_webhook().post(
        "/webhook",
        json=_envelope("on_note_captured", _VALID_PAYLOADS["on_note_captured"]),
    )
    assert r.status_code == 401
    assert captured_submits == []


def test_webhook_returns_401_when_bearer_wrong(monkeypatch, captured_submits):
    monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "false")

    r = _client_with_webhook().post(
        "/webhook",
        headers={"Authorization": "Bearer wrong-token"},
        json=_envelope("on_note_captured", _VALID_PAYLOADS["on_note_captured"]),
    )
    assert r.status_code == 401
    assert captured_submits == []


def test_webhook_returns_202_when_bearer_correct(monkeypatch, captured_submits):
    monkeypatch.setenv("GRAPH_WEBHOOK_SECRET", "s3cret")
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "false")

    r = _client_with_webhook().post(
        "/webhook",
        headers={"Authorization": "Bearer s3cret"},
        json=_envelope("on_note_captured", _VALID_PAYLOADS["on_note_captured"]),
    )
    assert r.status_code == 202
    assert len(captured_submits) == 1


# ---------------------------------------------------------------------------
# Envelope validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event,payload", list(_VALID_PAYLOADS.items()))
def test_webhook_returns_202_on_valid_envelope_per_event(monkeypatch, captured_submits, event, payload):
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "true")

    r = _client_with_webhook().post("/webhook", json=_envelope(event, payload))
    assert r.status_code == 202, r.json()
    assert r.json() == {"status": "accepted"}


@pytest.mark.parametrize("event,payload", list(_VALID_PAYLOADS.items()))
def test_webhook_enqueues_correct_writer_handler_per_event(monkeypatch, captured_submits, event, payload):
    """The handler enqueued for each event MUST be `graph.writer.on_<event>`."""
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "true")

    _client_with_webhook().post("/webhook", json=_envelope(event, payload))

    assert len(captured_submits) == 1, captured_submits
    fn, kwargs = captured_submits[0]
    assert fn.__name__ == event
    for k, v in payload.items():
        assert kwargs.get(k) == v, f"payload field {k!r} not propagated to handler kwargs"


def test_webhook_returns_422_on_unknown_event(monkeypatch, captured_submits):
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "true")

    r = _client_with_webhook().post(
        "/webhook",
        json=_envelope("on_does_not_exist", {"user_id": "default"}),
    )
    assert r.status_code == 422
    assert captured_submits == []


def test_webhook_returns_422_on_missing_payload_field(monkeypatch, captured_submits):
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "true")

    r = _client_with_webhook().post(
        "/webhook",
        json=_envelope("on_note_captured", {"note_id": "x"}),  # missing user_id
    )
    assert r.status_code == 422
    assert captured_submits == []


def test_webhook_returns_422_on_unsupported_schema_version(monkeypatch, captured_submits):
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "true")

    r = _client_with_webhook().post(
        "/webhook",
        json=_envelope(
            "on_note_captured",
            _VALID_PAYLOADS["on_note_captured"],
            schema_version=99,
        ),
    )
    assert r.status_code == 422
    body = r.json()
    assert body["detail"] == "unsupported schema_version"
    assert body["received"] == 99
    assert body["supported"] == [1]
    assert captured_submits == []


def test_webhook_returns_422_on_malformed_envelope(monkeypatch, captured_submits):
    monkeypatch.delenv("GRAPH_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("KG_ALLOW_INSECURE_WEBHOOKS", "true")

    r = _client_with_webhook().post("/webhook", json={"foo": "bar"})
    assert r.status_code == 422
    assert captured_submits == []
