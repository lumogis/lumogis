# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Chat route auto-RAG injection (LUM-308) — FastAPI TestClient."""

from __future__ import annotations

from unittest.mock import patch

import main
import pytest
from fastapi.testclient import TestClient
from models.memory import DocumentContextHit
from services.injection_sanitiser import sanitize_attribute_source_token


@pytest.fixture
def alice():
    from auth import UserContext

    return UserContext(user_id="alice", role="user", is_authenticated=True)


def test_chat_injects_document_context_when_enabled(monkeypatch, alice) -> None:
    import config as cfg

    hit = DocumentContextHit(
        point_id="pt-1",
        file_path="/docs/note.md",
        chunk_text="SYNTHETIC_CHUNK_TEXT",
        score=0.9,
        score_kind="rrf_gated",
        scope="personal",
    )

    monkeypatch.setattr(cfg, "get_auto_rag_enabled", lambda: True)
    monkeypatch.setattr(
        "services.auto_rag.retrieve_document_context",
        lambda *a, **k: [hit],
    )
    monkeypatch.setattr("services.memory.retrieve_context", lambda *a, **k: [])
    monkeypatch.setattr(cfg, "get_graph_mode", lambda: "disabled")
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    cfg.clear_graph_mode_env_cache()

    monkeypatch.setattr(cfg, "is_model_enabled", lambda model, user_id=None, **_kw: True)
    monkeypatch.setattr(
        cfg,
        "get_model_config",
        lambda _m: {"tools": False},
    )

    with patch("routes.chat.get_user", return_value=alice):
        with patch("routes.chat.ask", return_value="ok") as mock_ask:
            with TestClient(main.app) as client:
                resp = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "claude",
                        "stream": False,
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                )
    assert resp.status_code == 200
    call_hist = mock_ask.call_args.kwargs.get("history") or []
    assert call_hist, "ask should receive injected context messages"
    joined = "\n".join(str(m.get("content", "")) for m in call_hist)

    if cfg.is_injection_sanitiser_enabled():
        expected_sub = sanitize_attribute_source_token("document:/docs/note.md")
        assert joined.count(expected_sub) == 1
    else:
        assert "SYNTHETIC_CHUNK_TEXT" in joined
        assert "Retrieved excerpts for grounding:" in joined


def test_chat_streaming_search_files_dedupe(monkeypatch, alice) -> None:
    """Streaming path must thread auto_rag_point_ids into the tool loop."""
    from models.stream import StreamEvent

    import config as cfg

    hit = DocumentContextHit(
        point_id="dup-id",
        file_path="/f.md",
        chunk_text="AUTO_BODY",
        score=0.9,
        score_kind="rrf_gated",
        scope="personal",
    )

    monkeypatch.setattr(cfg, "get_auto_rag_enabled", lambda: True)
    monkeypatch.setattr(
        "services.auto_rag.retrieve_document_context",
        lambda *a, **k: [hit],
    )
    monkeypatch.setattr("services.memory.retrieve_context", lambda *a, **k: [])
    monkeypatch.setattr(cfg, "get_graph_mode", lambda: "disabled")
    monkeypatch.setenv("GRAPH_MODE", "disabled")
    cfg.clear_graph_mode_env_cache()

    monkeypatch.setattr(cfg, "is_model_enabled", lambda model, user_id=None, **_kw: True)
    monkeypatch.setattr(
        cfg,
        "get_model_config",
        lambda _m: {"tools": True},
    )
    monkeypatch.setattr(cfg, "get_llm_provider", lambda *a, **k: object())

    captured: dict = {}

    def fake_ask_stream(*_a, **kwargs):
        s = kwargs.get("auto_rag_point_ids")
        captured["auto_rag_snapshot"] = set(s) if s is not None else set()
        yield StreamEvent(type="text", content="x")

    with patch("routes.chat.get_user", return_value=alice):
        with patch("routes.chat.ask_stream", side_effect=fake_ask_stream):
            with TestClient(main.app) as client:
                resp = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "claude",
                        "stream": True,
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                )
    assert resp.status_code == 200
    snap = captured.get("auto_rag_snapshot")
    assert isinstance(snap, set)
    assert "dup-id" in snap
