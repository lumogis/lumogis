# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Caddy + Lumogis Web front-door smoke (parent plan Phase 1 Pass 1.5 step 16).

Exercises same-origin routing through Caddy: SPA HTML, ``/health``,
``POST /api/v1/auth/login``, chat/models/search/kg/approvals JSON, and a
short read from ``GET /api/v1/events`` (SSE).

**Run:** full stack up with Pass 1.5 compose (Caddy on port 80), then::

    export LUMOGIS_WEB_BASE_URL=http://127.0.0.1
    export LUMOGIS_WEB_SMOKE_EMAIL=you@example.com
    export LUMOGIS_WEB_SMOKE_PASSWORD='your-twelve-char-password'
    make test-integration

Or point at a remote host by changing ``LUMOGIS_WEB_BASE_URL``.

If email/password env vars are unset, the module skips (no failure in
contributor laptops without a running stack).
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

pytestmark = pytest.mark.integration

WEB_BASE = os.environ.get("LUMOGIS_WEB_BASE_URL", "http://127.0.0.1").rstrip("/")
SMOKE_EMAIL = os.environ.get("LUMOGIS_WEB_SMOKE_EMAIL", "").strip()
SMOKE_PASSWORD = os.environ.get("LUMOGIS_WEB_SMOKE_PASSWORD", "")


@pytest.fixture(scope="module")
def web_client() -> httpx.Client:
    c = httpx.Client(base_url=WEB_BASE, timeout=120.0)
    try:
        r = c.get("/health")
        if r.status_code != 200:
            pytest.skip(f"{WEB_BASE}/health not OK (got {r.status_code}); is Caddy up?")
    except httpx.ConnectError as e:
        pytest.skip(f"Caddy / stack unreachable at {WEB_BASE}: {e}")
    yield c
    c.close()


def _require_smoke_creds() -> None:
    if not SMOKE_EMAIL or not SMOKE_PASSWORD:
        pytest.skip(
            "Set LUMOGIS_WEB_SMOKE_EMAIL and LUMOGIS_WEB_SMOKE_PASSWORD (≥12 chars) "
            "to run the Caddy front-door smoke test.",
        )
    if len(SMOKE_PASSWORD) < 12:
        pytest.skip("LUMOGIS_WEB_SMOKE_PASSWORD must be at least 12 characters.")


@pytest.mark.integration
@pytest.mark.public_rc
def test_spa_index_html(web_client: httpx.Client) -> None:
    r = web_client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    body = r.text.lower()
    assert "root" in body or "lumogis" in body


@pytest.mark.integration
@pytest.mark.public_rc
def test_health_through_caddy(web_client: httpx.Client) -> None:
    r = web_client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert "status" in data or "qdrant_doc_count" in data


@pytest.mark.integration
def test_auth_chat_search_kg_approvals_sse_roundtrip(web_client: httpx.Client) -> None:
    _require_smoke_creds()

    login = web_client.post(
        "/api/v1/auth/login",
        json={"email": SMOKE_EMAIL, "password": SMOKE_PASSWORD},
    )
    if login.status_code == 503:
        pytest.skip(
            "Login returned 503 (AUTH_ENABLED=false dev mode?). "
            "Smoke test needs AUTH_ENABLED=true with a real user.",
        )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    mr = web_client.get("/api/v1/models", headers=auth)
    assert mr.status_code == 200, mr.text
    models = mr.json()["models"]
    assert isinstance(models, list)
    enabled = [m for m in models if m.get("enabled")]
    assert enabled, "need at least one enabled model for chat smoke"
    model_id = enabled[0]["id"]

    sr = web_client.get("/api/v1/memory/search", params={"q": "integration", "limit": 3}, headers=auth)
    assert sr.status_code == 200, sr.text

    kr = web_client.get("/api/v1/kg/search", params={"q": "a", "limit": 3}, headers=auth)
    assert kr.status_code == 200, kr.text

    pr = web_client.get("/api/v1/approvals/pending", headers=auth)
    assert pr.status_code == 200, pr.text
    pending = pr.json().get("pending")
    assert isinstance(pending, list)

    chat_body = {
        "model": model_id,
        "stream": True,
        "messages": [{"role": "user", "content": "Say only: ok"}],
    }
    with web_client.stream(
        "POST",
        "/api/v1/chat/completions",
        json=chat_body,
        headers={**auth, "Accept": "text/event-stream"},
        timeout=httpx.Timeout(120.0, read=120.0),
    ) as stream:
        if stream.status_code != 200:
            detail = stream.read().decode(errors="replace")
            pytest.fail(f"chat stream HTTP {stream.status_code}: {detail[:800]}")
        buf = b""
        t0 = time.monotonic()
        for chunk in stream.iter_bytes():
            buf += chunk
            if len(buf) >= 64 or time.monotonic() - t0 > 45:
                break
        text = buf.decode(errors="replace")
        assert "data:" in text or "[DONE]" in text, f"expected SSE chunk, got: {text[:200]!r}"

    headers = {
        **auth,
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    with web_client.stream(
        "GET",
        "/api/v1/events",
        headers=headers,
        timeout=httpx.Timeout(60.0, read=60.0),
    ) as ev:
        assert ev.status_code == 200, ev.read().decode(errors="replace")
        buf = b""
        t0 = time.monotonic()
        for chunk in ev.iter_bytes():
            buf += chunk
            if len(buf) >= 32 or time.monotonic() - t0 > 25:
                break
        head = buf.decode(errors="replace")
        assert "event:" in head or "data:" in head or ": ping" in head, (
            f"expected SSE prelude, got: {head[:200]!r}"
        )
