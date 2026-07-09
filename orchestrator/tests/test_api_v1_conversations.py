# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Tests for /api/v1/conversations (LUM-162)."""

from __future__ import annotations

import uuid
from datetime import datetime
from datetime import timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from models.memory import SessionSummary
from services.memory_purge import PurgeResult
from tests.sessions_memory_store import SessionsMemoryMetadataStore

import config


@pytest.fixture
def sessions_ms(monkeypatch: pytest.MonkeyPatch) -> SessionsMemoryMetadataStore:
    store = SessionsMemoryMetadataStore()
    config._instances["metadata_store"] = store
    return store


@pytest.fixture
def client(sessions_ms: SessionsMemoryMetadataStore):
    import main

    with TestClient(main.app) as c:
        yield c


def _insert_session(
    store: SessionsMemoryMetadataStore, *, user_id: str = "default", scope: str = "personal"
):
    sid = str(uuid.uuid4())
    store.sessions[sid] = {
        "session_id": uuid.UUID(sid),
        "summary": "First line title\nMore text",
        "topics": ["a"],
        "entities": [],
        "entity_ids": [],
        "user_id": user_id,
        "scope": scope,
        "updated_at": datetime.now(timezone.utc),
    }
    return sid


def test_list_returns_only_visible_sessions(client, sessions_ms):
    sid = _insert_session(sessions_ms)
    resp = client.get("/api/v1/conversations")
    assert resp.status_code == 200
    body = resp.json()
    assert any(c["conversation_id"] == sid for c in body["conversations"])


