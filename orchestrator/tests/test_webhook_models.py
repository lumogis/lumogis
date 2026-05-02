# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Contract tests for `models/webhook.py`.

These tests pin the wire format every Core/KG webhook crosses. They run
in Core's pytest suite so a contract-breaking change shows up before the
KG vendored copy is even synced.
"""

from datetime import datetime
from datetime import timezone

import pytest
from models.webhook import _PAYLOAD_BY_EVENT
from models.webhook import SUPPORTED_SCHEMA_VERSIONS
from models.webhook import AudioTranscribedPayload
from models.webhook import ContextRequest
from models.webhook import ContextResponse
from models.webhook import DocumentIngestedPayload
from models.webhook import EntityCreatedPayload
from models.webhook import EntityMergedPayload
from models.webhook import NoteCapturedPayload
from models.webhook import SessionEndedPayload
from models.webhook import WebhookEnvelope
from models.webhook import WebhookEvent
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Per-payload round-trip
# ---------------------------------------------------------------------------


def test_document_ingested_roundtrip():
    p = DocumentIngestedPayload(file_path="/data/x.md", chunk_count=5, user_id="u1")
    restored = DocumentIngestedPayload.model_validate_json(p.model_dump_json())
    assert restored == p


def test_entity_created_defaults_is_staged_false():
    p = EntityCreatedPayload(
        entity_id="e1",
        name="Ada Lovelace",
        entity_type="PERSON",
        evidence_id="doc1",
        evidence_type="document",
        user_id="u1",
    )
    assert p.is_staged is False
    restored = EntityCreatedPayload.model_validate_json(p.model_dump_json())
    assert restored == p


def test_session_ended_entity_ids_optional():
    p = SessionEndedPayload(session_id="s1", summary="hello", topics=["t1"], entities=["e1"])
    assert p.entity_ids is None
    assert p.user_id == "default"
    restored = SessionEndedPayload.model_validate_json(p.model_dump_json())
    assert restored == p


def test_entity_merged_required_fields():
    p = EntityMergedPayload(winner_id="w", loser_id="l", user_id="u1")
    restored = EntityMergedPayload.model_validate_json(p.model_dump_json())
    assert restored == p


def test_note_captured_minimal():
    p = NoteCapturedPayload(note_id="n1", user_id="u1")
    restored = NoteCapturedPayload.model_validate_json(p.model_dump_json())
    assert restored == p


def test_audio_transcribed_default_duration():
    p = AudioTranscribedPayload(audio_id="a1", file_path="/x.wav", user_id="u1")
    assert p.duration_seconds == 0.0
    restored = AudioTranscribedPayload.model_validate_json(p.model_dump_json())
    assert restored == p


# ---------------------------------------------------------------------------
# Envelope validation
# ---------------------------------------------------------------------------


def _aware_now() -> datetime:
    return datetime.now(timezone.utc)


def test_envelope_roundtrip_default_schema_version_is_1():
    env = WebhookEnvelope(
        event=WebhookEvent.DOCUMENT_INGESTED,
        occurred_at=_aware_now(),
        payload={"file_path": "/x.md", "chunk_count": 1, "user_id": "u1"},
    )
    assert env.schema_version == 1
    restored = WebhookEnvelope.model_validate_json(env.model_dump_json())
    assert restored == env


def test_envelope_rejects_naive_occurred_at():
    """Naive datetimes are rejected at the contract boundary so Core/KG
    can never silently disagree on what timezone a timestamp is in."""
    naive = datetime(2026, 4, 17, 12, 0, 0)
    with pytest.raises(ValidationError) as ei:
        WebhookEnvelope(
            event=WebhookEvent.DOCUMENT_INGESTED,
            occurred_at=naive,
            payload={"file_path": "/x.md", "chunk_count": 1, "user_id": "u1"},
        )
    assert "timezone-aware" in str(ei.value)


def test_envelope_normalises_non_utc_to_utc():
    """An aware datetime in a non-UTC zone is accepted and normalised to
    UTC by `_require_aware_utc`. Wire JSON serialises with the UTC offset."""
    from datetime import timedelta
    from datetime import timezone as tz

    plus_two = tz(timedelta(hours=2))
    when = datetime(2026, 4, 17, 14, 0, 0, tzinfo=plus_two)
    env = WebhookEnvelope(
        event=WebhookEvent.DOCUMENT_INGESTED,
        occurred_at=when,
        payload={"file_path": "/x.md", "chunk_count": 1, "user_id": "u1"},
    )
    assert env.occurred_at.utcoffset() == timezone.utc.utcoffset(env.occurred_at)
    assert env.occurred_at.hour == 12  # 14:00 +02:00 -> 12:00 UTC


def test_envelope_rejects_unknown_event_value():
    with pytest.raises(ValidationError):
        WebhookEnvelope.model_validate(
            {
                "event": "on_made_up",
                "occurred_at": _aware_now().isoformat(),
                "payload": {},
            }
        )


def test_envelope_payload_is_raw_dict_not_typed():
    """`payload` is intentionally `dict` — KG re-validates it via
    `_PAYLOAD_BY_EVENT` after envelope parsing succeeds. Sending a payload
    whose shape doesn't match the event MUST NOT raise here."""
    env = WebhookEnvelope(
        event=WebhookEvent.ENTITY_CREATED,
        occurred_at=_aware_now(),
        payload={"this": "is the wrong shape", "but": "envelope still parses"},
    )
    assert env.payload == {"this": "is the wrong shape", "but": "envelope still parses"}


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------


