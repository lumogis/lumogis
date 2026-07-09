# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Route + tethered integration tests for egress guard (LUM-553)."""

from __future__ import annotations

import socket
from unittest.mock import patch

import main
import pytest
from auth import UserContext
from fastapi.testclient import TestClient
from services import egress_guard as eg
from services.egress_guard import EgressBlockedError

_ALICE = UserContext(user_id="alice", role="user", is_authenticated=True)


@pytest.fixture
def chat_client():
    with patch("routes.chat.get_user", return_value=_ALICE):
        with TestClient(main.app) as c:
            yield c


@pytest.fixture
def api_v1_client():
    with patch("routes.api_v1.chat.get_user", return_value=_ALICE):
        with TestClient(main.app) as c:
            yield c


def _passthrough_history(q, h, m, u, **kw):
    from models.context_injection import ContextInjectionResult

    return ContextInjectionResult(messages=h)


@patch("routes.chat.build_injected_context", side_effect=_passthrough_history)
@patch("services.privacy_mode.resolve_model_for_request", return_value=("chatgpt", None))
@patch(
    "routes.chat.config.get_model_config",
    return_value={"tools": False, "api_key_env": "OPENAI_API_KEY"},
)
@patch("routes.chat.config.is_model_enabled", return_value=True)
@patch("routes.chat.ask", side_effect=EgressBlockedError(host="evil.example"))
def test_chat_egress_blocked_returns_503_non_stream(
    _ask, _enabled, _cfg, _resolve, _ctx, chat_client, monkeypatch
):
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")
    resp = chat_client.post(
        "/v1/chat/completions",
        json={"model": "chatgpt", "stream": False, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "egress_blocked"
    assert body["error"]["model"] == "chatgpt"
    assert body["error"]["type"] == "server_error"


@patch("services.privacy_mode.resolve_model_for_request", return_value=("chatgpt", None))
@patch("routes.api_v1.chat._resolve_scoped_injection")
@patch("routes.api_v1.chat.config.is_model_enabled", return_value=True)
@patch("routes.api_v1.chat.ask", side_effect=EgressBlockedError(host="evil.example"))
def test_api_v1_egress_blocked_returns_503_non_stream(
    _ask, _enabled, _resolve_inj, _resolve_model, api_v1_client, monkeypatch
):
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")
    _resolve_inj.return_value = ("hi", [], set(), [], False)
    resp = api_v1_client.post(
        "/api/v1/chat/completions",
        json={"model": "chatgpt", "stream": False, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 503
    body = resp.json()
    assert body["detail"]["error"] == "egress_blocked"
    assert body["detail"]["model"] == "chatgpt"


def test_stream_egress_blocked_yields_sse_error(monkeypatch, chat_client):
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")

    def _fake_stream(*_a, **_kw):
        from loop import StreamEvent

        yield StreamEvent(
            type="error",
            content="Connection blocked by egress guard. Check allowlist configuration.",
        )

    with patch("services.privacy_mode.resolve_model_for_request", return_value=("llama", None)):
        with patch("routes.chat.build_injected_context", side_effect=_passthrough_history):
            with patch("routes.chat.config.get_model_config", return_value={"tools": False}):
                with patch("routes.chat.config.is_model_enabled", return_value=True):
                    with patch("routes.chat.config.get_llm_provider", return_value=object()):
                        with patch("routes.chat.ask_stream", side_effect=_fake_stream):
                            resp = chat_client.post(
                                "/v1/chat/completions",
                                json={
                                    "model": "llama",
                                    "stream": True,
                                    "messages": [{"role": "user", "content": "hi"}],
                                },
                            )
    assert resp.status_code == 200
    assert "egress guard" in resp.text


def test_legitimate_local_traffic_under_scope(monkeypatch):
    tethered = pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")
    with tethered.scope(allow=["127.0.0.1", "localhost"], allow_localhost=False):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            sock.connect(("127.0.0.1", 9))
        except (ConnectionRefusedError, TimeoutError, OSError):
            pass
        finally:
            sock.close()


def test_loopback_subnet_not_blanket_exempt(monkeypatch):
    """127.0.0.2 must not bypass allowlist when allow_localhost=False (LUM-570)."""
    tethered = pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")
    with tethered.scope(allow=["127.0.0.1"], allow_localhost=False):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            with pytest.raises((tethered.EgressBlocked, eg.EgressBlockedError, OSError)):
                sock.connect(("127.0.0.2", 9))
        finally:
            sock.close()


def test_egress_blocks_disallowed_host(monkeypatch):
    tethered = pytest.importorskip("tethered")
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")
    with tethered.scope(allow=["127.0.0.1"]):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        try:
            with pytest.raises((tethered.EgressBlocked, EgressBlockedError, OSError)):
                sock.connect(("93.184.216.34", 80))
        finally:
            sock.close()


def test_stream_preflight_egress_blocked_chat_route(monkeypatch, chat_client):
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")
    with patch("services.privacy_mode.resolve_model_for_request", return_value=("chatgpt", None)):
        with patch("routes.chat.build_injected_context", side_effect=_passthrough_history):
            with patch(
                "routes.chat.config.get_model_config",
                return_value={"tools": False, "api_key_env": "OPENAI_API_KEY"},
            ):
                with patch("routes.chat.config.is_model_enabled", return_value=True):
                    with patch(
                        "routes.chat.config.get_llm_provider",
                        side_effect=EgressBlockedError(host="evil.example"),
                    ):
                        resp = chat_client.post(
                            "/v1/chat/completions",
                            json={
                                "model": "chatgpt",
                                "stream": True,
                                "messages": [{"role": "user", "content": "hi"}],
                            },
                        )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "egress_blocked"


@patch("services.privacy_mode.resolve_model_for_request", return_value=("chatgpt", None))
def test_api_v1_stream_preflight_egress_blocked(monkeypatch, api_v1_client):
    monkeypatch.setenv("LUMOGIS_FF_EGRESS_GUARD", "true")
    with patch("routes.api_v1.chat._resolve_scoped_injection") as resolve:
        resolve.return_value = ("hi", [], set(), [], False)
        with patch("routes.api_v1.chat.config.is_model_enabled", return_value=True):
            with patch(
                "routes.api_v1.chat.config.get_llm_provider",
                side_effect=EgressBlockedError(host="evil.example"),
            ):
                resp = api_v1_client.post(
                    "/api/v1/chat/completions",
                    json={
                        "model": "chatgpt",
                        "stream": True,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "egress_blocked"