def test_get_detail_404_unknown_id(client):
    resp = client.get(f"/api/v1/conversations/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "conversation_not_found"


def test_delete_404_cross_user(client, sessions_ms):
    sid = _insert_session(sessions_ms, user_id="alice")
    resp = client.delete(f"/api/v1/conversations/{sid}")
    assert resp.status_code == 404


def test_delete_404_shared_scope_session(client, sessions_ms):
    sid = _insert_session(sessions_ms, scope="shared")
    with patch("services.conversations.purge_session_memory") as mock_purge:
        resp = client.delete(f"/api/v1/conversations/{sid}")
    assert resp.status_code == 404
    mock_purge.assert_not_called()


def test_delete_removes_published_from_projection_rows(client, sessions_ms, monkeypatch):
    sid = _insert_session(sessions_ms)
    proj = str(uuid.uuid4())
    sessions_ms.sessions[proj] = {
        "session_id": uuid.UUID(proj),
        "summary": "proj",
        "topics": [],
        "entities": [],
        "entity_ids": [],
        "user_id": "default",
        "scope": "shared",
        "published_from": uuid.UUID(sid),
        "updated_at": datetime.now(timezone.utc),
    }
    with patch(
        "services.conversations.purge_session_memory",
        return_value=PurgeResult(
            postgres_deleted=True,
            qdrant_deleted=True,
            graph_deleted=True,
            qdrant_entities_deleted=True,
        ),
    ):
        resp = client.delete(f"/api/v1/conversations/{sid}")
    assert resp.status_code == 200


def test_delete_removes_from_list_subsequent_get(client, sessions_ms, monkeypatch):
    sid = _insert_session(sessions_ms)
    with patch(
        "services.conversations.purge_session_memory",
        return_value=PurgeResult(
            postgres_deleted=True,
            qdrant_deleted=True,
            graph_deleted=True,
            qdrant_entities_deleted=True,
        ),
    ):
        client.delete(f"/api/v1/conversations/{sid}")
    sessions_ms.sessions.pop(sid, None)
    resp = client.get("/api/v1/conversations")
    assert all(c["conversation_id"] != sid for c in resp.json()["conversations"])


def test_continue_returns_seed_messages_slice1(client, sessions_ms):
    sid = _insert_session(sessions_ms)
    resp = client.post(f"/api/v1/conversations/{sid}/continue")
    assert resp.status_code == 200
    seeds = resp.json()["seed_messages"]
    assert len(seeds) == 1
    assert seeds[0]["role"] == "user"
    assert "Prior conversation context" in seeds[0]["content"]


def test_list_maps_updated_at_to_ended_at(client, sessions_ms):
    sid = _insert_session(sessions_ms)
    resp = client.get("/api/v1/conversations")
    row = next(c for c in resp.json()["conversations"] if c["conversation_id"] == sid)
    assert "ended_at" in row


def test_list_uses_household_union_and_isolates_personal(client, sessions_ms):
    """LUM-582 P0 — the sidebar now shows own-personal + household-shared, but a
    member's PERSONAL conversations are never exposed to another caller."""
    own_personal = _insert_session(sessions_ms, scope="personal")  # user "default"
    shared_from_other = _insert_session(sessions_ms, user_id="bob", scope="shared")
    other_personal = _insert_session(sessions_ms, user_id="bob", scope="personal")
    resp = client.get("/api/v1/conversations")
    by_id = {c["conversation_id"]: c for c in resp.json()["conversations"]}
    assert own_personal in by_id  # own personal
    assert shared_from_other in by_id  # household-shared (the P0)
    assert other_personal not in by_id  # another member's personal — never leaked
    # The shared row is correctly labelled (not mislabelled personal), and the
    # viewer is not its owner.
    assert by_id[shared_from_other]["share_status"] == "shared"
    assert by_id[shared_from_other]["is_owner"] is False
    assert by_id[own_personal]["share_status"] == "personal"


def test_delete_returns_partial_true_when_qdrant_fails_after_retries(
    client, sessions_ms, monkeypatch
):
    sid = _insert_session(sessions_ms)
    partial = PurgeResult(postgres_deleted=True, qdrant_deleted=False, graph_deleted=True)
    with patch("services.conversations.purge_session_memory", return_value=partial):
        resp = client.delete(f"/api/v1/conversations/{sid}")
    assert resp.status_code == 200
    assert resp.json()["partial"] is True


def test_delete_partial_purge_retry_succeeds(client, sessions_ms, monkeypatch):
    sid = _insert_session(sessions_ms)
    partial = PurgeResult(postgres_deleted=True, qdrant_deleted=False, graph_deleted=True)
    with patch("services.conversations.purge_session_memory", return_value=partial):
        first = client.delete(f"/api/v1/conversations/{sid}")
    assert first.status_code == 200
    assert first.json()["partial"] is True
    sessions_ms.sessions.pop(sid, None)
    sessions_ms.purged_conversations.add(("default", sid))
    ok = PurgeResult(
        postgres_deleted=True,
        qdrant_deleted=True,
        graph_deleted=True,
        qdrant_entities_deleted=True,
    )
    with patch("services.conversations.purge_session_memory", return_value=ok) as mock_retry:
        second = client.delete(f"/api/v1/conversations/{sid}")
    assert second.status_code == 200
    assert second.json()["partial"] is False
    assert mock_retry.called


def test_session_end_rejects_non_uuid_session_id(client):
    resp = client.post(
        "/session/end",
        json={"session_id": "not-a-uuid", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 422


def test_continue_seed_uses_user_role_not_system(client, sessions_ms):
    sid = _insert_session(sessions_ms)
    resp = client.post(f"/api/v1/conversations/{sid}/continue")
    assert resp.json()["seed_messages"][0]["role"] == "user"


def test_malformed_uuid_returns_422(client):
    resp = client.get("/api/v1/conversations/not-valid")
    assert resp.status_code == 422


def test_put_upserts_web_header_before_session_row(client, sessions_ms):
    """Client-minted thread ids must create web_conversations via PUT (LUM-162 slice 2)."""
    sid = str(uuid.uuid4())
    resp = client.put(
        f"/api/v1/conversations/{sid}",
        json={"title": "Active chat", "model": "test-model"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_id"] == sid
    assert body["title"] == "Active chat"
    key = f"{sid}:default"
    assert key in sessions_ms.web_conversations


def test_put_then_append_message_persists_transcript(client, sessions_ms):
    sid = str(uuid.uuid4())
    assert (
        client.put(
            f"/api/v1/conversations/{sid}",
            json={"title": "Chat", "model": "m"},
        ).status_code
        == 200
    )
    mid = str(uuid.uuid4())
    resp = client.post(
        f"/api/v1/conversations/{sid}/messages",
        json={
            "message_id": mid,
            "role": "user",
            "content": "hello world",
            "model": "m",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["content"] == "hello world"
    assert mid in sessions_ms.web_messages


def test_get_and_continue_web_only_transcript_before_session_end(client, sessions_ms):
    """Slice-2 sync must be readable before POST /session/end creates a sessions row."""
    sid = str(uuid.uuid4())
    assert (
        client.put(
            f"/api/v1/conversations/{sid}",
            json={"title": "Live chat", "model": "m"},
        ).status_code
        == 200
    )
    mid = str(uuid.uuid4())
    assert (
        client.post(
            f"/api/v1/conversations/{sid}/messages",
            json={
                "message_id": mid,
                "role": "user",
                "content": "persisted before end",
                "model": "m",
            },
        ).status_code
        == 201
    )

    detail = client.get(f"/api/v1/conversations/{sid}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["messages"][0]["content"] == "persisted before end"

    cont = client.post(f"/api/v1/conversations/{sid}/continue")
    assert cont.status_code == 200
    seeds = cont.json()["seed_messages"]
    assert seeds[0]["role"] == "user"
    assert seeds[0]["content"] == "persisted before end"


def test_continue_uses_verbatim_transcript_when_user_messages_present(client, sessions_ms):
    sid = _insert_session(sessions_ms)
    key = f"{sid}:default"
    sessions_ms.web_conversations[key] = {
        "conversation_id": uuid.UUID(sid),
        "user_id": "default",
        "title": "Chat",
        "model": "m",
        "message_count": 2,
        "updated_at": datetime.now(timezone.utc),
    }
    for role, content in (("user", "What did the roofer quote?"), ("assistant", "2400")):
        mid = str(uuid.uuid4())
        sessions_ms.web_messages[mid] = {
            "message_id": uuid.UUID(mid),
            "conversation_id": uuid.UUID(sid),
            "user_id": "default",
            "role": role,
            "content": content,
            "model": "m",
            "created_at": datetime.now(timezone.utc),
        }
    resp = client.post(f"/api/v1/conversations/{sid}/continue")
    assert resp.status_code == 200
    seeds = resp.json()["seed_messages"]
    assert len(seeds) == 2
    assert seeds[0]["role"] == "user"
    assert seeds[0]["content"] == "What did the roofer quote?"
    assert seeds[1]["role"] == "assistant"
    assert seeds[1]["content"] == "2400"


def test_continue_falls_back_to_summary_when_transcript_has_no_user_messages(client, sessions_ms):
    """Assistant-only slice-2 rows must not shadow the slice-1 summary on continue."""
    sid = _insert_session(sessions_ms)
    key = f"{sid}:default"
    sessions_ms.web_conversations[key] = {
        "conversation_id": uuid.UUID(sid),
        "user_id": "default",
        "title": "Chat",
        "model": "m",
        "message_count": 1,
        "updated_at": datetime.now(timezone.utc),
    }
    mid = str(uuid.uuid4())
    sessions_ms.web_messages[mid] = {
        "message_id": uuid.UUID(mid),
        "conversation_id": uuid.UUID(sid),
        "user_id": "default",
        "role": "assistant",
        "content": "partial assistant-only sync",
        "model": "m",
        "created_at": datetime.now(timezone.utc),
    }
    resp = client.post(f"/api/v1/conversations/{sid}/continue")
    assert resp.status_code == 200
    seeds = resp.json()["seed_messages"]
    assert len(seeds) == 1
    assert seeds[0]["role"] == "user"
    assert "Prior conversation context" in seeds[0]["content"]
    assert "partial assistant-only sync" not in seeds[0]["content"]


def test_put_cross_user_does_not_corrupt_existing_header(client, sessions_ms):
    sid = str(uuid.uuid4())
    sessions_ms.web_conversations[f"{sid}:alice"] = {
        "conversation_id": uuid.UUID(sid),
        "user_id": "alice",
        "title": "Alice chat",
        "model": "alice-model",
        "message_count": 0,
        "updated_at": datetime.now(timezone.utc),
    }
    resp = client.put(
        f"/api/v1/conversations/{sid}",
        json={"title": "Hijacked", "model": "evil"},
    )
    assert resp.status_code == 404
    assert sessions_ms.web_conversations[f"{sid}:alice"]["title"] == "Alice chat"


def test_append_message_id_conflict_other_user_returns_404(client, sessions_ms):
    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())
    mid = str(uuid.uuid4())
    sessions_ms.web_conversations[f"{sid_a}:alice"] = {
        "conversation_id": uuid.UUID(sid_a),
        "user_id": "alice",
        "title": "A",
        "model": "m",
        "message_count": 1,
        "updated_at": datetime.now(timezone.utc),
    }
    sessions_ms.web_messages[mid] = {
        "message_id": uuid.UUID(mid),
        "conversation_id": uuid.UUID(sid_a),
        "user_id": "alice",
        "role": "user",
        "content": "alice secret",
        "model": "m",
        "created_at": datetime.now(timezone.utc),
    }
    assert (
        client.put(
            f"/api/v1/conversations/{sid_b}",
            json={"title": "B", "model": "m"},
        ).status_code
        == 200
    )
    resp = client.post(
        f"/api/v1/conversations/{sid_b}/messages",
        json={
            "message_id": mid,
            "role": "user",
            "content": "bob attempt",
            "model": "m",
        },
    )
    assert resp.status_code == 404


def test_delete_web_only_conversation(client, sessions_ms, monkeypatch):
    sid = str(uuid.uuid4())
    assert (
        client.put(
            f"/api/v1/conversations/{sid}",
            json={"title": "Web only", "model": "m"},
        ).status_code
        == 200
    )
    with patch(
        "services.conversations.purge_session_memory",
        return_value=PurgeResult(
            postgres_deleted=True,
            qdrant_deleted=True,
            graph_deleted=True,
            qdrant_entities_deleted=True,
        ),
    ) as mock_purge:
        resp = client.delete(f"/api/v1/conversations/{sid}")
    assert resp.status_code == 200
    mock_purge.assert_called_once()


def test_put_and_append_blocked_after_purge_tombstone(client, sessions_ms):
    sid = str(uuid.uuid4())
    sessions_ms.purged_conversations.add(("default", sid))
    assert client.put(f"/api/v1/conversations/{sid}", json={"title": "x"}).status_code == 404
    mid = str(uuid.uuid4())
    assert (
        client.post(
            f"/api/v1/conversations/{sid}/messages",
            json={"message_id": mid, "role": "user", "content": "nope"},
        ).status_code
        == 404
    )


def test_session_end_to_list_e2e(client, sessions_ms, monkeypatch):
    sid = str(uuid.uuid4())
    summary = SessionSummary(
        session_id=sid,
        summary="Ended chat summary",
        topics=["topic"],
        entities=[],
    )
    with patch("routes.data.enqueue", return_value=1):
        end = client.post(
            "/session/end",
            json={
                "session_id": sid,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
    assert end.status_code == 200
    from services.memory import store_session

    store_session(summary, user_id="default")
    listed = client.get("/api/v1/conversations")
    assert any(c["conversation_id"] == sid for c in listed.json()["conversations"])


def _mint_conversation(client) -> str:
    sid = str(uuid.uuid4())
    assert (
        client.put(
            f"/api/v1/conversations/{sid}",
            json={"title": "Chat", "model": "m"},
        ).status_code
        == 200
    )
    return sid


def test_append_message_round_trips_source_refs(client, sessions_ms):
    sid = _mint_conversation(client)
    mid = str(uuid.uuid4())
    refs = [{"document_id": 1, "chunk_index": 0, "quote": "citation proof"}]
    resp = client.post(
        f"/api/v1/conversations/{sid}/messages",
        json={
            "message_id": mid,
            "role": "user",
            "content": "with refs",
            "model": "m",
            "source_refs": refs,
        },
    )
    assert resp.status_code == 201
    detail = client.get(f"/api/v1/conversations/{sid}")
    assert detail.status_code == 200
    msg = detail.json()["messages"][0]
    assert msg["source_refs"] == refs


def test_append_message_round_trips_action_proposal_id(client, sessions_ms):
    sid = _mint_conversation(client)
    mid = str(uuid.uuid4())
    sessions_ms.action_proposals[42] = {
        "id": 42,
        "user_id": "default",
        "action_name": "test_action",
        "payload": {},
        "status": "pending",
    }
    resp = client.post(
        f"/api/v1/conversations/{sid}/messages",
        json={
            "message_id": mid,
            "role": "user",
            "content": "linked",
            "model": "m",
            "action_proposal_id": 42,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["action_proposal_id"] == 42
    detail = client.get(f"/api/v1/conversations/{sid}")
    assert detail.json()["messages"][0]["action_proposal_id"] == 42


def test_append_message_action_proposal_other_user_returns_404(client, sessions_ms):
    sid = _mint_conversation(client)
    sessions_ms.action_proposals[7] = {
        "id": 7,
        "user_id": "alice",
        "action_name": "alice_action",
        "payload": {},
        "status": "pending",
    }
    resp = client.post(
        f"/api/v1/conversations/{sid}/messages",
        json={
            "message_id": str(uuid.uuid4()),
            "role": "user",
            "content": "cross-user proposal",
            "action_proposal_id": 7,
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "conversation_not_found"


def test_append_message_action_proposal_unknown_id_returns_422(client, sessions_ms):
    sid = _mint_conversation(client)
    resp = client.post(
        f"/api/v1/conversations/{sid}/messages",
        json={
            "message_id": str(uuid.uuid4()),
            "role": "user",
            "content": "unknown proposal",
            "action_proposal_id": 999_999,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_action_proposal"


def test_append_message_omits_new_fields_by_default(client, sessions_ms):
    sid = _mint_conversation(client)
    mid = str(uuid.uuid4())
    resp = client.post(
        f"/api/v1/conversations/{sid}/messages",
        json={
            "message_id": mid,
            "role": "user",
            "content": "plain",
            "model": "m",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body.get("source_refs") in (None, [])
    assert body.get("action_proposal_id") is None
    detail = client.get(f"/api/v1/conversations/{sid}")
    msg = detail.json()["messages"][0]
    assert msg.get("source_refs") in (None, [])
    assert msg.get("action_proposal_id") is None


def test_append_message_idempotent_conflict_preserves_first_write(client, sessions_ms):
    sid = _mint_conversation(client)
    mid = str(uuid.uuid4())
    sessions_ms.action_proposals[1] = {
        "id": 1,
        "user_id": "default",
        "action_name": "first",
        "payload": {},
        "status": "pending",
    }
    sessions_ms.action_proposals[2] = {
        "id": 2,
        "user_id": "default",
        "action_name": "second",
        "payload": {},
        "status": "pending",
    }
    first_refs = [{"document_id": 1, "quote": "first"}]
    second_refs = [{"document_id": 2, "quote": "second"}]
    assert (
        client.post(
            f"/api/v1/conversations/{sid}/messages",
            json={
                "message_id": mid,
                "role": "user",
                "content": "first write",
                "source_refs": first_refs,
                "action_proposal_id": 1,
            },
        ).status_code
        == 201
    )
    resp = client.post(
        f"/api/v1/conversations/{sid}/messages",
        json={
            "message_id": mid,
            "role": "user",
            "content": "second write",
            "source_refs": second_refs,
            "action_proposal_id": 2,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["content"] == "first write"
    assert body["source_refs"] == first_refs
    assert body["action_proposal_id"] == 1