def test_payload_dispatch_covers_every_event():
    """Every WebhookEvent member MUST have an entry in _PAYLOAD_BY_EVENT.

    This is the canonical check: adding a new event without registering a
    payload class would cause silent runtime failures in KG's
    `routes/webhook.py` second-pass validation.
    """
    assert set(_PAYLOAD_BY_EVENT.keys()) == set(WebhookEvent)


def test_payload_dispatch_classes_validate_their_payloads():
    """For every event, the dispatch class accepts a sample payload and
    rejects an obviously bad one (missing required field). This locks the
    "two-pass validation" contract KG depends on."""
    samples: dict[WebhookEvent, dict] = {
        WebhookEvent.DOCUMENT_INGESTED: {"file_path": "/x.md", "chunk_count": 1, "user_id": "u1"},
        WebhookEvent.ENTITY_CREATED: {
            "entity_id": "e",
            "name": "n",
            "entity_type": "PERSON",
            "evidence_id": "ev",
            "evidence_type": "document",
            "user_id": "u1",
        },
        WebhookEvent.SESSION_ENDED: {
            "session_id": "s",
            "summary": "sum",
            "topics": [],
            "entities": [],
        },
        WebhookEvent.ENTITY_MERGED: {"winner_id": "w", "loser_id": "l", "user_id": "u1"},
        WebhookEvent.NOTE_CAPTURED: {"note_id": "n", "user_id": "u1"},
        WebhookEvent.AUDIO_TRANSCRIBED: {"audio_id": "a", "file_path": "/x.wav", "user_id": "u1"},
    }
    for event, model_cls in _PAYLOAD_BY_EVENT.items():
        good = model_cls.model_validate(samples[event])
        assert good is not None
        with pytest.raises(ValidationError):
            model_cls.model_validate({"unrelated": "garbage"})


def test_supported_schema_versions_includes_one():
    assert 1 in SUPPORTED_SCHEMA_VERSIONS


# ---------------------------------------------------------------------------
# Context request/response
# ---------------------------------------------------------------------------


def test_context_request_defaults():
    req = ContextRequest(query="who is ada lovelace?")
    assert req.user_id == "default"
    assert req.max_fragments == 3


@pytest.mark.parametrize("bad", [0, -1, 21, 100])
def test_context_request_rejects_max_fragments_out_of_bounds(bad: int):
    with pytest.raises(ValidationError):
        ContextRequest(query="x", max_fragments=bad)


def test_context_response_roundtrip():
    resp = ContextResponse(fragments=["[Graph] ada is a mathematician"])
    restored = ContextResponse.model_validate_json(resp.model_dump_json())
    assert restored == resp
