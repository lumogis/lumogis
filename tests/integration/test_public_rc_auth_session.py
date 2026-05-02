# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Family-LAN login → refresh cookie rotation → ``GET /api/v1/auth/me``."""

from __future__ import annotations

import os
import re

import httpx
import pytest

pytestmark = pytest.mark.integration

ORIGIN = os.environ.get("LUMOGIS_PUBLIC_ORIGIN", "http://127.0.0.1").strip().rstrip("/") or "http://127.0.0.1"


def _lumogis_refresh_cookie_value(login_response: httpx.Response) -> str | None:
    """Extract refresh cookie value from login ``Set-Cookie``.

    The orchestrator marks ``lumogis_refresh`` ``Secure``; HTTP stacks normally
    **suppress sending it over plain HTTP**, even though Set-Cookie is visible on
    the wire — paste it explicitly on ``POST /auth/refresh``.
    """
    raw = login_response.headers.get("set-cookie") or ""
    m = re.search(r"lumogis_refresh=([^;]+)", raw)
    return m.group(1) if m else None


@pytest.mark.public_rc
def test_login_refresh_me_round_trip():
    email = os.environ.get("LUMOGIS_WEB_SMOKE_EMAIL", "").strip()
    password = os.environ.get("LUMOGIS_WEB_SMOKE_PASSWORD", "")
    base = os.environ.get("LUMOGIS_API_URL", "http://127.0.0.1:8000").strip().rstrip("/")

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
        assert "access_token" in lr.json()

        refresh_val = _lumogis_refresh_cookie_value(lr)
        assert refresh_val, "login must emit lumogis_refresh Set-Cookie"

        rr = raw.post(
            "/api/v1/auth/refresh",
            headers={
                "Origin": ORIGIN,
                "Cookie": f"lumogis_refresh={refresh_val}",
            },
        )
        assert rr.status_code == 200, rr.text[:800]
        refreshed = rr.json().get("access_token")
        assert refreshed

        me = raw.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {refreshed}", "Origin": ORIGIN},
        )
        assert me.status_code == 200
        body = me.json()
        assert body.get("email") == email


@pytest.mark.public_rc
def test_me_rejects_bad_bearer():
    base = os.environ.get("LUMOGIS_API_URL", "http://127.0.0.1:8000").strip().rstrip("/")
    with httpx.Client(base_url=base, timeout=30.0) as c:
        r = c.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401
