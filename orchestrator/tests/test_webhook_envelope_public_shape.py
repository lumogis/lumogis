# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Schema smoke for KG webhook contract shapes (dispatcher / premium strip safe)."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone

from models.webhook import WebhookEnvelope
from models.webhook import WebhookEvent


def test_webhook_envelope_minimal_roundtrip():
    occurred = datetime(2026, 1, 15, tzinfo=timezone.utc)
    payload = {"document_id": "doc-123", "checksum": None}
    env = WebhookEnvelope(
        schema_version=1,
        event=WebhookEvent.DOCUMENT_INGESTED,
        occurred_at=occurred,
        payload=payload,
    )
    dumped = env.model_dump(mode="json")
    parsed = WebhookEnvelope.model_validate(dumped)
    assert parsed.event == WebhookEvent.DOCUMENT_INGESTED
    assert parsed.payload == payload
