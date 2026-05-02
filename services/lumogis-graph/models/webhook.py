# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
# VENDORED FROM orchestrator/models/webhook.py — DO NOT EDIT BY HAND.
# Run `make sync-vendored` after changing the canonical Core copy.
"""Webhook + /context wire contracts shared by Core and the KG service.

This module is the canonical home of the contract. The KG service vendors
a byte-identical copy at `services/lumogis-graph/models/webhook.py` (one
extra SPDX header line) so the two processes cannot drift in dev. See the
`make sync-vendored` Make target for the copy step and the lumogis-graph
service extraction plan for the rationale.

Design notes:
    - The envelope's `payload` field is a raw `dict`, NOT a typed Union.
      Pydantic's discriminated-union story is heavy for a six-event closed
      set, and a dict + post-validation step (via `_PAYLOAD_BY_EVENT`) is
      both simpler and avoids the silent cross-coercion that a union would
      enable. The KG service's `routes/webhook.py` does this in two steps:
      (1) validate the envelope with `event` + raw `payload` as `dict`,
      (2) re-validate the payload against the class chosen via
      `_PAYLOAD_BY_EVENT[envelope.event]`.
    - `occurred_at` is an aware UTC datetime on the wire (ISO-8601 with a
      `Z` offset). Naive datetimes are rejected at validation time. This
      matches Postgres TIMESTAMPTZ semantics and prevents subtle
      timezone-drift bugs across the Core/KG boundary.
    - `schema_version` is `1` in this release. Bumping is a coordinated
      release: KG must accept the new version BEFORE Core starts emitting
      it, and old versions stay supported for at least one release cycle.
"""

from datetime import datetime
from datetime import timezone
from enum import Enum
from typing import Annotated

from pydantic import AfterValidator
from pydantic import BaseModel
from pydantic import Field


class WebhookEvent(str, Enum):
    """The closed set of events Core fires across the webhook to KG.

    Values are the canonical handler names (`on_*`) so that the KG side can
    use them directly to look up the matching `graph.writer.on_X` function
    without an extra mapping layer.
    """

    DOCUMENT_INGESTED = "on_document_ingested"
    ENTITY_CREATED = "on_entity_created"
    SESSION_ENDED = "on_session_ended"
    ENTITY_MERGED = "on_entity_merged"
    NOTE_CAPTURED = "on_note_captured"
    AUDIO_TRANSCRIBED = "on_audio_transcribed"


class DocumentIngestedPayload(BaseModel):
    file_path: str
    chunk_count: int
    user_id: str


class EntityCreatedPayload(BaseModel):
    entity_id: str
    name: str
    entity_type: str
    evidence_id: str
    evidence_type: str
    user_id: str
    is_staged: bool = False


class SessionEndedPayload(BaseModel):
    session_id: str
    summary: str
    topics: list[str]
    entities: list[str]
    entity_ids: list[str] | None = None
    user_id: str = "default"


class EntityMergedPayload(BaseModel):
    winner_id: str
    loser_id: str
    user_id: str


class NoteCapturedPayload(BaseModel):
    note_id: str
    user_id: str


class AudioTranscribedPayload(BaseModel):
    audio_id: str
    file_path: str
    duration_seconds: float = 0.0
    user_id: str


def _require_aware_utc(v: datetime) -> datetime:
    """Reject naive datetimes; normalise aware ones to UTC.

    Pydantic accepts both aware and naive ISO-8601 strings by default. We
    require the wire format to be aware (`Z` or `+00:00` offset) so that
    Postgres TIMESTAMPTZ comparisons across processes don't silently use
    the local server timezone.
    """
    if v.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware (UTC, ISO-8601 with Z offset)")
    return v.astimezone(timezone.utc)


AwareUTCDatetime = Annotated[datetime, AfterValidator(_require_aware_utc)]


class WebhookEnvelope(BaseModel):
    """The single envelope shape posted to KG `/webhook` for every event.

    `payload` is a raw dict. The KG side re-validates it against the class
    chosen by `_PAYLOAD_BY_EVENT[event]` after the envelope itself has
    parsed cleanly — see module docstring.
    """

    schema_version: int = 1
    event: WebhookEvent
    occurred_at: AwareUTCDatetime
    payload: dict


class ContextRequest(BaseModel):
    """Body for `POST /context` on the KG service (synchronous chat path)."""

    query: str
    user_id: str = "default"
    max_fragments: int = Field(default=3, ge=1, le=20)


class ContextResponse(BaseModel):
    """Body returned by `POST /context` on the KG service.

    `fragments` is a list of `[Graph]`-prefixed strings ready to be
    appended to the chat context budget, in the same order the in-process
    `on_context_building` hook would have appended them.
    """

    fragments: list[str]


_PAYLOAD_BY_EVENT: dict[WebhookEvent, type[BaseModel]] = {
    WebhookEvent.DOCUMENT_INGESTED: DocumentIngestedPayload,
    WebhookEvent.ENTITY_CREATED: EntityCreatedPayload,
    WebhookEvent.SESSION_ENDED: SessionEndedPayload,
    WebhookEvent.ENTITY_MERGED: EntityMergedPayload,
    WebhookEvent.NOTE_CAPTURED: NoteCapturedPayload,
    WebhookEvent.AUDIO_TRANSCRIBED: AudioTranscribedPayload,
}
"""Single source of truth for envelope.payload validation.

Adding a new webhook event requires three coordinated edits:
    1. New `*Payload(BaseModel)` class above.
    2. New entry in `WebhookEvent`.
    3. New entry in `_PAYLOAD_BY_EVENT`.
The contract test `test_payload_dispatch_covers_every_event` enforces
that every `WebhookEvent` member has a `_PAYLOAD_BY_EVENT` entry so the
implementer cannot land a partial update.
"""


SUPPORTED_SCHEMA_VERSIONS: list[int] = [1]
"""Schema versions the KG service accepts on `/webhook`.

When bumping the contract: append the new version here in the SAME PR
that teaches KG to handle the new payload shape; let that PR ride out at
least one release before any PR teaches Core to emit the new version.
This guarantees KG accepts the new envelope BEFORE Core can possibly
send it, eliminating the rolling-deploy race.
"""
