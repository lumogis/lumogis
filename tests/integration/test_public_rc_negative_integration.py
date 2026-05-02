# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Negative integration checks when ``AUTH_ENABLED=true`` (RC compose).

Skipped automatically when the stack runs single-user dev mode (login → 503).
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

BASE_URL = os.environ.get("LUMOGIS_API_URL", "http://127.0.0.1:8000").strip().rstrip("/")


def _family_lan_auth_enabled(client: httpx.Client) -> bool:
    """``False`` when ``AUTH_ENABLED=false`` (login handler returns 503)."""
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "__probe__@invalid.local", "password": "not-a-real-password"},
    )
    return r.status_code != 503


@pytest.fixture(scope="module")
def api_anon():
    c = httpx.Client(base_url=BASE_URL, timeout=120.0)
    try:
        hz = c.get("/healthz")
        if hz.status_code != 200:
            pytest.skip(f"Orchestrator not healthy at {BASE_URL}: HTTP {hz.status_code}")
    except httpx.ConnectError as e:
        c.close()
        pytest.skip(f"Orchestrator unreachable at {BASE_URL}: {e}")
    yield c
    c.close()


@pytest.mark.public_rc
def test_refresh_without_cookie_returns_401(api_anon: httpx.Client):
    if not _family_lan_auth_enabled(api_anon):
        pytest.skip("AUTH_ENABLED=false — refresh negative paths not applicable")

    origin = os.environ.get("LUMOGIS_PUBLIC_ORIGIN", "http://127.0.0.1").strip().rstrip("/") or "http://127.0.0.1"
    r = api_anon.post("/api/v1/auth/refresh", headers={"Origin": origin})
    assert r.status_code == 401
    assert r.json().get("detail") == "missing refresh cookie"


@pytest.mark.public_rc
def test_refresh_wrong_origin_returns_403_when_origin_pinned(api_anon: httpx.Client):
    if not _family_lan_auth_enabled(api_anon):
        pytest.skip("AUTH_ENABLED=false")

    expected = os.environ.get("LUMOGIS_PUBLIC_ORIGIN", "").strip().rstrip("/")
    if not expected:
        pytest.skip("LUMOGIS_PUBLIC_ORIGIN unset — CSRF dependency bypassed")

    r = api_anon.post(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://attacker.example"},
    )
    assert r.status_code == 403
    assert r.json().get("detail") == "origin mismatch"


@pytest.mark.public_rc
def test_auth_me_without_bearer_returns_401(api_anon: httpx.Client):
    if not _family_lan_auth_enabled(api_anon):
        pytest.skip("AUTH_ENABLED=false")

    r = api_anon.get("/api/v1/auth/me")
    assert r.status_code == 401


@pytest.mark.public_rc
def test_chat_completions_without_bearer_returns_401(api_anon: httpx.Client):
    if not _family_lan_auth_enabled(api_anon):
        pytest.skip("AUTH_ENABLED=false")

    r = api_anon.post(
        "/api/v1/chat/completions",
        json={
            "model": "llama",
            "stream": False,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert r.status_code == 401


@pytest.mark.public_rc
def test_captures_list_without_bearer_returns_401(api_anon: httpx.Client):
    if not _family_lan_auth_enabled(api_anon):
        pytest.skip("AUTH_ENABLED=false")

    r = api_anon.get("/api/v1/captures")
    assert r.status_code == 401


@pytest.mark.public_rc
def test_approvals_pending_without_bearer_returns_401(api_anon: httpx.Client):
    if not _family_lan_auth_enabled(api_anon):
        pytest.skip("AUTH_ENABLED=false")

    r = api_anon.get("/api/v1/approvals/pending")
    assert r.status_code == 401


@pytest.mark.public_rc
def test_capture_blank_text_returns_422(api):
    """Whitespace-only text normalizes to empty — service rejects before insert."""
    r = api.post(
        "/api/v1/captures",
        json={"text": " \n\t ", "client_id": str(uuid.uuid4())},
    )
    assert r.status_code == 422
    body = r.json()
    assert body.get("detail", {}).get("error") == "capture_requires_text_or_url"
