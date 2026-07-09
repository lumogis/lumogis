# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""API route tests for privacy mode (LUM-194)."""

from __future__ import annotations

import json
from unittest.mock import patch

import main
import pytest
from auth import UserContext
from fastapi import HTTPException
from fastapi.testclient import TestClient
from models.privacy_mode import PrivacyUserRestriction
from services.privacy_mode import PrivacyModeBlocked

_ADMIN = UserContext(user_id="admin", role="admin", is_authenticated=True)
_ALICE = UserContext(user_id="alice", role="user", is_authenticated=True)


@pytest.fixture(autouse=True)
def _auth_off_for_privacy_route_tests(monkeypatch):
    """Privacy route unit tests patch auth deps; ignore maintainer .env AUTH_ENABLED=true."""
    monkeypatch.setenv("AUTH_ENABLED", "false")


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


@patch("routes.api_v1.privacy_mode.privacy_svc.get_me_privacy_mode")
@patch("routes.api_v1.privacy_mode.require_user", return_value=_ALICE)
def test_get_me_privacy_mode(_req, mock_get, client):
    mock_get.return_value = {
        "instance": {
            "privacy_mode": "allow_cloud",
            "privacy_mode_locked": False,
            "privacy_effective": "allow_cloud",
        },
        "user_restriction": "inherit",
        "privacy_effective": "allow_cloud",
        "can_allow_cloud": True,
    }
    resp = client.get("/api/v1/me/privacy-mode")
    assert resp.status_code == 200
    assert resp.json()["privacy_effective"] == "allow_cloud"


@patch("routes.api_v1.privacy_mode.privacy_svc.patch_me_privacy_mode")
@patch("routes.api_v1.privacy_mode.require_user", return_value=_ALICE)
def test_patch_me_privacy_loosen_returns_403(_req, mock_patch, client):
    mock_patch.side_effect = HTTPException(status_code=403, detail="privacy_restriction_denied")
    resp = client.patch(
        "/api/v1/me/privacy-mode",
        json={"user_restriction": "inherit"},
    )
    assert resp.status_code == 403


@patch("routes.chat._inject_context", side_effect=lambda q, h, m, u, **kw: h)
@patch("routes.chat.config.get_model_config", return_value={"tools": False, "api_key_env": "ANTHROPIC_API_KEY"})
@patch("routes.chat.config.is_model_enabled", return_value=True)
@patch("routes.chat.ask", return_value="ok")
@patch(
    "services.privacy_mode.resolve_model_for_request",
    side_effect=PrivacyModeBlocked("claude"),
)
def test_chat_403_envelope_privacy_mode_blocked(_resolve, _ask, _enabled, _cfg, _ctx, client):
    with patch("routes.chat.get_user", return_value=_ALICE):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude",
                "stream": False,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 403
    body = resp.json()
    assert "detail" not in body
    assert body["error"]["code"] == "privacy_mode_blocked"
    assert body["error"]["model"] == "claude"


@patch("routes.chat._inject_context", side_effect=lambda q, h, m, u, **kw: h)
@patch(
    "services.privacy_mode.resolve_model_for_request",
    return_value=("llama", {"fallback_applied": True, "requested_model": "claude", "message": "warn"}),
)
@patch("routes.chat.config.get_model_config", return_value={"tools": False})
@patch("routes.chat.config.is_model_enabled", return_value=True)
@patch("routes.chat.ask", return_value="local answer")
def test_chat_fallback_includes_lumogis_privacy(_ask, _enabled, _cfg, _resolve, _ctx, client):
    with patch("routes.chat.get_user", return_value=_ALICE):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude",
                "stream": False,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "llama"
    assert body["lumogis"]["privacy"]["fallback_applied"] is True


@patch("routes.chat._inject_context", side_effect=lambda q, h, m, u, **kw: h)
@patch("routes.chat.config.get_model_config", return_value={"tools": False, "api_key_env": "ANTHROPIC_API_KEY"})
@patch("routes.chat.config.is_model_enabled", return_value=True)
@patch("routes.chat.ask", return_value="should not run")
@patch(
    "services.privacy_mode.resolve_model_for_request",
    side_effect=PrivacyModeBlocked("claude"),
)
def test_chat_403_when_no_local_fallback_available(_resolve, _ask, _enabled, _cfg, _ctx, client):
    with patch("routes.chat.get_user", return_value=_ALICE):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude",
                "stream": False,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "privacy_mode_blocked"
    _ask.assert_not_called()


def test_streaming_privacy_metadata_shape_matches_non_streaming():
    from routes.chat import stream_completion

    chunks = list(
        stream_completion(
            iter([]),
            "llama",
            privacy_metadata={"fallback_applied": True, "requested_model": "claude", "message": "m"},
        )
    )
    assert chunks
    payload = json.loads(chunks[0].removeprefix("data: ").strip())
    assert payload["lumogis"]["privacy"]["fallback_applied"] is True
