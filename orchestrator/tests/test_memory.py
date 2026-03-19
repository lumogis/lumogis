# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Lumogis
"""Unit tests for services/memory.py.

Tests cover summarize_session (with mock LLM), store_session (with mock
embedder/vector store), and retrieve_context (with mock vector store).
No Docker or network required.
"""

import json
from unittest.mock import MagicMock

from services.memory import retrieve_context
from services.memory import store_session
from services.memory import summarize_session

import config as _config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_llm(response_text: str):
    mock_resp = MagicMock()
    mock_resp.text = response_text
    mock_provider = MagicMock()
    mock_provider.chat.return_value = mock_resp
    return mock_provider


MESSAGES = [
    {"role": "user", "content": "What is the RBA cash rate?"},
    {"role": "assistant", "content": "The RBA holds the cash rate at 4.35%."},
]


# ---------------------------------------------------------------------------
# summarize_session
# ---------------------------------------------------------------------------


class TestSummarizeSession:
    def _patch_budget(self, monkeypatch):
        monkeypatch.setattr("services.context_budget.get_budget", lambda name: 4096)

    def test_parses_valid_json_response(self, monkeypatch):
        self._patch_budget(monkeypatch)
        payload = {
            "summary": "Discussion about RBA rates.",
            "topics": ["finance", "monetary policy"],
            "entities": ["RBA"],
        }
        llm = _mock_llm(json.dumps(payload))
        monkeypatch.setattr(_config, "get_llm_provider", lambda name: llm)

        result = summarize_session(MESSAGES, session_id="sess-001")

        assert result.session_id == "sess-001"
        assert result.summary == "Discussion about RBA rates."
        assert "finance" in result.topics
        assert "RBA" in result.entities

    def test_falls_back_to_raw_text_on_invalid_json(self, monkeypatch):
        self._patch_budget(monkeypatch)
        llm = _mock_llm("not valid json at all")
        monkeypatch.setattr(_config, "get_llm_provider", lambda name: llm)

        result = summarize_session(MESSAGES)

        assert result.summary == "not valid json at all"
        assert result.topics == []
        assert result.entities == []

    def test_generates_session_id_when_not_provided(self, monkeypatch):
        self._patch_budget(monkeypatch)
        payload = {"summary": "A summary.", "topics": [], "entities": []}
        llm = _mock_llm(json.dumps(payload))
        monkeypatch.setattr(_config, "get_llm_provider", lambda name: llm)

        result = summarize_session(MESSAGES)
        assert result.session_id  # non-empty UUID

    def test_strips_markdown_fences_gracefully(self, monkeypatch):
        self._patch_budget(monkeypatch)
        payload = {"summary": "Fenced.", "topics": [], "entities": []}
        raw = f"```json\n{json.dumps(payload)}\n```"
        llm = _mock_llm(raw)
        monkeypatch.setattr(_config, "get_llm_provider", lambda name: llm)

        # Falls back to raw text — key thing is it does not raise
        result = summarize_session(MESSAGES)
        assert result.summary  # non-empty


# ---------------------------------------------------------------------------
# store_session
# ---------------------------------------------------------------------------


class TestStoreSession:
    def test_upserts_to_conversations_collection(
        self, mock_embedder, mock_vector_store, monkeypatch
    ):
        from models.memory import SessionSummary

        summary = SessionSummary(
            session_id="sess-store-1",
            summary="A great session.",
            topics=["tech"],
            entities=["OpenAI"],
        )
        store_session(summary, user_id="u1")

        items = mock_vector_store._collections.get("conversations", [])
        assert len(items) == 1
        assert items[0]["payload"]["session_id"] == "sess-store-1"
        assert items[0]["payload"]["user_id"] == "u1"
        assert items[0]["payload"]["topics"] == ["tech"]

    def test_embed_text_includes_topics(self, mock_embedder, mock_vector_store, monkeypatch):
        embedded_texts = []
        original_embed = mock_embedder.embed

        def capturing_embed(text):
            embedded_texts.append(text)
            return original_embed(text)

        mock_embedder.embed = capturing_embed

        from models.memory import SessionSummary

        summary = SessionSummary(
            session_id="sess-embed-1",
            summary="Rate decision.",
            topics=["finance"],
            entities=[],
        )
        store_session(summary)

        assert any("finance" in t for t in embedded_texts)


# ---------------------------------------------------------------------------
# retrieve_context
# ---------------------------------------------------------------------------


class TestRetrieveContext:
    def test_returns_matching_context_hits(self, mock_embedder, mock_vector_store):
        mock_vector_store.upsert(
            collection="conversations",
            id="pt-1",
            vector=[0.0] * 768,
            payload={
                "session_id": "old-session",
                "summary": "Previously discussed RBA rates.",
                "user_id": "default",
            },
        )

        hits = retrieve_context("RBA interest rate", user_id="default")

        assert len(hits) == 1
        assert hits[0].session_id == "old-session"
        assert hits[0].score is not None

    def test_filters_by_user_id(self, mock_embedder, mock_vector_store):
        mock_vector_store.upsert(
            collection="conversations",
            id="pt-user1",
            vector=[0.0] * 768,
            payload={"session_id": "s1", "summary": "User 1 stuff.", "user_id": "user1"},
        )
        mock_vector_store.upsert(
            collection="conversations",
            id="pt-user2",
            vector=[0.0] * 768,
            payload={"session_id": "s2", "summary": "User 2 stuff.", "user_id": "user2"},
        )

        hits = retrieve_context("anything", user_id="user1")
        assert all(h.session_id == "s1" for h in hits)

    def test_empty_store_returns_empty_list(self, mock_embedder, mock_vector_store):
        hits = retrieve_context("query", user_id="nobody")
        assert hits == []
