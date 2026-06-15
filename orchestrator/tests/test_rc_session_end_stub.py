# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""RC session-end stub for slim web-e2e stacks without Ollama (LUM-414, ADR-064)."""

from __future__ import annotations

import uuid

import pytest
from models.sessions import SessionEndPayload
from services.batch_handlers.session_end import handle as session_end_handle
from services.conversations import list_conversations
from tests.sessions_memory_store import SessionsMemoryMetadataStore

import config


@pytest.fixture
def sessions_ms(monkeypatch: pytest.MonkeyPatch) -> SessionsMemoryMetadataStore:
    store = SessionsMemoryMetadataStore()
    config._instances["metadata_store"] = store
    monkeypatch.setenv("LUMOGIS_RC_SESSION_END_STUB", "true")
    return store


def test_session_end_handler_lists_without_ollama(
    sessions_ms: SessionsMemoryMetadataStore,
    mock_vector_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sid = str(uuid.uuid4())
    payload = SessionEndPayload(
        session_id=sid,
        messages=[
            {"role": "user", "content": "Remind me what the roofer quoted."},
            {"role": "assistant", "content": "2,400 for the garage roof last spring."},
        ],
    )

    session_end_handle(user_id="alice", payload=payload)

    listed = list_conversations("alice")
    assert any(c.conversation_id == sid for c in listed)
    assert mock_vector_store._collections.get("conversations", []) == []
