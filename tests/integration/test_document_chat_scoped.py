# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Live document-scoped chat round-trip (LUM-503).

Proves ``POST /api/v1/chat/completions`` with ``document_id`` returns scoped
citations referencing the seeded document. Requires the document-chat fixture to
be seeded first (``scripts/seed-document-chat-fixture.sh``) and smoke credentials.

Complements the mocked-Qdrant units in
``orchestrator/tests/test_api_v1_document_chat.py``: this is the live retrieval
round-trip those cannot cover.
"""

from __future__ import annotations

import os

import httpx
import pytest

pytestmark = pytest.mark.integration

ORIGIN = (
    os.environ.get("LUMOGIS_PUBLIC_ORIGIN", "http://127.0.0.1").strip().rstrip("/")
    or "http://127.0.0.1"
)
# Matches scripts/seed_document_chat_fixture.py FIXTURE_PATH basename.
FIXTURE_BASENAME = "lum503-document-chat.md"
# A query whose answer lives in a distinctive fixture sentence (drives retrieval).
SCOPED_QUERY = "What is the secret pangram in this document?"
CHAT_MODEL = os.environ.get("LUMOGIS_E2E_CHAT_MODEL", "claude")


def _smoke_login(base: str) -> str | None:
    email = os.environ.get("LUMOGIS_WEB_SMOKE_EMAIL", "").strip()
    password = os.environ.get("LUMOGIS_WEB_SMOKE_PASSWORD", "")
    if not email or len(password) < 12:
        pytest.skip("smoke credentials unset")
    with httpx.Client(base_url=base, timeout=180.0) as raw:
        lr = raw.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            headers={"Origin": ORIGIN},
        )
        if lr.status_code == 503:
            pytest.skip("AUTH_ENABLED=false — login unavailable")
        assert lr.status_code == 200, lr.text[:800]
        token = lr.json().get("access_token")
        assert token, "login must return access_token"
        return token


def _find_seeded_document_id(base: str, token: str) -> int:
    with httpx.Client(base_url=base, timeout=180.0) as c:
        resp = c.get(
            "/api/v1/documents",
            params={"limit": 100},
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
        assert resp.status_code == 200, resp.text[:800]
        for doc in resp.json().get("documents", []):
            file_path = doc.get("file_path") or ""
            if file_path.endswith(FIXTURE_BASENAME) and doc.get("document_id") is not None:
                return int(doc["document_id"])
    pytest.skip(
        "document-chat fixture not seeded — run scripts/seed-document-chat-fixture.sh first"
    )


@pytest.mark.public_rc
def test_scoped_chat_returns_document_citations():
    base = os.environ.get("LUMOGIS_API_URL", "http://127.0.0.1:8000").strip().rstrip("/")
    token = _smoke_login(base)
    document_id = _find_seeded_document_id(base, token)

    with httpx.Client(base_url=base, timeout=180.0) as c:
        resp = c.post(
            "/api/v1/chat/completions",
            json={
                "model": CHAT_MODEL,
                "stream": False,
                "document_id": document_id,
                "messages": [{"role": "user", "content": SCOPED_QUERY}],
            },
            headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN},
        )
    assert resp.status_code == 200, resp.text[:800]
    body = resp.json()
    lumogis = body.get("lumogis")
    assert lumogis is not None, "scoped chat must carry the lumogis extension block"
    citations = lumogis.get("context_citations") or []
    assert citations, "scoped chat must return at least one document citation"
    # chunk_index is ranking-dependent; the stable scoping signal is the file_path.
    assert any(
        (cit.get("file_path") or "").endswith(FIXTURE_BASENAME) for cit in citations
    ), f"citations did not reference the seeded document: {citations}"
